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

## Progressive upgrades at a rolling horizon

`data/progressive_upgrades.json` stores separate planning hints for the next
run: exact source bookings, any verified upgraded prefix, the desired full
session, the next opening boundary and the relevant preference fingerprint.
The recurring worker uses these hints before its ordinary broad scan. It
prepares within three minutes of the boundary, using freshly observed horizons
and conservative site-clock evidence; preparation never permits early trimming.
The existing recurring task starts every 15 minutes from 07:13 through 22:58,
leaving preparation time before quarter-hour boundaries through 23:00. A missed
run recalculates the largest currently useful prefix from fresh availability.

Once a minimum useful prefix is available, a coordinated transfer can shorten
or move the fallback and create that prefix in the better room. Subsequent runs
grow the prefix in legal quarter-hour increments while retaining useful fallback
bookings. The final minimum-sized fallback is transferred together. Shifted
times and several original bookings are handled by interval planning rather
than assuming their boundaries line up. The completed mixed arrangement must
preserve booked minutes, time preferences, quotas and attainable missing daily
target hours. The solver can try another fallback layout before rejecting a
candidate. A normal extension needed to fill missing target time has priority
over waiting for a room-only upgrade at the same boundary.

The original full-session upgrade/consolidation path remains preferred when the
whole replacement is available. If a teacher occupies the later part of an
aspirational session, a still-free earlier prefix can nevertheless improve.
Discovery also considers short free gaps without requiring any full-session
gap in that room. Its bounded search validates the actual prefix and fallback;
it never treats the aspirational remainder as observed free time.
New partial prospects receive one bounded execution pass when the ordinary
full-session sweep finishes, avoiding an unnecessary wait for the next run.
If fragmented sessions are disabled, partial transfers are disabled too; full
single-booking upgrades retain their existing path.
If no further useful growth remains, the completed partial bookings are retained
and released for ordinary future planning. New lessons, closures, relevant
preference edits, changed booking identities and ignored/disabled dates require
fresh planning. Window geometry and unrelated UI state do not invalidate plans.

### Transfer risk and recovery

Asimut does not provide an atomic transfer across two reservations. A
shorten-first transfer briefly releases time before securing its replacement.
Fresh preflight accepts only the exact destination with approved rules or the
specific personal-overlap/compensated peak-quota warnings that the planned
source changes will remove. Unknown, permission, horizon and other rule errors
stop the transfer before release. Both new and existing destination editors
receive this preflight, and the actual post-trim Save requires fresh approval.
Asimut may move the end time automatically when the start changes. Same-room
edits watch the entire change and correct the observed end before accepting the
exact validation response; an unchanged field is not a fresh validation trigger.

A strict `transfer` parent receipt records every before/after interval, the
source adjustment order and each attempted operation before its remote effect.
Create receipts are tied to that parent and their exact seed/restoration role.
Progressive IDs cannot be independently moved by ordinary upgrades/extensions.
Each completed transfer is independently proved from the full agenda and exact
event pages. On a known failure, recovery reverses the recorded source order;
a cancelled fallback may need recreation under a newly verified event ID.
An unattempted source changed by the user is not silently recreated.
Attempt markers are written at the final guarded click after exact identity
proof, so a manual deletion during preparation is not treated as the booker's
own cancellation. Recovery first checks the current disabled dates, blackouts,
ignored bookings, enabled zero targets and strict hours. Incompatible recovery
requires user attention instead of overriding those controls.

Unknown Save outcomes, changed originals or unsuccessful restoration retain the
pending transaction and block unrelated mutations. If another student takes the
released fallback before restoration, booked time can remain missing until
recovery; the system cannot guarantee an atomic handover. Saved state survives
process interruption, including a crash after continuation publication but
before receipt finalization. Read-only and isolated create/extension modes do
not perform transfer recovery. Explicit action caps reserve rollback capacity.
Scheduled transfers reserve time beneath the existing task hard limit, including
authentication/lock waiting, and recheck this allowance after preparation.

Known room-specific permission refusals skip the room/date for the current
pass and a bounded 30-minute retry delay; they do not permanently blacklist the
room. Authentication, service failures and uncertain Saves are not treated as
room refusals. A duration, horizon or quota rejection still permits other valid
shorter/earlier candidates. Invalid planning hints may be quarantined and rebuilt only with
a valid journal and no pending transfer; invalid mutation evidence still stops
autonomous changes.

Offline tests include repeated restarts through seed/extension/final transfer,
rejected and lost responses, last-donor recreation, unrelated/manual changes,
teacher occupancy, shifted/fragmented hours, preference/closure changes, full
quotas, capacity-preserving alternative layouts, and exact Chromium form checks.
These are simulation/browser-fixture evidence until a real eligible horizon
transfer is independently verified. The earlier live whole-room upgrades below
do not establish live proof of this new progressive path.

