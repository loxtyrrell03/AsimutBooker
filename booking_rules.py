"""Saved quota presets shared by the worker, settings and assistant.

These are local planning ceilings, never permission to override ASIMUT.
Room horizons, access, duration and every final Save still use live checks.
"""
from dataclasses import asdict, dataclass, replace
from math import isfinite
from app_settings import SettingsError

@dataclass(frozen=True)
class BookingRules:
    preset: str = 'new'
    rolling_quota_hours: float = 6
    peak_quota_minutes: int = 60
    free_horizon_minutes: int = 300
    peak_start_minutes: int = 540
    peak_end_minutes: int = 960
    free_horizon_overrides_peak: bool = False

    def to_dict(self):
        return asdict(self)

PRESETS = {'new': BookingRules(),
           'legacy': BookingRules(preset='legacy', rolling_quota_hours=28, peak_quota_minutes=120)}
PRESET_LABELS = {'new':'New quotas (6h / 1h peak)', 'legacy':'Previous quotas (28h / 2h peak)',
                 'custom':'Custom quotas'}

def load_booking_rules(settings):
    raw = settings.get('booking_rules', {})
    if not isinstance(raw, dict) or set(raw) - set(BookingRules.__dataclass_fields__):
        raise SettingsError('Booking rules contain unsupported fields')
    preset = raw.get('preset', 'new')
    if preset not in (*PRESETS, 'custom'):
        raise SettingsError('Choose new, legacy or custom booking rules')
    base = PRESETS.get(preset, PRESETS['new'])
    values = base.to_dict() | raw
    quota = values['rolling_quota_hours']
    if type(quota) not in (int, float) or not isfinite(quota) or not .5 <= quota <= 168 or quota * 4 % 1:
        raise SettingsError('Advance quota must be 0.5-168 hours in 15-minute steps')
    for key in ('peak_quota_minutes', 'free_horizon_minutes', 'peak_start_minutes', 'peak_end_minutes'):
        value = values[key]
        if type(value) is not int or not 0 <= value <= 1440 or value % 15:
            raise SettingsError(f'{key} must be 0-1440 minutes in 15-minute steps')
    if values['peak_start_minutes'] >= values['peak_end_minutes']:
        raise SettingsError('Peak end must be later than peak start')
    if type(values['free_horizon_overrides_peak']) is not bool:
        raise SettingsError('Free-horizon peak exception must be on or off')
    result = BookingRules(**values)
    if preset != 'custom' and replace(result, free_horizon_overrides_peak=base.free_horizon_overrides_peak) != base:
        raise SettingsError('Select custom before changing preset values')
    return result

def apply_booking_rules(settings, patch):
    if not isinstance(patch, dict) or not patch:
        raise SettingsError('Supply booking rules to save')
    if patch.get('preset') in PRESETS:
        # Numeric presets do not silently change the separately verified exception.
        raw = {'free_horizon_overrides_peak': load_booking_rules(settings).free_horizon_overrides_peak} | patch
    else:
        raw = load_booking_rules(settings).to_dict() | patch
    rule = load_booking_rules({'booking_rules':raw})
    settings['booking_rules'] = rule.to_dict()
    return rule.to_dict()

def describe_booking_rules(settings):
    rule = load_booking_rules(settings)
    return {
        'preset': rule.preset,
        'rolling_quota': f'{rule.rolling_quota_hours:g} hours of advance reservations; live ASIMUT balance may be lower',
        'weekday_peak_quota': f'{rule.peak_quota_minutes} minutes per weekday between '
            f'{rule.peak_start_minutes // 60:02d}:{rule.peak_start_minutes % 60:02d} and '
            f'{rule.peak_end_minutes // 60:02d}:{rule.peak_end_minutes % 60:02d}. ' +
            ('Completed sessions still count. The peak cap is waived only for a complete booking inside the free horizon.'
             if rule.free_horizon_overrides_peak else
             'Completed sessions still count that day; the free horizon does not reset this cap.'),
        'free_horizon': f'Both endpoints must fit inside the next {rule.free_horizon_minutes} minutes. '
            'Available quota is consumed normally. ASIMUT must approve every booking.',
    }
