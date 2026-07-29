# AsimutBooker

Automated, policy-aware booking of RWCMD practice rooms through Asimut.

> **AI maintenance rule:** update this file whenever behavior, architecture,
> selectors, rules, commands, or file layout changes. Never reintroduce
> live-writing scripts under pytest-discoverable names.

## Core safety contract

Asimut is the source of truth. Local state is coordination/audit state only.

1. A failed or incomplete parse must stop writes. It must never become an
   empty agenda or an available room.
2. Unknown rooms have no inferred horizon. They are not bookable until an
   explicit 3/5/7-day policy is verified and configured.
3. A booking or extension is successful only after the exact remote event ID,
   local date, room, start, and end are observed in a freshly loaded agenda.
4. Once Save may have been clicked, any inability to reconcile is
   `AmbiguousBookingResult`. Do not retry that intent automatically.
5. All browser writes require the process `FileLock`. Scheduled overlap also
   uses Task Scheduler `IgnoreNew`.
6. Never use input `.fill()` for Asimut time fields. The visible text can
   change without Angular persisting the model. Use the native time picker and
   read the live input property afterward.
7. Cancelled agenda events are identified by explicit status semantics, not
   CSS color.
8. Runtime files may contain personal/authentication data and must remain
   ignored.
9. RWCMD dates and rolling horizons are always interpreted in
   `Europe/London`; configuration rejects another timezone.

## Architecture

```
CLI / Tk control panel / one Windows task
                  |
          BookingCoordinator
        /          |          \
 pure planner   SQLite WAL   AsimutGateway
 and policies   + OS lock    (Playwright)
                                |
                    validated HTML observations
```

### Pure domain

- `asimut_booker/intervals.py`: same-day half-open integer-minute intervals.
- `asimut_booker/models.py`: typed events, rooms, intents, candidates, and
  availability states.
- `asimut_booker/policies.py`: exact London horizon release, duration, quota,
  peak, personal-conflict, and same-room-gap checks.
- `asimut_booker/planner.py`: exhaustive 15-minute candidate generation,
  preference ranking, currently bookable candidates, and upcoming snipes.

This layer has no browser, filesystem, database, or current-time side effects.

### HTML observations

- `overview.py` parses the current SVG overview.
- `agenda.py` parses the signed-in agenda.
- `forms.py` validates booking/edit and arrangement snapshots.
- `adapters.py` translates observations/configuration to the pure domain.

Observed overview contract:

- `[data-cy="display-date"]`
- `[data-cy="increment-date-chevron"]` and decrement counterpart
- `#svg-grid` with a `viewBox`
- `#legend-x-inner p[style*=left]` for live x/minute calibration
- `#legend-y-inner a[data-location-id]` for stable room identity
- `#closed-hours rect`
- `#event-overlays rect[data-event-id][data-location-id]`
- quota counters under `rolling-quota` and `peak-quota`

Do not hardcode `07:00`, `6.25%`, row pixels, event colors, or viewport-relative
Y proximity. Calibrate from the page and require the requested date.

Observed agenda contract:

- `[day-header="<ISO date with offset>"]`
- `[data-cy="event_<event id>"]`
- `[data-cy="event-datetime"]`
- `[data-cy="event-display-name"]`
- `[data-cy="event-location-link"]`
- arrangement href and aria status

### Browser boundary

`asimut_booker/gateway.py` owns Playwright. It:

- validates and atomically updates saved browser state;
- positively proves signed-in agenda HTML;
- navigates by semantic date controls and checks the observed date after every
  click;
- clicks a calibrated SVG slot and verifies the contextual room/time heading;
- selects time through the native picker;
- reads live form properties and server warning text;
- permits a pre-release snipe form only when its sole blocker matches the
  observed Asimut horizon warning contract;
- submits once; and
- reconciles against arrangement plus refreshed agenda.

Diagnostic HTML/screenshots are local-only under the configured artifacts
directory.

### Coordinator and persistence

`coordinator.py` performs a bounded run:

1. verify authentication;
2. read and persist the authoritative agenda/quota;
3. reconcile uncertain prior intents;
4. reconcile and process extensions;
5. scan every enabled date from today through the maximum horizon;
6. derive free intervals from validated SVG HTML;
7. rank imminent snipes before ordinary opportunities;
8. persist an idempotent intent before each write;
9. revalidate immediately before submission; and
10. persist only remotely verified mutations.

`database.py` uses SQLite WAL, full synchronous durability, schema migrations,
foreign keys, compare-and-swap run completion, non-regressing extensions, and
idempotency keys. `locking.py` supplies the cross-process kernel lock with
holder metadata and crash recovery.

## RWCMD rules represented

- Minimum duration: 30 minutes.
- Maximum duration: 120 minutes.
- Increment: 15 minutes.
- Configured future/rolling quota: 28 hours; live Asimut remaining quota is
  also required and constrains decisions.
- Peak: Monday-Friday 09:00-16:00, maximum 120 minutes per day.
- Same-room gap: 60 minutes; touching bookings have zero gap and are rejected.
- Personal calendar conflicts include reservations and non-reservation events.
- Only active `Reservation` events count toward reservation quota.

### Exact room horizon semantics

The live server warning confirmed that a horizon limits the booking **end** to
`now + horizon_days`, rolling to the minute.

For a room with horizon `H`, an interval ending at `E` releases at:

```
local_datetime(target_date, E) - H days
```

Thus a 10:00 start with 30-minute minimum first releases at 10:30 `H` days
earlier. `released_end_minute()` rounds the current frontier down to the
configured increment.

Verified configuration:

