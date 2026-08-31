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

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs through Windows Task Scheduler, requests wake-from-sleep on AC or battery, and books rooms as they become available. Actual wake behavior depends on Windows, firmware, and hardware support.

### Key Features

- **Autonomous Login**: Reuses persistent browser state first, then recovers an expired Microsoft 365 session with a Windows Credential Manager password and the local RWCMD SMS bridge—without an AI model
- **Room Preferences**: GUI ordering, exclusions, live instrument/type/feature requirements, minimum block length, and fragmentation policy
- **Live Room Policy**: Refreshes the current AHC catalog plus selected promoted All Locations rooms, metadata, per-room horizons, and booking-window cutoff from Asimut before every authenticated booking or check run
- **Scheduled Execution**: One non-overlapping task runs every 15 minutes (07:13-21:58) with AC/DC wake-timer requests and missed-start recovery
- **RWCMD Booking Rules**: Respects rolling quota (28 hours/week), peak hours (2hr/day Mon-Fri 9am-4pm), and the greater of the configured/default same-room gap and Asimut's fresh minimum
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts; extracts room names for same-room gap enforcement; distinguishes "Reservation" events from classes for accurate quota tracking
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring, manual control, preferences, and automatic-schedule repair
- **In-App Assistant**: ChatGPT-style Codex chat pinned to `gpt-5.6-terra` with medium reasoning, sanitized Booker context, visible concise reasoning summaries, and typed application actions
- **Health Dashboard**: Shows the last successful run, next scheduled run, saved-session evidence, auth cooldown, pending mutations, and physical wake-test evidence
- **Practice Plan**: Set a default daily target from 0.5-12 hours, override individual dates, or turn dates off across Asimut's current live booking window
- **Daily Foresight**: Ranks the complete fresh room grid across a configurable lookahead, can preserve scarce peak allowance for stronger later sessions, and falls back before an opportunity becomes too risky to lose
- **Booking Plan UI**: Explains ready, waiting, in-progress, and alternative sessions in the dashboard and calendar; hatched blocks are explicitly potential rather than booked
- **Verified Mutations**: A booking or extension counts only after the positive event ID and exact persisted room/date/time survive a reload
- **Manual Reconfirmation Boundary**: Student bookings remain provisional; the user reconfirms them on RWCMD Wi-Fi when Asimut enables the action, and this runtime never attempts remote reconfirmation
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
- **Location Scope**: All current Music Practice Rooms - AHC plus exact promoted rooms discovered from All Locations
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
├── assistant_ui.py       # Thread-safe ChatGPT-style Tk assistant surface
├── assistant_runtime.py  # Conversation state and typed-tool orchestration
├── codex_chat.py         # Long-lived Codex App Server stdio bridge
├── assistant_context.py  # Sanitized validated Booker context
├── assistant_tools.py    # Allow-listed read and mutation operations
├── assistant_plans.py    # Exact dated future-intention persistence
├── app_settings.py       # Locked, atomic shared JSON persistence
├── event_identity.py     # Collision-safe shared agenda-event identity
├── practice_plan.py      # Strict daily-target schema and budget helpers
├── booking_strategy.py   # Strict daily-planning preference schema
├── daily_planner.py      # Pure fresh-grid opportunity ranking
├── booking_plan.py       # Locked, display-only booking-plan snapshots
├── agenda_snapshot.py    # Validated display-only existing booking/event snapshots
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
│   ├── booking_plan.json # Display-only daily plan cache (gitignored)
│   ├── agenda_snapshot.json # Display-only complete agenda cache (gitignored)
│   ├── assistant_state.json # Local bounded chat transcript/thread pointer (gitignored)
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
└── AGENTS.md
```

## Usage

### GUI (Recommended)
```bash
# Double-click AsimutBooker.bat or run:
pythonw gui.py
```

The GUI provides:
- A first-tab conversational assistant for questions, status, preferences, future practice intentions, bounded booking, and exact reservation cancellation
- Streaming answers, concise reasoning summaries, live tool progress, Stop/New chat controls, and a locally restored bounded transcript
- A six-card health dashboard with independently sourced status and detail
- Run booker manually (visible or headless)
- View booking history
- Setup/remove scheduled tasks
- Default and per-date desired practice hours
- Live-window booking/off controls and strict preferred-time controls
- A room editor for ordering, exclusions, live metadata requirements, minimum block length, and fragmented-session policy
- A daily-strategy editor for the preferred peak window, desired session length, lookahead, confidence threshold, fallback lead, and room/time ordering
- A booking-plan dashboard card plus month and timeline calendar overlays that distinguish confirmed time from potential future extensions
- Existing reservations and other Asimut agenda events in the month, day, and timeline calendar views

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

# Read-only fresh-grid planning; publishes the display-only booking plan
python book_week.py --headless --plan-only

# Bounded live verification (works when the selected minimum is 30 minutes)
python book_week.py --headless --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 30

# Isolated initial horizon-edge test: creates the selected minimum block only,
# retains a larger extension target, and never falls through to another mode
python book_week.py --headless --target-time HH:MM --horizon-only --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 120

# Isolated extension test: edits tracked horizon bookings only and never creates
# a new booking
python book_week.py --headless --extensions-only --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 30
```

