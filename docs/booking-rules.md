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

The college's September 2026 rules allow six hours of advance reservations
within a seven-day rolling horizon, one hour per weekday during peak time
(09:00-16:00), and bookings lasting 30-120 minutes. Room-specific access and
shorter room horizons continue to come from ASIMUT on every run.

The free horizon is the next five hours. Both the start and end of a booking
must be inside it. Available quota is still used normally; ASIMUT can allow a
booking in that window when advance quota is exhausted. It is not five extra
hours that can be used for future dates. The booker continues to enforce one
hour of peak time even when ASIMUT permits a short-notice exception.

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

The recurring worker checks every five minutes during its existing 07:13-22:58
window. Between quarter-hour preparations it focuses on today's extensions,
free-horizon bookings and room upgrades. At exhausted advance quota, that daily
work runs before future upgrades can stop on a quota refusal. Unresolved
transactions still require recovery before any unrelated booking.

An imminent free-window opportunity can be forecast up to three minutes ahead.
The worker waits for the actual boundary, then rereads the room grid and replans
before the normal exact booking checks. Preferred times (including strict/soft
behaviour), room order, goals, fragmentation, exclusions and extension holds
remain the same planning constraints. The worker never treats a future opening
as permission to book early or promises hours that the site has not confirmed.

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
