"""Optional, local Tempo plan constraints for the existing booking engine.

This is not a scheduler or an ASIMUT event source. It contributes conflicts only:
reservations, quotas, receipts, authentication and worker ownership stay with
Booker. Publishing and the final Save decision share the same interprocess lock.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json
from runtime_guard import SingleInstanceLock


SNAPSHOT_PATH = Path.home() / "Documents" / "Apps" / "Tempo" / "data" / "coordination.json"
RUNTIME_LOCK_PATH = Path(__file__).resolve().parent / "data" / "booker-runtime.lock"
_ACTIVE = ContextVar("tempo_coordination", default=None)
_MANUAL_WINDOW = ContextVar("tempo_manual_practice_window", default=None)


@contextmanager
def manual_practice_window(day, start, end):
    """One explicit fill may replace planning windows, but never busy tasks.

    The accepted document, expiry and revision checks remain unchanged.
    """
    key = _date(day)
    if not 0 <= start < end < 24:
        raise CoordinationError('Invalid explicit practice interval')
    token = _MANUAL_WINDOW.set((key, start, end))
    try:
        yield
    finally:
        _MANUAL_WINDOW.reset(token)


class CoordinationError(SettingsError):
    """An accepted Tempo plan cannot safely authorize this booking."""


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise CoordinationError("Coordination time must include its timezone")
    return value


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed
    except (ValueError, TypeError):
        pass
    raise CoordinationError("Tempo plan has an invalid timestamp")


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise CoordinationError("Tempo plan has an invalid date")


def _minutes(value, *, end=False):
    if isinstance(value, str) and len(value) == 5 and value[2] == ":":
        try:
            hour, minute = map(int, value.split(":"))
            if value == f"{hour:02}:{minute:02}" and 0 <= hour < 24 and 0 <= minute < 60:
                return hour * 60 + minute
            if end and value == "24:00":
                return 1440
        except ValueError:
            pass
    raise CoordinationError("Tempo plan has an invalid clock time")


def _text(value, name, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CoordinationError(f"Tempo plan has an invalid {name}")
    return value


def validate_snapshot(raw):
    """Return a normalized bounded document; reject ambiguous/out-of-scope data."""
    if not isinstance(raw, dict) or raw.get("version") != 1 or isinstance(raw.get("version"), bool):
        raise CoordinationError("Unsupported Tempo coordination format")
    instance = _text(raw.get("instance_id"), "instance")
    revision = raw.get("revision")
    if type(revision) is not int or revision < 1:
        raise CoordinationError("Tempo revision must be a positive integer")
    if type(raw.get("enabled")) is not bool:
        raise CoordinationError("Tempo enabled state must be explicit")
    generated, until = _instant(raw.get("generated_at")), _instant(raw.get("valid_until"))
    if not generated < until <= generated + timedelta(hours=24):
        raise CoordinationError("Tempo plan must expire within 24 hours")
    dates = raw.get("dates", [])
    if not isinstance(dates, list) or len(dates) > 35:
        raise CoordinationError("Tempo can coordinate at most 35 dates")
    dates = [_date(day) for day in dates]
    if len(dates) != len(set(dates)):
        raise CoordinationError("Tempo dates must be unique")
    if any(abs((date.fromisoformat(day) - generated.date()).days) > 40 for day in dates):
        raise CoordinationError("Tempo dates are outside the bounded planning window")
    if raw["enabled"] and not dates:
        raise CoordinationError("An enabled Tempo plan needs an explicit date scope")
    result = dict(version=1, instance_id=instance, revision=revision, enabled=raw["enabled"],
                  generated_at=generated.isoformat(), valid_until=until.isoformat(), dates=sorted(dates))
    for kind, limit in (("busy", 2000), ("windows", 500)):
        values = raw.get(kind, [])
        if not isinstance(values, list) or len(values) > limit:
            raise CoordinationError(f"Tempo {kind} are invalid")
        normalized = []
        for item in values:
            if not isinstance(item, dict):
                raise CoordinationError(f"Tempo {kind} entry is invalid")
            day = _date(item.get("date"))
            start, end = _minutes(item.get("start")), _minutes(item.get("end"), end=True)
            if day not in dates or start >= end:
                raise CoordinationError(f"Tempo {kind} entry lies outside its date scope")
            entry = dict(date=day, start=item["start"], end=item["end"])
            if kind == "windows":
                rooms = item.get("rooms", [])
                if not isinstance(rooms, list) or len(rooms) > 100:
                    raise CoordinationError("Tempo room choices are invalid")
                entry["rooms"] = sorted(set(_text(room, "room", 100) for room in rooms))
                for key in ("task_id", "block_id"):
                    if key in item:
                        entry[key] = _text(item[key], key)
            normalized.append(entry)
        result[kind] = normalized
    ids = raw.get("protected_event_ids", [])
    if not isinstance(ids, list) or len(ids) > 2000 or any(type(i) is not int or i <= 0 for i in ids):
        raise CoordinationError("Tempo protected reservations are invalid")
    result["protected_event_ids"] = sorted(set(ids))
    return result


def _read(path):
    if path.stat().st_size > 1024 * 1024:
        raise CoordinationError("Tempo coordination file is too large")
    return validate_snapshot(json.loads(path.read_text(encoding="utf-8")))


def _accepted_path(path):
    return path.with_name(path.name + ".accepted.json")


def _lock(path):
    return InterProcessFileLock(path.with_suffix(path.suffix + ".lock"), timeout=2)


@dataclass(frozen=True)
class Snapshot:
    document: dict | None
    path: Path
    problem: str = ""

    @property
    def enabled(self):
        return self.document is not None and self.document["enabled"]

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.document, sort_keys=True).encode()).hexdigest()

    def scoped(self, day):
        return self.enabled and _date(day) in self.document["dates"]

    def protected_ids(self):
        return set(self.document["protected_event_ids"]) if self.enabled else set()

    def planning_blocker(self, day):
        if not self.scoped(day):
            return ""
        if self.problem:
            return self.problem
        if not any(window["date"] == _date(day) for window in self.document["windows"]):
            return "Tempo has no accepted practice window for this date; replan in Tempo to resume automatic booking"
        return ""

    def blocked_ranges(self, day, room=None):
        """Hours blocked by Tempo, without reservation/quota accounting."""
        key = _date(day)
        if not self.scoped(key):
            return []
        if self.problem:
            return [(0.0, 24.0)]
        windows = sorted((_minutes(w["start"]), _minutes(w["end"], end=True))
            for w in self.document["windows"] if w["date"] == key
            and (room is None or not w["rooms"] or room in w["rooms"]))
        blocked, edge = [], 0
        for start, end in windows:
            if edge < start:
                blocked.append((edge / 60, start / 60))
            edge = max(edge, end)
        if edge < 1440:
            blocked.append((edge / 60, 24.0))
        manual = _MANUAL_WINDOW.get()
        if manual and manual[0] == key:
            left, right = manual[1:]
            blocked = [(a, b) for start, end in blocked
                       for a, b in ((start, min(end, left)), (max(start, right), end)) if a < b]
        blocked.extend((_minutes(b["start"]) / 60, _minutes(b["end"], end=True) / 60)
                       for b in self.document["busy"] if b["date"] == key)
        return blocked

    def permits(self, day, start, end, room=None):
        if not self.scoped(day):
            return True
        if self.problem or any(start < b and end > a for a, b in self.blocked_ranges(day, room)):
            return False
        manual = _MANUAL_WINDOW.get()
        if manual and manual[0] == _date(day) and manual[1] <= start < end <= manual[2]:
            return True
        # An interval cannot span two adjacent sessions with different room/task
        # ownership merely because their union contains no free gap.
        return any(w["date"] == _date(day) and _minutes(w["start"]) / 60 <= start
                   and end <= _minutes(w["end"], end=True) / 60
                   and (room is None or not w["rooms"] or room in w["rooms"])
                   for w in self.document["windows"])

    def allowed_end(self, day, start, desired_end, room):
        """Cap a contiguous extension against its whole accepted session."""
        if not self.scoped(day):
            return desired_end
        if self.problem:
            return start
        ends = [start]
        for window in self.document["windows"]:
            if (window["date"] != _date(day) or _minutes(window["start"]) / 60 > start
                    or (window["rooms"] and room not in window["rooms"])):
                continue
            end = min(desired_end, _minutes(window["end"], end=True) / 60)
            for busy in self.document["busy"]:
                if busy["date"] == _date(day):
                    a, b = _minutes(busy["start"]) / 60, _minutes(busy["end"], end=True) / 60
                    if start < b and end > a:
                        end = min(end, max(start, a))
            ends.append(end)
        return max(ends)

    def constrain_availability(self, values, day):
        """Clip per-room upgrade gaps; never fabricate an ASIMUT event."""
        if not self.scoped(day):
            return values
        result = []
        for room in values:
            slots = []
            for slot in room.get("slots", ()):
                # Preserve each task window as a separate gap; merging adjacent
                # windows could propose one reservation spanning two tasks.
                segments = [(max(slot["startHour"], _minutes(w["start"]) / 60),
                             min(slot["endHour"], _minutes(w["end"], end=True) / 60))
                    for w in self.document["windows"] if w["date"] == _date(day)
                    and (not w["rooms"] or room.get("room") in w["rooms"])]
                segments = [(a, b) for a, b in segments if a < b]
                for a, b in self.blocked_ranges(day, room.get("room")):
                    clipped = []
                    for start, end in segments:
                        if end <= a or start >= b:
                            clipped.append((start, end))
                        else:
                            if start < a:
                                clipped.append((start, a))
                            if end > b:
                                clipped.append((b, end))
                    segments = clipped
                slots.extend({**slot, "startHour": a, "endHour": b} for a, b in segments if a < b)
            result.append({**room, "slots": slots})
        return result

    def practice_plan_overlay(self, plan, reservation_ranges):
        """Raise only this run's demand to include explicitly accepted tasks.

        Install once after the first complete agenda scan. Taking the interval
        union counts confirmed prefixes once; copying the immutable plan keeps
        later newly created bookings from continually increasing the target.
        Saved preferences, disabled planning and all booking limits are retained.
        """
        if not self.enabled or self.problem or not plan.enabled:
            return plan
        overrides = dict(plan.date_overrides or {})
        for day in self.document["dates"]:
            intervals = [(_minutes(w["start"]), _minutes(w["end"], end=True))
                         for w in self.document["windows"] if w["date"] == day]
            intervals.extend((round(a * 60), round(b * 60))
                             for a, b, _room in reservation_ranges.get(day, ()))
            total, edge = 0, 0
            for start, end in sorted(intervals):
                total += max(0, end - max(edge, start))
                edge = max(edge, end)
            overrides[day] = max(plan.target_for(date.fromisoformat(day)) or 0, total / 60)
        return replace(plan, date_overrides=overrides)


def _load_locked(path, now):
    accepted_path = _accepted_path(path)
    accepted = None
    if accepted_path.exists():
        try:
            accepted = _read(accepted_path)
        except (OSError, ValueError, TypeError, SettingsError) as exc:
            raise CoordinationError("The durable Tempo coordination scope cannot be verified") from exc
    try:
        document = _read(path)
        if accepted and (document["instance_id"] != accepted["instance_id"]
                         or document["revision"] < accepted["revision"]
                         or (document["revision"] == accepted["revision"] and document != accepted)):
            raise CoordinationError("Tempo coordination revision changed unexpectedly")
        # Expiration never restores autonomous scheduling over the accepted days.
        problem = ("Tempo's plan expired; replan in Tempo to resume booking" if document["enabled"]
                   and (_instant(document["valid_until"]) <= now
                        or _instant(document["generated_at"]) > now + timedelta(minutes=2)) else "")
        if document != accepted:
            atomic_write_json(accepted_path, document)
        return Snapshot(document, path, problem)
    except (OSError, ValueError, TypeError, SettingsError) as exc:
        if accepted:
            return Snapshot(accepted, path, "Tempo's latest plan is unavailable; replan in Tempo to resume booking")
        if not path.exists():
            return Snapshot(None, path)
        raise CoordinationError("The Tempo coordination file is unreadable; no booking was authorized") from exc


def read_snapshot(path=None, *, now=None):
    path = Path(path or SNAPSHOT_PATH)
    # Standalone installations remain a pure no-op, without even creating files.
    if not path.exists() and not _accepted_path(path).exists():
        return Snapshot(None, path)
    with _lock(path):
        return _load_locked(path, _now(now))


def read_public_status(path=None, *, now=None):
    snapshot = read_snapshot(path, now=now)
    document = snapshot.document or {}
    return dict(enabled=snapshot.enabled, available=not bool(snapshot.problem),
                revision=document.get("revision", 0), instance_id=document.get("instance_id"),
                dates=document.get("dates", []), valid_until=document.get("valid_until"),
                problem=snapshot.problem)


def publish_snapshot(raw, expected_revision=None, path=None, *, now=None):
    """CAS publication; call through Booker's Python so Save uses the same lock."""
    path, now = Path(path or SNAPSHOT_PATH), _now(now)
    document = validate_snapshot(raw)
    if _instant(document["generated_at"]) > now + timedelta(minutes=2) or _instant(document["valid_until"]) <= now:
        raise CoordinationError("Cannot publish an expired or future-dated Tempo plan")
    with _activation_boundary(document, path, now) as activation_locked, _lock(path):
        previous = _load_locked(path, now)
        old = previous.document
        revision = old["revision"] if old else 0
        if old == document:
            # An exact retry repairs an interrupted publication without creating
            # another revision or permitting another ASIMUT mutation.
            atomic_write_json(path, document)
            return dict(revision=revision, enabled=document["enabled"], unchanged=True)
        if expected_revision is not None and expected_revision != revision:
            raise CoordinationError("Tempo's plan changed; refresh before publishing")
        if old and old["instance_id"] != document["instance_id"]:
            raise CoordinationError("A different Tempo installation owns this coordination file")
        if document["revision"] != revision + 1:
            raise CoordinationError("Tempo coordination revision must advance by one")
        if document["enabled"] and not previous.enabled and not activation_locked:
            raise CoordinationError("Tempo activation changed; refresh before enabling coordination")
        # Durable scope goes first. A crash before the live file replacement
        # leaves the new scope blocked, never silently falling back to standalone.
        atomic_write_json(_accepted_path(path), document)
        atomic_write_json(path, document)
    return dict(revision=document["revision"], enabled=document["enabled"], unchanged=False)


