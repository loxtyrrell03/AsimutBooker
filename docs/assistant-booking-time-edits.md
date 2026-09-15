# Assistant booking time edits

The assistant exposes `edit_reservation_time` for one exact reservation selected
by `find_reservations` in the current turn:

- `trim_start`, with `new_start_time`: start later and retain the existing end.
  For example, 12:45–14:45 becomes 13:30–14:45.
- `shift_later`, with positive `minutes`: move both endpoints later by that
  amount. A 45-minute shift of 12:45–14:45 becomes 13:30–15:30.

Both operations retain the event ID, date and room. Times must fall on quarter
hours; the new start must still be future and the duration must meet current
site limits. A still-running reservation may be trimmed to a future start if
Asimut approves it. These actions do not support cross-date moves, changing
rooms, earlier moves, end trims, or moving another booking out of the way.
Ambiguous identity, direction or end/duration intent requires clarification.

## Execution and recovery

The host consumes one fresh exact selection under the assistant mutation lock.
The isolated worker takes the existing Booker runtime lock, refreshes live room
policy and the complete agenda, then revalidates the exact original using a
separate page. Every other personal event blocks overlap, including events the
automatic planner would ignore. Later shifts also need the full new interval
available in the room, the complete room horizon, same-room spacing and peak
allowance. The existing original interval counts as owned room time; a short
unavailable gap between it and a later free interval cannot be bridged.

An explicit edit overrides autonomous room-ranking/filter preferences only for
that isolated run. It does not rewrite saved room, target or preferred-time
settings. Protected free-time windows remain enforced. The final Save boundary
still checks Stop and concurrent preference changes.

The shared editor installs exact-check listeners before changing either time.
It corrects Asimut's automatic end-time movement for a trim. A separate strict
`time_edit` receipt records both exact states before one permitted Save; the
automatic room-upgrade receipt keeps its existing equal-duration/better-room
contract. Rejections require independent proof of the original. Unknown Save
outcomes remain pending and are never retried or replaced by cancellation.

After independent persisted-event proof, the worker retires stale extension
tracking and protects only the released part of the original interval against
automatic rebooking. It invalidates the display plan before completing the
receipt. A crash during these local updates leaves a pending receipt so the
same work can finish idempotently after independent agenda/event readback.
Missing, duplicate, wrong-ID or partially changed results remain uncertain.

The host checks the new receipt and a newer complete agenda before reporting
`verified_changed`. A verified Save followed by a failed agenda refresh is
reported separately as `verified_saved_refresh_required`. Exit status or a
printed success message alone never proves the change.

## Verification

The deterministic suite covers exact selection and turn binding, both time
calculations, site limits, partial room gaps, later clashes, recovery and the
real editor code against intercepted Chromium pages. The assistant evaluator
uses synthetic bookings and the same pure time-edit construction; it cannot
invoke production Booker tools or mutate real bookings.

The time-edit tool declares all fields in one object; the host enforces the
exact mode-dependent argument set. `oneOf` branches, including those nested
under `allOf`, were observed to expose only `mode` to the model, hiding the
booking selection and time arguments.

Source tests, model simulation, served phone verification and real booking
mutations are separate evidence. No real booking is needed to test this feature.

### 15 September 2026 result

- Full offline suite: 1,289 tests passed. After final tool-schema corrections,
  all 165 focused assistant/editor/recovery checks passed.
- All nine final Luna/high/standard model scenarios passed. An initial failure
  for the truthful phrase "did not confirm" was a wording-grader omission;
  the existing trace was regraded, and 43 evaluator tests pass. No production
  model setting changed and no real reservation was edited for testing.
- Canonical source and the existing idle phone backend were activated. Private
  HTTPS session/bootstrap and the rendered connected Assistant passed. The
  existing phone shell, private route, all unrelated Serve handlers, settings,
  history and desktop sessions were retained. Reopen existing desktop windows
  to load the new Python code. Physical-phone and real time-edit proof remain
  separate from these checks.