`--horizon-only` and `--extensions-only` require a room/date scope and an
action cap. The initial horizon-edge Save uses the selected minimum block. The
action ceiling must be at least that minimum and must be larger when later
extension is intended; otherwise the run safely leaves no larger target.

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
   - Processes extensions immediately after the complete agenda scan and receipt reconciliation, before loading or navigating the room overview and before any new snipe
   - Loads pending extendable bookings
   - Sorts already-due extensions first, then edges due within three minutes, using room priority as the stable tie-break
   - Binds legacy records only from one exact complete-agenda reservation with a positive event ID, persisting that migration before editing
   - Selects the one current/legacy event card bound to that exact ID; a missing, duplicate, cancelled, or tuple-mismatched card stops without Save
   - Opens the exact editor up to three minutes before the next boundary, waits there, then fills and revalidates the new end time at the boundary
   - Requires the editor URL to remain the exact tracked positive event ID and the Save control to be visible and enabled before creating a receipt
   - Extends to the latest completed 15-minute boundary; a missed run catches up in one verified edit without rounding into a future boundary
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
- `plan_horizon_extension()`: Pure exact-boundary plan for due, imminent, future, and completed extensions
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

Pending extensions take priority over all new snipes. The extension phase runs
before room-grid discovery, so an action cap cannot be consumed by an unrelated
create. If an extension uses the exact boundary first, a just-opened snipe
remains eligible only inside the bounded three-minute grace. Verified isolated
actions return without fallible cleanup navigation.

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
- **Daily strategy**: Enable foresight, set a preferred peak window and desired block, require a configurable number of distinct better later rooms before waiting, choose a 0-24-hour lookahead and fallback lead, and select room-first/time-first and after-peak ordering
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
| `assistant_ui.py` | Responsive, thread-safe assistant transcript, progress cards, and composer |
| `assistant_runtime.py` | Persistent Codex conversation host and typed Booker-tool adapter |
| `codex_chat.py` | Exact-model Codex App Server protocol bridge and event stream |
| `assistant_context.py` | Strict sanitized context from app, settings, agenda, plan, rooms, health, receipts, and history |
| `assistant_tools.py` | Allow-listed question, refresh, preference, plan, booking, and cancellation tools |
| `assistant_plans.py` | Complete-range dated practice-target validation and persistence |
| `phone_api.py` | Strictly reduced agenda, plan, preference, and health snapshot for the phone UI |
| `phone_configure.py` | Atomic strict runtime configuration for the private phone origin and allow-listed Tailnet login |
| `phone_server.py` | Loopback-only authenticated PWA/API/SSE host around the existing AssistantRuntime |
| `phone/` | Installable React phone UI, local static build, manifest, icons, and service worker |
| `app_settings.py` | Strict, locked, atomic settings/history storage primitives |
| `event_identity.py` | Deterministic v2 ignored-event identity and legacy-key resolution |
| `practice_plan.py` | Daily-target validation and booking-budget helpers |
| `booking_strategy.py` | Strict GUI/runtime schema for customizable daily foresight |
| `daily_planner.py` | Pure ranking, wait/book decisions, and quota-aware day-plan selection |
| `booking_plan.py` | Locked, expiring, display-only daily-plan snapshots and preference fingerprints |
| `agenda_snapshot.py` | Strict, locked, display-only snapshots of complete validated agenda scans |
| `runtime_guard.py` | Single-instance lock and exact URL/identity confirmation |
| `mutation_receipts.py` | Durable pre-Save receipts and reconciliation state |
| `room_catalog.py` | Strict live category/group/room metadata and horizon discovery |
| `room_preferences.py` | Shared GUI/runtime preference validation and room matching |
| `live_room_policy.py` | Fresh-catalog policy, eligible room order, and dynamic date window |
| `health_status.py` | Tk-free readers for history, session, auth, mutation, and wake evidence |
| `run_booker.bat` | Wrapper script called by Task Scheduler |
| `setup_scheduled_tasks.ps1` | Installs and verifies the one recurring task |
| `setup_phone_app.ps1` | Builds, configures, starts, and privately publishes the phone companion |
| `verify_phone_deployment.ps1` | Proves the exact startup task, loopback process, PWA assets, API rejection, and tailnet-only route |
| `tools/generate_phone_icons.py` | Deterministically renders the three-bar phone and maskable icons |
| `tools/verify_phone_build.py` | Validates synchronized offline shell assets, manifest icons, API cache exclusion, and no source maps |
| `config/config.example.yaml` | Exact supported advanced configuration schema |
| `config/config.yaml` | Optional local advanced-rule override; room policy is site-owned |
| `data/browser_state/state.json` | Saved browser session (cookies, localStorage) |
| `data/auth_recovery_state.json` | Gitignored no-secret credential latch / transient retry cooldown |
| `data/booking_history.json` | JSON log of all booking runs |
| `data/settings.json` | GUI settings, room preferences, practice plan, ignored events, and extendable bookings |
| `data/room_catalog.json` | Gitignored display-only cache of the last complete live room observation |
| `data/booking_plan.json` | Gitignored expiring display-only plan; never booking authority |
| `data/agenda_snapshot.json` | Gitignored display-only existing bookings/classes from the latest complete agenda scan |
| `data/assistant_state.json` | Gitignored bounded local assistant transcript and Codex thread pointer |
| `data/physical_wake_test.json` | Gitignored optional evidence from a dedicated physical wake test |
| `data/mutation_receipts.json` | Gitignored crash-recovery journal for remote mutations |

