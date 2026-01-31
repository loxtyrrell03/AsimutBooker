# AsimutBooker

Automated booking system for Royal Welsh College of Music and Drama (RWCMD) practice rooms via Asimut.

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs on a schedule via Windows Task Scheduler (with wake-from-sleep support) and books rooms as soon as they become available in the booking window.

### Key Features

- **Automated Login**: Handles Microsoft 365 SSO authentication with persistent session storage (no manual login required after initial setup)
- **Priority Room Booking**: Targets specific preferred rooms before falling back to alternatives
- **Scheduled Execution**: Runs every 30 minutes (07:30-22:00) via Windows Task Scheduler with wake-from-sleep
- **RWCMD Booking Rules**: Respects rolling quota (28 bookings), peak hours (2hr max Mon-Fri 9am-4pm), and 60-min same-room gap
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring and manual control
- **Booking History**: Tracks all runs and bookings made

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
│   └── booking_history.json  # Run history
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
- **Rolling Quota**: Maximum 28 active bookings
- **Peak Hours**: Maximum 2 hours during Mon-Fri 9am-4pm
- **Booking Duration**: Minimum 30 min, maximum 2 hours
- **Same-Room Gap**: 60-minute gap required between bookings in same room

## Room Priority

Rooms are prioritized in `book_week.py` via the `PRIORITY_ROOMS` list:
```python
PRIORITY_ROOMS = [
    "B0.11", "B0.12", "B0.13", "B0.14", "B0.15",
    # ... etc
]
```

The script tries to book rooms in this order, preferring earlier rooms in the list.

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
| `data/browser_state/state.json` | Saved browser session (cookies, localStorage) |
| `data/booking_history.json` | JSON log of all booking runs |
