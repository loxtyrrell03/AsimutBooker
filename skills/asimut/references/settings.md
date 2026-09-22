# Settings

Read `read_phone_preferences` and relevant context first. Use
`tools --name update_booker_preferences` for current names and values.

| User intention | Section / important detail |
| --- | --- |
| Daily goal | `practice_plan.default_hours`; total across sessions |
| One date's goal | `practice_plan.date_overrides`; preserve other dates |
| More/less practice that date | `practice_plan.date_adjustments` with literal signed delta; host resolves it |
| Practice on/off for dates | `booking_days` |
| Usual preferred hours | `time_preferences`; preserve soft/strict choice |
| One date's hours | `date_time_preferences`, exact date and full window |
| College opening/closing | `rooms_open` start, `rooms_closed` end; no invented closing clock |
| Breaks, block length, room changes | `booking_strategy.daily_planning`: `preferred_rest_minutes`, `preferred_block_minutes`, `prefer_fewer_room_changes` |
| Room order, exclusions, requirements | `room_preferences`; exact live names and actual instrument metadata |
| Advance distribution/rooms/periods | `advance_quota`; use current schema |

A global window patch can use:
`{"time_preferences":{"enabled":true,"start_time":"12:00","end_time":"rooms_closed"}}`.
Preserve the saved strict/soft choice unless asked to change it.

`save_phone_preferences` also supports `booking_rules`. Supply the revision from
the read and only changed sections:
`{"revision":"OBSERVED REVISION","changes":{"booking_rules":{"preset":"new"}}}`.
A preset preserves `free_horizon_overrides_peak`. Custom quotas use
`preset: "custom"` and the existing `booking_rules.apply_booking_rules` validator.
These are local ceilings, never permission to bypass ASIMUT.

The phone save format for dated windows is a date-to-window/null map; the core
assistant tool uses an array of `{date, window}`. Do not interchange them.

Successful changed preferences already queue a normal check through
`preference_runs`. Read back saved values and its status; do not launch a
competing duplicate run merely to apply the save. Automatic booking Off stays
Off. A completed run may find no eligible booking; report that accurately.

For other settings, locate their actual owner in `gui.py`, `src/config.py`,
`phone_preferences.py`, `booking_strategy.py` or the relevant validator before
making a scoped change. Use `preference_runs.update_preferences` for practice
preference writes; use existing dedicated controls for schedule/system/notification
settings. Preserve unrelated keys, credentials and sessions. Do not overwrite
all of settings.json.

Automation belongs to `AsimutBooker_Recurring`. Discover its exact action and
enabled state before a requested On/Off change. `src.scheduler` is retired;
never start a second scheduler. Practice settings do not authorize rebuilding
scheduled tasks, changing routes or restarting active services.