## Reliability Contract

- Authentication, existing configuration, settings, mutation journal, full
  agenda reach, calendar date, form identity, requested time values, and
  persisted Save identity all fail closed.
- Every authenticated booking, `--check-only`, and `--plan-only` run builds a new room policy
  from Asimut before agenda or mutation work. Discovery reads the current
  booking category, AHC group, complete All Locations group, merged metadata,
  event defaults, and one no-Save check response for each selected room. Only
  exact promoted room names are added; unrelated All Locations rooms are not
  probed. An incomplete or inconsistent observation stops the run;
  `data/room_catalog.json` is never booking authority.
- Discovery never calls `event/type=save`. Participant arrays required by the
  in-memory check template are neither logged nor cached.
- Room names are exact site names, including multiword names. The live global
  cutoff determines the complete date set, and per-room warning cutoffs
  determine arbitrary positive 15-minute horizons. Request-minute rollover is
  normalized without accepting a changed duration or a mixed snapshot.
- Grid navigation uses an exact run-scoped
  `/overview?locationGroupId=0&locationIds=...` URL built from the freshly
  validated selected location IDs. Every expected room row must appear exactly
  once before availability, coordinates, or a Save path may use the grid;
  Asimut may render those rows in a different order from the URL.
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
- Daily foresight ranks only opportunities derived from the current live grid
  and current site-owned room horizons, booking cutoff, room metadata, and
  booking limits. Holds are date-specific and reserve daily, weekly, and peak
  capacity, including only the feasible remainder of pending extensions.
- `data/booking_plan.json` is an expiring explanation artifact. Its complete
  strategy fingerprint and fresh-policy timestamp must match current settings
  before the GUI renders it; neither the GUI nor the mutation runtime treats it
  as booking authority.
- `data/agenda_snapshot.json` is refreshed only after the exact complete-agenda
  extraction succeeds. It is display evidence for the GUI, never mutation or
  booking authority; malformed, unrelated-window, and stale snapshots are
  labelled or hidden while the previous valid artifact survives failed scans.
- Ignored events use a v2 identity covering date, start, end, title, room, and
  reservation type. A legacy time-only key applies only when it identifies one
  distinct scanned event; ambiguous matches ignore nothing. Ignored reservations
  still consume daily/weekly/peak budgets and enforce their same-room gap.
- Scheduled launches require the repository `.venv` and prove required imports
  before booking; there is no global-Python fallback. Schedule status, setup,
  and elevated idempotent removal run outside the Tk UI thread.
- `--check-only` performs a read-only authenticated policy refresh and agenda
  scan, then traverses every date currently exposed by Asimut. `--only-date`, `--only-room`, `--max-actions`, and
  `--max-action-minutes` provide bounded live verification without weakening the
  confirmation rules. `--horizon-only` and `--extensions-only` additionally
  isolate the mutation type and return after the scoped phase, so a missing
  candidate cannot fall through to an unrelated booking.
- `--plan-only` performs the same authenticated fresh-policy and complete-agenda
  validation, traverses the live date window, and replaces only the display
  snapshot. It never creates, edits, or deletes an Asimut event and cannot be
  combined with scheduling, target timing, or mutation limits.
- The in-app assistant uses a long-lived hidden `codex app-server --stdio`
  process and fails closed unless the server confirms exact model
  `gpt-5.6-terra`, medium reasoning, no approvals, and a read-only/no-network
  sandbox. Shell, filesystem, browser, web-search, and subagent tools are not
  exposed; every state change must pass one typed Booker operation tied to an
  exact contiguous quote from the active user message.
- Assistant context explicitly allow-lists sanitized settings, agenda, plan,
  room, health, receipt, and history fields. Credentials, cookies, browser
  storage, OTPs, bridge tokens, participant arrays, and arbitrary files are not
  read. Schedule and site text is untrusted data, never instructions.
- Future intentions become complete exact dated targets for every day in a
  1-92-day range, which the ordinary scheduled Booker then pursues subject to
  live availability and all existing quotas and safeguards. Materially vague
  quantities require one conversational clarification. The assistant's direct
  Booker invocation is limited to one plan-selected action; it cannot promise
  an exact start time unless preferences deliberately constrain the planner.

## 2026-08-30 GUI Restart-Loop and Single-Instance Milestone

- The shared development watcher now excludes Asimut's `data/` and `logs/`
  runtime trees, so atomic settings, history, plan, health, and scheduler writes
  do not masquerade as source edits and relaunch the control panel. It still
  fingerprints Python and supported config sources, prunes generated dependency
  trees, and relaunches for genuine source/config changes.
- The shared watcher owns a per-project/executable/argument Windows mutex, so
  opening the Asimut shortcut again cannot create a second supervisor. The GUI
  independently owns `data/gui-runtime.lock`, which protects direct and legacy
  launch paths from creating a second control-panel process.
