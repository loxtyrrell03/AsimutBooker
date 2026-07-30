# AsimutBooker

Reliable, policy-aware room booking automation for the Royal Welsh College of
Music and Drama (RWCMD) Asimut system.

The application reads Asimut's current semantic HTML/SVG, plans bookings with
pure policy code, and uses Playwright only at the browser boundary. It never
reports a booking or extension as successful until the exact event ID, date,
room, start, and end are visible again in a freshly loaded Asimut agenda.

## Reliability model

- Unknown or malformed page state is **unknown**, never "free".
- Room identity comes from `data-location-id`; occupied intervals come from
  `data-event-id` overlays; time coordinates are calibrated from the live SVG
  hour legend.
- The requested overview date must exactly match the observed date.
- Agenda rows use stable `day-header` and `data-cy="event_<id>"` identity.
- All times are integer-minute, half-open intervals in `Europe/London`.
- Configuration cannot override the institution timezone, preventing horizon
  and calendar-date drift.
- Per-room 3/5/7-day horizons apply to the booking **end**, matching Asimut's
  server warning and rolling frontier.
- Native Asimut time-picker controls are used. Direct text filling is not
  trusted because it can change the visible value without updating Angular.
- Save is a transaction with post-write reconciliation. An uncertain result is
  quarantined rather than blindly retried.
- One OS-level worker lock and one Windows scheduled task prevent overlapping
  runs.
- SQLite WAL state records runs, observations, event identities, idempotent
  intents, extensions, and health.

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config\config.example.yaml config\config.yaml
```

Authorize the dedicated persistent booker profile once:

```powershell
.\.venv\Scripts\python.exe -m asimut_booker.cli login
```

The full Chromium profile is retained locally for subsequent manual and
scheduled runs. `state.json` is only an atomic cookie recovery/migration source,
not a new temporary browser identity for every run.

If a visible manual run encounters expired authentication, it preserves the
same browser window through Microsoft MFA for up to 15 minutes, saves the
verified profile, and then resumes that run. Headless and scheduled commands
never prompt interactively. The control panel reports the current authentication
host and elapsed time every 10 seconds instead of appearing idle.

Then verify the live session and page contracts:

```powershell
.\.venv\Scripts\python.exe -m asimut_booker.cli doctor --json
```

## Use

Open `AsimutBooker.bat` for the control panel, or use the CLI:

```powershell
# Visible manual run
.\.venv\Scripts\python.exe -m asimut_booker.cli run

# Read-only planning run
.\.venv\Scripts\python.exe -m asimut_booker.cli run --headless --dry-run

# Scheduled/headless run
.\.venv\Scripts\python.exe -m asimut_booker.cli run --headless --scheduled

# Local status
.\.venv\Scripts\python.exe -m asimut_booker.cli status --json
```

Status remains degraded until a live login/doctor check succeeds; an existing
but expired session file cannot produce a false healthy result. A newer
successful scheduled authentication supersedes an older doctor failure.

Install exactly one 15-minute Windows task:

```powershell
.\setup_scheduled_tasks.ps1 `
  -StartTime 07:30 -EndTime 22:15 `
  -IntervalMinutes 15 -LeadSeconds 120 `
  -ExecutionTimeLimitMinutes 12
```

The task wakes two minutes before each quarter-hour release, uses
`IgnoreNew`, wakes from sleep, requires network, and does not alter machine
power policy. A delayed launch is accepted only through the configured
lateness window; cadence and active-window endpoints are validated against the
same 15-minute grid used by Asimut.

## Configuration

`config/config.yaml` controls:

- ordered, enabled rooms and their explicit 3/5/7-day horizons;
- preferred windows per weekday and strict/fallback behavior;
- blackout dates and maximum bookings per run;
- duration, quota, peak-time, and same-room-gap rules;
- scheduler window, lead time, lateness, and timeout;
- browser state, database, lock, and diagnostic paths;
- optional notifications using a non-guessable topic.

Unknown room horizons are rejected. The example contains the 28 B0/B1
practice rooms observed in the live AHC location group. A newly appearing room
must be verified before it can be enabled. Fallback still happens through the
ordered configured list; an unlisted-room fallback is deliberately rejected.

## Extension and snipe behavior

If a target booking is not fully inside its horizon, the booker creates the
minimum released interval and persists the desired end. On later runs it:

1. reloads the exact event from the agenda;
2. recalculates the released end at the current instant;
3. checks the room's newly free tail in the live overview;
4. reevaluates conflicts, quota, peak time, and same-room gap with the current
   event removed as a replacement;
5. edits through the native time picker; and
6. verifies the new end in a refreshed agenda.

Pending extensions run before new snipes. Imminent snipes can pre-fill within
the configured lead window and wait against a monotonic deadline. Only the
live, recognised horizon warning is allowed during pre-fill; other blockers
fail closed. Dry-run plans simulate each accepted target so the displayed
choices cannot overlap one another or collectively exceed quota. If a horizon
releases while the multi-day scan is still running, the candidate is
reclassified immediately and remains eligible in that same run.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Normal test collection is fixture-based and cannot access the live service.
Legacy scripts that could make bookings during collection have been removed.

## Privacy

`data/`, `logs/`, screenshots, browser state, SQLite files, and diagnostic
artifacts are ignored because they can contain personal calendar information
or authentication material. They remain local. If this repository was
previously public, removing them from the current tree does not erase older Git
history; history cleanup is a separate deliberate operation.
