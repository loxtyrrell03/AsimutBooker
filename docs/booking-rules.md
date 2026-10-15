# Current college booking rules

Choose **Booking rules** in desktop or phone Settings. **Previous quotas** uses
28 advance hours and 120 weekday peak minutes; **New quotas** uses six advance
hours and 60 weekday peak minutes. **Custom quotas** lets you edit both quotas,
the free window and weekday peak times. Saving applies to subsequent runs and
invalidates old plans; an in-flight Save stops if its rules have changed.

The selected values are local ceilings. ASIMUT may already enforce stricter
limits, so its information page and exact validation must be checked together.
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

The authenticated [college rules](https://rwcmd.asimut.net/booking-info), checked
on 15 October 2026, allow six hours of advance reservations within a seven-day
rolling horizon and one hour per weekday during **09:00-15:00**. Exact unsaved
checks confirmed the 15:00 cutoff: an interval crossing it received a peak-quota
refusal, while one starting at 15:00 did not. The current preset normalizes its
former 16:00 cutoff to 15:00; custom and previous-quota presets remain unchanged.
The preferred practice period is separate and is not shortened by this change.
Room-specific access and shorter room horizons come from ASIMUT on every run.

The general session limits are 30-120 minutes, with a 60-minute same-room gap.
The information page separately describes a 60-minute last-minute maximum.
Existing verified receipts and newer agendas show this account successfully
extending a free-window booking to 120 minutes on 14 October. That observed
behavior does not establish a universal two-hour last-minute entitlement:
Booker retains its live duration and exact-request checks for every create and
extension. No experimental reservation was saved for this investigation.

The free horizon is the next five hours. Both the start and end of a booking
must be inside it. Available quota is still used normally; ASIMUT can allow a
booking in that window when advance quota is exhausted. It is not five extra
hours that can be used for future dates. With the separately saved free-horizon
peak exception enabled, Booker can use ASIMUT-approved additional peak practice
inside that window. Verified 15 October receipts and agenda evidence include
more than one hour before 15:00 for this account. Completed peak sessions still
count: the exception does not reset the ordinary advance peak allowance. With
the exception disabled, Booker retains the local cap even inside the window.

For example, a weekday reservation from 14:00 to 14:30 can extend to 15:00 if
no other reservation uses that day's peak allowance. A reservation starting at
15:00 can extend to 17:00 without using peak allowance. Other peak
reservations reduce that allowance. An extension at exhausted advance quota
must fit the entire edited reservation inside the five-hour window.

The scheduler reads live quota balances rather than assuming hours reset on a
particular day. It checks short-notice availability at full quota, prioritises
pending extensions, and retains the saved targets, room order, strict hours,
fragmentation choice and conflict checks. A room-specific refusal can use an
eligible backup room; a quota refusal ends that pass and waits for a later run.
Unknown quota responses stop writes safely rather than assuming availability.

The recurring worker checks every five minutes throughout the day and night,
so morning free-horizon openings are covered before practice starts. One time
trigger repeats indefinitely, anchored at 00:02, retaining the :12/:27/:42/:57
preparation runs. It has no daily repetition cutoff or duplicate daily trigger.
Automatic Off and the shared Pause remain authoritative. Between quarter-hour
preparations it focuses on today's extensions,
free-horizon bookings and room upgrades. At exhausted advance quota, that daily
work runs before future upgrades can stop on a quota refusal. Unresolved
transactions still require recovery before any unrelated booking.

The single time trigger follows [Microsoft's indefinite-repetition guidance](https://jpwinsup.github.io/blog/2023/02/07/UserInterfaceAndApps/Repeat-task-indefinitely-issues/).
An omitted repetition duration means [repeat indefinitely](https://learn.microsoft.com/en-us/windows/win32/taskschd/repetitionpattern-duration).

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