- Live verification changed the settings-file timestamp and attempted a second
  shortcut launch without changing the two-process Python GUI tree. The real
  16:58 scheduled run then completed its runtime writes while the GUI retained
  the same process IDs and remained responsive. The complete offline suite
  passes 451 tests, including GUI lock contention and failure-release coverage.
- The observed 16:58 run successfully extended B1.09 and created the verified
  B0.29 16:30-17:00 horizon booking for 4 September. It later failed closed
  during ordinary post-snipe refresh because the site returned today's grid
  while Day 7 was expected; that booking-flow follow-up is separate from the
  resolved GUI relaunch loop.

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
  Extensions require the exact positive `/event?eventId=N` editor route to
  match the tracked event ID both after opening and immediately before Save;
  only the separate `/arrangement?eventId=N` route counts as persisted proof.
  Just-opened creates receive only a three-minute post-boundary grace.
- `--horizon-only` and `--extensions-only` are scoped, action-capped live-test
  modes. They cannot fall through to ordinary booking and return after the
  verified scoped phase without risky cleanup navigation. Use an action ceiling
  at least as large as the selected minimum, and a larger ceiling when later
  extension tracking is intended.
- Deterministic boundary coverage uses the default minimum at +29:59, exact
  +30, and every +15-minute extension
  through +120, missed-run catch-up, priority/order, action short-circuiting,
  form drift, exact editor and persisted-event routes, disabled Save, editor
  disappearance, and isolated runtime modes. Never log out or create unrelated
  bookings to manufacture an extension candidate.
- The initial historical `--check-only` pass left zero active extensions and
  zero pending receipts. A later genuine tracked candidate and its bounded live
  verification are recorded in the exact-editor milestone below.

## 2026-08-30 Daily Foresight and Booking-Plan Milestone

- `booking_strategy.py` owns a strict GUI/runtime schema for daily foresight.
  The default prefers one continuous 120-minute peak session inside 12:00-16:00
  and can defer an inferior early edge only when at least two distinct better
  live rooms support the later choice. Users can change the peak window,
  desired duration, 0-24-hour lookahead, evidence threshold, fallback lead,
  after-peak ordering, room/time priority, and chronological/furthest-first
  date order.
- Decisions are made independently per target date because peak allowance is
  daily. The best actionable GUI-ranked room still takes the fast path to edge
  preparation; waiting on one date never suppresses an unrelated edge on
  another. Daily targets, rolling quota, peak allowance, non-overlap,
  fragmentation, and the fresh same-room gap are enforced across the complete
  selected day plan, not only one candidate.
- Pending horizon extensions reserve only their still-feasible remainder after
  strict-time, conflict, same-room, daily-target, peak, and rolling-week caps.
  New-session plans share one aggregate weekly allowance across dates. If
  foresight is disabled, the display plan mirrors established gap-start booking
  order instead of advertising a smart decision the runtime will not take.
- `booking_plan.py` writes a locked, atomic, 20-minute display snapshot with a
  complete settings/config fingerprint. The Overview card explains the next
  ready or waiting action and daily progress. Month cells and the seven-day
  time-axis view render selected and alternative sessions as hatched potential
  blocks; confirmed extension progress is solid and only its unconfirmed
  remainder remains hatched. Stale or replaced plans disappear immediately and
  no plan block is clickable as a booking action.
- Live discovery remains the sole source of room horizons and the date cutoff.
  A real read-only refresh exposed two ordinary site-boundary cases: an HTTP
  Date at a minute rollover and a class ending at 10:20. Date evidence now
  considers only its existing one-second uncertainty before exact horizon
  validation selects one minute; free gaps round inward to the 15-minute booking
  grid so occupied time is never expanded into.
- The final live `--headless --plan-only` proof reused the saved session,
  discovered 29 eligible rooms at the site's current 3-, 5-, and 7-day
  horizons, derived the eight-date window through 6 September 2026, scanned the
  complete five-event agenda, traversed every exposed day, and published a
  current eight-day plan. It identified a ready 90-minute B0.14 session on
  1 September and correctly retained 60/90 minutes of tracked B1.09 extension
  progress on 2 September. Pending mutation receipts remained zero and the
  read-only run made no Asimut mutation.
- The complete offline suite passes 448 tests. A real Tk Month -> Plan -> Month
  smoke test also proves view-specific row/column geometry is reset instead of
  leaking a blank eighth column or oversized timeline rows.

## 2026-08-30 Readable Control Panel Redesign Milestone

- The Tk control panel now uses one restrained, high-contrast visual system
  across the main window and dialogs: a 12-point base font, Segoe UI Variable
  when available, larger display headings, quiet grouped surfaces, 34-pixel
  table rows, touch-friendly controls, and one blue primary-action treatment.
- The Overview is organized around readiness, the next likely booking, and the
  primary background run action. Independent health evidence remains truthful:
  a pure summary reports errors and warnings before readiness, compact indicators
  keep all six sources visible, and a focused details dialog retains the complete
  evidence and the physical-wake proof boundary.
