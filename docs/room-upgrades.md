# Room upgrades

The booker secures practice hours using its existing daily target and preferred
time rules, then looks for better rooms. An upgrade changes one existing
reservation. Its event ID, date and full duration stay the same.

## Selection rules

- Only a room ranked above the current room can qualify. Excluded rooms and
  rooms that fail the saved requirements remain excluded.
- The full session must fit an observed free gap and the destination room's
  current booking horizon. A future opening is a reason to check on a later run,
  never evidence that the room is currently available.
- All suitable quarter-hour starts are considered. Time fit must be at least as
  good as the current reservation; strict preferred windows remain strict.
- Other bookings, non-ignored college events, peak allowances, same-room gaps,
  cancelled-time exclusions and pending extension plans remain constraints.
- The existing whole-day planner compares remaining target coverage before and
  after the proposed move. An upgrade cannot consume the only useful gap for
  missing practice hours or worsen the remaining plan's time fit.
- By default, both the current and proposed start must be more than **24 hours**
  away. The deadline does not prevent ordinary booking of missing target hours.

Settings → Booking strategy exposes **Improve booked rooms** and
**Stop upgrades before start (hours)** on desktop and phone. The shared assistant
preference schema accepts `upgrade_rooms` and `upgrade_freeze_hours` inside
`booking_strategy.daily_planning`. Upgrades are enabled by default; the deadline
accepts whole hours from 0 through 168. These controls work independently of the
existing plan-ahead toggle.

## Save and recovery

1. Verify the exact original reservation on its persisted event page.
2. Prepare any changed times, then refresh the complete agenda and room grid and
   recompute the candidate and whole-day constraints.
3. Select the destination's real dropdown option and require Asimut's fresh
   approval for that exact single-event request. HTTP 200 alone is insufficient.
4. Recheck preferences and Stop, persist both exact reservation states in the
   recovery journal, and allow one matching Save request.
5. Verify the new room, date and times on a separate persisted event page before
   reporting success or allowing another upgrade.

The upgrade path never clicks Cancel, creates a replacement event, shrinks a
session, or retries an uncertain Save. A rejected Save is resolved only after
verifying the original reservation intact. A lost response, unexpected event,
partial change or missing reservation remains pending and blocks further
mutations until reconciliation establishes the exact outcome.

Asimut's Save reply can omit fields present in its validation reply. Omitted
fields do not prove success; an independent reload of the exact changed
reservation must establish it. Explicit errors or a different event ID still
require reconciliation.

Asimut's normal provisional-booking reconfirmation requirement still applies.
Upgrades preserve reserved duration; they do not establish that an unbooked
daily target can always be filled when rooms are unavailable.

## Operational checks

Normal runs perform upgrades after creates/extensions, and can improve dates
whose target or weekly quota is already full. Each phase considers at most six
attempts and starts no new attempt after its three-minute planning budget.
An in-flight Save always completes its verification.

For a controlled inspection:

```powershell
.venv\Scripts\python.exe book_week.py --headless --upgrades-only --only-date YYYY-MM-DD --upgrade-dry-run
```

A mutation-capable isolated run also requires `--max-actions N`.
`--upgrade-event-id ID` restricts either mode to one exact reservation.
`--only-room` restricts the destination, and `--max-action-minutes` skips longer
reservations rather than shortening them.

The focused suites are `test_room_upgrades`, `test_room_upgrade_runtime`,
`test_upgrade_editor` and `test_upgrade_recovery`. They cover actual Chromium
form interactions against an intercepted synthetic site, including rejection,
lost responses, wrong requests, preference changes, Stop and recovery. Shared
journal, planner, CLI, desktop and phone tests cover the integration.

Live inspection established that dropdown icons contribute hidden raw text,
unchanged values do not reliably trigger validation, and a single-event editor
retains an unused Monday recurrence default even on another weekday. Selectors
use accessible room names; the actual single mode, exact timestamps and event ID
establish scope, while the complete checked payload must remain unchanged at Save.
