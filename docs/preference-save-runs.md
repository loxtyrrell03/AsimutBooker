# Booking checks after saving preferences

Successful changes in desktop or phone preference editors, calendars and the
assistant queue a fresh ordinary Booker run. The request is written atomically
with the preferences. Cancel, validation/conflict errors, unchanged saves and
internal booking progress do not create requests. The phone Save confirmation
reports a queued check without claiming that any reservation has been made.

`preference_runs.update_preferences` is the shared save boundary;
`preferences_transaction` supports the locked event-conflict editor. The private
`preference_run` settings entry records the newest request and its outcome. It
does not change the phone preference revision or invalidate a prepared Save by
itself. Actual control changes retain the existing final Save guard.

One hidden dispatcher coalesces saves for two seconds and waits for both Booker
and assistant ownership. It uses the normal headless Booker entry point, with
fresh settings, site checks and receipt recovery. It never interrupts an active
Save or extension. A save during a run remains pending and gets a subsequent
pass; an older result cannot acknowledge it. A matching full ordinary run can
also fulfil the request. Scoped checks and intermediate today-only scheduled
passes cannot consume a request intended for the whole booking plan.

The dispatcher respects the existing Automatic booking Off state and will not
install, enable or repair a schedule. A failed attempt is recorded once rather
than immediately repeating it. Busy ownership is retried, not treated as an
attempt. Pending work survives a failed launch or process exit; phone startup
and the end of a regular worker run check for it again. There is no new service,
scheduled task, public endpoint or hosting route. Logs are private under
`logs/preference-runs/`.

Saving new preferences asks the existing planner to pursue them. It does not
authorize additional cancellation behavior or count intentions as booked time.
Room upgrades, extensions, exclusions, quotas and target limits still apply.

Verification includes atomic saves, stale/invalid input, busy locks, simultaneous
dispatchers, rapid and mid-run edits, pause/failure/restart behavior, receipt and
prepared-Save guards, desktop/calendar/assistant callers and phone revision
stability. `tools/check_preference_run_worker.py` launches the real hidden
dispatcher against an isolated fake Booker; it never accesses ASIMUT. Chromium
and WebKit fixtures exercise the phone Save confirmation and existing editors.
