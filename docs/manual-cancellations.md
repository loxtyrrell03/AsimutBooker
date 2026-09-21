# Cancellations made directly in ASIMUT

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
their evidence. Ended reservations expire without blocking time; a retained ID
whose room/time changed updates its baseline. Pending internal edits keep missing
originals until recovery decides the outcome, and verified consolidation donors
are retired without creating cancellation exclusions.

This deliberately treats unexplained external removals conservatively: the
agenda cannot identify who cancelled a reservation. It only detects bookings
previously observed by this tracking feature, and cannot reconstruct earlier
cancellations or a booking created and cancelled between observations. Protection
begins on the next successful scan, not at the instant of a remote click.

Persistent agenda refreshes share this hook in `scan_agenda`, before display
publication. A tracking/settings/journal failure stops the scan. Long-lived
desktop processes need reopening to load new Python code; scheduled workers
load it at startup from the canonical checkout.
