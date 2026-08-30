# AsimutBooker

## Local checkout

- The canonical Windows checkout is `C:\Users\Lox\Desktop\repo\AsimutBooker`.
- `C:\Users\Lox\Desktop\Development\repo\AsimutBooker` is a compatibility junction to the canonical checkout so existing launchers and path-based integrations continue to work.
- The shared launcher is `C:\Users\Lox\Desktop\repo\.dev-tools\Start-DevApp.ps1`; the regenerated Asimut Booker shortcut lives under `C:\Users\Lox\Desktop\Dev Apps` and uses the canonical checkout directly.

## Milestone documentation

- Agents must update this `AGENTS.md` after every meaningful, verified milestone and include that update in the same milestone commit.
- Record concise, durable context: important behavior or architecture changes, decisions and their rationale, relevant tests or verification, deployment or runtime state, and material limitations or follow-up work.
- Update or replace stale guidance instead of accumulating contradictory history; keep notes factual and useful to future agents.
- Do not record secrets, credentials, personal data, raw transcripts, routine command logs, or transient debugging noise.


Automated booking system for Royal Welsh College of Music and Drama (RWCMD) practice rooms via Asimut.

> **IMPORTANT FOR AI AGENTS**: When making changes to this codebase, always update this `CLAUDE.md` file to reflect new features, changed behavior, or updated architecture. Keep documentation in sync with code.

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs through Windows Task Scheduler, requests wake-from-sleep on AC or battery, and books rooms as they become available. Actual wake behavior depends on Windows, firmware, and hardware support.

### Key Features

- **Autonomous Login**: Reuses persistent browser state first, then recovers an expired Microsoft 365 session with a Windows Credential Manager password and the local RWCMD SMS bridge—without an AI model
- **Room Preferences**: GUI ordering, exclusions, live instrument/type/feature requirements, minimum block length, and fragmentation policy
- **Live Room Policy**: Refreshes the current AHC room catalog, metadata, per-room horizons, and booking-window cutoff from Asimut before every authenticated booking or check run
- **Scheduled Execution**: One non-overlapping task runs every 15 minutes (07:13-21:58) with AC/DC wake-timer requests and missed-start recovery
- **RWCMD Booking Rules**: Respects rolling quota (28 hours/week), peak hours (2hr/day Mon-Fri 9am-4pm), and the greater of the configured/default same-room gap and Asimut's fresh minimum
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts; extracts room names for same-room gap enforcement; distinguishes "Reservation" events from classes for accurate quota tracking
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring, manual control, preferences, and automatic-schedule repair
- **Health Dashboard**: Shows the last successful run, next scheduled run, saved-session evidence, auth cooldown, pending mutations, and physical wake-test evidence
- **Practice Plan**: Set a default daily target from 0.5-12 hours, override individual dates, or turn dates off across Asimut's current live booking window
- **Verified Mutations**: A booking or extension counts only after the positive event ID and exact persisted room/date/time survive a reload
- **Crash Recovery**: Durable pre-Save receipts stop further mutations when a result is uncertain and force agenda reconciliation on the next run
- **Booking History**: Tracks verified runs and bookings with locked, atomic persistence
- **Push Notifications**: Optional ntfy.sh notifications for booking results

## Technical Stack

- **Language**: Python 3.11+
- **Browser Automation**: Playwright (handles JavaScript-heavy sites, SSO flows)
- **Scheduling**: Windows Task Scheduler with wake timers
- **GUI**: Tkinter-based control panel
- **Session Management**: Persistent browser context to maintain login state

## Target Institution

- **Institution**: Royal Welsh College of Music and Drama (RWCMD)
- **Asimut URL**: `https://rwcmd.asimut.net/`
- **Location Category**: Music Practice Rooms - AHC
- **Authentication**: Microsoft 365 SSO

### Authentication Recovery for Agents

