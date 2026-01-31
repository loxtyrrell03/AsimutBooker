# AsimutBooker

Automated booking system for Royal Welsh College of Music and Drama (RWCMD) practice rooms via Asimut.

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs on a schedule (self-hosted) and books rooms as soon as they become available in the booking window.

### Key Features

- **Automated Login**: Handles Microsoft 365 SSO authentication with persistent session storage (no manual login required after initial setup)
- **Priority Room Booking**: Targets specific preferred rooms (e.g., B0.35) before falling back to alternatives
- **Scheduled Execution**: Runs hourly to catch rooms as they enter the booking window
- **Configurable Booking Rules**: Supports different booking horizons for different room types (1 week, few days, etc.)
- **Headless Browser Automation**: Uses Playwright for reliable browser automation

## Technical Stack

- **Language**: Python 3.11+
- **Browser Automation**: Playwright (handles JavaScript-heavy sites, SSO flows)
- **Scheduling**: APScheduler or cron (self-hosted)
- **Configuration**: YAML/JSON config file for rooms, times, preferences
- **Session Management**: Persistent browser context to maintain login state

## Target Institution

- **Institution**: Royal Welsh College of Music and Drama (RWCMD)
- **Asimut URL**: `https://rwcmd.asimut.net/`
- **Location Category**: Music Practice Rooms - AHC
- **Authentication**: Microsoft 365 SSO

## Room Configuration

Priority rooms are configured in `config/config.yaml`. Edit this file to set your preferred rooms in order of priority:

```yaml
rooms:
  priority:
    - "B0.35"    # First choice
    - "B0.28"    # Second choice
    - "B0.24"    # Third choice
    # Add more rooms as needed
  fallback: true  # If true, book any available room when priorities are full
```

## Booking Strategy

1. Calculate which dates are entering the booking window (e.g., 7 days ahead)
2. Check availability for priority rooms on those dates
3. Book the best available slot at preferred times
4. Log all booking attempts and results

## Directory Structure

```
AsimutBooker/
├── src/
│   ├── __init__.py
│   ├── main.py           # Entry point
│   ├── auth.py           # Microsoft 365 SSO handling
│   ├── booker.py         # Core booking logic
│   ├── scheduler.py      # Scheduled task management
│   └── config.py         # Configuration loader
├── config/
│   ├── config.yaml       # User configuration (rooms, times, preferences)
│   └── config.example.yaml
├── data/
│   └── browser_state/    # Persistent login state (gitignored)
├── logs/                 # Booking logs
├── requirements.txt
├── CLAUDE.md
└── README.md
```

## Environment Variables

```
ASIMUT_EMAIL=your.email@student.rwcmd.ac.uk
ASIMUT_PASSWORD=your_microsoft_password
```

Note: After initial login, the browser state is saved and credentials may not be needed for subsequent runs (session persistence).

## Usage

```bash
# Initial setup - will open browser for manual SSO login
python src/main.py --setup

# Run once (for testing)
python src/main.py --once

# Start scheduler (continuous hourly checks)
python src/main.py --schedule
```

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run with visible browser (debugging)
python src/main.py --headed

# Check room availability without booking
python src/main.py --dry-run
```