- Technical output moved to a dedicated Activity tab with readable dark-console
  presentation and direct history/log controls. Preferences are grouped on a
  scrollable page with simplified language; room, practice-plan, strategy, and
  history dialogs inherit the larger typography and spacing.
- Booking, authentication, scheduling, and mutation behavior were not changed.
  The complete offline suite passes 469 tests, including new deterministic
  readiness-summary coverage. A real Tk layout smoke check at the host's
  constrained 1320x784 window kept every idle Overview section within the
  visible tab area without reducing the configured type size.

## 2026-08-30 Shortcut Relaunch Lifecycle Milestone

- The canonical Dev Apps shortcut still launches Asimut through the shared
  hidden Python watcher, but this app now opts into exit-on-child behavior. When
  the control-panel process closes or fails during startup, its hidden watcher
  exits and releases the per-app mutex instead of silently blocking every later
  shortcut click.
- Live verification used the actual `Asimut Booker.lnk` and its shared Python
  environment: it opened one visible responsive window, closing the GUI removed
  the complete watcher chain, the same shortcut opened a fresh responsive
  window, and another click while it was running preserved the exact two-process
  Python GUI tree. The reopened control panel was left running.
- Both shared PowerShell launch scripts parse without errors, a zero-work child
  proved exit-on-child in under one second, and the focused GUI/launcher suite
  passes 26 tests. Booking, scheduling, authentication, and Asimut browser state
  were not invoked by this repair.

## 2026-08-30 Existing Agenda Calendar Milestone

- Every successful complete agenda scan now publishes a strict, locked, atomic
  `data/agenda_snapshot.json` containing the exact current live-window dates,
  observation time, reservations, other events, rooms, times, titles, and
  positive event IDs where supplied. This artifact is display-only and never
  booking or mutation authority; failed/partial scans preserve the prior file.
- The Calendar and event-preference refreshes now reuse the booker's one
  authenticated, identity-checked, complete-agenda scanner instead of separate
  DOM scrapers. The GUI loads the snapshot immediately, labels stale or
  changed-window evidence, filters the former settings cache to current dates
  during migration, and refreshes in the background without blanking known
  events on failure.
- Month/day cells and the seven-day timeline distinguish green reservations
  from amber College events. Reservations show the exact booked room instead of
  the generic `Reservation` title; other events retain their actual titles.
  Booking-plan confirmation progress and ignored-event preferences read the same
  snapshot, so every display agrees on existing agenda evidence.
- The normal 18:13 scheduled pass published a current eight-day snapshot from a
  complete eight-event agenda: seven reservations and one other College event
  across four occupied dates. A real Tk timeline smoke render showed both an
  exact room booking and a named non-booking event. The complete offline suite
  passes 481 tests, and the shortcut-launched control panel was left visible and
  responsive. The scheduled run continued under its existing autonomous policy;
  snapshot publication itself performed no Asimut mutation.

## 2026-08-30 Exact Extension Editor Route Milestone

- Live read-only inspection proved that Asimut opens an existing reservation at
  the exact `/event?eventId=N` editor route, while its durable read-only identity
  remains `/arrangement?eventId=N`. The runtime now validates the trusted host,
  exact path, sole canonical positive ID query, and the same tracked ID at both
  editor checkpoints without weakening post-Save persistence proof.
- A bounded `--extensions-only` run used the genuine tracked event 3580122 and
  extended B1.09 on 2 September 2026 from 14:00-14:30 to 14:00-15:00. The run
  was limited to that date, room, one action, and 30 minutes, then reload-verified
  the exact persisted event before counting success; no create path ran.
- A separate read-only `--plan-only` pass found the exact 14:00-15:00 agenda
  reservation, retained its 15:30 target as 60/90 minutes confirmed, refreshed
  all live 3-, 5-, and 7-day room horizons and the site-owned cutoff, and left
  zero pending mutation receipts. The complete offline suite passes 448 tests.

## 2026-08-30 Promoted All Locations Rooms Milestone

- The live catalog now combines all current AHC group-10 rooms with exact
  `Weston Gallery` and `Corus Recital Room` identities freshly discovered from
  complete All Locations group 2. Other All Locations rooms are not added or
  probed. Cross-source ID/name disagreement, duplicate identities, malformed
  metadata, or incomplete checks fail closed; a promoted room absent from a
  complete current group response is omitted only for that run and regains its
  saved rank when it reappears.
- Default priority is Weston Gallery, Corus Recital Room, then B0.29 and the
  previous room order. Explicit saved user ordering, exclusions, metadata
  requirements, and the preferred-time/day planner remain authoritative; room
  rank decides otherwise comparable opportunities rather than overriding a
  materially better configured practice time.
- Every merged room receives current metadata and its own no-Save horizon check.
  The runtime constructs one exact group-0 selected-location overview from the
  fresh location IDs and requires every expected row exactly once before using
  availability or click coordinates. This avoids loading all 130 current All
  Locations rows while keeping group membership, IDs, horizons, opening hours,
  and occupancy site-owned.
- A production `--headless --plan-only` run exited zero with 31 live rooms and
  traversed every date in the current eight-date window. It proved Weston
  Gallery as location 96 / B0.08 and Corus Recital Room as location 93 / B0.03;
  both independently reported a current 10,080-minute horizon, while B0.29
  independently reported 7,200 minutes. The effective order was Weston, Corus,
  B0.29; the full agenda contained eight events, and pending receipts remained
  zero. No booking or edit was attempted.
