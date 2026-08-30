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

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs on a schedule via Windows Task Scheduler (with wake-from-sleep support) and books rooms as soon as they become available in the booking window.

### Key Features

- **Automated Login**: Handles Microsoft 365 SSO authentication with persistent session storage (no manual login required after initial setup)
- **Priority Room Booking**: Targets specific preferred rooms before falling back to alternatives
- **Scheduled Execution**: Runs every 30 minutes (07:30-22:00) via Windows Task Scheduler with wake-from-sleep
- **RWCMD Booking Rules**: Respects rolling quota (28 hours/week), peak hours (2hr/day Mon-Fri 9am-4pm), and 60-min same-room gap
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts; extracts room names for same-room gap enforcement; distinguishes "Reservation" events from classes for accurate quota tracking
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring, manual control, and day selection
- **Day Selection**: Toggle specific days on/off for booking via GUI checkboxes
- **Booking History**: Tracks all runs and bookings made
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

- If Asimut is signed out, use the `rwcmd-outlook` skill to sign back in through the established RWCMD Microsoft authentication and verification workflow. Do not improvise a separate login flow or store credentials or verification codes in the repository.

## Directory Structure

```
AsimutBooker/
├── book_week.py          # Main booking script (entry point)
├── gui.py                # Desktop control panel GUI
├── run_booker.bat        # Batch file called by Task Scheduler
├── AsimutBooker.bat      # GUI launcher (double-click to open)
├── setup_scheduled_tasks.ps1  # Creates Windows scheduled tasks
├── create_shortcut.ps1   # Creates desktop shortcut
├── config/
│   └── config.yaml       # User configuration
├── data/
│   ├── browser_state/    # Persistent login state (gitignored)
│   │   └── state.json
│   ├── booking_history.json  # Run history
│   └── settings.json     # GUI settings (disabled dates, etc.)
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
- Day selection checkboxes (enable/disable specific days for booking)

### Command Line
```bash
# Run with visible browser (for testing/debugging)
python book_week.py

# Run headless (for scheduled tasks)
python book_week.py --headless
```

### Initial Setup
1. Run `python book_week.py` with visible browser
2. Complete Microsoft 365 SSO login manually
3. Browser state is saved to `data/browser_state/state.json`
4. Subsequent runs use saved session (no login required)

### Scheduled Tasks Setup
```powershell
# Run as Administrator
.\setup_scheduled_tasks.ps1
```

This creates 30 scheduled tasks (every 30 min from 07:30-22:00) with:
- Wake-from-sleep enabled
- Lid close action set to "Do nothing" when plugged in

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

1. **Initial Booking**: At the first possible moment (30 min after horizon), books the minimum 30-minute slot and saves it to `extendable_bookings` in settings.json

2. **Extension Runs**: Every 15 minutes, the scheduled task runs again and:
   - Loads pending extendable bookings
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

## Multi-Day Horizon Snipe

The booker scans **all** horizon days (7, 5, 3) for snipe candidates, not just the furthest day. This catches opportunities across different room horizons.

### How Multi-Day Snipe Works

1. **Pre-scan phase** (before target time):
   - Navigate to Day 7 → scan for 7-day horizon rooms becoming bookable
   - Navigate to Day 5 → scan for 5-day horizon rooms becoming bookable
   - Navigate to Day 3 → scan for 3-day horizon rooms becoming bookable
   - Collect all candidates within 3-minute snipe window

2. **Sort candidates**: Furthest day first, then by room priority within each day

3. **Sequential snipe**: Attempt each candidate in order
   - Navigate to candidate's day
   - Pre-fill form, wait for exact moment, click Save
   - On success: record booking, continue to next
   - On failure: skip, try next candidate

4. **Resume normal booking**: Navigate back to furthest enabled day

### Extension Priority

Pending extensions take priority over new snipes. If a slot has a pending extension (from a previous horizon edge booking), the snipe scanner skips that slot to avoid conflicts.

### Key Functions

- `navigate_to_day()`: Helper to navigate calendar forward/backward
- `find_all_snipe_candidates_multi_day()`: Scans all horizon days for snipe candidates
- `find_horizon_snipe_candidate()`: Original single-day scanner (still used internally)

## Configuration

The script loads settings from `config/config.yaml` with fallback to hardcoded defaults. Key configurable options:

- **Room priority**: Order of preferred rooms (first = most preferred)
- **Room horizons**: How many days in advance each room can be booked (3, 5, or 7 days)
- **Booking rules**: Rolling quota, peak hours limits, same-room gap
- **Schedule**: Run times for the booker

See `config/config.yaml` for all available options. Changes take effect on next run.

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
```

## Files Overview

| File | Purpose |
|------|---------|
| `book_week.py` | Main booking script - scans agenda, navigates calendar, books slots |
| `gui.py` | Tkinter GUI for monitoring and control |
| `run_booker.bat` | Wrapper script called by Task Scheduler |
| `setup_scheduled_tasks.ps1` | Creates Windows scheduled tasks with wake timers |
| `config/config.yaml` | User configuration (rooms, horizons, rules) |
| `data/browser_state/state.json` | Saved browser session (cookies, localStorage) |
| `data/booking_history.json` | JSON log of all booking runs |
| `data/settings.json` | GUI settings including disabled dates and extendable bookings |

## Maintenance Notes

When modifying this codebase:
- **Always update `CLAUDE.md`** when adding features, changing behavior, or modifying architecture
- Keep the "Key Functions" sections current with new/changed functions
- Document any new booking rules or constraints
- Update the Files Overview table if adding new files
