# Current college booking rules

Choose **Booking rules** in desktop or phone Settings. **Previous quotas** uses
28 advance hours and 120 weekday peak minutes; **New quotas** uses six advance
hours and 60 weekday peak minutes. **Custom quotas** lets you edit both quotas,
the free window and weekday peak times. Saving applies to subsequent runs and
invalidates old plans; an in-flight Save stops if its rules have changed.

The selected values are local ceilings. ASIMUT may already enforce stricter
limits, even while its information page still describes the previous rules.
Its live available quota and exact booking checks remain mandatory. Both
built-in presets retain the five-hour free window tested on the current site.
The previous-quota preset does not promise the historical policy is still active.

The examples below describe the **New quotas** preset.

With an enabled daily target and daily planner, ordinary New/Custom quota runs
share advance credit across useful blocks in your first two preferred rooms
(currently Weston and Corus), prioritising your preferred times. A soft time
window permits useful fallbacks; a strict window forbids outside time. Uncovered dates come
before enlarging already-covered dates. For seven equally available dates, six
hours can provide four 45-minute blocks and three one-hour blocks. Existing
good-room practice and pending extensions count, and disabled dates, conflicts,
saved minimum durations and the split-session preference remain binding.

Each room keeps its actual ASIMUT horizon. Credit can be held for a good block
that opens later in a five-day room rather than spent on a second block on an
already-covered date. An equally suitable preferred room that is open now can
be secured immediately. The plan is recalculated after each verified booking
and as quota returns. A waiting block is an intention, not a guaranteed booking.

The daily target stays unchanged. Last-minute bookings and extensions pursue
the rest. While advance credit is available, routine extras wait until the
saved fallback lead before their start, so they do not immediately consume the
week's reserved credit. Imminent practice can take priority over holding future
credit. At exhausted quota the normal full free window applies. Explicit
single-date/room actions and the Previous quotas preset retain their existing
scoped planning behaviour. Quota alone cannot guarantee availability every day.

The college's September 2026 rules allow six hours of advance reservations
within a seven-day rolling horizon, one hour per weekday during peak time
(09:00-16:00), and bookings lasting 30-120 minutes. Room-specific access and
shorter room horizons continue to come from ASIMUT on every run.

The free horizon is the next five hours. Both the start and end of a booking
must be inside it. Available quota is still used normally; ASIMUT can allow a
booking in that window when advance quota is exhausted. It is not five extra
hours that can be used for future dates. The booker continues to enforce one
hour of peak time even when ASIMUT permits a short-notice exception. Completed
peak sessions still count for that day: finishing 12:00-13:00 does not allow
another peak hour that afternoon. Further practice must avoid 09:00-16:00.

For example, a weekday reservation from 14:00 to 14:30 can extend to 15:00 if
no other reservation uses that day's peak allowance. A reservation starting at
15:00 can extend to 17:00 because only 15:00-16:00 is peak time. Other peak
reservations reduce that allowance. An extension at exhausted advance quota
must fit the entire edited reservation inside the five-hour window.

The scheduler reads live quota balances rather than assuming hours reset on a
particular day. It checks short-notice availability at full quota, prioritises
pending extensions, and retains the saved targets, room order, strict hours,
fragmentation choice and conflict checks. A room-specific refusal can use an
eligible backup room; a quota refusal ends that pass and waits for a later run.
Unknown quota responses stop writes safely rather than assuming availability.

The recurring worker checks every five minutes during its 07:12-22:57
window. Between quarter-hour preparations it focuses on today's extensions,
free-horizon bookings and room upgrades. At exhausted advance quota, that daily
work runs before future upgrades can stop on a quota refusal. Unresolved
transactions still require recovery before any unrelated booking.

An imminent free-window opportunity can be prepared up to three minutes ahead.
The worker chooses from the current grid and opens and fills the exact room form
before the boundary. At the opening it requires a new, matching ASIMUT validation
and an enabled Save before submitting once. A refusal can use a freshly checked
backup; an uncertain Save stops for reconciliation. Routine upgrade work leaves
time for the next preparation window. Preferred times (including strict/soft
behaviour), room order, goals, fragmentation, exclusions and extension holds
remain the same planning constraints. The worker never treats a future opening
as permission to book early or promises hours that the site has not confirmed.

With no advance credit, 16:00-16:30 becomes eligible at **11:30**, not 11:00:
the end must also be inside the five-hour window. A verified seed keeps its
intended longer session, allowing further legal extensions as the window moves.
The first Save still depends on live approval, network speed and competition.

Optional block length, rest time and fewer-room-change controls refine equally
good plans. They never reduce planned minutes or override room quality, preferred
times or hard rules. They default off. Equivalent room-upgrade choices can improve
rest while retaining the primary upgrade portfolio and its exact originals.

Last-minute opportunities retain the intended full session, even when only its
first 30 minutes are currently inside the free window. The shared day planner
can wait for a better preferred-time option, and a verified short initial booking
keeps its extension target and capacity. Later extensions still require their
entire edited interval to be inside the current window when advance quota is full.

Room upgrades retain their exact-event validation, duration and recovery
guarantees. Existing reservations above a new limit are not cancelled or
shortened automatically. A whole-room improvement may retain an existing
over-limit reservation if peak usage does not increase and ASIMUT approves it.
Consolidation and progressive transfers still require their intermediate and
final quota checks. An uncertain Save always stops for reconciliation.

Quota and horizon observations update automatically. Saved quota presets take
precedence over historical YAML quota values; an unknown new quota type pauses
booking for review. Cached plans made under different rules are
marked stale, and the assistant starts a fresh reasoning context while keeping
the transcript.