- If Asimut is signed out, use the dedicated `asimut-booker-auth` skill. Its wrapper invokes the same deterministic recovery code used by scheduled booking runs; do not use an AI/browser-control model to type the password or OTP.
- The password belongs only in the current user's Windows Credential Manager under `AsimutBooker/RWCMD-Microsoft365`. Configure it through the booker's masked interactive prompt; never put credentials, OTPs, or bridge tokens in commands, repository files, logs, or agent messages.

## Directory Structure

```
AsimutBooker/
├── book_week.py          # Main booking script (entry point)
├── asimut_auth.py        # Deterministic Credential Manager + SMS sign-in recovery
├── gui.py                # Desktop control panel GUI
├── app_settings.py       # Locked, atomic shared JSON persistence
├── event_identity.py     # Collision-safe shared agenda-event identity
├── practice_plan.py      # Strict daily-target schema and budget helpers
├── runtime_guard.py      # Single-instance and exact confirmation helpers
├── mutation_receipts.py  # Crash-safe booking mutation journal
├── room_catalog.py       # Strict read-only live Asimut room/window discovery
├── room_preferences.py   # Shared room-preference schema and matching
├── live_room_policy.py   # Immutable run policy built from fresh site data
├── health_status.py      # Read-only health evidence readers
├── run_booker.bat        # Batch file called by Task Scheduler
├── AsimutBooker.bat      # GUI launcher (double-click to open)
├── setup_scheduled_tasks.ps1  # Creates Windows scheduled tasks
├── create_shortcut.ps1   # Creates desktop shortcut
├── config/
│   ├── config.example.yaml  # Exact supported advanced-config schema
│   └── config.yaml       # Optional user override (gitignored)
├── data/
│   ├── browser_state/    # Persistent login state (gitignored)
│   │   └── state.json
│   ├── auth_recovery_state.json  # No-secret auth retry breaker (gitignored)
│   ├── booking_history.json  # Run history
│   ├── settings.json     # GUI settings, room preferences, plans, and extensions
│   ├── room_catalog.json # Display-only live catalog cache (gitignored)
│   ├── physical_wake_test.json # Optional dedicated wake-test evidence (gitignored)
│   └── mutation_receipts.json  # Runtime reconciliation journal (gitignored)
├── logs/                 # Booking logs
│   └── scheduler.log
├── src/                  # Legacy CLI compatibility; booking/config engines retired
│   ├── __init__.py
│   ├── main.py           # Translates safe old flags to book_week.py
│   ├── auth.py
│   ├── booker.py
│   ├── scheduler.py
│   └── config.py
├── requirements.txt
└── CLAUDE.md
```

## Usage

### GUI (Recommended)
```bash
# Double-click AsimutBooker.bat or run:
pythonw gui.py
```

The GUI provides:
- A six-card health dashboard with independently sourced status and detail
- Run booker manually (visible or headless)
- View booking history
- Setup/remove scheduled tasks
- Default and per-date desired practice hours
- Live-window booking/off controls and strict preferred-time controls
- A room editor for ordering, exclusions, live metadata requirements, minimum block length, and fragmented-session policy

### Command Line
```bash
# Run with visible browser (for testing/debugging)
python book_week.py

# Run headless (for scheduled tasks)
python book_week.py --headless

# One-time Microsoft/RWCMD sign-in
python book_week.py --setup-login

# One-time secure autonomous-login credential setup (private terminal)
python book_week.py --configure-autonomous-login

# Verify/recover login only; never scans or changes bookings
python book_week.py --headless --login-only

# Read-only login, live room-policy refresh, agenda, and current-window grid check
python book_week.py --headless --check-only

# Bounded live verification (one verified 30-minute mutation at most)
python book_week.py --headless --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 30
```

### Initial Setup
1. Run `python book_week.py --configure-autonomous-login` in a private terminal and enter the RWCMD email and masked password; Windows Credential Manager stores them for the current user.
2. Run `python book_week.py --headless --login-only`. It reuses an existing session or completes Microsoft password + SMS recovery through the local bridge.
3. Browser state is saved to `data/browser_state/state.json` and refreshed after every authenticated run.
4. `python book_week.py --setup-login` remains a visible manual fallback, not the scheduled recovery path.