- 3 days: B1.09, B1.16
- 5 days: B0.23, B0.24, B0.27, B0.29, B1.06-B1.08, B1.10-B1.11,
  B1.14-B1.15, B1.17-B1.21
- 7 days: B0.11, B0.13, B0.14, B0.16, B0.28, B0.35, B1.22-B1.25

Hopkins Studio was observed in the live group but is not enabled because its
horizon has not been explicitly verified.

There is no unlisted-room fallback mode. Room priority already supplies safe
fallback choices among the 28 explicitly configured rooms; enabling an
unknown room without a verified horizon is rejected at configuration load.

## Extensions

An initial horizon-edge booking stores its stable external event ID, current
end, and target end in SQLite. Each extension run:

- finds the exact event in a new agenda;
- accepts already-advanced remote state after a prior crash;
- calculates the latest released end;
- checks only the tail after the existing event against live room occupancy;
- validates the replacement interval with the existing event removed from
  conflicts/quota accounting;
- uses the native end-time picker; and
- records progress only after agenda reconciliation.

Manual cancellation in the GUI cancels only future extension attempts, not the
existing Asimut reservation.

## Snipes and scheduling

The canonical task runs every 15 minutes, normally two minutes before the
quarter-hour frontier. The coordinator pre-fills only candidates within that
lead window and waits using a monotonic deadline. A disabled prefilled form is
accepted only for the observed "not allowed ... end later than" horizon
warning; occupancy or unknown warnings fail closed. Pending extensions have
priority. Each candidate is reevaluated after prior writes so the booker cannot
book two rooms over the same personal time. Dry-run output also reserves each
hypothetical target in memory, so its "Would book" lines are mutually
compatible and collectively respect quota. Horizon-only classifications are
refreshed after scanning, so a release crossed during slow page navigation is
booked in the same run rather than missed until the next quarter hour.

Scheduled invocations are accepted only from `lead_seconds` before a target
through `max_lateness_seconds` afterward. Cadence is fixed at 15 minutes,
active-window endpoints must align to that cadence, and lead plus lateness must
be shorter than one interval. This makes the control-panel lateness setting
real and prevents a delayed wake from replaying a stale snipe as if on time.

`setup_scheduled_tasks.ps1` creates exactly one `AsimutBooker` task with:

- `IgnoreNew`
- `WakeToRun`
- `StartWhenAvailable`
- network required
- least-privilege interactive user token
- a configured execution limit
- no mutation of laptop lid or global power policy

The wrapper uses an absolute `.venv` interpreter, unique logs, and preserves
the real exit code.

## Control panel

`gui.py` is a thin Tk client with Dashboard, Rooms, Availability, Blackouts,
Extensions, History, and Diagnostics views. It:

- edits canonical typed YAML atomically with a backup;
- reads SQLite in read-only mode;
- runs the canonical CLI and renders JSONL progress;
- supports visible/headless/manual runs and dedicated login;
- manages one scheduled task;
- treats Task Scheduler's pre-epoch "never run" sentinel as empty; and
- isolates and persistently logs individual UI callback failures so one bad
  health/status result cannot freeze subsequent controls or live output;
- exposes extension reconcile/retry/cancel controls; and
- exports local diagnostics/history.

Overall health is never inferred from the mere presence of a session file.
An unhealthy live doctor/authentication result stays unhealthy, and a
positive live validation is required before the dashboard reports healthy.
When doctor and scheduled authentication results disagree, the newest
timestamped result wins so a successful recovery clears an older failure.

Do not add a second booking implementation to the GUI.

## Commands

```powershell
# Environment
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium

# Login and health
.\.venv\Scripts\python.exe -m asimut_booker.cli login
.\.venv\Scripts\python.exe -m asimut_booker.cli doctor --json
.\.venv\Scripts\python.exe -m asimut_booker.cli status --json

# Runs
.\.venv\Scripts\python.exe -m asimut_booker.cli run
.\.venv\Scripts\python.exe -m asimut_booker.cli run --headless --dry-run
.\.venv\Scripts\python.exe -m asimut_booker.cli run --headless --scheduled

# Tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q asimut_booker
```

`book_week.py` remains only as a compatibility wrapper.

## Exit codes

- 0: success, no-op, or deliberate scheduled skip
- 2: invalid configuration
- 3: login required
- 4: another worker holds the lock
- 5: page contract/navigation failure
- 6: ambiguous remote write
- 7: unexpected failure
- 8: local database failure
- 130: interrupted

## Repository layout

```
AsimutBooker/
├── asimut_booker/
│   ├── cli.py, coordinator.py, gateway.py
│   ├── overview.py, agenda.py, forms.py, adapters.py
│   ├── intervals.py, models.py, policies.py, planner.py
│   ├── config.py, database.py, locking.py, notifications.py
│   └── errors.py
├── tests/                     # offline fixtures and deterministic tests
├── config/config.example.yaml
├── gui.py
├── book_week.py               # compatibility wrapper
├── run_booker.ps1 / .bat
├── setup_scheduled_tasks.ps1
├── AsimutBooker.bat
├── pyproject.toml
└── data/README.md
```

## Privacy and maintenance

- `config/config.yaml`, browser state, SQLite, legacy JSON, logs, screenshots,
  and diagnostics are ignored.
- Never commit credentials, cookie state, participant names, agenda HTML, or
  guessable notification topics.
- Removing sensitive runtime files from the current Git tree does not remove
  them from old public history. A history rewrite/credential rotation is a
  separate destructive remediation requiring explicit authorization.
- Keep selector fixtures current when the site changes. A contract change
  should first cause a safe stop, then a fixture and parser update.
