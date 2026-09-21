# Booking notifications

Notifications explain observations from the current worker run. They do not use
an expired display plan or infer success from the worker exit code.

- A no-change message gives confirmed daily minutes and the target, remaining
  rolling credit (live or explicitly estimated), and the day's peak usage.
  The actual planner contributes its candidate, first eligible seed time,
  room/time priority, and eligible higher-ranked alternatives when present.
- Extension messages explain saved targets, waiting reasons and refusals.
  Upgrade summaries name the dates actually checked; a same-day check does not
  claim the whole week was scanned. ASIMUT quota refusals remain distinct from
  occupied rooms, local preferences and future opening times.
- Verified success messages show the actual room/interval and the selection or
  fallback reason. A longer intended tail is explicitly not booked yet.
  Existing receipt verification and reconfirmation reminders remain in place.
- The generic second quota alert is removed. Quiet fast passes stay quiet, but
  their full explanation is saved in the local booking-history file. Normal notifying
  passes send one no-change summary. Newly visible sign-in and quota-policy
  failures use the failure notification path; identical consecutive failures
  retain existing suppression.

`booking_run_report.py` holds explanations in a run-local context. Fresh quota
reads attach their tracker; same-run agenda-only refreshes retain the last live
credit observation and label it as such. Notes cannot authorize a Save. The
context is reset on every exit. The history retains complete explanations and
ntfy payloads are bounded by UTF-8 byte length.

No booking preference, quota rule, worker schedule or phone route changes as
part of this feature. This changes worker notifications only; the phone needs no new static build.
Its existing history view continues to show typed run summaries. Successful HTTP delivery proves ntfy accepted a message, not
that a particular phone displayed it.
