# Free-horizon peak exception

Current evidence supports ASIMUT's free-horizon peak exception for this account:
verified 15 October 2026 receipts and a newer complete agenda include more than
one hour before 15:00. The authenticated rules describe last-minute booking
regardless of quota. Exact unsaved next-day checks still refuse exhausted peak
quota, distinguishing the exception from ordinary advance allowance.

The 15 October audit corrected the peak window to **09:00-15:00**, confirmed by
the information page and exact server checks across 15:00. Earlier September
notes treated time after 15:00 as peak; those examples alone cannot establish
the exception under the current window. The fresh survey found only two visible
other-student bookings exceeding one actual peak hour, both in a specialist harp
room. Their creation times and individual permissions were unavailable, so
neither proves this account's eligibility. No diagnostic reservation was saved.

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

With the exception enabled, eligible free-window practice within the effective
preferred times is not withheld to conserve peak credit for a future opening.
The daily portfolio keeps useful time first, then favours preferred sessions
whose minimum booking is already eligible. With time-first priority, it then
keeps the most preferred-window minutes before comparing session count and
ordinary room/time ranking. Several short afternoon gaps can therefore beat
fewer long evening sessions without reducing the daily target. Room-first
priority and ordinary advance planning retain their existing comparisons.
Bounded search keeps complete feasible improvements found before its limit,
and comfort refinement cannot discard preferred minutes. This also prevents
trimming a useful morning block merely
to reserve more of the daily target for an evening room. Future extension intent
remains unconfirmed and every actual saved interval must fit its window.
Date-specific time preferences apply here; ordinary advance planning and waiting
for preferred times when current slots lie outside them retain their rules.
Comfort refinement cannot replace this ready preferred practice with future
promises. Prepared-booking notifications use the exact projected candidate's
reason, rather than the earlier waiting plan's explanation.

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
