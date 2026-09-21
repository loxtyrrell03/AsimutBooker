# Free-horizon peak exception

Authenticated, read-only checks on 21 September 2026 established that ASIMUT
waives this account's peak quota for eligible free-horizon requests. Both live
rolling and peak balances were zero. An exact same-day peak request entirely
inside the five-hour window returned success with no quota warnings; next-day
peak requests returned both rolling and peak quota warnings. No diagnostic
reservation was saved. One other visible individual reservation contained 75
peak minutes, but its creation time was unavailable: the decisive evidence is
the account's exact server check, not that other reservation.

`booking_rules.free_horizon_overrides_peak` enables the verified exception.
Missing values remain false. The switch is independent of numeric quota presets,
survives preset changes and is exposed in desktop and phone Booking rules.
Normal rolling balance and daily peak accounting are unchanged. Completed and
free-window reservations still count in actual peak usage; this exception does
not manufacture advance credit.

Both endpoints of the complete proposed reservation must fit inside the free
window. At 07:30 a 12:00-12:30 seed qualifies, while its intended 12:00-14:00
session does not yet qualify. The intended tail retains daily capacity and can
extend as later quarters open. Every extension tests the complete edited
interval, including its original start. An already-started reservation cannot
qualify solely through a future tail. Normal peak allowance applies outside
the window, even when advance credit remains available.

The free-only planner retains actual peak/time/room quality scores while removing
the normal peak budget from that specific set of free-window opportunities.
Advance allocation retains its normal peak budget. Creates and prepared prefixes
use the exact interval guard. Extensions, room upgrades, consolidation staging,
progressive transfers and explicit time shifts share the complete-window test.
Exempt portfolio gains cannot give peak credit to another non-exempt move.
Upgrade capacity checks also preserve attainable free-window practice at zero
advance credit. Conflicts, room access/horizons, durations, daily targets, saved
time preferences, room order, breaks and receipt/recovery requirements remain.

An exact ASIMUT refusal still stops the pass; no local setting authorizes Save.
Changing this switch invalidates display plans and vetoes stale prepared Saves.
Run reports and assistant contract revision 11 explain the conditional exception.

Regression coverage includes exhausted balances, an independent 432-request
quarter-hour matrix, full-window boundaries, classes and room spacing, room
selection, planned seed tails, extension tracking, upgrades, progressive fallback
coverage, consolidation conflicts, time shifts, persistence and settings drift.
The intercepted Chromium prepared-Save fixture additionally exercises a peak
seed with zero peak balance. It proves local control flow, not live booking speed
or success against competition. Private live diagnostic evidence is ignored under
`artifacts/peak-free-horizon-20260921/` in the canonical checkout.
