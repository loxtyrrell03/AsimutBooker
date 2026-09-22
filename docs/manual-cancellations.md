# Manual changes made directly in ASIMUT

Complete authenticated agenda refreshes remember upcoming reservations by event
ID in `observed_reservations` in the shared settings. When a previously observed
reservation disappears from a date covered by the new complete scan, its exact
time becomes a persistent, room-independent `rebooking_blackouts` interval.
Planning and mutation guards already enforce these intervals across automatic
creates, extensions and upgrades. A newly added blackout also invalidates an
in-flight preference snapshot before Save; the next run replans with it.

The existing assistant action `reopen_booking_window` removes all or part of a
protected interval only on a direct user request, for example “Allow booking
Thursday 24 September from 14:00 to 15:00 again.” Detection and consumption of the
missing reservation happen in one settings transaction, so later refreshes do
not undo that explicit reopening. No new interface or preference toggle is needed.

Failed/incomplete scans do not change tracking state. Unscanned dates retain
their evidence. Ended reservations expire without blocking time. Same-ID edits
protect the difference between the old and new occupied intervals, independent
of room. For example, changing 11:00–12:00 to 11:30–12:00 protects 11:00–11:30.
Trimming either end, shifting a session, or moving it to another date uses the
same rule. Even a trim ending before the current time protects its released tail.

The edited reservation is recorded in `manual_booking_overrides`. Automatic
extensions, room upgrades, consolidation and progressive transfers leave that
reservation unchanged. Its stale extension goal is retired, and its confirmed
minutes still count toward the daily target. Untouched reservations retain their
usual optimization. A room-only edit protects the selected room without adding
a time blackout. Explicit time edits through the booker also record this pin.
Explicit cancellation remains possible; cancellation or expiry retires the pin.
There is no automatic pin timeout or reset when general preferences are saved.
An explicit request to allow automatic changes again can remove the exact pin
through the shared atomic settings editor; it must not implicitly reopen time.

Booker-owned changes are identified using exact verified receipt transitions
since the last observation (`observed_reservation_times`). Automatic extension,
upgrade and transfer changes therefore do not create false manual protections.
Pending internal changes retain the original baseline until recovery decides
the outcome. Verified consolidation/transfer donors and verified recreated
donors from a resolved compensation transaction retire without exclusions.
Observation timestamps are tracking metadata; pins and blackouts invalidate
prepared Saves and cached plans. Each automatic mutation checks pins again at
the final Save boundary, including staged and queued transactions.

This deliberately treats unexplained external removals conservatively: the
agenda cannot identify who cancelled or edited a reservation. It only detects bookings
previously observed by this tracking feature, and cannot reconstruct earlier
cancellations/edits or changes made and reversed between observations. Protection
begins on the next successful scan, not at the instant of a remote click.

Persistent agenda refreshes share this hook in `scan_agenda`, before display
publication. A tracking/settings/journal failure stops the scan. Long-lived
desktop processes need reopening to load new Python code; scheduled workers
load it at startup from the canonical checkout.