### Scheduled Tasks Setup
```powershell
# Run as Administrator
.\setup_scheduled_tasks.ps1
```

This replaces obsolete per-time tasks with one `AsimutBooker_Recurring` task:
- Every 15 minutes from 07:13 through 21:58
- The task requests wake-from-sleep and setup enables AC/DC wake timers; actual wake support remains hardware/firmware dependent
- Network gating and missed-start recovery enabled
- Overlapping starts ignored; the booker also holds an OS single-instance lock
- Plugged-in lid close action set to "Do nothing"
- GUI installation/repair uses the headless Agent UAC helper and verifies the registered task

## Booking Rules (RWCMD)

The script enforces these rules:
- **Rolling Quota**: Maximum 28 hours per rolling week (only "Reservation" events count, not classes)
- **Peak Hours**: Maximum 2 hours **per day** during Mon-Fri 9am-4pm
- **Booking Duration**: Asimut currently reports 30-120 minutes; the GUI-selected minimum is enforced within those fresh limits
- **Same-Room Gap**: Enforces the greater of the configured/default 60 minutes and Asimut's freshly reported minimum
- **Room Horizons**: Uses each room's current site-reported advance window; arbitrary positive 15-minute horizons are supported
- **Dynamic Allocation**: Bookings per day adjust based on enabled days to maximize quota usage
- **Smart Redistribution**: Hours are distributed evenly across enabled days - days with existing bookings get fewer new slots

## Horizon Edge Booking & Extension

Rooms become bookable at the exact per-room horizon reported by Asimut. The current site may group rooms into whole-day windows, but the runtime does not encode those groups and accepts arbitrary positive 15-minute horizons. Due to the selected minimum-block rule, this creates a staggered booking pattern. The timeline below is the default 30-minute example:

### How It Works

**Example**: Room B0.27 (5-day horizon), slot at 10:00 on Feb 7

1. **Horizon edge** = Feb 2 at 10:00 (exactly 5 days before)
2. **At 10:00**: Slot just became visible, but only 0 minutes are "past" the horizon - cannot book yet
3. **At 10:30**: 30 minutes past horizon - can now book **10:00-10:30** (minimum 30 min)
4. **At 10:45**: 45 minutes past - can extend to **10:00-10:45**
5. **At 11:00**: 60 minutes past - can extend to **10:00-11:00**
6. **...continues in 15-minute increments...**
7. **At 12:00**: 120 minutes past - can extend to **10:00-12:00** (maximum 2 hours)

### Extension Flow

The booker automatically handles this in two phases:

1. **Initial Booking**: At the first possible moment (the selected minimum block after the horizon), books exactly that block and saves the exact verified positive event ID/URL with its `extendable_bookings` record in settings.json

2. **Extension Runs**: Every 15 minutes, the scheduled task runs again and:
   - Loads pending extendable bookings
   - Binds legacy records only from one exact complete-agenda reservation with a positive event ID, persisting that migration before editing
   - Selects the one current/legacy event card bound to that exact ID; a missing, duplicate, cancelled, or tuple-mismatched card stops without Save
   - Calculates how much more time is now available (based on time since horizon, not time since booking)
   - Extends each booking by the available amount (in 15-minute increments)
   - Continues until reaching 2 hours or the target duration

### Timeline Example

```
10:00  Horizon edge - slot becomes visible but not bookable
10:30  Book 10:00-10:30 (first possible moment)
10:45  Extend to 10:00-10:45  (scheduled task runs)
11:00  Extend to 10:00-11:00  (scheduled task runs)
11:15  Extend to 10:00-11:15  (scheduled task runs)
11:30  Extend to 10:00-11:30  (scheduled task runs)
11:45  Extend to 10:00-11:45  (scheduled task runs)
12:00  Extend to 10:00-12:00  (scheduled task runs) - DONE
```

### Key Functions

- `is_room_available_to_book()`: Checks if a slot is past its horizon edge
- `save_extendable_booking()`: Saves a booking for later extension
- `calculate_max_extension()`: Determines how much a booking can be extended based on current time vs horizon
- `try_extend_booking()`: Attempts to extend a booking via Asimut's edit feature

