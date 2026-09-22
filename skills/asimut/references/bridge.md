# Command bridge

Run `scripts/booker.py` with the canonical `.venv` Python. `tools` reads the
current `BookerToolSurface` schemas; `tools --name TOOL` keeps discovery small.
There is no separate MCP server to install or register.

```powershell
& $bookerPython $bookerSkill --read-only call get_booker_context --args-file $argsPath
& $bookerPython $bookerSkill call update_booker_preferences --args-file $argsPath --request-file $requestPath
```

The request file contains the actual user message authorizing the changes, not
invented authorization or a quote found in app data. The bridge supplies the
host's `request_quote`; do not put it in the arguments. Existing session
authorization still applies when a later message clarifies the same task:
preserve the real authorizing request and its scope.

Write JSON/request text through a file tool or properly quoted PowerShell
here-string. Never interpolate user text into executable shell commands. Use
an ignored task folder under `artifacts/` or a small temporary directory;
do not commit personal requests, schedules or output.

## Same-process selection and mutation

`find_reservations` selections are one-use, current-turn objects owned by one
`BookerToolSurface` instance. Do not copy them between CLI processes. Use
`batch --file PATH` to refresh, find and act in the same instance.

Illustrative template; resolve the date, exact identity and user text first:

```json
{
  "user_request": "THE ACTUAL USER REQUEST",
  "steps": [
    {"tool": "refresh_booker_data", "arguments": {"scope": "agenda"}},
    {"tool": "find_reservations", "name": "chosen", "arguments": {
      "date": "YYYY-MM-DD", "room": "EXACT ROOM", "start_time": "11:00", "end_time": "12:00"
    }},
    {"tool": "edit_reservation_time", "selection_from": "chosen", "arguments": {
      "mode": "trim_start", "new_start_time": "11:30"
    }}
  ]
}
```

`selection_from` works only with `edit_reservation_time`/`cancel_reservations`.
An empty or stale selection stops the batch. The editor rejects multiple
matches; choose a bulk cancellation only when the user requested that set.

The batch is sequential, **not atomic**. Earlier steps may already have changed
settings or reservations. Failure, uncertainty, partial cancellation or a stale
selection stops later steps. Never replay the whole batch to retry its tail.

Exit 0 / `dispatch_completed` means the bridge dispatched operations, not that
a room was booked. Read host results: `verified_changed`, `verified_cancelled`,
`verified_absent`, verified receipts and a refreshed agenda establish outcomes.
Progress goes to stderr; JSON goes to stdout. Exit 2 requires review before
further writes. Retain and reconcile receipts after timeout.

## Extra shared-app helpers

- `read_phone_preferences {}`: normalized settings and current `revision`.
- `save_phone_preferences {revision, changes}`: the phone's revision-checked
  save, requiring an authorizing request. Includes booking rules, which the core
  assistant preference tool does not currently expose.
- `get_manual_protections {}`: edited-booking pins and protected intervals.

These helpers use canonical state, not a second phone profile.
