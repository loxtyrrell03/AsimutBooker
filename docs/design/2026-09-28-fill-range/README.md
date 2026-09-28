# Fill time range

The owner requested implementation of the best option without another approval.
Option A was selected: a dated My Week action, plus a Today shortcut. The PC
form opens inline above the day list; the phone uses a focused form. Both reuse
Quiet Focus controls and adjacent help. All example schedules in the SVG boards
are invented. `index.html` shows three alternatives, shared states and narrow
layouts. The editable SVG source and generators are retained.

The action is immediate and create-only. It uses one date and a half-open time
range, counts existing reservations once and attempts uncovered intervals. It
temporarily replaces scheduling preferences without rewriting them. Existing
reservations and real conflicts remain authoritative, as do room requirements,
live site rules, quotas and exact receipt/agenda proof. It has no background
watcher and does not extend or move existing reservations.

Results distinguish complete, partial, unavailable, stopped and uncertain
outcomes. Each verified new reservation links to details. Uncertainty retains
the original request and permits only outcome checking; a retry is a fresh
request using a fresh agenda. Stop preserves bookings already confirmed.

Prototype bounds and gallery checks pass at 390/1040px. Source verification and
activation are recorded in the repository root guidance, not inferred from
these prototypes.