## Live-Window Horizon Snipe

For an exact boundary `T`, the booker derives each room's one newly bookable
start as `T + fresh room horizon - selected minimum block`. It then checks
whether that exact minimum block is contained inside a free grid gap. This
catches quarter-hour edges inside a full-day gap and supports arbitrary live
15-minute horizons without encoding 3-, 5-, or 7-day room groups.

### How Multi-Day Snipe Works

1. **Fresh planning phase** (inside the three-minute edge window):
   - Refresh the current category, group, booking limits, rooms, and warnings from Asimut
   - Derive the exact calendar dates and room horizons from that observation
   - Apply `--only-date` and `--only-room` before any calendar traversal
   - Wait outside the runtime lock when an explicit target is early, so policy and agenda evidence are not collected until the live window

2. **Priority-progressive discovery**: Visit the date containing the highest-ranked untested room. As soon as the highest-priority free exact edge is proven, prepare it without scanning lower-ranked horizon dates.

3. **Verified snipe**:
   - Navigate to candidate's day
   - Pre-fill the selected minimum block and wait using conservative fresh Asimut clock bounds
   - At the boundary, force and await a new trusted `event/type=check` response so a warning captured during early preparation cannot remain stale
   - Re-prove the unsaved-event URL, room/date/times, live inputs, cleared warnings, and a fresh visible/enabled Save control before writing the receipt and clicking Save
   - A missing/ambiguous site clock, missing validation response, changed form, or rejection stops without Save

4. **Resume normal booking**: Navigate to the first date in the selected chronological or furthest-first strategy

### Extension Priority

Pending extensions take priority over new snipes. If a slot has a pending extension (from a previous horizon edge booking), the snipe scanner skips that slot to avoid conflicts.

### Key Functions

- `navigate_to_day()`: Uses unique semantic date buttons, exact date/grid proof, and one canonical-reset retry
- `plan_horizon_edge_slots()`: Derives exact room/date/start plans from the fresh site horizons
- `find_all_snipe_candidates_multi_day()`: Tests exact edge containment and stops after the best free GUI-ranked room
- `find_horizon_snipe_candidate()`: Retained tested helper for one already-loaded date

## Configuration

Everyday preferences belong in the GUI and `data/settings.json`:

- **Practice plan**: Optional default hours per enabled day plus exact-date overrides
- **Booking days**: Dates can be enabled or disabled independently
- **Preferred time**: Presets or a custom range, with an optional strict-only mode
- **Date order**: Chronological or furthest-first
- **Room order and exclusions**: Rank every live room and explicitly disable rooms
- **Room requirements**: Optional instrument or room-type tag choices plus required feature search terms, all matched against current site metadata
- **Session shape**: Choose a 30-120 minute minimum block in 15-minute steps. With fragments disabled, a date with an existing reservation is skipped and at most one new reservation block is created on a previously empty date

Advanced rule overrides are optional. Copy `config/config.example.yaml` to
`config/config.yaml` and edit only its documented `rules` fields. Room order,
exclusions, requirements, and session shape belong in the GUI. Room lists,
metadata, horizons, and the global booking cutoff are site-owned and cannot be
overridden by YAML; legacy room/horizon fields fail closed instead of silently
reintroducing stale policy.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run with visible browser (debugging)
python book_week.py

# Run headless
python book_week.py --headless

