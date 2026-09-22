export type TimePreference = { enabled: boolean; start_time: string; end_time: string; strict_mode: boolean };
export type DailyPlanning = {
  enabled: boolean; preferred_peak_start: string; preferred_peak_end: string;
  desired_peak_block_minutes: number; hold_early_peak_edges: boolean; foresight_minutes: number;
  minimum_later_options: number; fallback_lead_minutes: number;
  after_peak_mode: 'longest_first' | 'earliest_first' | 'room_first'; priority_mode: 'time_first' | 'room_first';
  upgrade_rooms: boolean; upgrade_freeze_hours: number;
  preferred_block_minutes: number; preferred_rest_minutes: number; prefer_fewer_room_changes: boolean;
};
export type AdvanceQuota = {
  room_mode: 'top' | 'selected' | 'all'; top_room_count: number; room_order: string[]; room_fallback: boolean;
  periods: { days: number[]; start: string; end: string }[]; period_mode: 'prefer' | 'only';
  distribution: 'balanced' | 'weighted' | 'concentrated' | 'quality';
  day_weights: number[]; day_caps_minutes: number[]; anchor_minutes: number; block_minutes: number;
  priority_mode: 'inherit' | 'time_first' | 'room_first'; date_order: 'inherit' | 'nearest' | 'furthest';
  reserve_minutes: number; wait_for_opening: boolean; fallback_lead_minutes: number | null;
};
export type Preferences = {
  preference_run?: { state: 'pending' | 'running' | 'completed' | 'failed' | 'paused'; message: string } | null;
  advance_quota: AdvanceQuota;
  booking_rules: BookingRules;
  revision: string;
  practice_plan: { enabled: boolean; default_hours: number; date_overrides: Record<string, number> };
  time_preferences: TimePreference & { preset?: string };
  disabled_dates: string[];
  date_time_preferences: Record<string, TimePreference>;
  booking_strategy: { reverse_date_order: boolean; daily_planning: DailyPlanning };
  room_preferences: { ordered_rooms: string[]; excluded_rooms: string[]; acceptable_instrument_tags: string[];
    acceptable_room_type_tags: string[]; required_feature_terms: string[]; minimum_block_minutes: number; allow_fragmented_sessions: boolean };
};

export function preferenceRunNotice(values: Preferences): string {
  if (values.preference_run?.state === 'pending') return ' Booker check queued; it will wait if another run is active.';
  if (values.preference_run?.state === 'running') return ' Booker is checking your changes.';
  return '';
}

export type BookingRules = { preset: 'new' | 'legacy' | 'custom'; rolling_quota_hours: number;
  free_horizon_overrides_peak: boolean; peak_quota_minutes: number; free_horizon_minutes: number; peak_start_minutes: number; peak_end_minutes: number };
