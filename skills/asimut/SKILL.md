---
name: asimut
description: Operate Lachlan's RWCMD ASIMUT practice-room Booker (also called Asimut, Azimuth or the booker). Use for checking availability and schedules, changing practice settings, booking, cancelling, editing, extending or upgrading rooms, and diagnosing booking runs through the existing local app.
---

# Asimut Booker

Carry the requested outcome through the existing app and verify it. A clear
request to change settings or reservations authorizes those scoped actions;
do not ask the user to approve the same action again. Questions and skill setup
alone do not authorize booking changes. Ask only when the intended booking,
date, time or material alternative cannot be resolved from the request and fresh
state. Do useful read-only work first. Do not turn a booking request into a code
project unless a missing or broken capability actually requires that repair.

## Access

- Canonical runtime: `C:/Users/Lox/Desktop/repo/AsimutBooker`.
- Python: `C:/Users/Lox/Desktop/repo/AsimutBooker/.venv/Scripts/python.exe`.
- Phone app: `https://lox-pc.tail89d19b.ts.net:10443/`.
- ASIMUT: `https://rwcmd.asimut.net/agenda`.
- Read the canonical repository's `AGENTS.md` for current behavior before writes.
  Do not use a stale worktree or copy its settings/session into production.

Use the companion command bridge when Booker tools are not exposed directly in
the current agent's tool list. It imports the canonical app's validated action
layer, locks and subprocess worker; it does not create a second service.

```powershell
$bookerPython = 'C:\Users\Lox\Desktop\repo\AsimutBooker\.venv\Scripts\python.exe'
$bookerSkill = 'C:\Users\Lox\.codex\skills\asimut\scripts\booker.py'
& $bookerPython $bookerSkill tools
& $bookerPython $bookerSkill --read-only call read_phone_preferences
& $bookerPython $bookerSkill --read-only call get_manual_protections
```

Start with scoped `get_booker_context` sections (`preferences`, `agenda`, `rooms`,
`health`, `mutations`, `plan`, `history`) as needed. Pass arguments using a UTF-8
JSON file via `--args-file`; the live `tools --name TOOL` schema is authoritative.
The read-only flag rejects every mutating step before dispatch.

## Choose the operation

- **Settings:** [references/settings.md](references/settings.md) covers daily
  goals, dates, hours, breaks, room ranking/exclusions, strategy, advance
  allocation, quota presets and other app settings.
- **Find/book/edit/cancel/upgrade/extend:** Read
  [references/bookings.md](references/bookings.md). It distinguishes an exact
  reservation from a daily target or one automatic planning action.
- **Commands and batched selections:** [references/bridge.md](references/bridge.md)
  describes argument files, request binding, selection lifetime and results.
- **Login, errors, services or a missing operation:** Read
  [references/runtime.md](references/runtime.md). Reuse the existing engine or
  browser editor within the requested scope instead of weakening its checks.

## Preserve the user's choices

Read current values; do not assume historical targets, hours, room orders or
quota settings. Resolve relative dates using Europe/London and verify weekday/
date agreement. A dated request normally changes that date, not the global
default. Keep unrelated preferences and reservations.

Manual edits are authoritative. Starting 11:00-12:00 later at 11:30 protects
11:00-11:30 in **every room**. Do not fill it to hit the daily target, extend back
into it, or silently clear `manual_booking_overrides`. Reopen only the exact
interval the user explicitly asks to book again. Explicit new instructions can
supersede an earlier pin; otherwise leave the edited reservation alone.

Live ASIMUT checks determine access, room-specific horizons, duration and quota.
The saved free-horizon peak exception is separate from ordinary peak credit.
Both endpoints must fit the free window; an extension tests the whole resulting
reservation. Do not infer permission from another student's booking, remaining
rolling credit, or a cached room gap.

## Completion

Use exact event identity, guarded Save, a verified receipt and a newer complete
agenda for booking success. Plans, queued goals, exit zero and room gaps are not
booked practice. State the actual room, date, start and end; link
`https://rwcmd.asimut.net/arrangement?eventId=ID` when available. Read settings
back and distinguish a queued preference check from completed bookings.
Explain a concrete blocker or partial result plainly; do not keep retrying an
uncertain mutation or invent a reservation.
