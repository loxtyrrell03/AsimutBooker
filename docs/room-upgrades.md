# Comprehensive room upgrades

The booker fills daily targets using the saved time and room preferences, then
improves the existing reservations. Saved room order is authoritative, including
Weston Gallery above Corus when that is the user's chosen order.

## Complete discovery and planning

- Scan every eligible date in the live booking window before editing anything.
- Consider every legal quarter-hour start, including a move such as
  12:30–13:30 in one room to 12:00–13:00 in a better room.
- Compare compatible improvements together so one attractive choice does not
  needlessly block another booking's upgrade. After each verified change,
  refresh the agenda and affected room grid, then plan again.
- Combine multiple non-overlapping bookings into one continuous session when
  the whole duration fits. For example, 12:00–12:30, 12:30–13:30 and
  13:30–14:00 can become one 12:00–14:00 booking.
- Preserve the total booked minutes, date, hard constraints and time fit.
  A consolidation cannot downgrade any of its original rooms. Remaining daily
  target capacity is compared using both newly occupied and freed room gaps.
- Preserve conflicts, peak and weekly quotas, room spacing, disabled dates,
  ignored bookings, cancellation exclusions and unfinished extension plans.
- Continue until no executable improvement remains. There is no implicit
  six-attempt or three-minute cutoff. Explicit action limits and Stop still apply.

The default **Stop upgrades before start (hours)** is zero: upcoming sessions
on the same day can improve. A saved nonzero cutoff is retained. Started
reservations are never rescheduled or retired by an upgrade.

The ignored local `data/upgrade_plan.json` records checked dates, current
prospects and shorter-horizon rooms that open later. It reaches across the full
observed booking window rather than the ordinary seven-hour foresight period.
A future gap can be taken by someone else; these records never authorize Save.

## Safe execution and recovery

A single-room upgrade retains its exact event ID, date and duration. Both times
are changed when needed. Fresh complete agenda/grid checks, exact Asimut form
validation and a final preference/Stop check precede the single guarded Save.
An independently loaded persisted event page must prove the result.

A consolidation follows this order:

1. Verify every exact original reservation and the complete proposed day plan.
2. Ask Asimut to approve the longer anchor while all smaller bookings still exist.
3. Record every original in one durable transaction, Save the enlarged anchor,
   and independently verify its full room, date and times.
4. Before each redundant booking is retired, refresh the complete agenda and
   independently prove that the full replacement still exists.
5. Verify every donor absent and the full anchor present before declaring success.

If Asimut rejects temporary overlapping quota or another rule, keep the originals.
Never cancel first in the hope that the longer booking will subsequently succeed.
A lost response, changed identity, missing anchor or incomplete proof leaves the
transaction pending and blocks further changes. Recovery resumes only unfinished
retirement; it never repeats an uncertain anchor Save. Read-only scans do not
retire bookings. Removing redundant upgrade bookings does not create a blackout.

The site's normal provisional-booking reconfirmation requirement still applies.
An upgrade preserves booked practice time; no planner can guarantee filling an
unbooked target when suitable rooms are unavailable.

## Operating modes

Full preview, without Save or cancellation:

```powershell
.venv\Scripts\python.exe book_week.py --headless --upgrades-only --upgrade-dry-run
```

Full upgrade sweep:

```powershell
.venv\Scripts\python.exe book_week.py --headless --upgrades-only
```

Optional `--only-date`, `--only-room`, `--upgrade-event-id`, `--max-actions` and
`--max-action-minutes` restrict scope. Each consolidation uses one action for
its anchor Save and one for each donor retirement, so an explicit action cap
cannot be exceeded by hiding multiple writes inside one group operation.

## Verification

The initial comprehensive implementation passed all **1,042 Python tests**, 19
phone Node tests, TypeScript, lint and the private static phone build. Tests
cover complete discovery before mutation, more than six upgrades, shifted times,
three-fragment consolidation, whole-day competing choices, future horizons,
user-ranked Weston preference, retained daily capacity, explicit freeze values,
real Chromium editor requests, rejected checks, lost Save responses, donor
retirement guards and recovery after partial completion.

These are source/fixture checks. The real-booking sweep, full before/after agenda
comparison and publication evidence are recorded after live validation.
