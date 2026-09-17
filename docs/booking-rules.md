# Current college booking rules

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

Room upgrades retain their exact-event validation, duration and recovery
guarantees. Existing reservations above a new limit are not cancelled or
shortened automatically. A whole-room improvement may retain an existing
over-limit reservation if peak usage does not increase and ASIMUT approves it.
Consolidation and progressive transfers still require their intermediate and
final quota checks. An uncertain Save always stops for reconciliation.

Quota and horizon observations update automatically. The six-hour, one-hour
and five-hour policy limits above are explicit college rules; an unknown new
quota type pauses booking for review. Older configuration files cannot raise
the college's rolling or peak caps. Cached plans made under old rules are
marked stale, and the assistant starts a fresh reasoning context while keeping
the transcript.