- Live read-only evidence confirms that Student bookings remain provisional and
  Asimut currently opens reconfirmation 300 minutes before the reservation.
  Reconfirmation requires RWCMD Wi-Fi and is intentionally user-owned: the
  runtime never calls the reconfirm endpoint. Successful-booking notifications
  and the plan UI state this manual step explicitly. Until the user confirms,
  the reservation still consumes its normal conflict, peak, daily-target, and
  quota capacity; if Asimut cancels it, the next complete agenda scan removes
  that coverage and replans from fresh availability. The complete offline suite
  passes 471 tests.

## 2026-08-31 Exact Reservation Cancellation Milestone

- The runtime now has one isolated exact-cancellation CLI mode requiring a
  positive agenda event ID plus the unchanged room, date, start, and end tuple.
  It cannot be combined with booking, extension, check, plan, schedule, target,
  or maintenance modes, so a missing or rejected target never falls through to
  another action.
- Cancellation re-authenticates, refreshes live policy, requires a complete
  agenda, reload-proves the exact persisted event, and binds the visible
  Reservation card and explicit cancellation controls to that ID and tuple. A
  durable cancellation receipt is written before the first destructive click;
  success requires a second complete agenda in which both the ID and exact
  tuple are absent.
- An unchanged immediate agenda remains pending for later reconciliation rather
  than being reported as success. Reconciliation either proves the exact event
  still exists and closes the action as not applied, or proves absence, removes
  only extension state bound to that event ID, and finalizes the receipt. DOM,
  identity, tuple, control, receipt, or post-action ambiguity fails closed.
- The focused receipt/cancellation/save/runtime/overview regression slice passes
  116 tests. A later authenticated read-only inspection opened one exact
  reservation's options menu without selecting its cancellation action and
  established the current Material control shape; no live cancellation has
  been performed as validation.

## 2026-08-31 Terra In-App Assistant Milestone

- The control panel now opens on an Assistant tab with a centered responsive
  conversation, starter prompts, Enter/Shift+Enter behavior, persistent local
  history, streaming responses, collapsible concise reasoning/activity cards,
  live tool progress, and Stop/New chat controls. Raw model reasoning is never
  rendered, and GUI shutdown stops the owned App Server process.
- A long-lived Codex App Server bridge is pinned to `gpt-5.6-terra` at medium
  reasoning with no fallback. It validates that exact configuration on new and
  resumed threads, disables shell, filesystem, web, network, approval, and
  multi-agent routes, rejects stale cross-turn tool calls, and keeps delayed
  workers cancelled after Stop.
- The assistant can explain the app and read sanitized current preferences,
  complete agenda, booking plan, live room cache, health, receipts, and recent
  history. Its only actions are typed read-only refreshes, exact preference
  patches, complete dated future practice plans, one plan-selected Booker
  action, and positive-ID exact-tuple single or bounded bulk reservation
  cancellation. Every mutation requires a verbatim authorization quote from
  the current user message; negated, quoted, descriptive, informational, or
  ambiguous requests do not authorize a change.
- High-level plans are persisted as both an explainable intention and ordinary
  per-date practice-plan targets, so scheduled automation pursues them when the
  dates enter Asimut's live window. Each 1-92-day range must contain every date
  exactly once with a numeric 0 or 0.5-12-hour target; overlapping revisions
  must replace the complete prior range so they cannot leave orphan targets.
- The complete offline regression suite passes 573 tests, static compilation
  and diff checks pass, and a withdrawn real-Tk render smoke passes. The actual
  Terra-medium bridge passed 6/6 synthetic, production-effect-blocked scenarios:
  schedule Q&A, exact cancellation, ambiguous cancellation, explicit dated week
  planning, vague weekend clarification, and prompt-injection/reconfirmation
  refusal. No live booking, cancellation, settings change, or Asimut request was
  made by those evaluations; authenticated cancellation markup remains limited
  to the separately documented offline contract.
- New chat and turn start share one serialized transition, so an immediate Send
  cannot target the previous thread; Stop also waits for an in-flight turn ID.
  Shutdown escalates from bounded graceful cleanup to an owned-process kill,
  preventing a stalled App Server child from surviving GUI exit. Obvious
  password, credential, passcode, OTP, and verification-code pastes are rejected
  before they reach Codex or the visible/local transcript.
- Assistant mutation authorization rejects leading negation, informational and
  example text, quoted instructions, and terminal withdrawals such as “actually,
  don't” or “never mind.” A duration-capped direct Booker run additionally
  requires both an exact date and room. `tzdata` is an explicit dependency so
  Europe/London resolution also works on clean Windows Python installations.

## 2026-08-31 Assistant Bulk Cancellation Repair Milestone

- The failed assistant request to cancel five previously listed reservations
  stopped before its first destructive click and wrote no cancellation receipt.
  Read-only authenticated inspection proved the current event-options action is
  a `mat-list-item` whose Material icon is exactly `cancel` and whose visible
  label is exactly `Cancel`; the prior resolver rejected it because it required
  `Cancel booking` or `Cancel reservation` text.
