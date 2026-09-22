# Reservations and availability

Refresh the agenda before writes. Query `find_availability` for a dated window
or `next_minutes`; use `find_reservations` for exact event identities. A free
grid cell is not proof of personal eligibility. Check conflicts, quota policy,
room access, instrument requirements and manual protections.

## Book practice

For a daily-total request, save dated targets through `set_future_practice_plan`
or `update_booker_preferences`. This means total practice, including existing
bookings, split into legal sessions. Saving preferences queues a normal check.

`run_booker` performs one **plan-selected** action, scoped by `only_date`,
`only_room` and an optional duration cap. It can upgrade or extend. It cannot
bind exact start/end times and is not create-only. Never substitute it for
"book 11:30-12:00" and assume the clock times match. Do not change global
preferences just to force a one-off booking.

For an exact create or other operation outside the typed surface, use the
guarded engine/browser path in [runtime.md](runtime.md). Bind the exact date,
start, end and eligible room(s) through the final receipt/Save boundary. Reopen
a protected interval only when the request supersedes that protection, and
only for that interval. Follow allowed room fallbacks without silently changing
requested times. An urgent booking takes priority over unrelated diagnostics;
confirm it promptly, then resume any ongoing repair.

## Edit, cancel, extend or upgrade

- Later start, same end: fresh one-booking selection, then
  `edit_reservation_time {mode: "trim_start", new_start_time: "HH:MM"}`.
- Shift the whole booking: `mode: "shift_later", minutes: N`; both endpoints
  move. Stop on a clash; this differs from shortening.
- Cancel: fresh explicitly scoped selection, then `cancel_reservations`.
  Bulk cancellation must match the requested set. Cancelled time stays protected.
- Upgrade: the worker CLI supports `--upgrades-only`, `--upgrade-event-id`,
  `--only-date`, `--only-room`, `--max-actions`, `--upgrade-dry-run`. Read current
  `book_week.py --help` for combinations. Event ID scopes the source; room scopes
  the destination. This is quality planning, not arbitrary relocation.
- Extend: `--extensions-only` acts on verified saved extension targets. An empty
  queue is not a quota refusal. Extending another booking needs fresh identity,
  availability and `edit_reservation_end_time`, not an invented queue record.
- Other edits: inspect `BookingTimeEdit`, `RoomUpgrade`, `TransferEdit` and the
  live editor as needed. Retain exact identity and unrelated bookings. Never
  cancel first to make an edit easier.

Pins exclude automatic changes. When the user explicitly changes that pinned
booking, apply that exact instruction. `edit_reservation_time` repins its result.
Other explicit operations may need a scoped pin update through
`manual_booking_overrides` and `app_settings.update_settings`, under assistant/
runtime locks after fresh identity proof. Preserve unrelated pins/blackouts;
restore protection if not applied. Pending receipt recovery precedes new work.

## Evidence and retries

`verified_saved_refresh_required` means Save succeeded but needs a read-only
refresh, not another Save. `unconfirmed`/pending receipts stop writes. Reconcile
the same event and receipt first. A definite no-Save refusal may justify a fresh
bounded alternative within scope; never loop on the same refusal.

Recheck the agenda after success. A later scheduled room upgrade can preserve
times: report the latest verified room, not just an old create receipt. Day-of
attendance reconfirmation on College Wi-Fi is separate and does not prevent
ordinary booking management.