## Safe execution and recovery

A single-room upgrade retains its exact event ID, date and duration. Both times
are changed when needed. Fresh complete agenda/grid checks, exact Asimut form
validation and a final preference/Stop check precede the single guarded Save.
An independently loaded persisted event page must prove the result.
The complete scan finishes before opening the editor; the original form identity
is then checked again and the exact replacement receives a fresh server check.

A consolidation follows this order:

1. Verify every exact original reservation and the complete proposed day plan.
2. Account for Asimut's prohibition on overlapping personal bookings. If a donor
   would block the enlarged anchor, find an available superior room and move its
   exact reservation there temporarily, preserving its duration and date. Prefer
   the saved hours; an explicitly strict window is never relaxed. All original,
   intermediate and final states are journalled before the first Save.
3. Ask Asimut to freshly approve the longer anchor with every donor still held,
   Save the enlarged anchor,
   and independently verify its full room, date and times.
4. Before each redundant booking is retired, refresh the complete agenda and
   independently prove that the full replacement still exists.
5. Verify every donor absent and the full anchor present before declaring success.

If staging or the anchor is rejected, restore any moved donors to their exact
original slots. A failed restoration retains the full intermediate booking and
leaves a pending transaction requiring attention; it never cancels that fallback.
Peak limits apply during staging and anchor Save as well as to the final schedule.
If no safe intermediate slot exists or the site rejects temporary weekly quota,
keep the originals.
Never cancel first in the hope that the longer booking will subsequently succeed.
A lost response, changed identity, missing anchor or incomplete proof leaves the
transaction pending and blocks further changes. Before anchor Save, recovery
restores exact originals. After the anchor is secured, it resumes unfinished
retirement; it never repeats an uncertain Save. Read-only scans do not
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
each intermediate edit, its anchor Save and each donor retirement. Restorations
also consume the action allowance, so an explicit action cap
cannot be exceeded by hiding multiple writes inside one group operation.

## Verification

Two real partial transfers upgraded **90 minutes**, preserving every existing
reservation ID, each day's total minutes and unrelated events. Both new seeds
and transaction parents were independently verified. A separate final scan of
31 rooms across five eligible dates found 258 gaps, no pending mutation and no
remaining currently open partial upgrade passing the planner's constraints.
Later-horizon prospects remain routing hints, subject to fresh checks.

The corrected editor passed the full **1,248-test Python suite**; a final
single-booking preference guard passed 45 focused planner/discovery checks.
These live transfers used already-open prefixes. Rolling-edge timing and final
donor retirement retain offline/browser-fixture verification, rather than a
claim of competitive live proof at every boundary.

After sparse discovery and recovery refinements, all **1,246 Python tests**
passed, including guarded cancellation timing, changed recovery controls,
same-run partial dispatch and preserving a final 15-minute extension's peak
allowance. A fresh five-date live whole-session preview found no further eligible
whole-session improvement and retained all 13 existing reservations unchanged.

The progressive transfer implementation passed all **1,218 Python tests**,
including 278 deterministic crowded-calendar simulations and intercepted
Chromium checks. Authenticated read-only inspection confirmed the live new-form
request and conflict/quota warning format with all non-check mutations blocked.
This verifies form compatibility, not a completed real progressive transfer.

The initial comprehensive implementation passed all **1,042 Python tests**, 19
phone Node tests, TypeScript, lint and the private static phone build. Tests
cover complete discovery before mutation, more than six upgrades, shifted times,
three-fragment consolidation, whole-day competing choices, future horizons,
user-ranked Weston preference, retained daily capacity, explicit freeze values,
real Chromium editor requests, rejected checks, lost Save responses, donor
retirement guards and recovery after partial completion.

After the live-path refinements, the full **1,062-test Python suite** passed.
Additional publication, runtime, recovery and scheduled-queue checks cover
accurate consolidation counts, notification deduplication and retaining earlier
verified successes when a later action requires reconciliation.

The real preview exposed Asimut's overlapping-person rejection. Staging solved
that constraint; live testing also exposed a sticky time picker. The editor now
dismisses only the identified picker through its own backdrop, verifies the
times unchanged, and stops before Save if an unknown overlay blocks it.

Three real staged consolidations and one ordinary upgrade completed. A separate
final scan covered all five eligible dates, 31 rooms and 260 observed gaps, with
no further candidate meeting the saved constraints. Exact receipt-to-agenda
comparison preserved every date's booked minutes and unrelated events, accounted
for every removed donor and found no pending transaction. The private evidence
is retained locally in the ignored `artifacts/comprehensive-upgrades/` folder.
