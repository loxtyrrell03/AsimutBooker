"""Display-only evidence of a comprehensive sweep; never booking authority."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app_settings import InterProcessFileLock, atomic_write_json

PLAN_FILE = Path(__file__).resolve().parent / 'data' / 'upgrade_plan.json'


def publish_upgrade_plan(days, *, settings, status, actions, path=PLAN_FILE):
    controls = {key: settings.get(key) for key in ('room_preferences', 'time_preferences', 'date_time_preferences',
                'booking_strategy', 'practice_plan', 'disabled_dates', 'ignored_events', 'rebooking_blackouts')}
    fingerprint = hashlib.sha256(json.dumps(controls, sort_keys=True).encode()).hexdigest()
    document = dict(version=1, observed_at=datetime.now(timezone.utc).isoformat(),
                    settings_fingerprint=fingerprint, status=status, verified_actions=actions, days=days)
    path = Path(path)
    with InterProcessFileLock(path.with_suffix(path.suffix + '.lock')):
        atomic_write_json(path, document)
    return document
