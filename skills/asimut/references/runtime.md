# Runtime, fallback operations and diagnosis

The bridge uses `assistant_tools.BookerToolSurface` with the same validators,
request binding, selections and worker as the desktop/phone assistant. Canonical
state is in the repository's `data/`; the phone shares it.

## Authentication

For a login fault, use the installed
[asimut-booker-auth skill](C:/Users/Lox/.codex/skills/asimut-booker-auth/SKILL.md).
It reuses the session, Credential Manager and existing SMS bridge.
`refresh_booker_data {scope: "login"}` invokes the same engine recovery. Never
print credentials, cookies, storage state, OTPs or bridge tokens. Do not sign out
to test login. Configure credentials only when requested.

## Missing typed action

Not every possible edit has one typed tool. Continue through existing helpers
or the standard ASIMUT browser editor; add a small reusable adapter if necessary.
Do not silently perform a broader action or abandon an authorized request just
because the typed tool does not expose it.

| Operation | Existing source |
| --- | --- |
| Authenticated read-only room scan | `assistant_availability.main` |
| Worker setup and guards | `book_week.main`, `run_booking` |
| Exact new slot | `try_book_slot`, `record_pending_create`, `wait_for_created_booking_outcome` |
| Room/time edit | `edit_reservation_room_time`, `upgrade_validation.py`, `booking_time_edits.py` |
| Extension | `book_week.edit_reservation_end_time`, `calculate_extension_capacity_holds` |
| Agenda/manual-edit detection | `scan_agenda`, `manual_cancellations.py` |
| Receipt recovery | `mutation_receipts.py`, `reconcile_pending_mutation_receipts` |
| Quotas/access/horizons | `room_catalog.py`, `booking_rules.py`, `booking_quotas.py` |

Read current signatures. Low-level calls need the worker's live policy, current
agenda, preference snapshot and locks. Retain `booking_preference_run`, the final
`booking_save_boundary`, exact identity/form checks, receipt before Save and
independent persisted-event/new-agenda proof. The editor may move the end when
start changes; restore and verify the requested end. Never bypass validators,
fake availability, suppress receipt errors or use a fake Save-success result.
Old private artifact scripts with fixed IDs/dates are not general tools.

Chrome/browser control is permitted by repository instructions; native Computer
Use requires an explicit current request. Prefer the app's session and scripts.
A browser route does not bypass login, site refusals or mutation ownership.

## Ownership and failure handling

- Shared locks: `data/assistant-mutation.lock`, `data/booker-runtime.lock` and,
  for activation, `data/preference-dispatch.lock`. Standalone writes require
  assistant/runtime ownership; bridge writes and worker runs already use these.
- Wait for a busy owner; do not terminate a scheduled Save or launch a competing
  mutation. Preserve Automatic booking Off.
- Diagnose with sanitized health/history/mutations, preferences and fresh agenda.
  Distinguish unavailable rooms, horizons, quota, access, manual protections,
  target met, authentication and stale data.
- Uncertain writes keep receipts. Never delete the journal or retry under a new
  identity. Reconcile the existing outcome first.
- Read the actual saved peak-exception flag. Quotas are not permissions; both
  endpoints must fit the free window and ASIMUT must approve the exact request.

Ordinary settings/bookings need no deployment or restart. For an actual repair,
inspect launcher/listener, idle state, repository instructions and receipts
before activation. Preserve credentials, settings, unfinished work and unrelated
routes. Phone task: `AsimutBooker_Phone`; worker: `AsimutBooker_Recurring`.
Keep `https://lox-pc.tail89d19b.ts.net:10443/` -> `127.0.0.1:8794`. Verify session,
data API and connected app after a hosting change; a PC browser is not proof of
physical-phone behavior.
