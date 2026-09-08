export type TimePreference = { enabled: boolean; start_time: string; end_time: string; strict_mode: boolean };
export type DailyPlanning = {
  enabled: boolean; preferred_peak_start: string; preferred_peak_end: string;
  desired_peak_block_minutes: number; hold_early_peak_edges: boolean; foresight_minutes: number;
  minimum_later_options: number; fallback_lead_minutes: number;
  after_peak_mode: 'longest_first' | 'earliest_first' | 'room_first'; priority_mode: 'time_first' | 'room_first';
};
export type Preferences = {
  revision: string;
  practice_plan: { enabled: boolean; default_hours: number; date_overrides: Record<string, number> };
  time_preferences: TimePreference & { preset?: string };
  disabled_dates: string[];
  date_time_preferences: Record<string, TimePreference>;
  booking_strategy: { reverse_date_order: boolean; daily_planning: DailyPlanning };
  room_preferences: { ordered_rooms: string[]; excluded_rooms: string[]; acceptable_instrument_tags: string[];
    acceptable_room_type_tags: string[]; required_feature_terms: string[]; minimum_block_minutes: number; allow_fragmented_sessions: boolean };
};
