# AsimutBooker

Automated booking system for Royal Welsh College of Music and Drama (RWCMD) practice rooms via Asimut.

> **IMPORTANT FOR AI AGENTS**: When making changes to this codebase, always update this `CLAUDE.md` file to reflect new features, changed behavior, or updated architecture. Keep documentation in sync with code.

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs through Windows Task Scheduler, requests wake-from-sleep on AC or battery, and books rooms as they become available. Actual wake behavior depends on Windows, firmware, and hardware support.

### Key Features

- **Autonomous Login**: Reuses persistent browser state first, then recovers an expired Microsoft 365 session with a Windows Credential Manager password and the local RWCMD SMS bridge—without an AI model
- **Priority Room Booking**: Targets specific preferred rooms before falling back to alternatives
- **Scheduled Execution**: One non-overlapping task runs every 15 minutes (07:13-21:58) with AC/DC wake-timer requests and missed-start recovery
- **RWCMD Booking Rules**: Respects rolling quota (28 hours/week), peak hours (2hr/day Mon-Fri 9am-4pm), and 60-min same-room gap
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts; extracts room names for same-room gap enforcement; distinguishes "Reservation" events from classes for accurate quota tracking
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring, manual control, preferences, and automatic-schedule repair
- **Practice Plan**: Set a default daily target from 0.5-12 hours, override individual dates, or turn dates off in one eight-day editor
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
│   ├── settings.json     # GUI settings, day targets, and extension state
│   └── mutation_receipts.json  # Runtime reconciliation journal (gitignored)
├── logs/                 # Booking logs
│   └── scheduler.log
├── src/                  # Alternative module-based implementation
│   ├── __init__.py
│   ├── main.py
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
- Status indicators (login state, scheduled tasks)
- Run booker manually (visible or headless)
- View booking history
- Setup/remove scheduled tasks
- Default and per-date desired practice hours
- Eight-day booking/off controls and strict preferred-time controls

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

# Read-only login, agenda, navigation, and eight-day room-grid health check
python book_week.py --headless --check-only

# Bounded live verification (one verified 30-minute mutation at most)
python book_week.py --headless --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 30

# Isolated initial horizon-edge test: creates 30min only, but retains a 2hr
# extension target. Requires an imminent HH:MM boundary and never falls through
# to extensions or ordinary booking.
python book_week.py --headless --target-time HH:MM --horizon-only --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 120

