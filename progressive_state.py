"""Persistent planning hints, separate from authoritative mutation receipts."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app_settings import InterProcessFileLock, atomic_write_json, SettingsError
from progressive_transactions import record, reservation
from room_upgrades import local_instant

STATE_FILE = Path(__file__).resolve().parent / 'data/progressive_upgrades.json'


def fingerprint(settings):
    # Use the exact preference projection rechecked by every Save. Opening a
    # calendar tab or saving window geometry must not discard a competitive
    # booking plan; changing time/target/ranking/blackouts must invalidate it.
    from booking_preferences_guard import _controls
    return hashlib.sha256(_controls(settings).encode()).hexdigest()


def validate_state(data):
    """Validate planning hints before both read and write; never relax the journal."""
    try:
        if (not isinstance(data, dict) or type(data.get('version')) is not int
                or data['version'] != 1 or set(data) != {'version', 'plans', 'denials'}):
            raise ValueError('Unsupported progressive plan format')
        if not isinstance(data['plans'], list) or not isinstance(data['denials'], dict):
            raise ValueError('Invalid progressive plan collections')
        seen = set()
        for p in data['plans']:
            if set(p) != {'id', 'fingerprint', 'originals', 'target', 'seed', 'next_at', 'observed_at'}:
                raise ValueError('Invalid progressive plan fields')
            UUID(p['id'])
            if p['id'] in seen:
                raise ValueError('Duplicate progressive plan identity')
            seen.add(p['id'])
            digest = p['fingerprint']
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid progressive preference fingerprint')
            if not isinstance(p['originals'], list) or not p['originals']:
                raise ValueError('Progressive plan needs fallback originals')
            originals = [reservation(r) for r in p['originals']]
            target = reservation(p['target'])
            seed = reservation(p['seed']) if p['seed'] is not None else None
            before = [*originals, *([seed] if seed else [])]
            if (len({r.event_id for r in before}) != len(before)
                    or any(r.day != target.day for r in before)
                    or sum(r.duration for r in before) != target.duration
                    or target.event_id not in {r.event_id for r in before}):
                raise ValueError('Progressive plan must preserve exact source identities and booked minutes')
            if seed and (seed.room != target.room or seed.start != target.start or seed.end >= target.end):
                raise ValueError('Progressive seed must be an unfinished prefix')
            ordered = sorted(before, key=lambda r: r.start)
            if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
                raise ValueError('Progressive sources cannot overlap')
            for key in ('next_at', 'observed_at'):
                if datetime.fromisoformat(p[key]).tzinfo is None:
                    raise ValueError('Progressive timestamps need timezone')
        for key, value in data['denials'].items():
            scope = json.loads(key)
            if (not isinstance(scope, list) or len(scope) != 2 or not isinstance(scope[0], str)
                    or not scope[0].strip() or date.fromisoformat(scope[1]).isoformat() != scope[1]
                    or datetime.fromisoformat(value).tzinfo is None):
                raise ValueError('Invalid room permission retry boundary')
        return data
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SettingsError('Progressive upgrade plan is unreadable; pending transactions remain protected') from exc


def _empty_state():
    return {'version': 1, 'plans': [], 'denials': {}}


def _load_state(path, *, lock_held=False):
    try:
        return validate_state(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        return _empty_state()
    except (SettingsError, ValueError) as exc:
        if not lock_held:
            # Re-read beneath the lock: a concurrent writer may already have
            # repaired/replaced the malformed snapshot we initially observed.
            with InterProcessFileLock(path.with_suffix('.json.lock')):
                return _load_state(path, lock_held=True)
        from mutation_receipts import load_journal
        journal = load_journal()  # Malformed mutation evidence always stops.
        if any(r['kind'] == 'transfer' and r['status'] == 'pending' for r in journal['receipts'].values()):
            raise SettingsError('Progressive hints are malformed while a transfer is pending; keeping all evidence for recovery') from exc
        backup = path.with_name(path.name + '.corrupt-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex)
        try:
            path.rename(backup)
        except FileNotFoundError:
            return _empty_state()
        empty = _empty_state()
        atomic_write_json(path, empty, backup=False)
        print(f'Progressive planning hints were malformed; retained {backup.name} and will rebuild from fresh availability. Bookings are unchanged.')
        return empty
    except OSError as exc:
        raise SettingsError('Progressive upgrade plan is unreadable; pending transactions remain protected') from exc


def load_state(path=None):
    return _load_state(Path(path or STATE_FILE))


def update_state(callback, path=None):
    path = Path(path or STATE_FILE)
    with InterProcessFileLock(path.with_suffix('.json.lock')):
        data = _load_state(path, lock_held=True)
        callback(data)
        validate_state(data)
        atomic_write_json(path, data, backup=True)
    return data


def protected_event_ids():
    return {r['event_id'] for p in load_state()['plans'] if p['seed'] is not None
            for r in [*p['originals'], p['seed']]}


def remember_opportunities(prospects, *, settings, policy, now, checked_dates):
    """A cache routes the next scan; every tuple and rule is freshly revalidated."""
    from progressive_discovery import PartialUpgradeOpportunity
    digest = fingerprint(settings)
    def update(data):
        active = [p for p in data['plans'] if p['seed'] is not None]
        protected = {r['event_id'] for p in active for r in [*p['originals'], p['seed']]}
        retained = [p for p in data['plans'] if p['seed'] is None
                    and p['target']['date'] not in checked_dates and p['fingerprint'] == digest]
        seen = set()
        new = []
        for prospect in prospects:
            c = prospect.change
            if any(r.event_id in protected for r in c.originals):
                continue
            target = c.replacement
            horizon = timedelta(minutes=policy.horizon_minutes_for(target.room),
                                seconds=policy.site_clock_offset_bounds[0])
            full_at = local_instant(target.day, target.end) - horizon
            if target.duration <= policy.minimum_block_minutes:
                continue
            if isinstance(prospect, PartialUpgradeOpportunity):
                # A real sparse gap can justify a partial upgrade even after
                # the full target is temporally open. Only this planner-proved
                # marker admits it; the tail is never represented as free.
                first = prospect.first_step.opens_at
            else:
                if full_at <= now:
                    continue
                first = local_instant(target.day, target.start + policy.minimum_block_minutes) - horizon
            key = (c.originals, target.room, target.start, target.end)
            if key in seen:
                continue
            seen.add(key)
            new.append(dict(id=str(uuid4()), fingerprint=digest,
                originals=[record(r) for r in c.originals], target=record(target), seed=None,
                next_at=first.isoformat(), observed_at=now.isoformat()))
        # Preserve all active transfers; limit only speculative alternatives.
        new.sort(key=lambda p: (p['next_at'], policy.room_order.index(p['target']['room'])))
        data['plans'] = active + retained + new[:512]
        data['denials'] = {k: v for k, v in data['denials'].items()
                           if datetime.fromisoformat(v) > now}
    return update_state(update)


def forget_plan(plan_id):
    return update_state(lambda data: data.update(plans=[p for p in data['plans'] if p['id'] != plan_id]))


def denial_key(room, day):
    return json.dumps([room, str(day)], separators=(',', ':'))


def remember_denial(room, day, *, now):
    # A live refusal is scoped to this date and expires; it is not permanent eligibility policy.
    return update_state(lambda data: data['denials'].update(
        {denial_key(room, day): (now + timedelta(minutes=30)).isoformat()}))