- Cancellation control resolution is now scoped to exactly one visible CDK
  event-options overlay. Plain `Cancel` is accepted only for that exact
  `mat-list-item` plus single `cancel` icon structure; iconless, wrong-icon,
  multi-icon, button, disabled, outside-overlay, and ambiguous controls remain
  fail closed. Explicit booking, reservation, and event cancellation labels
  remain supported.
- The assistant now exposes one typed `cancel_reservations` action for at most
  12 exact reservations. Every target must be re-resolved through one or more
  complete `find_reservations` match sets in the active user turn, carry its
  unchanged positive event ID, tuple, and fresh token, and belong to the exact
  union selected for the batch. Partial broad results, additions, duplicates,
  stale or reused tokens, new-chat carryover, and a second concurrent
  cancellation operation are rejected before a Booker command starts.
- Bulk execution is sequential and fail-stop. Each verified cancellation must
  publish a newer complete agenda in which all remaining IDs and tuples are
  re-resolved before the next command. Exit 7 is reported as safely not applied
  before receipt, exit 5 or another command failure is uncertain/pending, and
  every untouched target is explicitly not attempted; no non-success advances
  to a later reservation.
- The complete offline suite passes 595 tests. The real Terra-medium synthetic
  harness passes 7/7 guarded scenarios, including a same-thread two-turn list
  followed by `Cancel all of those bookings.` It selected exactly the five
  referenced IDs across four dates, excluded two unrelated reservations, used
  the current message for authorization, and reached no production effect.
  No live booking was cancelled during implementation or verification.

## 2026-08-31 Secure Phone Companion Foundation Milestone

- `phone/` is an assistant-first installable PWA with three compact destinations:
  Assistant, Schedule, and Status. The first viewport keeps Booker health and
  the next confirmed/potential sessions visible above a ChatGPT-style transcript,
  concise reasoning summaries, typed-tool progress, Stop/New chat controls, and
  a natural-language composer. Confirmed agenda events and potential plan blocks
  remain visually and semantically distinct; cancellation shortcuts only prefill
  an exact assistant request and never bypass current-message authorization.
- The production companion is designed for one dedicated tailnet-only HTTPS
  origin backed by a loopback-only Python server. The browser receives a reduced
  display snapshot with no event IDs, cancellation match tokens, receipt bodies,
  command output, file paths, credentials, cookies, browser state, or raw context.
  It has no direct booking/cancellation endpoint; messages reuse the existing
  `AssistantRuntime`, exact `gpt-5.6-terra` medium configuration, and typed Booker
  tool surface.
- Phone configuration version 2 pins one existing absolute `codex.exe` path and
  passes it directly into the App Server controller. The logon task therefore
  never depends on an interactive shell's `PATH`; setup discovers the current
  Codex installation, and both configuration loading and deployment verification
  reject a missing or differently named executable.
- Phone API access requires the exact public Host and Origin, one allow-listed
  Tailscale login header, a Secure/HttpOnly/SameSite=Strict server session, and a
  synchronizer CSRF token. API and transcript responses are no-store; CSP denies
  external connections, framing, objects, and cross-origin access. Static serving
  rejects dotfiles, traversal, source maps, and all repository/data paths.
- Phone message IDs are durably reserved before a turn starts, without storing
  prompt text, and remains unresolved through the full controller turn, so a
  lost response, interrupted mutation, or double tap cannot repeat a mutation. The UI
  retains that same ID across ambiguous delivery and ignores late promises after
  SSE confirmation. A crash-window reservation survives browser and server
  restarts, blocks every new request, and remains a visible review gate until a
  CSRF-protected explicit acknowledgement; the uncertain request itself is never
  replayed. One active phone turn is allowed. Each server process has a distinct
  stream generation, so a stale high cursor resets after restart while same-process
  resume remains max-only and duplicate-free. SSE also has bounded authorization,
  overflow recovery, and a three-attempt backoff rather than a retry storm. The
  public event filtering omits internal IDs, arguments, quotes, tokens, raw tool
  results, provider errors, and hidden reasoning.
- All assistant mutation tools now also hold one interprocess lock. This serializes
  phone and desktop preference, plan, booking, and cancellation changes while
  leaving read-only questions available. Existing per-turn cancellation, exact
  identity, receipt, and booker-runtime locks remain authoritative.
- The PWA build injects every hashed first-render JavaScript/CSS asset into its
  source-versioned offline shell. `/api/`, health, schedules, transcripts, prompts,
  and mutation results are never service-worker cached or replayed offline. Stale
  plan candidates are suppressed, unavailable context is visible, and saved future
  intentions are shown as exact dated ranges rather than misleading defaults.
  Cache writes are awaited inside the fetch lifecycle so failures cannot become
  unhandled background promises.
- The complete offline regression suite passes 638 tests; 43 focused phone/API/
  PWA/lock tests and 6 browser-state decision tests pass. The
  production static build passes TypeScript, accessibility/correctness linting,
  synchronized-cache verification, and source-map rejection.
