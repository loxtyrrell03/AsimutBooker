# Manual live-site diagnostics

> **WARNING: These are not automated tests. Some commands below can create or
> edit real RWCMD Asimut reservations. Never run this directory through test
> discovery, CI, or an unattended test command.**

Run commands from the repository root. A saved real-account browser session is
required for every mode that opens Asimut. `--headless` only hides the browser;
it does **not** make a command read-only.

## Read-only or offline modes

These modes do not intentionally create or edit an Asimut reservation:

```powershell
# Offline calculations plus a read of local extendable-booking state.
python tools/manual_live/extension_live.py --dry-run

# Read local extendable-booking state only.
python tools/manual_live/extension_live.py --list

# Open the real agenda and read reservations to validate panel matching.
python tools/manual_live/panel_matching_live.py

# Open the real agenda and find a reservation without editing it.
python tools/manual_live/edit_reservation_live.py --dry-run
```

The two agenda commands still access the real account and network. They are
read-only by intent, not isolated unit tests.

## Local-state mutation modes

These do not intentionally save an Asimut reservation, but they modify
`data/settings.json`:

```powershell
# Adds a synthetic extendable-booking entry to local settings.
python tools/manual_live/extension_live.py --add-test --dry-run

# Removes every locally tracked extendable booking.
python tools/manual_live/extension_live.py --clear
```

Do not use `--clear` when real pending extensions must be preserved.

## Dangerous real-booking or real-edit modes

The following commands may click **Save** against the real Asimut account:

```powershell
# Legacy fixed-date booking probes. There is no dry-run mode.
python tools/manual_live/book_feb2_live.py
python tools/manual_live/book_feb6_live.py

# Finds a reservation and attempts to extend its end time by 15 minutes.
python tools/manual_live/edit_reservation_live.py

# Runs the extension UI workflow for locally tracked pending extensions.
python tools/manual_live/extension_live.py
python tools/manual_live/extension_live.py --headless
```

For `edit_reservation_live.py`, `--date`, `--time`, and `--room` narrow the
target but do not prevent editing. For `extension_live.py`, omitting
`--dry-run` permits the real edit workflow; `--headless` is equally dangerous.

Before any dangerous command, verify the intended date, room, start/end time,
current quota, and saved login account. Prefer adding deterministic coverage in
`tests/` instead of using a live script for logic that can be tested offline.