@contextmanager
def _activation_boundary(document, path, now):
    """Do not activate while a pre-hook worker owns the runtime.

    Lock order is runtime then coordination, matching the normal booking worker.
    Later revisions need only the short coordination/Save lock. The publication
    recheck rejects a concurrent disable/enable transition without reversing the
    lock order or starting another worker.
    """
    if not document["enabled"] or read_snapshot(path, now=now).enabled:
        yield False
        return
    runtime = SingleInstanceLock(RUNTIME_LOCK_PATH)
    try:
        if not runtime.acquire():
            raise CoordinationError("AsimutBooker is busy; enable Tempo coordination after its current check finishes")
        yield True
    finally:
        runtime.release()


@contextmanager
def coordination_run(path=None, *, now=None):
    snapshot = read_snapshot(path, now=now)
    token = _ACTIVE.set(snapshot)
    try:
        yield snapshot
    finally:
        _ACTIVE.reset(token)


def current_snapshot():
    return _ACTIVE.get() or read_snapshot()


@contextmanager
def save_boundary(*, day=None, start=None, end=None, room=None, event_ids=(), automatic=True):
    expected = _ACTIVE.get()
    path = expected.path if expected is not None else SNAPSHOT_PATH
    if expected is None and not Path(path).exists() and not _accepted_path(Path(path)).exists():
        yield
        return
    with _lock(Path(path)):
        current = _load_locked(Path(path), _now())
        relevant = day is None or current.scoped(day) or (expected is not None and expected.scoped(day))
        if relevant and expected is not None and current.fingerprint != expected.fingerprint:
            raise CoordinationError("Tempo's plan changed during this run; replan before booking")
        if automatic and set(event_ids) & current.protected_ids():
            raise CoordinationError("This confirmed reservation is protected by Tempo")
        if day is not None and current.scoped(day):
            if start is None or end is None or room is None:
                raise CoordinationError("The complete booking interval is required by Tempo")
            start_hour, end_hour = _minutes(start) / 60, _minutes(end, end=True) / 60
            if start_hour >= end_hour or not current.permits(day, start_hour, end_hour, room):
                raise CoordinationError(current.problem or "This booking no longer fits Tempo's accepted practice time")
        yield