- Commit `32f34ea4396d` is installed as the limited interactive-user
  `AsimutBooker_Phone` logon task. It owns exactly one `127.0.0.1:8794` listener
  and is privately exposed at `https://lox-pc.tail89d19b.ts.net:10443/` through
  one Tailscale Serve route; the verifier confirms exact task arguments, process
  identity, build version, security headers, install assets, API denial without
  identity, and absence of a public Funnel route while preserving unrelated
  Serve routes.
- `phone/.openai/hosting.json` is bound to Sites project
  `appgprj_6a95803dab688191a40ab5e49eaedb21`. Cloud-hosted builds intentionally
  render only the `RemoteGate` launcher into the private Tailscale origin; they
  never receive a tunnel binding or direct access to Booker state, assistant
  messages, schedules, or mutations. Clean pnpm installs explicitly allow build
  scripts only for the pinned esbuild, Sharp, and workerd dependencies required
  by the Vite/Vinext/Cloudflare toolchain.
- Sites version 1 was saved from pushed standalone phone-source commit
  `aa0b880d9236c6ecf396e81883efd8f1f069228d` and privately deployed at
  `https://asimut-booker-phone-lox.loxtyrrell.chatgpt.site`. Deployment access
  was verified as custom owner-only: the current owner is the sole allowed
  account, with no external user, workspace group, tenant group, or HTTP tunnel
  binding. The production page is therefore a private launcher, not an alternate
  Booker API origin.
- A fresh live phone session confirmed `GPT-5.6 Terra · medium`, ready Booker
  context, zero unresolved request reservations, and 3/3 harmless assistant turns
  through the actual HTTPS/SSE boundary. It answered tomorrow's reservations,
  explained the next plan and automatic-booker behavior, and summarized saved
  practice targets/future intentions while emitting reasoning-summary/activity
  and `get_booker_context` progress plus correlated terminal events. No mutation
  tool, booking, cancellation, settings write, or live Booker action ran. A
  physical iPhone Add-to-Home-Screen launch remains separate device evidence.

## 2026-08-31 Cancellation Reconciliation Repair Milestone

- Agenda verification now captures one atomic, day-scoped DOM snapshot for both
  structural completeness and semantic events. Dates come from each card's one
  direct day header, and one shared bounded classifier handles outer-card and
  descendant cancellation markers. This removes the former race between two DOM
  walks that could disagree immediately after an Asimut cancellation rerender.
- Active semantic cards must expose one positive event ID, one canonical time,
  one title, and, for reservations, one exact live-catalog location. Missing or
  duplicate day headers, missing identities, ambiguous fields, and conflicting
  copies of one event ID fail closed. Exact lazy-rendered card clones remain
  supported and are deduplicated only after their complete fields agree.
- Post-click verification may retry the read-only complete-agenda scan once, but
  the cancellation control remains outside that loop and is invoked exactly
  once. Both immediate proof and restart reconciliation compare the receipt with
  every active validated event, so a same-ID card whose title changes cannot be
  mistaken for a successful absence. Active IDs under valid extra dates remain
  mutation-proof evidence, while active cards under unmappable headers stop the
  scan rather than disappearing from cancellation reconciliation.
- Regression coverage includes descendant-only cancellation signals, inherited
  red theme styling, split and duplicate headers, missing IDs, exact and
  conflicting virtualized clones, complete-event cancellation proof, one-click
  retry behavior, and pending-receipt preservation. The complete offline suite
  passes 648 tests.
- Live receipt reconciliation proved the interrupted first cancellation was
  applied remotely and preserved the untouched later batch target; no retrying
  cancellation click was issued. A fresh `--headless --check-only` run then
  passed authentication, the complete agenda, all 8 live-window dates, 31 room
  rows per date, and 220 visible gaps with no pending receipts.
- The ordinary scheduled run passed the repaired agenda/reconciliation gate but
  then exposed a separate pre-existing overview refresh bug: Asimut reset the
  SPA-only selected day from day 7 to today while the runtime still expected day
  7. This was not another cancellation or receipt failure.

## 2026-08-31 Overview Refresh Date Restoration Milestone

- A normal-booking refresh now treats today's complete room grid as the only
  trusted state after reloading Asimut's canonical overview URL. If the run was
  viewing a later live-window day, it restores that exact offset through the
  existing verified calendar navigator and proves the requested complete grid
  again before availability can be read or any mutation can proceed.
- The target-time path passes its frozen run date explicitly, preventing a
  midnight rollover from changing offset interpretation. Past-date restoration
  is rejected before reload; every navigation step retains the existing exact
  date, complete-room-inventory, and authenticated-session checks.
- Regression coverage proves exact reload/today/restoration/final-proof ordering
  for day 7, the no-navigation path for today, and fail-closed past-date handling.
  The complete offline suite passes 650 tests.
- A live 15:58 scheduled run crossed the former 16:00 failure point: after the
  canonical reload it re-walked seven verified dates, proved the day-7 complete
  grid, and entered normal day-7 availability scanning. No cancellation command
  was repeated during this verification.

## Maintenance Notes

When modifying this codebase:
- **Always update `AGENTS.md`** when adding features, changing behavior, or modifying architecture
- Keep the "Key Functions" sections current with new/changed functions
- Document any new booking rules or constraints
- Update the Files Overview table if adding new files