# Run the complete offline regression suite
python -m unittest discover -s tests
```

## Files Overview

| File | Purpose |
|------|---------|
| `book_week.py` | Main booking script - scans agenda, navigates calendar, books slots |
| `asimut_auth.py` | Deterministic Windows credential, Microsoft SSO, and SMS-bridge recovery |
| `gui.py` | Tkinter GUI for monitoring and control |
| `app_settings.py` | Strict, locked, atomic settings/history storage primitives |
| `event_identity.py` | Deterministic v2 ignored-event identity and legacy-key resolution |
| `practice_plan.py` | Daily-target validation and booking-budget helpers |
| `runtime_guard.py` | Single-instance lock and exact URL/identity confirmation |
| `mutation_receipts.py` | Durable pre-Save receipts and reconciliation state |
| `room_catalog.py` | Strict live category/group/room metadata and horizon discovery |
| `room_preferences.py` | Shared GUI/runtime preference validation and room matching |
| `live_room_policy.py` | Fresh-catalog policy, eligible room order, and dynamic date window |
| `health_status.py` | Tk-free readers for history, session, auth, mutation, and wake evidence |
| `run_booker.bat` | Wrapper script called by Task Scheduler |
| `setup_scheduled_tasks.ps1` | Installs and verifies the one recurring task |
| `config/config.example.yaml` | Exact supported advanced configuration schema |
| `config/config.yaml` | Optional local advanced-rule override; room policy is site-owned |
| `data/browser_state/state.json` | Saved browser session (cookies, localStorage) |
| `data/auth_recovery_state.json` | Gitignored no-secret credential latch / transient retry cooldown |
| `data/booking_history.json` | JSON log of all booking runs |
| `data/settings.json` | GUI settings, room preferences, practice plan, ignored events, and extendable bookings |
| `data/room_catalog.json` | Gitignored display-only cache of the last complete live room observation |
| `data/physical_wake_test.json` | Gitignored optional evidence from a dedicated physical wake test |
| `data/mutation_receipts.json` | Gitignored crash-recovery journal for remote mutations |

## Reliability Contract

- Authentication, existing configuration, settings, mutation journal, full
  agenda reach, calendar date, form identity, requested time values, and
  persisted Save identity all fail closed.
- Every authenticated booking and `--check-only` run builds a new room policy
  from Asimut before agenda or mutation work. Discovery reads the current
  booking category/group, group metadata, event defaults, and one no-Save check
  response per room. An incomplete or inconsistent observation stops the run;
  `data/room_catalog.json` is never booking authority.
- Discovery never calls `event/type=save`. Participant arrays required by the
  in-memory check template are neither logged nor cached.
- Room names are exact site names, including multiword names. The live global
  cutoff determines the complete date set, and per-room warning cutoffs
  determine arbitrary positive 15-minute horizons. Request-minute rollover is
  normalized without accepting a changed duration or a mixed snapshot.
- Creates and extensions write a receipt before Save. Explicit rejection closes
  it; confirmed persistence verifies it; a timeout or mismatch leaves it pending
  and prevents further mutations until an exact agenda/event reconciliation.
  Receipts whose intended slot has already ended close safely instead of
  permanently blocking future runs.
- Only exact positive HTTPS `rwcmd.asimut.net/arrangement?eventId=N` URLs count
  as saved. Stale or foreign-host URLs, `eventId=0`, wrong rooms/dates/times,
  partial agenda scans, and ambiguous date navigation never count as success.
  Any unexpected error after Save is reconciliation-required, never a retryable
  slot failure in the same run.
- Current agenda cards are matched by positive event ID as well as exact
  room/date/time. Each required day and every non-cancelled event card must be
  represented by the extractor; missing, duplicate, malformed, or mismatched
  identities stop the run before any mutation.
- Authentication always tries saved browser state first. Only an actual sign-in
  redirect reads the Windows credential; recovery makes one password attempt,
  consumes at most one SMS code, and persists state only after Asimut's
  authenticated shell is visible. Missing credentials or an unaccepted factor
  fail closed before agenda scanning or mutation.
- Settings, history, session state, and receipts use atomic replacement; shared
  JSON writes use interprocess locks. GUI live scans use the same deterministic
  authentication recovery as the booker and discard stale background results.
- Ignored events use a v2 identity covering date, start, end, title, room, and
  reservation type. A legacy time-only key applies only when it identifies one
  distinct scanned event; ambiguous matches ignore nothing. Ignored reservations
  still consume daily/weekly/peak budgets and enforce their same-room gap.
- `--check-only` performs a read-only authenticated policy refresh and agenda
  scan, then traverses every date currently exposed by Asimut. `--only-date`, `--only-room`, `--max-actions`, and
  `--max-action-minutes` provide bounded live verification without weakening the
  confirmation rules.

## 2026-08-30 Boundary-Driven Horizon Reliability Milestone

- Horizon discovery now derives one exact start per eligible room from the
  current boundary, fresh per-room horizon, and GUI-selected minimum block. It
  tests that block inside maximal free gaps, so a full-day `07:15-22:15` gap no
  longer hides every edge after its first start. Only exact site-derived dates
  are visited; controlled room/date scopes apply before navigation.
- Discovery is priority-progressive. The date containing the best-ranked
  untested GUI room is scanned first, and a proven free preferred edge is
  prepared immediately instead of traversing every horizon date first. A
  scheduled pass attempts one best edge; ordinary booking may continue later.
- Calendar traversal uses the unique semantic date controls, proves the exact
  displayed date and stable 29-row grid after every ordinary click, never uses
  `force`, and retries once through the canonical today route without risking a
  double click. The GUI room scanner now uses this same navigation, fresh live
  policy, and renderer-independent slot parser instead of a swallowed legacy
  chevron loop.
- Live discovery intersects the existing trusted HTTPS Date responses into
  conservative run-only Asimut clock bounds, including one full second of Date
  approximation uncertainty. Edge Save is disabled when this evidence is
  missing, inconsistent, or too broad. Early explicit targets wait outside the
  runtime lock; a delayed `--horizon-only --scheduled` run exits instead of
  falling through to ordinary booking.
- A prepared form now forces and awaits a fresh exact
  `/services/v2/event/type=check` response at the boundary. It then re-proves
  URL, room, date, times, warnings, and enabled Save before writing a receipt.
  The first 14:00 live test exposed and safely rejected a stale 13:57 warning;
  no receipt or booking was created.
- The corrected one-action 14:15 live test reload-verified B0.29 on 4 September
  2026 from 13:45-14:15 as event 3580112. Estimated Save-click latency was
  0.917-2.150 seconds after Asimut's edge and persistence confirmation took
  2.286 seconds. A separate `--headless --check-only` process then found the
  exact reservation in the complete four-event agenda and traversed all eight
  live dates, 29 rows per date, with 157 visible gaps. Pending mutation receipts
  remained zero. The complete offline suite passes 350 tests.
- Windows Time is running with Automatic startup on the host and was explicitly
  resynchronized before the successful proof. Site-derived timing remains the
  booking authority, so machine synchronization is an additional safeguard,
  not a replacement for live evidence.

## 2026-08-30 Live Room Policy and Health Dashboard Milestone

- The GUI now separates Overview and Preferences. The room editor exposes live
  room ordering, explicit exclusions, instrument and room-type filters,
  required feature terms, 30-120 minute minimum blocks, and a fragmentation
  toggle. New site rooms append in live order without destroying saved ranks.
- The Overview health dashboard reports last completed run, Task
  Scheduler-reported next start after exact task-contract validation,
  saved-session structure and file mtime, auth cooldown or
  credential latch, pending mutation receipts, and physical wake evidence.
  These are independently read-only: a structurally healthy saved session does
  not claim live authentication, and wake status remains Unknown until a
  dedicated wake-test artifact exists.
- Booking and GUI scan paths refresh the complete live catalog before using
  rooms or dates. The display cache cannot authorize a run. Current and legacy
  overview renderers retain physical row coordinates even when earlier rows are
  excluded; agenda and persisted-event verification still recognize all live
  rooms, including multiword names that are not code-shaped.
- The former `src` booking/config implementation is retired. Its safe legacy
  CLI flags route to `book_week.py`; direct construction of its scheduler,
  booker, or custom config fails closed, leaving one mutation-capable runtime
  and one source of room-window truth.
- Dashboard schedule health uses the installer's complete contract: exact
  launcher and working directory, enabled daily 07:13 trigger, repetition,
  current-user interactive limited principal, wake/recovery, network/battery,
  overlap, execution limit, and restart settings. Any drift is a repair state,
  not a green next-run card.
- YAML is now rules-only. Static YAML room lists, order, or horizons are rejected;
  saved GUI room ordering remains supported.
  Site values support arbitrary 15-minute horizons and dynamic global cutoffs,
  while the current live observation happens to contain 29 rooms at 3-, 5-,
  and 7-day horizons.
- The complete offline suite passes 328 tests. A fresh no-mutation
  `--headless --check-only` run reused the saved session, discovered all 29
  rooms, derived the current eight-date window through 6 September 2026,
  scanned the complete three-event agenda, traversed all 29 room rows on each
  date, and found 154 visible gaps. No booking or edit was attempted.

## 2026-08-30 Reliability and Practice-Plan Milestone

- The authoritative runtime is `book_week.py`. GUI **Check / Repair Login** runs
  `--headless --login-only`; **Update Secure Login** opens the masked
  `--configure-autonomous-login` prompt. The visible `--setup-login` path is a
  CLI-only manual fallback.
- Practice targets are optional and preserve legacy allocation when disabled.
  Enabled plans cap each date against existing reservations and the 28-hour
  rolling quota; the booking loop permits enough selected-minimum blocks to fill a
  target without exceeding it. Saving per-date customization enables the plan
  explicitly, and invalid edits restore the last persisted UI state.
- `AsimutBooker_Recurring` is the only supported task. It runs every 15 minutes
  from 07:13 through 21:58, ignores overlap, requests AC/DC wake, catches up,
  and calls the isolated `.venv` launcher in headless scheduled mode. It fails
  closed if that runtime is missing or unhealthy; physical wake still depends
  on Windows, firmware, and hardware support.
- Offline verification covers strict settings/config, daily and weekly budgets,
  zero/45/60-minute room gaps, date/datetime inputs, A3.39 agenda extraction,
  exact Save transitions, uncertain and expired receipts, collision-safe event
  identity, bounded action caps, concurrent GUI updates, authenticated GUI
  scans, current DOM fixtures, launchers, and scheduler shape. The combined
  suite passes 215 tests. Compact practice-plan edits merge only the field the
  user changed, and every save or rollback resynchronizes both controls from the
  confirmed persisted plan.

## 2026-08-30 Autonomous Authentication Milestone

- `asimut_auth.py` is the non-AI authentication runtime shared by ordinary,
  scheduled, `--login-only`, and mid-run navigation recovery paths.
- The separate `asimut-booker-auth` Codex skill is an agent-facing wrapper for
  that runtime; it does not receive or type credentials itself.
- Passwords are user-scoped Windows Credential Manager secrets. SMS uses the
  existing `RWCMD SMS Bridge` task and in-memory localhost endpoint; passwords,
  OTPs, and bridge tokens are never persisted or logged by the booker.
- Secret-bearing Playwright fills suppress their original exception context,
  and a closing Microsoft popup is followed only after one trusted context page
  proves the continuation. The flow leaves Microsoft's remember-device choice
  unchanged and never signs out to exercise recovery.
- `data/auth_recovery_state.json` stores only a version, normalized failure
  category, and optional retry timestamp. Explicit password rejection blocks
  scheduled retries until secure-login update or a successful explicit repair;
  verification, bridge, and automation failures cool down for 30 minutes.
  **Check / Repair Login** may make one deliberate bypass attempt and success
  clears either block.
- Offline verification covers the session-first boundary, missing-credential
  failure, exact six-digit OTP acceptance, bridge health, and secret redaction.
  Do not log out solely to exercise the fallback; validate it naturally when
  Microsoft next expires the saved session.
- Scheduler setup is transactional: the replacement is registered disabled,
  fully read back, enabled, and read back again. Any failed verification removes
  the replacement and restores a prior definition disabled, re-enabling it only
  when its saved contract had already been proven valid.

## 2026-08-30 Ignored-Event Identity Milestone

- GUI scans and the booking runtime share `event_identity.py`; only exact full
  identities are deduplicated, so simultaneous classes and reservations remain
  independently selectable.
- Saving event preferences always writes v2 keys. Existing time-only keys remain
  compatible when unique; ambiguous keys leave every matching event enabled and
  prompt the user to review and save the selection.

## 2026-08-30 Live Renderer, Booking, and Scheduler Validation

- Saved-state authentication is proven live after one deterministic Microsoft
  credential/SMS recovery. Subsequent login-only, manual, and scheduled checks
  reused the saved session without reading a password, consuming another code,
  logging out, or opening a user-controlled browser.
- `book_week.py` supports both the legacy overview and current
  `app-overview-svg` renderer. SVG readiness waits for asynchronous event
  overlays, room labels are normalized, and click coordinates are refreshed
  after scrolling. The current prefilled event URL and delayed time controls are
  accepted only when their exact fail-closed contracts pass.
- Two bounded 30-minute live bookings were created in distinct AHC rooms on
  distinct dates. Each positive arrangement event survived reload and matched
  its exact agenda event ID, room, date, start, and end. The independent
  `--check-only` pass then found both reservations, the unrelated class conflict,
  all eight dates, 29 grid rows per date, and 154 visible gaps.
- The first live Save exposed a verifier-only mismatch. Its pre-Save receipt
  prevented a duplicate; the corrected current arrangement-card parser and
  agenda/event dual proof safely reconciled it before any further mutation.
- `AsimutBooker_Recurring` is installed as the sole root Asimut task and its
  action, 07:13/15-minute trigger, current-user limited principal, wake,
  catch-up, network, battery, overlap, restart, and runtime limits were read back
  exactly. AC/DC wake timers are enabled, plugged-in lid close is Do nothing,
  and a temporary scheduled `--check-only` run completed with exit code zero and
  was removed. Physical wake still depends on Windows, firmware, and hardware.
- RWCMD bookings are currently labelled provisional, but no exact due-state,
  reconfirm action, eligibility window, or College presence/network policy has
  been proven. The tool therefore does not guess or auto-click reconfirmation;
  add it only after a read-only observation establishes the RWCMD-specific
  contract and it can be journalled and reload-verified like other mutations.

## 2026-08-30 Exact Horizon Lifecycle Milestone

- Horizon creates derive their exact unlock as slot time minus the freshly
  observed room horizon plus the selected minimum block, prepare inside a
  three-minute window, and Save exactly that block using fresh site-clock bounds
  and a boundary-time validation response. Pending extensions run after
  agenda/receipt reconciliation, before overview navigation and all new snipes.
- Extension timing is a pure 15-minute-boundary plan. Due work runs first,
  imminent work opens the exact editor for at most three minutes and fills at
  the boundary, and missed runs catch up in one verified edit to the latest
  completed boundary, never beyond the target or two-hour cap.
- Both horizon creates and extensions re-prove form identity, values, warnings,
  and a fresh visible/enabled Save immediately before writing the receipt.
  Extensions also require the page URL to equal the tracked positive event URL.
  Just-opened creates receive only a three-minute post-boundary grace.
- `--horizon-only` and `--extensions-only` are scoped, action-capped live-test
  modes. They cannot fall through to ordinary booking and return after the
  verified scoped phase without risky cleanup navigation. Use an action ceiling
  at least as large as the selected minimum, and a larger ceiling when later
  extension tracking is intended.
- Deterministic boundary coverage uses the default minimum at +29:59, exact
  +30, and every +15-minute extension
  through +120, missed-run catch-up, priority/order, action short-circuiting,
  form drift, exact event URL, disabled Save, editor disappearance, and isolated
  runtime modes. A complete live extension sequence remains pending a genuine
  safe horizon candidate; never log out or create unrelated bookings to force
  one.
- The historical live `--check-only` pass reused the saved session without
  sign-in, proved the complete three-event agenda and the eight dates then
  exposed by the site (154 visible gaps), and left zero active extensions and
  zero pending receipts. No live mutation was attempted because no genuine
  extension record existed. Current suite totals are recorded in the latest
  milestone above.

## Maintenance Notes

When modifying this codebase:
- **Always update `CLAUDE.md`** when adding features, changing behavior, or modifying architecture
- Keep the "Key Functions" sections current with new/changed functions
- Document any new booking rules or constraints
- Update the Files Overview table if adding new files