# Isolated extension test: edits tracked horizon bookings only and never creates
# a new booking. At a normal boundary, only the newly unlocked 15min is added.
python book_week.py --headless --extensions-only --only-date YYYY-MM-DD --only-room B0.29 --max-actions 1 --max-action-minutes 30
```

`--horizon-only` and `--extensions-only` require a room/date scope and an
action cap. Do not use a 30-minute action ceiling for the initial horizon-edge
test when later extension is intended: the initial Save is still exactly 30
minutes, but a 30-minute ceiling deliberately leaves no larger target to track.

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
- **Booking Duration**: Minimum 30 min, maximum 2 hours per slot
- **Same-Room Gap**: 60-minute gap required between bookings in same room
- **Room Horizons**: Different rooms have different advance booking windows (3, 5, or 7 days)
- **Dynamic Allocation**: Bookings per day adjust based on enabled days to maximize quota usage
- **Smart Redistribution**: Hours are distributed evenly across enabled days - days with existing bookings get fewer new slots

## Horizon Edge Booking & Extension

Rooms become bookable exactly X days before the slot time (where X is the room's horizon: 3, 5, or 7 days). Due to the 30-minute minimum booking rule, this creates a staggered booking pattern:

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

1. **Initial Booking**: At the first possible moment (30 min after horizon), books the minimum 30-minute slot and saves the exact verified positive event ID/URL with its `extendable_bookings` record in settings.json

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

## Multi-Day Horizon Snipe

The booker scans **all** horizon days (7, 5, 3) for snipe candidates, not just the furthest day. This catches opportunities across different room horizons.

### How Multi-Day Snipe Works

1. **Pre-scan phase** (before target time):
   - Navigate to Day 7 → scan for 7-day horizon rooms becoming bookable
   - Navigate to Day 5 → scan for 5-day horizon rooms becoming bookable
   - Navigate to Day 3 → scan for 3-day horizon rooms becoming bookable
   - Collect imminent candidates and candidates that opened within the previous three minutes; this bounded grace preserves a just-opened slot when a priority extension used the exact boundary first

2. **Sort candidates**: Furthest day first, then by room priority within each day

3. **Sequential snipe**: Attempt each candidate in order
   - Navigate to candidate's day
   - Pre-fill form, wait for exact moment, click Save
   - Immediately before the receipt and Save, re-prove the unsaved-event URL, room/date/times, live inputs, warnings, and a fresh visible/enabled Save control
   - On success: record booking, continue to next
   - On failure: skip, try next candidate

4. **Resume normal booking**: Navigate back to furthest enabled day

### Extension Priority

Pending extensions take priority over all new snipes. The extension phase runs
before any room-grid discovery, so an action cap cannot be consumed by an
unrelated create. If an extension uses the exact boundary first, new snipe
candidates remain eligible only inside the bounded three-minute catch-up grace.
Verified isolated actions return without fallible cleanup navigation.

### Key Functions

- `navigate_to_day()`: Helper to navigate calendar forward/backward
- `find_all_snipe_candidates_multi_day()`: Scans all horizon days for snipe candidates
- `find_horizon_snipe_candidate()`: Original single-day scanner (still used internally)

## Configuration

Everyday preferences belong in the GUI and `data/settings.json`:

- **Practice plan**: Optional default hours per enabled day plus exact-date overrides
- **Booking days**: Dates can be enabled or disabled independently
- **Preferred time**: Presets or a custom range, with an optional strict-only mode
- **Date order**: Chronological or furthest-first

Advanced room/rule overrides are optional. Copy `config/config.example.yaml` to
`config/config.yaml` and edit only the documented `rules` and `rooms.priority`
fields. A missing file uses built-in RWCMD defaults; an existing malformed file,
unsupported field, missing PyYAML, duplicate room, or invalid horizon stops the
run before Playwright rather than silently changing policy.

**Room booking horizons** (configured in config.yaml):
- **3 days**: B1.09, B1.16
- **5 days**: B0.23, B0.24, B0.27, B0.29, B1.06-B1.08, B1.10-B1.11, B1.14-B1.15, B1.17-B1.21
- **7 days**: All other rooms (B0.11, B0.13-B0.15, etc.)

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
| `run_booker.bat` | Wrapper script called by Task Scheduler |
| `setup_scheduled_tasks.ps1` | Installs and verifies the one recurring task |
| `config/config.example.yaml` | Exact supported advanced configuration schema |
| `config/config.yaml` | Optional local room/rule override |
| `data/browser_state/state.json` | Saved browser session (cookies, localStorage) |
| `data/auth_recovery_state.json` | Gitignored no-secret credential latch / transient retry cooldown |
| `data/booking_history.json` | JSON log of all booking runs |
| `data/settings.json` | GUI settings, practice plan, ignored events, and extendable bookings |
| `data/mutation_receipts.json` | Gitignored crash-recovery journal for remote mutations |

## Reliability Contract

- Authentication, existing configuration, settings, mutation journal, full
  agenda reach, calendar date, form identity, requested time values, and
  persisted Save identity all fail closed.
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
- GUI **Check / Repair Login** runs the isolated `--headless --login-only`
  recovery path. **Update Secure Login** opens the masked
  `--configure-autonomous-login` prompt; the visible `--setup-login` flow is a
  CLI-only manual fallback.
- Settings, history, session state, and receipts use atomic replacement; shared
  JSON writes use interprocess locks. GUI live scans use the same deterministic
  authentication recovery as the booker and discard stale background results.
- Ignored events use a v2 identity covering date, start, end, title, room, and
  reservation type. A legacy time-only key applies only when it identifies one
  distinct scanned event; ambiguous matches ignore nothing. Ignored reservations
  still consume daily/weekly/peak budgets and enforce their same-room gap.
- Scheduled launches require the repository `.venv` and prove required imports
  before booking. There is no global-Python fallback. Schedule status, setup,
  and elevated idempotent removal run outside the Tk UI thread.
- `--check-only` performs a read-only authenticated agenda scan and traverses all
  eight booking days. `--only-date`, `--only-room`, `--max-actions`, and
  `--max-action-minutes` provide bounded live verification without weakening the
  confirmation rules. `--horizon-only` and `--extensions-only` additionally
  isolate the mutation type and return immediately after the scoped phase, so a
  missing candidate can never fall through to an unrelated booking.

## 2026-08-30 Exact Horizon Lifecycle Milestone

- The initial horizon-edge create is derived from `slot time - room horizon +
  30 minutes`, prepares within three minutes, and always creates exactly the
  minimum 30-minute reservation. A 0.25-second server-clock allowance replaces
  the former one-second delay.
- Pending extensions run directly after agenda/receipt reconciliation, before
  overview work or new snipes. Due work is first, imminent boundaries are
  pre-opened for at most three minutes, and each Save occurs only after the
  exact 15-minute unlock. Missed runs catch up to the latest completed boundary
  in one verified mutation, capped by the target and two-hour maximum.
- Both create and extension paths revalidate the complete form immediately
  before the receipt and Save. Extensions additionally require the current URL
  to equal the tracked positive event URL and Save to be enabled. A changed
  form, stale candidate, wrong event ID, warning, missing control, or closed
  editor stops without a receipt or click.
- A verified isolated mutation returns before cleanup navigation. Just-opened
  new snipes have a three-minute grace after a priority extension, while older
  candidates are rejected as ordinary availability.
- Deterministic tests cover +29:59, exact +30 initial booking, +44:59, every
  extension boundary from +45 through +120, missed-run catch-up, queue order,
  form drift, exact event identity, disabled Save, editor disappearance, snipe
  grace, action-cap short-circuiting, and isolated runtime modes. Live
  end-to-end extension progression still requires a genuine safe horizon slot;
  never log out or create an unrelated booking merely to manufacture one.
- The scoped repository regression suite passes 237 tests; the current shared
  checkout passes 274 including concurrent GUI work. A live `--check-only` pass
  reused the saved session without sign-in, found the complete three-event
  agenda, traversed all eight dates, and found 154 visible gaps. There were zero
  active extension records and zero pending receipts, so no live mutation was
  fabricated merely to exercise the lifecycle.

## 2026-08-30 Reliability and Practice-Plan Milestone

- Optional practice targets preserve legacy allocation when disabled. When
  enabled, the GUI supports a 0.5-12 hour default plus exact-date overrides and
  booking/off choices in an eight-day editor.
- Saving customization merges only the edited dates into the latest settings,
  and invalid input restores the last persisted control state.
- Post-Save uncertainty stops all further mutations until exact reconciliation;
  bounded live checks can cap both the action count and each action's duration.
- The combined offline suite passes 215 tests across authentication, booking
  policy, Save verification, receipt recovery, GUI concurrency, event identity,
  current DOM fixtures, launchers, and scheduler shape.
- Compact practice-plan controls merge only locally edited fields into the
  latest locked settings, then resynchronize from the confirmed saved plan so a
  concurrent default/enable change cannot be silently overwritten or shown stale.

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

- The current saved session is proven after one deterministic Microsoft
  credential/SMS recovery. Later login-only, manual, and scheduled checks reused
  it without password access, another code, logout, or a user-controlled browser.
- The current `app-overview-svg` renderer is supported alongside the legacy
  grid. Readiness waits for asynchronous overlays; room labels, coordinates,
  exact prefilled URLs, delayed menus, and delayed time controls are verified
  before form mutation.
- Two bounded 30-minute live bookings in different AHC rooms and dates were
  created and reload-verified. Exact agenda event IDs and room/date/time matched,
  and a final read-only pass covered all eight dates, 29 rows per date, the two
  reservations, the unrelated class conflict, and 154 visible gaps.
- A live verifier mismatch left its crash receipt pending and prevented a
  duplicate. Current arrangement-card and two-digit-date parsing plus URL-first,
  same-event agenda reconciliation resolved it safely before another booking.
- The combined offline suite passes 215 tests. A temporary Task Scheduler
  `--check-only` run also completed with exit code zero and was removed.
- `AsimutBooker_Recurring` is installed as the only root Asimut task. Exact
  action, trigger, limited current-user principal, wake/catch-up/network/battery,
  overlap, restart, and execution settings were read back. AC/DC wake timers are
  enabled and plugged-in lid close is Do nothing; physical wake remains hardware
  and firmware dependent.
- Provisional reconfirmation remains deliberately unautomated: RWCMD's exact
  due-state, action, eligibility window, and presence/network policy have not
  been observed. Do not add a broad-text or coordinate click; first establish a
  read-only RWCMD-specific contract, then journal and reload-verify one semantic
  action with no retry on uncertainty.

## Maintenance Notes

When modifying this codebase:
- **Always update `CLAUDE.md`** when adding features, changing behavior, or modifying architecture
- Keep the "Key Functions" sections current with new/changed functions
- Document any new booking rules or constraints
- Update the Files Overview table if adding new files
