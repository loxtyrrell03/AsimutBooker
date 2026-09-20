## 2026-09-20 category recovery and selectable quota policy

- ASIMUT added a permitted chamber category requiring 3-7 participants. Category
  discovery now excludes groups that cannot admit one participant, while still
  rejecting ambiguous individual categories. The first observed scheduled
  discovery failure was 17 September at 15:58; later runs exited before scans.
- Policy-discovery and unexpected worker failures now reach failed run history,
  retaining earlier verified actions. They no longer leave the app showing only
  a prior successful run. The full 1,347-test Python suite passes.
- `booking_rules.py` owns saved new (6h/60min peak), legacy (28h/120min peak)
  and validated custom quota presets. Both built-ins retain the currently tested
  five-hour free window; legacy means previous quota ceilings, not an assertion
  that ASIMUT still grants them. Horizons/access/durations and final Save approval
  remain live. Rule edits invalidate plans and veto in-flight saves on drift.
  Worker, extensions, upgrades, explicit time edits, phone preferences and
  assistant context share the selected policy; contract revision is 5.
- Live read-only checks on 20 September showed the information page still
  advertising previous quotas, but the actual account balance was zero and
  future booking checks explicitly refused quota. Never bypass those checks or
  use the chamber category to obtain solo practice. Activation and actual booking
  outcomes are recorded separately below; offline tests do not prove a booking.
- Desktop Booking Rules and phone Settings > Booking rules expose both presets
  and custom quota/peak-window/free-window values, using the shared validator.
  Phone edits retain revision-conflict handling; changing presets does not alter
  targets or existing reservations. Chromium/WebKit checks cover 320/390/844px,
  preset/custom saves, midnight, invalid values and stale-settings reload. The
  desktop dialog regression, 19 Node checks, TypeScript, lint and isolated static
  build pass. This UI milestone does not itself prove deployment or live writes.
- Exact upgrade checks also return the live quota warning, even for unchanged
  duration. They now end the pass with a visible quota wait instead of trying
  each room. Known refusals restore staged/concurrent transfer work first;
  uncertain or refused restoration retains its pending receipt. The exact
  Chromium editor checks (86 focused tests) and 77 staging/progressive/runtime
  checks pass. Never infer edit permission from zero net added minutes.
- All 1,351 Python regressions pass after quota-wait/recovery changes. General
  failed runs now notify with the actual reason, suppressing identical consecutive
  failure alerts; history retains every run. Agenda-only quota estimates are
  explicitly labelled and cannot claim live account credit.
- Activated in the canonical worker checkout and existing idle phone service,
  preserving preferences beyond the authorized legacy preset, private state,
  task definitions and all Serve routes. Build `20260920-rule-presets` passed
  the exact HTTPS deployment/session and rendered 320/390px settings checks.
  The natural 14:58 scheduled run completed at 14:59 with a recorded quota wait,
  zero applied changes and no pending receipt. Independent complete agenda and
  eight-date/33-room scans preserved every existing event. Live booking checks
  and an unchanged-duration room-edit check both explicitly refused quota.
  This does not claim the requested future week was filled. Private evidence is
  ignored under `artifacts/recovery-20260920/`; no physical-phone check was made.

## 2026-09-17 revised college quotas and free horizon (superseded by presets above)

- Advance quota is six hours, with at most one weekday peak hour (09:00-16:00).
  Live room/global horizons and duration limits remain authoritative; the live
  global horizon is seven days. Older YAML cannot raise the new quota caps.
- `booking_quotas.py` reads the authenticated per-date quota endpoint in seconds.
  Complete snapshots replace local estimates and allow completed sessions to
  release credit. Unknown quota types or unreadable responses pause writes.
  Exact quota refusals end the pass without trying every room; unresolved
  transactions retain reconciliation status, including earlier verified work.
- Both endpoints must fit entirely inside the next five elapsed hours for a
  short-notice exception. It consumes any remaining credit normally and never
  creates advance credit. The local one-hour peak cap also applies here, even
  if ASIMUT waives that check. Targets, strict hours, conflicts, room access,
  duration, fragmentation and action limits still apply.
- Scheduled runs check short-notice opportunities even when advance quota is
  full. Pending extensions retain priority and capacity; exact-time room fallback
  can use the same free window. Extensions use the complete edited interval,
  not only the added tail, and stop at remaining peak allowance. Room upgrades,
  consolidation, progressive transfers and time edits retain identity/receipt
  checks. Existing over-limit reservations are never automatically reduced;
  whole-room upgrades may preserve their peak usage subject to live approval.
- Assistant contract revision 4 and rule-aware plan fingerprints retire old
  28-hour/two-hour assumptions without changing saved preferences or history.
  Full regression run: 1,339 Python tests pass; 72 focused checks pass after the
  final extension-hold and short-notice fallback changes. Authenticated quota,
  agenda and read-only plan checks passed with non-check submissions blocked.
  These checks made no real booking or edit.
- Activated in the canonical scheduled-worker checkout and reloaded only the
  idle existing phone task under both runtime/mutation locks. Private state
  hashes, task definitions and every Serve handler were preserved. Exact HTTPS
  session/bootstrap, deployment checks and a rendered 390px My Week with actual
  agenda data passed at the unchanged `https://lox-pc.tail89d19b.ts.net:10443/`.
  The phone shell is unchanged. Existing desktop windows remain open and need
  reopening for updated in-process assistant rules. This is PC-browser and
  read-only site evidence, not physical-phone or new-rule live-mutation proof.
  Private diagnostic/activation evidence is ignored under
  `artifacts/new-rules-20260917/`.

## 2026-09-16 lazy agenda extension lookup and failure reporting

- Extension lookup distinguishes an unloaded card from duplicate identity.
  Missing cards use the bounded agenda scroll/retry path; duplicate, changed,
  cancelled and wrong-ID cards still cannot reach the editor. Both current
  cards and canonical legacy links have real Chromium regressions.
- Pending extension capacity is not confirmed practice. Modern/legacy plans
  and horizon logs keep an unmet target explicit while preserving its holds.
  Failed editor attempts produce failed history and a nonzero worker result,
  retaining earlier verified changes; ordinary future unlock waits are not errors.
- A fresh complete, date-verified room grid can cap or retire runtime extension
  targets when occupied time blocks the tail, releasing capacity for alternatives.
  Exact reservation identity and a fully open target horizon are required;
  pending mutations, unknown evidence and concurrent tracking drift retain holds
  or abort. Saved practice targets and remote reservations are unchanged.
- Normal new bookings commit time controls and require approval for the exact
  date, room and interval before an enabled Save. Angular suppresses unchanged
  values, so an already-matching end uses a temporary no-Save change followed by
  the requested value; approval for the intermediate interval cannot authorize Save.
- All 1,307 Python tests pass, including real Chromium lookup and new-booking
  validation regressions. One live extension and two creates were independently
  verified against persisted events and a fresh complete agenda: all original
  IDs and unrelated events were preserved, with no pending receipts. Occupancy
  and explicit room-permission refusals prevented the full requested daily total.
- The canonical scheduled worker loads the repaired source. Existing hosts and
  saved preferences were preserved. Private live evidence remains ignored under
  `artifacts/extension-repair-20260916/`; it is separate from physical-phone proof.

## 2026-09-15 assistant booking time edits

- `edit_reservation_time` consumes one fresh exact `find_reservations` selection.
  `trim_start` keeps the end; `shift_later` moves both endpoints equally. The
  worker retains event ID/date/room, requires a future new start, checks every
  other personal event and any newly occupied room time, and uses the guarded
  single-Save editor. Existing automatic upgrade invariants remain separate.
- Strict `time_edit` receipts persist both states. Independent persisted proof
  precedes retiring stale extension tracking and protecting only released
  original time against automatic rebooking. Recovery finishes these steps
  before receipt completion; uncertain edits never trigger cancellation/retry.
  Saved targets/preferences remain unchanged. See `docs/assistant-booking-time-edits.md`.
- All 1,289 Python tests pass, including intercepted Chromium editor checks;
  the two core Luna/high/standard synthetic trim/shift turns pass. Tool schemas
  must expose all edit fields in one plain object: `oneOf`, including inside
  `allOf`, hid common arguments from the model. The host enforces mode-specific
  argument sets independently. Contract revision 3 refreshes stale reasoning
  context while retaining transcript history. These tests made no real bookings
  or edits. The final 165 focused checks passed after schema correction.
- All nine final synthetic model scenarios pass: trims, relative/absolute later
  shifts, explicitly later forward wording, clashes, ambiguous identity and
  direction, uncertain Save and read-only questions. The accurate phrase
  "did not confirm" initially failed a narrow wording grader; the saved trace
  passes the corrected grader, which has its own regression. Forty-three
  evaluator checks pass; production remains Terra/medium/Fast.
- Activated in the canonical worker checkout and reloaded only the existing
  idle phone task after the scheduled run finished, holding the Booker and
  assistant mutation locks. Settings/history hashes and every Serve handler
  were preserved. Exact private HTTPS/session/bootstrap and rendered Assistant
  "Booker ready" plus the contract-refresh banner passed on the unchanged
  `https://lox-pc.tail89d19b.ts.net:10443/` origin. The existing phone shell and
  desktop sessions were retained; reopen desktop windows to load the feature.
  This is PC-browser evidence, not physical-phone or live time-edit proof.
  Synthetic reports and local verification logs are ignored under
  `artifacts/assistant-time-edits/`.

## 2026-09-15 verified live progressive upgrades

- Two real partial transfers upgraded 90 booked minutes. Independent fresh
  agenda/grid evidence verified both parent/create receipt pairs, retained every
  existing reservation ID, preserved each date's total minutes and all unrelated
  events, and found no pending mutation. The final five-date scan covered 31 rooms
  and 258 gaps; remaining partial prospects require later horizon openings.
- The final editor repair passed all 1,248 Python tests. A subsequent explicit
  single-booking preference guard passed 45 focused planner/discovery tests;
  partial transfers cannot override disabled fragmented sessions. Actual rolling
  horizon boundary waiting/final donor retirement still have offline/browser
  fixture evidence; these two live transfers used already-open prefixes.
- Source is active in the canonical worker checkout. The existing recurring task
  retains its action, settings and 15-minute cadence, with launches 07:13–22:58.
  The idle phone backend was reloaded without changing its shell, private route,
  credentials or existing desktop sessions. Private HTTPS/session verification
  and PC browser rendering are separate from physical-phone proof. Private
  before/after records remain ignored under `artifacts/progressive-upgrades/`.

## 2026-09-15 live donor editor time coupling

- Live no-Save inspection confirmed that changing a reservation start from
  12:00 to 13:00 automatically moves its end from 14:00 to 15:00. Same-room
  editors now install exact-check listeners before either time change and
  correct the observed end afterward. Re-filling an unchanged final time must
  not be used to trigger validation; Asimut suppresses unchanged values.
- Both realistic Chromium regressions failed before the repair and pass after;
  76 focused editor/progressive checks pass. Two real attempts before the fix
  timed out before any Save, resolved their empty attempt journals and preserved
  all 13 bookings. Private request evidence is in the ignored progressive-upgrades
  artifact folder. Live successful transfers remain separate evidence.

## 2026-09-15 progressive discovery and recovery refinements

- Sparse better-room gaps can seed a partial upgrade even if no full-session
  gap exists. `progressive_discovery.py` bounds source-group/target evaluation;
  its typed prospects carry an actual planner-verified prefix, never free-tail
  evidence. Full-gap prospects keep the existing full-session upgrade path.
  After the whole-session sweep, one bounded dispatch can use newly discovered
  partial opportunities immediately. Read-only and scoped sweeps cannot do so.
- Attempt markers now persist at the final guarded click, after exact identity
  proof. A manual deletion during preparation cannot be mistaken for the
  worker's cancellation. Recovery checks current disabled dates, cancellation
  blackouts, ignored bookings, enabled zero targets and strict hours before any
  restoring write; incompatible recovery remains pending for user attention.
- Future-prefix capacity previews retain the current observed window for held
  extensions. Even a final 15-minute target deficit protects its extension's
  peak allowance. Timing/duration/quota refusals do not impose room-wide backoff.
- All 1,246 Python tests pass after these refinements, including the real
  tracker/extension-hold regression, exact cancellation-click checks and sparse
  discovery/dispatch. Actual source activation and booking results are recorded
  separately; the earlier live preview left all 13 reservations unchanged.

## 2026-09-15 progressive horizon transfers and room refusals

- `progressive_planner.py` plans a bookable superior-room prefix plus useful
  same-room fallback remainders, including shifted/fragmented originals. Every
  completed step preserves minutes, time fit, quotas and attainable daily-target
  capacity. Later teacher occupancy does not discard a still-available prefix;
  alternate remainder layouts are searched with bounded work. Whole-session
  upgrades retain their existing path when fully available.
- `progressive_state.py` persists routing hints and exact verified continuation
  identities separately from display-only `upgrade_plan.json`. The normal runner
  prepares due transfers before broad scanning; missing-target extensions retain
  priority. Actual room policy, agenda, grid and preference checks remain Save
  authority. No source is trimmed before the conservative live horizon boundary.
- A strict `transfer` parent in `mutation_receipts.py` records exact intervals,
  adjustment order and attempted operations. Parent-scoped seed/restoration
  creates cannot claim existing IDs. Recovery reverses actual source order,
  verifies newly recreated donor IDs when necessary, and never automatically
  restores an unattempted source changed externally. Unknown/failed restoration
  blocks unrelated mutations. Ordinary paths protect active transfer IDs.
- Asimut cannot atomically transfer across reservations: shortening first can
  expose released time to competitors. Preflight checks both new/existing editors
  before release and permits only approved or exactly compensated personal/peak
  warnings. Post-trim Save is freshly validated; recovery cannot promise that a
  released fallback remains available. Action/time budgets reserve rollback work.
- Explicit room-permission refusals continue to other candidates with scoped,
  expiring room/date backoff. Auth/service errors and uncertain responses remain
  separate. UI geometry does not invalidate planning; booking-control changes do.
  Corrupt hints may rebuild only with a valid journal and no pending transfer.
- See `docs/room-upgrades.md` for the transaction boundary and evidence limits.
  The former live whole-room/consolidation audit below does not prove a live
  progressive transfer. Source, simulation and exact browser checks are verified
  separately from live activation and real booking outcomes.
- All 1,218 Python tests pass, including 278 deterministic crowded-calendar
  simulations, restart/recovery cases and intercepted Chromium form checks.
  Authenticated new-booking checks confirmed the actual request/warning format
  with every non-check mutation blocked. No real progressive transfer is claimed
  by those checks. Recurring launch coverage now extends to 22:58 (07:13 start,
  15-minute interval, PT15H46M duration), preparing final edges through 23:00;
  installer and desktop task validation share the same schedule contract.

## 2026-09-15 completed live upgrade audit

- Three real staged consolidations and one ordinary room upgrade were completed.
  Every enlarged anchor was independently verified before donor retirement. The
  final separate agenda/grid scan covered 31 rooms across all five eligible dates
  and 260 observed gaps; no further candidate met the saved constraints. Exact
  receipt-to-agenda comparison accounted for every removed ID, preserved each
  date's total minutes and unrelated events, and found no pending receipt.
- Earlier blocked attempts were reconciled against the original reservations;
  they did not lose booked time. Private before/after agendas, network-check
  evidence, interrupted-attempt diagnostics and the full audit remain ignored
  under `artifacts/comprehensive-upgrades/`; do not commit personal schedules.
- A later uncertain action now retains earlier verified successes in its run
  history, scoped to the current run with a context variable. Ten focused
  publication/scheduled-queue checks pass. The broad 1,062-test run and additional
  focused reporting checks distinguish booking operations from internal actions.
- The existing private phone build is `20260915-comprehensive-upgrades`, on the
  unchanged canonical `lox-pc` route. Scheduled workers load the canonical source;
  existing desktop windows retain their sessions and reload new controls on reopen.
- The idle phone backend was reloaded after the final source activation. Exact
  private HTTPS deployment/session checks and a rendered 390px My Week confirmed
  the final reservations. This is PC browser evidence, not physical-phone proof.

## 2026-09-15 staged consolidation after live preview

- The real no-Save sweep proved that Asimut rejects an enlarged anchor while a
  donor overlaps it. `consolidation_staging.py` now plans intermediate exact-ID,
  same-duration edits into superior rooms before the anchor Save. Strict hours
  remain hard constraints; soft hours are preferred for intermediate bookings.
  Final schedule quality and booked minutes retain the original guarantees.
- The existing consolidation journal optionally records every intermediate
  state. Only those exact forward/reverse edits and the prepared anchor are
  allowed beneath that pending parent. Rejected anchors restore moved donors;
  unavailable restoration retains all intermediate minutes and blocks mutations.
  Recovery restores originals before anchor Save or retires donors after full
  replacement proof. Unknown responses never cause an automatic repeated Save.
- Temporary peak usage, personal conflicts, destination spacing and complete
  horizons constrain staging. Rejected intermediate slots yield alternatives.
  Each intermediate edit and verified restoration counts toward explicit limits.
  Read-only scans never perform recovery mutations, and isolated room/extension/
  horizon modes cannot silently retire a pending transaction's donors.
- Full regression run: 1,057 Python tests passed; focused staging/editor tests
  cover strict windows, exact parent scope, rejection, lost responses, restoration
  failure and three peak fragments. Source/fixture checks precede the live staged
  sweep; do not infer successful real consolidation from an approved bridge preview.
- Staging reuses the complete agenda already returned by independent transaction
  proof, then refreshes the room grid; it no longer scans the same agenda twice
  while leaving the editor open. Forty-two focused staging/editor checks pass,
  including real step revalidation and exact restoration. Blocking editor dialogs
  are reported for diagnosis; unknown overlays never authorize a forced click.
- Exact original proofs and fresh day revalidation now finish before opening
  the reservation editor. The editor must still match the original, issue its
  fresh exact check, and pass the final single-Save/preference boundary. This
  avoids leaving a dirty form open throughout cross-page scans. Sixty focused
  editor/staging/runtime checks cover the sequence and existing safety guards.
- Live failure capture identified `app-as-timepicker-body` covering the selector
  and Save; Escape does not dismiss this Asimut component. The editor now closes
  only that identified picker through its unique backdrop, verifies both times
  unchanged, and checks Save is unobstructed before recording Save intent. Unknown
  dark overlays fail closed. Sixty-two focused checks and an authenticated no-Save
  picker-dismissal check pass; blocked attempts were reconciled with originals intact.
- The first real staged consolidation completed with the full anchor verified
  before its temporary donor was retired, unchanged total practice minutes and
  no pending receipt. The final broad Python regression run passed 1,062 tests.
  Follow-up reporting now shares one consolidation summary between immediate
  publication and run history, preventing duplicate notifications. Completed
  history counts reservation operations instead of internal staging/retirement
  steps; explicit action limits still count those steps. Thirty-two focused
  publication/runtime/recovery checks pass after this reporting correction.

## 2026-09-15 comprehensive upgrade planning and consolidation

- Upgrade runs scan every eligible date in the live booking window before any
  edit. The implicit six-attempt/three-minute cutoff is removed; explicit action
  limits and cooperative Stop still apply. A compatible whole-day portfolio is
  selected, one change is verified, and the affected date is rescanned/replanned.
  Rejections are not blindly retried against the same day's unchanged agenda.
- `room_upgrades.py` supports full-duration room/time shifts and consolidating
  multiple non-overlapping originals into one session, preserving their total
  minutes and never downgrading a higher-ranked fragment. The user's saved room
  order is authoritative. Freed original-room gaps participate in the remaining
  daily-target comparison. Past/started and explicitly ignored bookings remain
  protected, as do active extension plans and user-saved blackout windows.
- Consolidation secures an enlarged anchor before retiring any redundant donor.
  Its strict `consolidation` mutation receipt records every original. Each donor
  retirement requires a fresh complete agenda and independent full-anchor proof;
  uncertain outcomes stop further mutation. Recovery resumes only unfinished
  donor retirement and never repeats the anchor Save. Read-only reconciliation
  never cancels donors. Upgrade retirement creates no rebooking blackout.
- Asimut must approve the enlarged anchor while all donors still exist. A site
  quota/conflict rejection leaves all originals intact; never cancel first to
  make an otherwise rejected replacement fit. Same-room expansions trigger the
  final check with the actual changed time after live revalidation.
- The default upgrade freeze is now zero hours, allowing future sessions today
  to improve; any explicitly saved cutoff remains authoritative. Both old/new
  starts must remain in the future. Desktop and phone share these defaults.
- `data/upgrade_plan.json` is ignored, display-only full-window planning evidence,
  including observed shorter-horizon prospects and their opening timestamps.
  Future gaps are not promises or Save authority. Normal live checks rediscover
  every executed candidate. `--upgrades-only` supports a complete sweep without
  a date restriction; optional date/event/room/action limits retain exact scope.
  Consolidation consumes one action for the anchor and one per retired donor.
- Initial verification: 1,042 Python tests pass, plus 19 phone Node checks,
  TypeScript, lint and the private phone static build. Exact shifted-time,
  three-fragment, competing-slot, seven-date/seven-upgrade, future-horizon,
  quota-rejection, same-room editor, crash/recovery and donor-protection cases
  are covered. This milestone is source/fixture evidence; live validation,
  publication and the full real-agenda before/after audit follow separately.

## 2026-09-15 initial duration-preserving room upgrades (historical)

- `room_upgrades.py` ranks single-reservation room/time replacements from fresh
  room gaps. Event ID, date and confirmed duration are invariant; superior rooms
  cannot worsen time fit. Full destination horizons, conflicts, peak allowance,
  same-room spacing, blackouts and pending extensions constrain candidates.
  Both old/new start times must be more than 24 hours away by default. Desktop,
  phone and assistant settings expose independent upgrade enablement and a
  0-168 hour settling deadline; missing target hours can still be booked.
- Upgrade receipts persist both exact states before Save. Missing, duplicate,
  partially changed or wrong-ID outcomes remain uncertain. Date/duration/ID
  changes are rejected before writing the journal. Recovery requires both a
  fresh complete agenda and independent exact-event page proof. A failed scan
  after a verified edit preserves success and stops further upgrades.
- Normal creates/extensions run before upgrades, including on target-met and
  quota-full dates. The whole-day solver checks that moving an existing session
  preserves remaining target coverage and time quality. Runtime work is bounded;
  `--upgrades-only --only-date DATE --max-actions N` supports controlled execution,
  with `--upgrade-dry-run` and optional `--upgrade-event-id` for inspection.
- The editor prepares changed times, rescans the agenda/grid, then selects an
  actual room option and requires fresh exact server approval. It journals before one Save
  and allows only the checked single-event payload through its request guard.
  It never cancels, recreates, shrinks, changes date, or retries an uncertain Save.
  Verify successful edits on a separate page to avoid Angular navigation races.
- All 1006 Python tests, 19 Node checks, TypeScript, lint and isolated phone build
  pass. Chromium/WebKit settings persistence and 320/390px layouts pass; owned
  desktop renders preserve navigation and controls at 760px. Chrome inspection confirmed
  exact-event PATCH validation carries the chosen location ID and both times;
  HTTP 200 can contain `success:false`, clashes and horizon warnings. Never use
  HTTP status alone as permission to Save. Personal-event clashes can be merely
  informational, so the booker's fresh agenda remains an independent veto.
- Complete live no-Save execution now reaches fresh approved validation and
  verifies the original intact. Match dropdown accessible names (raw text has
  an aria-hidden `place` icon). Select the room after fresh revalidation; unchanged
  time fields do not reliably trigger a check. Single-event edits retain an
  unused `[1]` recurrence default on other weekdays; exact single mode, event ID
  and timestamps establish scope. Stop and preference drift prevent Save.
  See `docs/room-upgrades.md` for behavior and recovery details.
- Activated the source and private phone build `20260915-room-upgrades` through
  the existing idle phone task under the Booker lock. Exact HTTPS deployment,
  session/preferences and rendered controls passed; no route or hostname changed.
  Existing desktop windows remain open and need reopening for the new controls.
- The first bounded live edit retained all 18 observed event IDs, dates, times
  and durations, changing only the selected room. Its Save response differed
  from the check schema; the pending receipt correctly stopped further changes
  and the queued agenda refresh independently reconciled it as applied.
  The observed Save reply omits only `forms`; its success, event ID, resolution
  and rule evidence remain mandatory, followed by independent persisted readback.
  The final validator also passes against the captured live acknowledgement.
- A second bounded live edit completed normally, without reconciliation. Both
  upgrades appear in the deployed phone My Week; two full-agenda comparisons
  confirmed only the intended room changed and all 18 events retained their
  dates, times and durations. Both receipts are verified with zero pending.
  Different-time edits are proven by intercepted Chromium fixtures, not by
  these two same-time live edits. Phone rendering was verified in PC Chrome,
  not on a physical iPhone. Slow grid scans cannot begin an edit after the
  upgrade phase deadline; an already-started Save still finishes verification.

## 2026-09-14 complete desktop calendar booking lists

- Month, fortnight, week and three-day cells render every existing event;
  the non-interactive `+N more` label and height-based event limit are removed.
  Potential plan chips no longer consume the booking display allowance.
- Booking names and day headings wrap to the cell width. Rows grow to fit
  content and the outer calendar scrolls, including when the pointer is over
  a booking or day surface. Reservation click/Enter detail actions are retained.
- All 25 focused desktop/calendar/settings checks pass, including nine events
  plus a potential plan at 760/1040/1200px across all four day grids. Owned
  fixture renders from `tools/render_calendar_bookings.py` verified busy days,
  narrow headings and retained bookings on closed dates. Existing desktop
  sessions, live bookings/settings and phone hosting were preserved; reopen
  the desktop app to load the change.

## 2026-09-14 permanent phone hostname repair

- The permanent phone origin is `https://lox-pc.tail89d19b.ts.net:10443/`, served through the existing Tailscale route to `127.0.0.1:8794`. The universal contract in `C:/Users/Lox/.codex/AGENTS.md` supersedes historical `windows-t8v5137` guidance below. Never rename the shared host or move a route/runtime to repair Booker; an owner-requested migration is required to change the contract.
- The former server configuration and PWA were still bound to the obsolete Windows-name origin: the new hostname served a shell but session creation rejected its Origin. Both are now aligned to `lox-pc`; isolated shell build `20260914-lox-pc` passed offline/install validation and was deployed through the existing `AsimutBooker_Phone` task. Its obsolete Codex executable path was refreshed to the installed executable before restarting. Keep the allowed tailnet identity and every other configuration field intact during an origin repair.
- The actual private HTTPS session now returns 200 with bootstrap data, the exact deployment verifier passes, and Chrome renders Today with the saved schedule. Booking/settings/history files and independent desktop/booking processes were retained. No booking/cancellation was submitted, and physical iPhone verification was not performed. The previous shell/config are retained under local app data `PhoneServiceRepair/20260914` for recovery; do not commit private configuration or generated builds.

<!-- USER-BROWSER-COMPUTER-POLICY -->
## Chrome plugin and Computer Use

- Chrome plugin use and Chrome browser control are allowed at will for the user's tasks; no separate request or permission is required.
- Computer Use (native desktop/app control) remains prohibited unless the user explicitly asks for it in the current prompt. Chrome plugin permission does not authorize Computer Use.
- Do not infer Computer Use permission from a task needing a GUI, an application or webpage being mentioned, an existing session, or permission in an earlier prompt. Use Chrome plugin tools, commands, scripts, APIs, connectors, or direct file operations where appropriate; if Computer Use is essential, explain the limitation and ask before invoking it.

Updated at the user's request on 2026-09-12.
<!-- /USER-BROWSER-COMPUTER-POLICY -->

<!-- USER-UI-DESIGN-POLICY -->
## UI and app design: standing user requirements

- Apply these requirements to all UI/app design work and all agents, in this repository and its delivery targets. Use the application's own visual language: inspect its current screens, colour/theme tokens, typography, spacing and reusable controls before designing. Do not invent a new palette or visual identity unless the user asks for it.
- Make interfaces simple, coherent and well organised around the user's tasks. Keep the default surface concise; remove filler, repeated explanations, implementation jargon and decorative panels that do not help the next action.
- Give every number a clear label, unit and scope. Explain percentages, probabilities, scores, sample sizes and estimates in plain language; distinguish an estimate from a confirmed fact, and missing data from zero. Keep detailed calculation/method copy off the default surface.
- Put short explanations behind a small, adjacent, hoverable question mark. Reuse the app's help component; support keyboard focus and touch as well as hover, with dismissible, viewport-bounded help. Prefer one or two short sentences. Keep essential errors, costs, destructive consequences and required decisions visible rather than hiding them in a tooltip.
- Make the immediate next action obvious and close to its item. Use explicit, state-appropriate verbs such as Import games, Import & prep, Open Prep or their domain equivalent. Separate acquiring data from opening already-ready content; expose progress, cancellation, failure and retry beside the action. Do not make users hunt through unrelated screens to begin their task.
- For a substantial new interface or redesign, map every affected surface and state first, then present three genuinely different SVG/Figma prototypes within the existing app style unless the user specifies another count or has already chosen a direction. Include setup, main views, details, settings, help, loading, empty, error, progress, cancellation and relevant confirmations, plus narrow/mobile layouts where applicable. Do not present one attractive main screen as the complete design.
- Make prototypes concrete, reviewable and editable; show them to the user and label invented example data. Honour the chosen design and subsequent feedback consistently across all affected surfaces. Once the user says to implement a direction, proceed without asking for the same approval again. Small fixes within an approved design do not require a fresh three-option exercise.
- Verify rendered layouts and the real interaction path, including action wiring, help behaviour and narrow widths. Fix overlap, clipping, unclear labels and state inconsistencies. Preserve active sessions, unsaved edits and existing data. Clearly distinguish prototype/source/test evidence from deployed or physical-device verification.

Adopted as cross-repository user guidance on 2026-09-08. Project-specific architecture and safety rules still apply; these requirements describe design and delivery, not authorization for unrelated actions.
<!-- /USER-UI-DESIGN-POLICY -->

# AsimutBooker

## 2026-09-14 Phone-style desktop My Week

- My Week uses a phone-style time column and labelled cards: blue for personal
  reservations, the existing amber palette for college events, and dotted blue
  outlines for unbooked plans (including explicitly labelled extensions).
  Reservation identity determines the colour; titles are not used to infer
  lesson categories. Booked and planned sessions share chronological ordering.
- Exact booking-detail callbacks, closure/off-day labels, freshness warnings
  and target summaries remain available. Wrapped labels remove their resize
  bindings when destroyed, preventing errors during repeated view refreshes.
- All 16 Quiet Focus/Open canvas checks pass. `tools/render_my_week.py` verifies
  isolated 760/1040px renders, long titles, chronology, dotted outlines, booking
  actions and unavailable/stale/off/closed/empty states using invented data.
  Existing desktop sessions and live state were preserved; reopen the desktop
  to load the change. The phone app and its deployment are unchanged.

## 2026-09-12 Availability clock boundaries and completed coverage audit

- Rolling availability durations now advance in elapsed UTC time and convert
  back to London time. Each scanned date resolves its own local offset; clipping
  and duration arithmetic use UTC. Previously a 24-hour request could span
  23/25 actual hours across a clock change and omit a valid following-day gap.
  Skipped/repeated wall-time boundaries without an offset fail closed.
- Availability returns `window_elapsed` when the requested interval expires
  during scanning. The prompt distinguishes this from confirmed empty coverage,
  preventing a slow scan from establishing false current availability.
- Five new offline regressions and the full 938-test suite pass. Two focused
  Luna/high/standard turns distinguish elapsed and empty scans without retries;
  the model suite now has 84 scenarios. No new Terra inference was used.
- The reliability report maps the user's requested examples to saved model
  traces and real-handler/planner tests, and records the model decision and
  evidence limits. Source/evaluation work is verified; existing hosts and live
  state were preserved. Reload hosts to activate these changes. No claim of
  exhaustive language reliability, live mutation proof or measured Terra Fast
  production latency is supported by this audit.

## 2026-09-12 Conversation continuity and ambiguous cancellation

- A focused Luna/high/standard evaluation exposed a real semantic failure:
  "one of my bookings" caused cancellation of both afternoon matches before
  clarification. The prompt now requires the selected set and its size to match
  the user's intent before cancellation; a non-empty selection is identity
  evidence, not permission to cancel every match. This is a prompt repair, not
  a deterministic natural-language authorization guarantee.
- Eleven additional scenarios bring the reusable suite to 83. Eight initial
  conversations used 14 model turns; seven passed after reviewing two overly
  narrow wording checks. Five final ambiguity/clarification/plural/comparative
  checks used seven turns and all passed. Replacement failure and uncertain Save
  preserved the original reservation. No Terra inference turns were used.
- The evaluator now persists future plans and cancelled reservations across
  messages, computes adjustment/remainder results from saved state, invalidates
  selections per turn even for identical text, and grades setup mutations.
  Production pure plan validation is reused; all effects remain synthetic.
- All 933 offline tests passed. Evidence and limitations are in
  `docs/assistant-reliability-2026-09-12.md`. Production remains Terra/medium/Fast;
  existing hosts, live bookings and preferences were preserved. Hosts need to
  reload Python to activate the revised prompt contract.

## 2026-09-12 Focused Luna tests and Terra Fast production

- Production now explicitly requests Terra/medium/Fast, including the Codex Fast
  feature flag. Model labels derive from these shared constants. The evaluation
  CLI defaults to Luna/high/standard and explicitly disables Fast in that path;
  overrides are process-local and never change account-wide Codex settings.
- Fourteen new scenarios bring the suite to 72. Twelve initial and six final
  Luna standard turns passed, with 17.8s/19.4s median turn times. No Terra inference
  turn was run; a real app-server startup accepted the production model/reasoning
  and Fast configuration without a user message. Do not treat that as measured
  production latency or a served-tier receipt. Details and synthetic evidence:
  `docs/assistant-reliability-2026-09-12.md`.
- The weekday veto now handles plurals, negative/preserved weekdays, exclusions
  within ranges and quoted examples. It no longer abandons exclusions after an
  open-ended boundary. Seven demonstrated unsafe variants have offline coverage.
  Qualified dates and session-specific exclusions must not become whole-weekday
  exclusions; the finer date/time selector still owns those scopes. This remains
  a limited contradiction veto, not natural-language mutation authorization.
- All 926 offline tests passed after the final changes, including real tool
  handlers, calendar constraints and start/resume configuration checks.
- Existing hosts, bookings and settings were preserved. Reopen/reload hosts to
  activate the new contract and Terra Fast setting. Earlier model comparisons
  below are historical; their inherited/default production-tier notes are
  superseded by this explicit user-requested setting.

## 2026-09-11 Cancellation weekday contradiction guard

- An incorrect weekday interpretation exposed a gap: exact reservation identity
  and verified-absence checks validated the selected booking, not whether its
  date contradicted the weekday in the user's request. The earlier 54-case suite
  did not pair an empty requested day with a plausible neighboring booking.
- `assistant_calendar.py` computes weekday facts for every covered agenda date
  and selected event. Its bounded named-weekday veto runs over every validated
  cancellation target before any worker or blackout write, including ID-selected
  and mixed batches. It supports named lists/exclusions and simple weekday ranges;
  range boundaries remain semantic interpretation, not authorization by regex.
- Agenda context includes independently freshness-filtered practice-room closure
  dates. An unreadable catalog does not hide a valid agenda. Missing/old closure
  evidence never implies open rooms, and practice closures do not invalidate an
  existing reservation or establish recital-venue closure.
- For an empty requested day, the assistant explains confirmed closure and asks
  before using a nearby booking, naming its real weekday/date. Conflicting
  weekday/date wording requires clarification. Four real-model synthetic checks
  pass, including a Wednesday/Thursday variant and a genuine Sunday reservation;
  separated-day and rolling-exclusion cancellation checks also pass. The suite
  now has 58 model scenarios. All 923 offline tests passed after the final change.
  Evidence is in the reliability report.
- Existing desktop/phone hosts and live booking state were preserved. Hosts must
  reload Python to activate this guard; the prompt contract also changes so stale
  semantic context is refreshed. This work does not restore a cancelled booking.

## 2026-09-11 Assistant scoped requests

- Assistant `update_booker_preferences` now exposes the shared exact-date time
  validator. Dated booking requests use strict dated windows and preserve global
  defaults; clearing a dated window restores inheritance. Combined patches are
  atomic, reject duplicate dates and retain unrelated settings.
- `find_availability` reuses the existing read-only scan worker in an owned
  temporary directory. It clips dated/rolling windows against completion time,
  reports unknown date coverage separately from empty results, and returns
  observed room gaps rather than claiming personal booking eligibility.
- Initial verification: 48 isolated tool/window/worker tests passed. No live
  reservations, preferences or sessions changed. Existing desktop/phone hosts
  need to reload Python to expose the new assistant contract; broad model
  evaluation and further reliability work follow this milestone.

## 2026-09-11 Scoped read-only room scans

- `--check-dates` is valid only with `--check-only`. Phone and assistant scan
  workers now visit only the requested room-grid dates within the live horizon,
  while retaining the complete agenda scan and existing booking safeguards.
  Out-of-window dates remain explicitly unavailable, never silently empty.
- Verified 96 isolated CLI, renderer, worker and tool tests, including separated
  dates, full agenda coverage and zero grid scans outside the live window.
  No live scan, booking, settings write or service restart was used as a test.

## 2026-09-11 Assistant reliability evaluation

- The evaluator now covers 54 request/failure/clarification scenarios and records
  turn latency. `--model`, `--effort` and `--service-tier` change only its process;
  production still uses Terra/medium and retains its inherited service tier.
  All evaluation tool effects are synthetic; production handlers and scan paths
  are separately verified against temporary files and mocked site access.
- Dated synthetic targets/windows use production validators and persist across
  reads. Scoring excludes commentary and permits equivalent safe requests, such
  as querying all rooms before reporting the requested room. Actual scope errors,
  missing rebooking protection, failed mutations and timeouts remain failures.
- Audit found rolling-range exclusions could include an event just beyond the
  seven-day timestamp boundary. The prompt now requires host-filtered rolling
  candidates before exclusions, and explicitly names the daypart selector.
  All five focused Terra retests passed, including those repairs, empty
  availability, failed prerequisites and uncertain Save handling.
- All 917 offline tests passed, followed by 111 focused checks after scoring and
  reporting refinements. The final-prompt 22-case comparison passed 22/22 on
  Terra/medium and Luna/high/Fast; Luna/xhigh/Fast passed 21/22, omitting one
  remainder explanation. Respective median turn times were 14.6/16.8/20.9s.
- Luna/high/Fast's complete 54-case run passed 52 contracts (18.6s median): one
  omitted remainder explanation and one rejected availability argument followed
  by successful recovery. Requested actions and final scopes were correct.
  Retain Terra/medium: cheaper published API rates do not establish equal quality
  or measured Codex account savings, and Luna was not faster in this comparison.
  See `docs/assistant-reliability-2026-09-11.md` and its synthetic evidence.
- Terra's corresponding 54-case run passed 53 contracts (16.0s median), with the
  same recovered availability error. The filter now uses the established
  `minimum_block_minutes` field name, aligning tool vocabulary with preferences.
  All three availability retests passed on both models; Luna also passed a fresh
  remainder-explanation retest. The final 917-test suite passed after alignment.
- Model labels now derive from the shared model/reasoning/tier constants. The
  evaluator supports `--output` for a JSON artifact with live progress. Model
  overrides remain process-local. Existing desktop/phone sessions and live
  settings/bookings were preserved; reopen/reload hosts to use the new contract.

## 2026-09-11 Published phone closure crosses

- `ClosedDayCross` supplies a non-interactive SVG X behind phone month/fortnight
  cells, week/three-day tracks, Plan cards, focused-date headings and My Week.
  Dates remain selectable and existing bookings remain readable and clickable.
  Plan includes closed dates even when no checked plan exists. Booking-off dates
  keep their neutral treatment and do not receive a red closure cross.
- `tools/check_calendar_closures_ui.py` now derives closure dates through the
  actual catalog-to-phone snapshot path using sanitized Asimut responses, rather
  than injecting closed dates. Chromium/WebKit checks pass in all five modes at
  320/390/844px, including booking details and navigation. Calendar preference and
  planned-date UI checks, 19 Node tests, TypeScript, lint and static validation
  also passed. WebKit month/Plan renders were inspected; the desktop design
  folder contains `phone-weekend-closures.png` (recorded closure data, example
  retained booking and isolated settings; not physical-device proof).
- Published `20260911-closure-crosses`, retained old hashed assets, and restarted
  only the idle phone task after checking assistant/cancellation/system jobs,
  unresolved requests, mutation receipts and the Booker lock. The running API
  now returns both 12 and 13 September; actual private HTTPS deployment checks
  and JS/CSS hashes passed. Close/reopen an existing phone client to load the
  new shell; PC windows and drafts were preserved.
- Before any future phone restart, validate `load_phone_server_config` while the
  old service is still running. The pinned Codex desktop hash path had expired
  after an update and prevented startup; this deployment repaired only that
  local configuration value via `resolve_codex_executable`. Keep the origin,
  identity allow-list and all unrelated settings intact when resolving it.

## 2026-09-11 Calendar month navigation

- Desktop Previous/Next now use calendar-month arithmetic. Adding 30 days and
  snapping to day one kept August (and other 31-day months) stuck; backward
  navigation from late March could also skip February.
- Reproduced five failing cases through the actual native buttons, then verified
  32 focused native/calendar tests. Coverage includes 28/29/30/31-day months,
  year boundaries, displayed month labels and Today recovery. Existing desktop
  sessions and drafts were preserved; reopen the PC app to load this fix.

## 2026-09-11 Actual College Closed events in Calendar

- Asimut's red full-day blocks can be College Closed category events, separate
  from `closed_hours` opening-hours metadata. Catalog discovery now reads the
  room agenda across the display window in one GET (three-second deadline),
  resolves active closure categories by live name/status, and caches only their
  room/time intervals alongside opening-hours closures. Event titles, red colour,
  ordinary bookings and cancelled categories cannot establish closure.
- Calendar closure coverage applies to every practice room, excluding the exact
  promoted recital venues in `PROMOTED_LOCATION_NAMES`. Those optional venues
  can have different hours while the AHC practice-room group is closed. Cached
  schema, policy freshness, booking eligibility and reservations are unchanged.
- Authenticated read-only metadata/agenda requests verified all 29 AHC practice
  rooms closed on 12 and 13 September, with partial closures only on Monday 14.
  The display cache was enriched under its lock without changing its policy
  timestamp. Requests using saved cookies must retain the browser user agent;
  Asimut can report unauthenticated when a generic API client changes it.
- Verified 900 Python tests, then one additional real-cache-to-Tk regression in
  all five Calendar modes. The sanitized response fixture reproduces both the
  missing-event and promoted-room failures. `tools/render_calendar_chrome.py
  --catalog <snapshot>` renders derived dates without mocking closure results;
  wide/narrow renders were inspected and `verified-weekend-closures.png` in the
  desktop design folder records the result (other calendar content omitted).
  Existing desktop/phone sessions were preserved; reopen the PC app to load the
  detection change. No live booking or preference changes were made.

## 2026-09-11 Centred navigation and closed-day crosses

- Desktop navigation is centred against the full window with equal side columns;
  it stays centred on a second row when the brand and navigation cannot fit.
  Geometry is verified at 760, 1040, 1200, 1920 and 3440px.
- Confirmed whole-day closures now have a red diagonal X across the entire day
  in all five Calendar modes. Month/day cells retain readable closure text,
  direct date editing and existing booking links. Booking-off dates remain grey;
  closure evidence is derived by the shared catalog rules documented above.
- Calendar columns have equal widths and day rows grow into the scroll area
  for labels/bookings. Avoid flushing Configure events during calendar rendering:
  that caused recursive rendering and duplicate day cells when changing views.
- Verified 33 focused native/calendar/catalog tests and the full Open canvas
  fixture renderer. Wide navigation and narrow/month/three-day/plan closure
  images were inspected using owned test windows. Reproduce with
  `tools/render_calendar_chrome.py`. No live preferences, bookings, running
  sessions or phone deployment changed; reopen the desktop to load the fix.

## 2026-09-11 Open canvas desktop implementation

- The user selected option B in `docs/design/2026-09-10-desktop-refresh/`.
  The desktop now uses the phone palette, horizontal navigation, bounded content,
  full-width Today summary and six-group Settings hub. The phone is unchanged.
- `open_canvas_ui.py` owns the native theme, responsive column, bounded HelpTip
  and embedded detail pages. Existing preference controllers remain responsible
  for validation and saving. Editors stay mounted when navigating away; explicit
  Save/Cancel closes them. Calendar view selection also survives navigation.
- System, Activity, room/strategy/date editors, scans, history and support tools
  remain available through the new shell. Existing desktop windows must reopen
  to load it; do not restart sessions or booking workers during verification.
- Booking details now offer direct confirmation/cancellation through the shared
  exact-reservation engine. `desktop_cancellation.py` reserves a durable record
  under an OS lock before dispatch, blocks duplicate/uncertain requests, and
  exposes read-only outcome review. Its non-daemon worker finishes verification
  after the UI closes. The record and operation files are local and ignored.
- `desktop_operation_worker.py` wraps manual/agenda/plan runs in the shared
  cooperative operation control. Stop writes a per-run marker; it never kills
  a process during Save. Output-draining threads survive UI closure and avoid
  posting to a destroyed Tk root. Existing runtime/mutation guards still apply.
- My Week retains confirmed bookings on off/closed dates, labels unbooked plans,
  and shows unavailable/stale evidence explicitly. Calendar modes, Settings,
  Assistant, tools and editors were checked at 760px; Rooms stacks at narrow
  widths. Quick Settings controls disclose automatic saving.
- Verified all 894 Python tests, followed by 30 focused native/operation checks
  after final editor teardown cleanup. `tools/render_open_canvas.py` renders
  owned fixtures and checks controls at narrow widths without controlling a
  live application or browser. Representative renders were inspected; the
  example-data contact sheet is `docs/design/2026-09-10-desktop-refresh/implemented-overview.png`.
  This is source/fixture evidence; live bookings, preferences, phone deployment
  and existing desktop sessions were not changed for verification.

## 2026-09-10 Phone-inspired desktop design review

- `docs/design/2026-09-10-desktop-refresh/` contains three editable SVG desktop
  proposals: A Quiet desktop (light sidebar), B Open canvas (top navigation),
  and C Week workspace (calendar home with adjacent detail). All retain the
  phone's current Quiet Focus colours and system typography. The user selected
  and authorized B on 11 September; implementation is recorded above.
- Each option has 29 example frames covering main pages, all calendar modes,
  compact settings and editors, system tools, setup, progress, cancellation,
  errors/recovery, confirmations, 760px narrow help/settings and 1040x740
  Settings. All values are invented. `index.html` is the comparison gallery;
  the main SVGs and `comparison.svg` link to local static review frames.
- All 87 frames passed text bounds/overlap, control-obstruction, palette and
  local-link checks and rendered directly with Sharp. The home screens,
  comparison/contact sheet and representative detail, calendar, settings,
  narrow-help, scan and strategy renders were inspected; all 106 gallery
  references resolve. This is static design evidence, not implemented form,
  hover-help, booking, deployment or physical-device interaction.
- No Computer Use or Chrome control was used. Only design documentation and
  artwork changed; live application source, sessions, preferences, booking
  records and deployment were preserved. Future implementation must retain
  draft persistence, deterministic settings and all verified-mutation controls.

## 2026-09-10 crossed-out booking-off dates

- Calendar dates whose saved state is Booking off are now crossed out on both
  phone and desktop. The phone applies this consistently in month, fortnight,
  week, three-day, plan and focused-day headings; desktop day cells and plan
  headings update immediately for saved or drafted day changes.
- Booking-off dates keep the existing neutral shaded treatment. Independently
  confirmed whole-day room closures remain red, crossed out and explicitly
  labelled Practice rooms closed, so a preference is not presented as site
  closure evidence. Existing reservations on an off date remain visible.
- Verified all 880 Python tests, 19 phone Node tests, TypeScript, lint, static
  build validation, and isolated Chromium/WebKit calendar rendering at 390px.
  The WebKit render was visually inspected. Tests used temporary preferences;
  no live booking or preference write was made. Existing desktop sessions must
  reopen to load the source change.
- Published private phone build `87bddeb-calendar-crossed` after confirming the
  assistant and Booker runtime were idle with no active system job, pending
  mutation receipt or unresolved phone request. Existing hashed assets and the
  running phone server were retained. Private HTTPS deployment verification
  passed; physical phone display remains separate user-side verification.

## 2026-09-10 distinctive phone icon

- The phone companion now uses an ImageGen-created calendar and open
  practice-room emblem in the existing Asimut blue, navy and confirmation-green
  palette. Apple touch, favicon, 192/512 px and maskable icons are versioned in
  metadata while the private application URL remains unchanged.
- The phone service worker cache key is bumped so an opened client fetches the
  new icon resources. Source/build and served-hash verification do not prove an
  existing iOS SpringBoard web clip refreshed; iOS may require removing and
  re-adding that clip at the same URL.
- Source commit `048193d` is published. The icon-only static deployment retained
  the running phone server and existing hashed assets; version
  `20260910-icons` and the touch-icon hash matched over loopback/private HTTPS
  with zero active system operations or unresolved reserved requests.

## Local checkout

- The canonical Windows checkout is `C:\Users\Lox\Desktop\repo\AsimutBooker`.
- `C:\Users\Lox\Desktop\Development\repo\AsimutBooker` is a compatibility junction to the canonical checkout so existing launchers and path-based integrations continue to work.
- The shared launcher is `C:\Users\Lox\Desktop\repo\.dev-tools\Start-DevApp.ps1`; the regenerated Asimut Booker shortcut lives under `C:\Users\Lox\Desktop\Dev Apps` and uses the canonical checkout directly.

## Milestone documentation

- Agents must update this `AGENTS.md` after every meaningful, verified milestone and include that update in the same milestone commit.
- Record concise, durable context: important behavior or architecture changes, decisions and their rationale, relevant tests or verification, deployment or runtime state, and material limitations or follow-up work.
- Update or replace stale guidance instead of accumulating contradictory history; keep notes factual and useful to future agents.
- Do not record secrets, credentials, personal data, raw transcripts, routine command logs, or transient debugging noise.

## 2026-09-10 Full-horizon display plans

- Read-only plan generation now selects suitable sessions from every freshly
  observed free interval in the live booking window, even when the first booking
  edge is beyond the configured immediate foresight period. These sessions stay
  explicitly waiting/not booked and include when booking starts opening.
- Runtime booking decisions retain the existing foresight filter. A distant
  display candidate cannot suppress or authorize a current Save, and every
  booking still requires fresh live revalidation at its exact horizon edge.
- Verified all 880 Python tests, 19 phone Node tests, TypeScript and lint. A live
  `--plan-only` scan made no reservation changes and produced a complete
  three-hour Thursday plan from two sessions beyond the immediate horizon. The
  existing phone shell renders the refreshed snapshot via its file-change SSE;
  no server restart or static rebuild was required. Physical phone display was
  not inspected.

## 2026-09-09 Immediate confirmed booking publication

- Verified create/extension receipts patch their exact positive event into the
  display agenda and notify immediately, before later scans or scheduled boundary
  waits. End-of-run history suppresses notifications already attempted by that
  process. Display/notification failures do not undo verified success.
- The agenda patch preserves unrelated events and the full scan's observed time;
  it does not make older agenda evidence appear freshly scanned. Invalid identity
  or an unavailable/out-of-window snapshot never fabricates a complete agenda.
- Phone SSE checks agenda/plan file changes at its existing twelve-second
  heartbeat and emits replayable snapshot requests, including external scheduled
  runs. Existing phone clients handle this event; no shell rebuild is needed.
- Verified 135 focused booking, receipt, agenda, extension and phone tests, plus
  the final five publication regressions (136 distinct tests). Tests use temporary
  snapshots and mocked notifications; no live reservation or notification tests.
- Reloaded only the owned phone task after acquiring the Booker runtime lock and
  checking idle assistant/cancellation/jobs, zero unresolved requests and zero
  pending mutations. Waited for the old listener and server lock to release.
  Private HTTPS deployment verification and the authenticated live update stream
  passed. Retained the current phone shell and its assets. Scheduled runs load
  the Python changes on their next start; physical notification timing remains
  untested, and no live booking was changed for deployment verification.

## 2026-09-09 My Week plans grouped by date

- My Week uses one chronological date list for agenda events, closed dates and
  plan days. Each date shows its existing bookings followed by its planned
  sessions and target summary; plan-only days and no-session reasons remain
  visible. The separate bottom Planned practice list is removed. Stale-plan and
  unavailable-agenda notices remain visible; potential sessions stay unbooked.
- Verified 19 Node tests, TypeScript, lint, static build validation, and isolated
  Chromium/WebKit grouping and cancellation checks. The grouping check covers
  multiple sessions, plan-only dates, unavailable agenda, stale plans, closures,
  and 320/390/844px widths; the WebKit render was inspected.
- Published static build `20260909-planned-dates` and verified private HTTPS.
  Old hashed assets and running sessions were retained; no server restart or
  live booking/preference writes. Physical phone interaction remains unverified.

## 2026-09-09 Phone desktop tools and Settings parity

- The chosen Desktop companion layout now includes grouped, dedicated Settings
  pages for automatic scheduling, manual background/PC-browser runs, deterministic
  login repair, agenda/plan refresh, availability scans with date/room/duration
  filters and CSV, history, exact event conflict choices, protected-time reopening,
  sanitized logs, supported advanced rules, old-log cleanup and setup information.
  Practice editors and system detail pages hide unrelated groups while retaining
  their drafts across navigation. Calendar timelines show potential sessions.
- `phone_operations.py` reserves durable UUIDs before dispatch and persists job
  state across phone reconnects. Jobs coordinate with the assistant/cancellation
  refresh lock and each worker uses the global Booker runtime lock. Reused IDs
  never dispatch twice; unconfirmed worker exits remain reserved for review.
  The last successful scan survives subsequent runs and failed scans.
- `operation_control.py` adds scoped progress and cooperative Stop hooks. Stop
  prevents the next Save, while a Save already entered completes its verification.
  Long runs request Stop after 20 minutes; the host retains ownership until the
  worker exits and never force-kills during Save. Normal runtime contention now
  returns exit code 6, consistent with scheduled/read-only modes.
- All system routes retain private identity/session/CSRF checks and a fixed action
  allow-list. No remote shell, arbitrary file path or credential form is exposed.
  Schedule changes reuse the exact Agent UAC helpers and verify registered state.
  Config uses the Booker's shared supported-schema validator. Event/config/history
  writes check revisions under their document locks; cleanup is limited to listed
  inactive dated Booker logs older than 48 hours. Logs expose known status
  categories, not raw browser/server output. Credential setup stays PC-local.
- Verified 871 Python tests before final retention hardening, then 78 focused host
  regressions; 19 Node tests, TypeScript, lint and static build validation.
  `tools/check_phone_system_ui.py` checks every tool page, confirmations, stale
  saves, scan CSV, run reconnect, failed Stop, lost responses and 320/390/844px
  geometry in Chromium/WebKit. Existing calendar/settings/interruption checks
  also pass. Rendered Settings, scan, rules and confirmation pages were inspected.
  Fixtures use temporary files and intercepted APIs; no live bookings, task
  changes, cleanup, credentials or preference writes were used for testing.
- Published privately as `e3c0f2d-phone-pc-parity`, retaining old hashed assets.
  The actual Tailscale HTTPS hostname and compiled origin passed
  `verify_phone_deployment.ps1`; all ten new/extended read-only endpoints were
  checked on the running host. Publication used fresh idle assistant/cancellation,
  zero unresolved requests/pending mutations, and the global runtime lock.
  No live booking, preference, scheduling or cleanup action was used as a test.
- Windows scheduled-task Stop is asynchronous. Before restarting the phone task,
  wait for the old loopback listener to disappear and the old server lock to be
  released; starting immediately can exit against the stopping instance's lock.
  Restart only the owned phone task. Existing PC windows and drafts were retained;
  Calendar controls load when the desktop opens. Physical phone tapping remains
  separate user-side verification.

## 2026-09-08 Shared calendar dates, times and complete practice preferences

- Phone Calendar is a persistent fifth destination with month, fortnight, week,
  three-day and plan modes, date navigation, day/bulk booking toggles, target
  overrides, and per-day default/custom/any-time choices with strict mode.
  Drafts survive navigation; atomic revision checks reject stale saves, and
  uncertain delivery requires a read before another Save.
- Desktop calendar cells and the plan headers open the same deterministic
  date editor; Edit visible days supports scoped multi-selection. It saves only
  edited fields, preserving distinct times/targets when toggling several dates.
- `date_time_preferences.py` validates full exact-date windows independently of
  the global preference. Normal booking, horizon discovery/ranking, fallback,
  extension and capacity calculations resolve each date from the original
  global default. Overrides enter the prepared-Save guard and plan fingerprint.
  A changed day rule stops a prepared run before its next Save. Planning future
  dates still cannot expand the live Asimut booking horizon.
- Phone preferences now include all room requirements/session controls and the
  complete booking strategy via shared validators. `help-tip.tsx` provides
  bounded keyboard/touch/hover help in the existing theme.
- Verification: all 838 Python tests passed, plus 19 phone Node tests, TypeScript,
  mobile Chromium/WebKit day/bulk persistence and conflict checks at 320/390/844px,
  all five calendar modes, and complete room/strategy controls. Isolated calendar
  and help renders were visually inspected; no live preference/booking writes.
- The subsequent phone-tools milestone completes the remaining application
  surfaces. Preserve open desktop drafts when loading these Calendar controls;
  private publication status is recorded in that milestone.

## 2026-09-08 Phone capability parity design review

- `docs/design/2026-09-08-phone-parity/README.md` maps the desktop controls to
  current phone support and proposed phone equivalents, including Calendar,
  complete preferences, system jobs, scans, events, activity and maintenance.
- Three editable SVG proposals retain Quiet Focus: Desktop companion (separate
  My Week/Calendar), Combined calendar (agenda inside Calendar), and Calendar
  home (Calendar/Assistant/Manage). Each has 30 example screens with setup,
  editing, loading, empty, error, progress, cancellation and confirmation states.
  Desktop companion was chosen: keep separate My Week and Calendar tabs.
  The user also requested easy day selection and per-day times on both devices.
- The static generator and isolated renderer check all 90 individual screens
  for text bounds/overlap and produce overview/narrow-help PNGs. Checks passed;
  overview and narrow-help renders were visually inspected. This is design
  evidence only; no app source, live settings, bookings or deployment changed.
- Full delivery needs shared revision-checked preference validators and scoped
  authenticated host jobs, preserving runtime locks and durable request IDs.
  Maintenance needs explicit sanitized file/config schemas before exposure.
  Secure credential changes remain in the masked PC setup; reconfirmation still
  uses Asimut on RWCMD Wi-Fi. Future calendar dates never widen live eligibility.

## 2026-09-08 Focused System details page

- `system_details_ui.py` groups desktop operations into full-width Automation,
  Run manually, and Health checks sections with collapsed Troubleshooting.
  Calendar, Activity, and schedule-settings navigation are no longer duplicated
  here. Plan refresh remains in Troubleshooting; shared plan display state is
  retained for Today and Calendar. All actions use existing host callbacks.
- `docs/design/2026-09-08-system-details.svg` is the visual reference with example
  status values. Text wraps to available width and the page scrolls when needed.
- Verified 100 focused desktop, plan, and health tests, including real Tk button
  geometry at 1040x740 and 1200x820, long status text, expanded tools, manual-run
  dispatch, and progress visibility. SVG preview was visually inspected; native
  window capture was inconclusive. No live booking or settings writes were used
  for verification. Existing desktop sessions must reopen to load the change.

## 2026-09-08 Unified deterministic desktop Settings

- The desktop has one Settings page; the separate Advanced preferences page and
  assistant-backed preference shortcuts are removed. Daily targets and preferred
  times use their existing validated direct-save controls. Dates, rooms, strategy
  and automatic scheduling open deterministic editors; support links remain on
  the same page. Settings never opens or prefills the assistant.
- The current design reference is `docs/design/2026-09-08-compact-settings.svg`
  (example values), superseding the original tall unified-settings design.
  Six sections use a compact two-column grid, 13px controls and 12px card padding.
  All groups fit without scrolling at 1040x740 and 1200x820, including expanded
  custom times in the isolated checks. Long content/errors retain scroll fallback.
  Settings errors occupy space only when present. Wheel scrolling consumes the
  event so spinboxes and comboboxes cannot also change their values.
- Verified 85 focused desktop tests, including real controls writing to temporary
  settings, editor cancellation, support navigation, assistant-draft preservation,
  and no-scroll geometry at 1040x740 and 1200x820. Isolated window captures were
  visually checked. No live preferences or bookings were changed for testing.
  Reopen the desktop app to load the change; existing sessions were preserved.

## 2026-09-08 Booking requests without a duration

- The assistant resolves an omitted duration from the requested date's saved
  target, then the saved default, without requiring the word "usual". Explicit
  durations take precedence; "too" does not copy another date's override.
  Existing reservations count toward the total, and only the requested date
  is enabled when necessary. The existing bounded booking flow remains in use.
- Verified 132 assistant tests and two real-model evaluations with synthetic
  tools: omitted duration used the three-hour default, and a dated three-hour
  override took precedence over a two-hour default. No live booking was made.
  Reopen the desktop app to load the instructions; existing sessions were
  preserved. The existing contract fingerprint refreshes stale model context.

## 2026-09-08 Compact desktop assistant progress

- The Tk assistant shows one progress card per request with up to three curated
  lines driven by actual agenda refresh, tool use, and answer streaming events.
  Repeated tools update the same stage; reasoning, commentary, and routine
  protocol activity no longer create transcript cards. Failed/blocked actions
  and clarification requests remain visible outside the progress limit.
- An indeterminate thinking indicator runs while busy and stops on completion,
  error, clear, or panel disposal. This is presentation-only; execution,
  model settings, persisted answers, and phone behavior are unchanged.
- Verified 65 assistant UI, desktop integration, runtime, and protocol tests
  using the repository virtual environment, including withdrawn Tk regressions.
  Reopen the desktop app to load this change; existing sessions were preserved.

## 2026-09-08 Closed practice days in both calendars

- Room metadata retains explicit `closed_hours`; legacy catalog caches without
  this optional field remain readable. Asimut selects closure data by
  `current_date`, so discovery reads each date through the live booking horizon
  (at most 31 days ahead), with a 12-second total budget and 1.5-second per-call
  deadline for the extra display requests. Missing/changed room identities or
  failed date reads never imply closure or block the proven booking policy.
- Shared `closed_practice_dates` requires continuous explicit closure of every
  practice room over the full 07:00–23:00 calendar timeline. The 11 September
  correction adds closure-category events and excludes promoted recital venues.
  Empty, partial, or older-than-24-hour evidence never marks a date.
  These annotations do not change saved booking preferences or reservations.
- Desktop month/day and plan headers turn red with strike-through. Phone My Week
  shows red crossed-out date headings and a closure label, including dates with
  no events. Existing events remain available. Both use the same derived dates.
- Verified all 816 Python tests, 19 phone Node tests, TypeScript, lint, static
  build validation, withdrawn Tk rendering, and isolated Chromium/WebKit mobile
  rendering (`tools/check_calendar_closures_ui.py`). A live read-only refresh
  confirmed dated closure intervals for 31 rooms; no fully closed date was
  reported in the current booking window. Desktop reopening is required.
- Published the private phone build as `calendar-closures`, preserving older
  hashed assets. Restarted only the owned phone task after verifying no active
  assistant, cancellation, unresolved request, pending mutation, or Booker
  process. Private HTTPS deployment verification passed, and the running API
  exposes `agenda.closed_dates`. Existing desktop sessions were preserved.

## 2026-09-08 Direct phone cancellation

- My Week and booking details offer a deterministic Cancel booking action.
  The authenticated, CSRF-protected `/api/v1/reservations/cancel` route refreshes
  the live agenda and requires the displayed positive event ID plus exact
  room/date/start/end to match one reservation before dispatching cancellation.
- `phone_cancellation.py` uses the existing typed cancellation engine directly,
  without starting a model. Receipts, persisted absence verification and
  no-rebook blackouts remain shared with assistant cancellations.
- Request IDs are durably reserved before work; duplicate requests never replay.
  The route returns 202 and runs an owned background job, waiting automatically
  behind an existing refresh; new refreshes yield to the queued cancellation.
  Uncertain outcomes remain gated for review, while failed preflight checks
  settle as rejected. Review cannot clear an active or queued operation.
- `cancellation.progress` SSE events and bootstrap state carry the active job
  and terminal result across reconnects. The phone shows a spinner and exact
  booking details with curated opening/cancelling/verifying stages emitted by
  the real engine; it never forwards raw logs or fabricates timed stages.
  Closing the client does not cancel or replay its server-owned job.
- Verified with 92 focused Python tests plus 24 cancellation-engine tests, mobile Chromium/WebKit direct-button
  checks, TypeScript, lint, Node tests and static build validation. UI checks use
  isolated intercepted requests; no real reservation was cancelled for testing.
- Build test shells outside `phone/dist-phone`, which is served live. Coordinate
  shared-file edits and deployment with concurrent agents; preserve old hashed
  assets when publishing so already-open phone sessions can finish loading.
- Intercepted browser tests must block service workers, especially for reload
  coverage, so the worker cannot bypass test routes and load an older live shell.
- Published the queued progress UI as `45cc2c8-cancel-progress` after confirming the assistant was idle and
  no Booker process was running, then restarting only the owned phone task.
  `verify_phone_deployment.ps1` passed against private HTTPS. A subsequent
  explicitly requested live cancellation completed through the phone endpoint;
  absence and persisted no-rebook protection were checked. A redundant host
  blackout write reported a warning despite the subprocess having saved it;
  phone success reporting now rereads exact persisted blackout coverage.
  Physical phone tapping remains user-side proof.

## 2026-09-08 UX audit: desktop draft preservation

- `load_booking_days` merges refreshed settings into existing Calendar controls,
  retaining edited current and future dates and updating untouched dates and
  their comparison baseline. Save and Discard explicitly reload saved values;
  display refreshes do not expand the live booking window or write preferences.
- A failed Booker process launch now captures its error before the delayed Tk
  callback runs, so the actual failure is shown instead of a callback NameError.
- Both failures were reproduced in isolated regressions before patching. All 77
  focused desktop preference, Calendar, assistant integration, and plan tests
  passed. Existing GUI sessions require reopening to load the desktop fixes.

## 2026-09-08 UX audit: phone edits and interrupted requests

- Settings stays mounted across phone navigation. Date edits are staged per
  date and saved together, so selecting another date does not discard a draft.
  Loading can be cancelled; timed-out or uncertain saves require a fresh read
  before another Save. Existing atomic revision checks remain in force.
- JSON requests have finite deadlines covering response bodies. Quick operations
  use 15 seconds; live planning allows 16 minutes for the backend's 15-minute
  bound. Writes never retry automatically. Assistant delivery retries keep the
  exact original ID/text and preserve a newer composer draft.
- Stop checks HTTP acceptance; late reset replies and older same-generation
  snapshots cannot overwrite newer streamed state. Action failures display once
  without falsely reporting a connection failure.
- `tools/check_phone_ux_ui.py` covers stalled loads, interrupted saves, delayed
  delivery, exact-ID retry, rejected Stop, reset ordering, and stale snapshots.
  Mobile Chromium and WebKit pass, along with the settings editor checks,
  19 Node tests, TypeScript, lint, and static build validation. These are isolated
  checks, not physical phone interaction or live booking mutations.
- Runtime action-cap/plan-isolation tests now stub the saved-settings reader.
  Three tests had read real pending extension records from the checkout and
  failed against their fixture room catalog; all three now pass in isolation.
- Manual phone Refresh now explains when the Assistant is busy instead of
  silently returning. The audit report is `docs/UX_AUDIT_2026-09-08.md`.
  Full Python discovery passed 772 tests, including the concurrent soft-time
  policy, followed by the policy task's final 773-test pass. Phone regression
  checks use no live preference writes or reservations. Static publication was
  verified on private HTTPS without restarting either the GUI or phone server.


Automated booking system for Royal Welsh College of Music and Drama (RWCMD) practice rooms via Asimut.

## Project Overview

This tool automatically books music practice rooms on the RWCMD Asimut system before other students can claim them. It runs through Windows Task Scheduler, requests wake-from-sleep on AC or battery, and books rooms as they become available. Actual wake behavior depends on Windows, firmware, and hardware support.

### Key Features

- **Autonomous Login**: Reuses persistent browser state first, then recovers an expired Microsoft 365 session with a Windows Credential Manager password and the local RWCMD SMS bridge—without an AI model
- **Room Preferences**: GUI ordering, exclusions, live instrument/type/feature requirements, minimum block length, and fragmentation policy
- **Live Room Policy**: Refreshes the current AHC catalog plus selected promoted All Locations rooms, metadata, per-room horizons, and booking-window cutoff from Asimut before every authenticated booking or check run
- **Scheduled Execution**: One non-overlapping task runs every 15 minutes (07:13-21:58) with AC/DC wake-timer requests and missed-start recovery
- **RWCMD Booking Rules**: Six hours of advance reservations, one peak hour/day Mon-Fri 9am-4pm, a five-hour short-notice exception, and the greater of the configured/default same-room gap and Asimut's fresh minimum
- **Agenda Scanning**: Detects existing events/classes to avoid booking conflicts; extracts room names for same-room gap enforcement; distinguishes "Reservation" events from classes for accurate quota tracking
- **Cancelled Event Filtering**: Ignores cancelled events (strikethrough/red styling) when scanning agenda
- **GUI Control Panel**: Desktop application for monitoring, manual control, preferences, and automatic-schedule repair
- **In-App Assistant**: ChatGPT-style Codex chat pinned to `gpt-5.6-terra` with medium reasoning; the host refreshes the complete live Asimut agenda before every prompt, then Terra interprets the active request against that fresh context, chooses typed application actions, and emits visible concise progress summaries
- **Phone Schedule**: The private PWA performs a real agenda-and-plan refresh when Today or My Week opens and every five minutes while it remains open; manual refresh bypasses the short server cooldown, and last-checked agenda/plan data stays visible during failures
- **Health Dashboard**: Shows the last successful run, next scheduled run, saved-session evidence, auth cooldown, pending mutations, and physical wake-test evidence
- **Practice Plan**: Set a default daily target from 0.5-12 hours, override individual dates, or turn dates off across Asimut's current live booking window
- **Daily Foresight**: Ranks the complete fresh room grid across a configurable lookahead, can preserve scarce peak allowance for stronger later sessions, and falls back before an opportunity becomes too risky to lose
- **Booking Plan UI**: Explains ready, waiting, in-progress, and alternative sessions in the dashboard and calendar; hatched blocks are explicitly potential rather than booked
- **Verified Mutations**: A booking or extension counts only after the positive event ID and exact persisted room/date/time survive a reload
- **Cancellation Memory**: Verified cancellations create persistent no-rebook windows, including the full requested daypart for broad cancellations, until the user explicitly reopens that time
- **Manual Reconfirmation Boundary**: Student bookings remain provisional; the user reconfirms them on RWCMD Wi-Fi when Asimut enables the action, and this separate attendance step never gates cancellation, editing, extension, planning, booking, or another supported action
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
- A Today home screen with the next booking, checked-agenda weekly hours, and other events; a My Week agenda and simple Settings page
- A conversational Assistant tab for questions, status, preferences, future practice intentions, bounded booking, and exact reservation cancellation
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

# Faster live-policy and complete-agenda refresh without grid traversal
python book_week.py --headless --agenda-only --wait-for-runtime-seconds 180

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
- **Rolling Quota**: Six hours of advance reservations; live credit returns as sessions finish, without a weekly reset (classes do not count)
- **Free Horizon**: A full interval inside the next five hours can be approved when advance quota is exhausted; any available quota is consumed normally
- **Peak Hours**: Maximum one hour **per day** during Mon-Fri 9am-4pm, including short-notice bookings
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
| `gui.py` | Tkinter controller, preference editors, and detailed monitoring |
| `quiet_focus.py` | Display-only Today and My Week widgets and local-time summaries |
| `quiet_focus_gui.py` | Quiet Focus sidebar, Settings, booking details, and controller adapter |
| `assistant_ui.py` | Responsive, thread-safe assistant transcript, progress cards, and composer |
| `assistant_runtime.py` | Persistent Codex conversation host and typed Booker-tool adapter |
| `codex_chat.py` | Exact-model Codex App Server protocol bridge and event stream |
| `assistant_context.py` | Strict sanitized context from app, settings, agenda, plan, rooms, health, receipts, and history |
| `assistant_tools.py` | Allow-listed question, refresh, preference, plan, booking, and cancellation tools |
| `assistant_plans.py` | Complete-range dated practice-target validation and persistence |
| `booking_blackouts.py` | Strict persistent no-rebook windows created by cancellation and removed only by explicit reopen actions |
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
- `--agenda-only` performs the authenticated live-policy and complete-agenda
  refresh, then exits before room-grid traversal or any booking mutation. Its
  bounded runtime-lock wait prevents a concurrent scheduled pass from being
  misreported as a successful refresh; unresolved contention returns nonzero.
- `--plan-only` performs the same authenticated fresh-policy and complete-agenda
  validation, traverses the live date window, and replaces only the display
  snapshot. It never creates, edits, or deletes an Asimut event and cannot be
  combined with scheduling, target timing, or mutation limits.
- The in-app assistant uses a long-lived hidden `codex app-server --stdio`
  process and fails closed unless the server confirms exact model
  `gpt-5.6-terra`, medium reasoning, no approvals, and a read-only/no-network
  sandbox. Shell, filesystem, browser, web-search, and subagent tools are not
  exposed. Terra owns semantic interpretation and selects the typed Booker
  operation; the host injects the complete active user message as immutable
  provenance, then independently enforces freshness, identity, one-use selection,
  receipt, and persistence checks before any state change.
- Every accepted assistant prompt first runs the host-controlled `--agenda-only`
  preflight and supplies that newly validated snapshot to Terra. A failed
  preflight is explicitly non-authoritative: cached absence can never justify a
  "no reservations" answer or an agenda-dependent mutation.
- Assistant context explicitly allow-lists sanitized settings, agenda, plan,
  room, health, receipt, and history fields. Credentials, cookies, browser
  storage, OTPs, bridge tokens, participant arrays, and arbitrary files are not
  read. Schedule and site text is untrusted data, never instructions.
- Codex agent-message phases remain distinct end to end: commentary is transient
  progress, while only `final_answer` content enters the assistant bubble and
  durable transcript. Public SSE deltas preserve their boundary whitespace;
  raw model reasoning is never exposed.
- Future intentions become complete exact dated targets for every day in a
  1-92-day range, which the ordinary scheduled Booker then pursues subject to
  live availability and all existing quotas and safeguards. Materially vague
  quantities require one conversational clarification. The assistant's direct
  Booker invocation is limited to one plan-selected action; it cannot promise
  an exact start time unless preferences deliberately constrain the planner.
- Cancellation discovery returns fresh exact reservation identities and a
  one-use opaque selection handle. Terra may select one reservation, explicit
  event IDs, an exact date/daypart, the rolling next seven days, or all upcoming
  reservations; the host never guesses the user's language and cancels only the
  unchanged identities Terra selected. A broad cancellation persists its full
  requested time window after the first verified success so a later scheduled
  run cannot recreate another booking there.

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
  overlays and room labels are normalized. `overview_geometry.py` shares the
  SVG row-boundary and time-axis interpretation between availability and clicks.
  It scrolls the actual booking surface, checks event location IDs against the
  row mapping, and hit-tests the freshly exposed point. Unsupported or ambiguous
  geometry stops without clicking; fixed row heights and pixel offsets are not
  booking authority. The current prefilled event URL and delayed time controls are
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
  action, explicit no-rebook-window reopen actions, and bounded reservation
  cancellation selected from fresh positive-ID exact tuples. The host injects
  the complete current user message into every mutation as immutable provenance;
  Terra interprets whether the request is an action, quotation, example, or
  question, while the host independently enforces structural safety.
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
- Assistant mutation authorization has no natural-language phrase gate. It binds
  each typed operation to the host-held active message and rejects model-supplied
  or stale provenance; tool-specific schemas then enforce exact dates, fresh
  identities, one-use selections, and bounded execution. `tzdata` is an explicit
  dependency so Europe/London resolution also works on clean Windows Python
  installations.

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
- The assistant exposes one typed `cancel_reservations` action for at most 64
  exact reservations. Terra first chooses a date/daypart, rolling upcoming
  scope, all-upcoming scope, or explicit event IDs through `find_reservations`;
  the host returns a one-use opaque selection. Every selected target must keep
  its positive event ID, exact tuple, and fresh token. Additions, duplicates,
  stale or reused selections, new-chat carryover, and a second cancellation
  operation in the same turn are rejected before a Booker command starts.
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
  the next booked/potential sessions visible above a ChatGPT-style transcript,
  concise reasoning summaries, typed-tool progress, Stop/New chat controls, and
  a natural-language composer. Booked agenda events and potential plan blocks
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
  grid, completed the ordinary plan-driven scan, made two receipt-verified
  bookings, and exited 0. No cancellation command was repeated during this
  verification.

## 2026-08-31 Fresh Assistant Mutation Grounding Milestone

- The 16:07 phone refusal was stale conversation reasoning rather than a Booker
  gate: the cited cancellation receipt had already been verified for 39 minutes,
  the phone header's fresh snapshot reported zero pending mutations, and the
  assistant turn made no Booker tool call before reusing the earlier failure text.
- Every assistant turn now receives a newly built, file-backed mutation snapshot
  as authoritative application context. It explicitly supersedes prior chat
  claims; a pending receipt requires a current context read and read-only agenda
  reconciliation before a decision. If the journal or provider cannot be read,
  the payload is labelled unavailable rather than fresh and mutation decisions
  remain fail closed.
- Day-of RWCMD Wi-Fi reconfirmation is documented as a separate attendance step
  that never gates cancellation, editing, extension, preferences, plans,
  booking, or any other supported action. Exact identity, receipt reconciliation,
  persisted-write proof, and verified absence remain automatic integrity checks
  and must not be described as user booking confirmation.
- Cancellation progress now says `before cancellation`, rejected typed actions
  use `booker_action_rejected`, and desktop/phone user copy labels persisted
  reservations as booked/current rather than confirmed.
- The complete Python suite passes 656 tests. The focused assistant/context/tool
  suite passes 49 tests, the phone state/copy suite passes 7 tests, lint passes,
  and both the static PWA and Sites production builds complete successfully.
- Commit `f270dd2e2656` is installed in the private phone task; the deployment
  verifier proves its exact task, fresh loopback process, static build version,
  security boundary, and tailnet-only route. A harmless turn through the existing
  durable Terra-medium chat correctly overrode its stale history, reported zero
  pending receipts, and stated that manual day-of reconfirmation never blocks
  cancellation or editing. It made no Booker mutation, left zero unresolved
  phone requests, and the file-backed journal independently reported no pending
  receipt.
- Sites version 2 was saved from standalone phone-source commit
  `be34952daaff70c12201f08c332dbd065b62907a` and successfully deployed to the
  existing owner-only launcher. The sole-owner custom access policy remains the
  deployment authority; the launcher still has no Booker tunnel or mutation
  access and only directs the user to the functional private Tailnet PWA.

## 2026-08-31 Assistant Progress Surface Stability Milestone

- Codex `agentMessage` phase metadata is now retained across streamed and
  completed protocol items. Commentary is emitted as a transient work update;
  only final-answer deltas are rendered and persisted as assistant chat text.
  This prevents mid-turn planning prose from becoming a large assistant answer.
- Phone SSE sanitization preserves leading and trailing whitespace for streamed
  fragments, fixing concatenated text such as `I'llcheck...` without weakening
  control-character or sensitive-text filtering. Generic thread-status churn is
  hidden, and indexed reasoning-summary parts remain separate and bounded.
- The phone progress card has a 42dvh/300px outer ceiling and a 30dvh/220px
  internally scrolling body. It keeps the newest four summary parts, six tool
  updates, and a bounded commentary narrative; raw markdown markers are removed
  for presentation. Its open state changes only at turn boundaries or by the
  user, rather than being forced on every render.
- Transcript scrolling no longer starts a new smooth-scroll animation for each
  reasoning or tool delta. Progress updates follow the internal card viewport,
  reduced-motion preferences disable decorative animations, and commentary
  deltas are coalesced in the desktop UI event buffer as well.
- The complete Python suite passes 660 tests. The phone state/UI suite passes 13
  tests, the focused assistant/runtime/UI/API suite passes 77 tests, lint passes,
  both production builds complete, and the offline phone-build verifier passes.
- Commit `d4bad2e54490` is installed in the private phone task. The deployment
  verifier proves the exact scheduled task, fresh loopback process, synchronized
  PWA assets, anonymous API rejection, and tailnet-only HTTPS route. A live
  read-only Terra-medium turn produced exactly one persisted final answer, no
  commentary transcript entry, zero unresolved phone requests, and no mutation.
- Sites version 3 was saved from pushed standalone phone-source commit
  `33f60a9cc2a9a2124990899ad17967be03a527d7` and deployed successfully to the
  existing launcher. Its custom access policy still contains only the owner and
  no groups; it remains a launcher without Booker tunnel or mutation access.

## Maintenance Notes

## 2026-08-31 Natural Daily Goals and Session Portfolio Milestone

- Clear outcome wording such as "book/get me three hours tomorrow" now
  authorizes the exact dated target write required to fulfil the request as well
  as one bounded date-scoped Booker run. Users never need to restate internal
  implementation language. The evaluator calls the same production
  authorization contract, so model tests can no longer pass wording that the
  installed assistant rejects.
- A dated duration is an aggregate daily goal, including existing reservations,
  rather than one impossible reservation. Targets above Asimut's 120-minute
  session limit are explicitly pursued through multiple non-overlapping ranked
  sessions; weekday peak use remains capped at 120 minutes in aggregate, and
  recurring runs continue pursuing the remaining saved target after the
  assistant's one-action immediate safety cap.
- `select_day_plan` now performs a bounded whole-day portfolio search. It
  maximizes fulfilled target minutes before preferring fewer and better-ranked
  sessions, while retaining overlap, same-room-gap, peak, weekly, and target
  limits. Regression coverage includes the former greedy trap where one
  attractive 120-minute block hid a feasible pair of 90-minute blocks.
- The phone schedule renders the primary plus every additional selected session
  and shows their combined planned duration, so a 120+60-minute plan is visible
  as two potential sessions rather than one incomplete block.
- Production-backed synthetic evaluation now covers formal, concise, polite,
  deferred, and explanation-only three-hour requests in addition to schedule,
  cancellation, multi-day-plan, vague-quantity, and injection cases. No
  evaluator path launches the live Booker or changes local settings.
- The complete offline Python suite passes 657 tests. The phone's 8 Node tests
  and production static build pass, including multi-session aggregation and
  rendering support.

## 2026-08-31 Terra-Led Intent and Persistent Cancellation Plan Milestone

- Natural-language semantics now belong to Terra rather than a host phrase
  parser. The host injects the complete active message into mutating calls and
  exposes only typed Booker operations; it does not ask the model to reproduce
  authorization quotes or special command wording.
- `find_reservations` lets Terra choose one or many exact event IDs, an exact
  date/daypart, the rolling next 1-31 days, or every upcoming reservation in the
  complete fresh agenda. It returns a current-turn, one-use opaque selection;
  `cancel_reservations` revalidates every unchanged positive ID, tuple, and token
  before executing the selected set sequentially.
- Verified cancellations persist no-rebook windows. Exact selections protect
  their exact intervals and named daypart selections protect the full app-defined
  window after the first verified success. All normal, horizon, extension, and
  plan-only paths treat those windows as hard conflicts until a direct
  `reopen_booking_window` action subtracts them.
- Cancellation results distinguish requested protection from successfully
  persisted protection, retain per-target partial outcomes, stop after uncertain
  state, and treat display-plan invalidation failure as a warning after the
  settings mutation has already committed.
- Aggregate dated practice targets count existing reservations and use a bounded
  whole-day portfolio search to select the fewest best-ranked non-overlapping
  sessions under the 120-minute session and aggregate weekday peak limits. The
  recurring 15-minute Booker keeps pursuing any remaining saved target.
- The complete offline Python suite passes 699 tests. The phone passes 15 Node
  tests, TypeScript checking, lint, both production builds, and the offline-build
  verifier. The production `gpt-5.6-terra` medium synthetic evaluator passes all
  19 intent, question, quotation, cancellation, planning, vague-request, and
  injection cases without accessing Asimut or changing runtime state.

## 2026-08-31 Assistant Contract Rotation and Calendar Intent Milestone

- Phone deployment alone is not a valid assistant-contract update: Codex dynamic
  tools are established when a durable thread starts, and resuming an older
  thread can retain its previous prompt/tool registry. Assistant state version 2
  therefore stores a SHA-256 contract fingerprint over the exact combined
  developer instructions, dynamic tool schemas, model, reasoning effort and
  summary, approval policy, thread/turn sandboxes, and fail-closed thread config.
  A missing or changed fingerprint keeps the bounded visible transcript, adds one
  explicit fresh-context boundary, clears the stale pointer, and starts a new
  Terra thread before another turn can run.
- New Chat has a synchronous thread generation. A Send captures its generation,
  plus a unique turn token, while reset increments the generation before
  asynchronous work; stale startup, completion, transcript, and pointer writes
  become no-ops. Controller events are also bound to the exact owned controller,
  so an obsolete process cannot clear a newer turn. The reset boundary is persisted before remote
  `thread/start`, state writes are serialized through the lifecycle/state locks,
  and a failed final pointer write leaves durable `thread_id=null`. This prevents
  an in-flight old turn or disk-write failure from resurrecting the prior thread.
- Cancellation `this week`, `the next week`, and `over the next week` mean the
  rolling upcoming seven-day interval for current-booking requests. On Monday
  31 August 2026 that is 31 August through 6 September, with a half-open
  7 September boundary; already-ended Monday reservations are excluded by the
  host's upcoming-scope selector. Practice-plan `next week` retains standard
  next-Monday-through-Sunday calendar meaning, so its weekend is 12-13 September.
- `find_reservations` now has a first-class inclusive `start_date`/`end_date`
  mode in addition to one date/daypart, upcoming scope, and arbitrary event IDs.
  The host requires both canonical ordered endpoints, at most 31 dates, complete
  fresh agenda coverage for every date, positive reservation event IDs, and no
  more than 64 targets, then issues one opaque current-turn selection. A natural
  clarification answer such as `From tomorrow to September 7` can therefore
  complete the pending cancellation in one selection/batch without restating a
  magic command or making an invalid range call.
- The complete offline Python suite passes 720 tests, including legacy-state
  rotation, prompt/tool/sandbox fingerprint drift, pointer-write failures, the
  in-flight Send/New Chat race, inclusive range coverage, Monday rolling scope,
  and natural clarification continuation. The production
  `gpt-5.6-terra` medium synthetic harness passes all 21 intent and safety cases;
  evaluator-only wording checks accept equivalent human date/duration phrasing.
  Evaluations use in-memory synthetic tools and did not read or change Asimut,
  live bookings, settings, receipts, or phone runtime state.
- Private phone runtime `04a1f8a3b10e` was deployed and independently verified at
  `https://lox-pc.tail89d19b.ts.net:10443/`. The prior v1 pointer
  `01a057fb-54ea-7031-b858-3a68b8ce6700` was replaced by v2 fingerprinted thread
  `01a05985-d677-7863-b1e0-1c5ef9f7d950`. Its persisted app-server metadata has
  `start_date`, `end_date`, `scope`, `days`, and `event_ids` reservation-selection
  fields, exposes only plural `cancel_reservations` plus
  `reopen_booking_window`, and has no legacy singular cancellation tool. One
  explicit no-tool capability probe created the rollout evidence; no booking,
  setting, receipt, or Asimut mutation was used as deployment proof.

## 2026-08-31 Live Prompt Freshness and Phone Schedule Milestone

- The assistant host now performs a complete read-only Asimut agenda preflight
  before every prompt reaches Terra. The fresh result supersedes cached chat
  claims; failure context explicitly forbids treating cached absence as proof
  that no reservations exist or attempting an agenda-dependent mutation.
- `--agenda-only` refreshes authenticated live room policy and the complete
  agenda snapshot without traversing availability grids. Assistant refreshes
  wait up to three minutes for the shared Booker lock, and read-only contention
  returns exit 6 instead of a false-success exit 0.
- The phone `/api/v1/live-refresh` endpoint runs the real typed read-only Booker
  workflow with single-flight protection. Schedule automatically requests a
  fresh plan when opened and every five minutes while visible; manual refresh is
  forced, while chats and live refreshes remain mutually exclusive.
- The Schedule tab now shows reservation count, booked time, potential-session
  count, live refresh progress, and last-checked timestamps. Stale agenda and
  plan content remains visible and clearly labelled instead of being hidden;
  plan staleness alone no longer labels the entire Booker stale.
- The complete offline Python suite passes 728 tests, including the final two
  runtime-lock regressions; the focused final suite passes 133 tests,
  the phone suite passes 16 tests, TypeScript checking and lint pass, both phone
  production builds complete, and the offline phone-build verifier passes.
- Commit `b5d5bfc1f9ee` is installed in the private phone task and independently
  verified at `https://lox-pc.tail89d19b.ts.net:10443/`. The new live
  agenda-only path surfaced current reservations absent from the prior stale
  snapshot, and the following read-only plan run published a fresh eight-day
  phone plan. The final reduced phone snapshot reported ready status, fresh
  agenda and plan artifacts, and zero pending mutations; no booking was changed.

## 2026-09-01 Direct Command Preference Precedence Milestone

- Saved practice preferences are autonomous defaults, not permission boundaries.
  A clear current booking command for a date, daypart, time, room, session shape,
  or amount replaces only the conflicting user-controlled fields and continues
  the requested action without asking whether the older preference should win.
  Live Asimut limits, existing-event conflicts, quotas, identity proof, and
  mutation receipts remain hard safeguards.
- Named dayparts map to the existing strict presets (morning 07:00-12:00,
  afternoon 12:00-18:00, evening 18:00-22:00). Terra atomically saves the dated
  total and relevant preference override, refreshes the plan, and starts one
  bounded date-scoped action. An explicit request to keep the usual preference
  unchanged or make the override temporary still requires clarification because
  no date-scoped time-preference surface exists.
- Preference mutation results now report the effective preset times rather than
  unrelated stored custom-clock values, so subsequent model reasoning and the UI
  receive an accurate applied window.
- Production `gpt-5.6-terra` medium evaluation proves that `Book 2 hours in the
  morning tomorrow` overrides a conflicting strict afternoon window, saves the
  two-hour total, refreshes planning, and invokes one bounded run without a
  follow-up question. The complete offline Python suite passes 730 tests. Three
  extendable-state tests now choose a future test date rather than expiring when
  the wall clock passed their former fixed fixture date.
- Commit `0aaf9b50215f` was installed and independently verified in the private
  phone runtime. The originating direct command was then executed through the
  typed surface: the conflicting strict window and dated target were updated,
  planning selected a morning session, one reservation was positively verified,
  the resulting daily total was complete, and a final read-only agenda refresh
  exposed the new reservation with no pending mutation receipts.

## 2026-09-07 Advance Calendar Planning Milestone

- The desktop Booking Calendar allows every non-past date to be selected or
  deselected, including dates months beyond Asimut's current live cutoff.
  Out-of-window cells are explicitly labelled `Waits for booking window`, and
  bulk selection works across whichever future month or multi-day view is open.
- Advance selections remain ordinary `disabled_dates` preferences. They do not
  expand `booking_dates`: authenticated planning, scanning, and mutation still
  derive their only actionable dates from the freshly observed
  `LiveRoomPolicy.booking_dates(today)` window, so a selected future date is
  ignored until it naturally enters Asimut's live booking horizon.
- Calendar navigation lazily creates edit state and an opening snapshot for
  each visited future date. Save merges only changed dates into the latest
  settings, while Cancel reloads persisted settings and discards both current-
  window and newly created future edits. The focused GUI suite passes 53 tests,
  and the complete offline suite passes 731 tests.

## 2026-09-08 Desktop and Phone Design Exploration

- `docs/design/2026-09-08-interface-options/` contains three explored visual
  directions: Today-first Quiet Focus, calendar-first Week at a Glance, and
  Personal Assistant. Each has desktop/phone main and follow-up screens, SVG
  sources, rendered PNG review boards, and a local comparison gallery.
- The mockups use fictional bookings. The user selected Option 1, Quiet Focus,
  for desktop and phone implementation on 2026-09-08. Planned time
  must remain distinct from persisted bookings, and technical controls remain
  accessible through Settings rather than disappearing.
- Figma file `T00qfnzqnYqRhI88VUUuBT` was created but remains blank: the first
  design access was blocked by the Starter-plan connector quota. Local SVGs are
  importable artwork, not verified native Figma components or prototypes.
- All six paired boards were visually reviewed; the gallery's selection and
  narrow-screen fit were checked. No application code or deployment changed.

## 2026-09-08 Quiet Focus Phone Interface

- The phone opens on Today with the next practice booking, other remaining events,
  and checked-agenda weekly hours. Bottom navigation is Today, My Week, Assistant,
  and Settings. Settings keeps technical health evidence inside System details.
- Booking details retain the college Wi-Fi reconfirmation reminder. Change and
  cancellation shortcuts prepare exact-context assistant drafts;
  they do not submit requests automatically or overwrite an existing draft.
- `phone/lib/today_state.js` calculates display summaries in Europe/London,
  includes in-progress reservations, and excludes classes from practice totals.
  Stale/unavailable agendas stay labelled; planned sessions remain unbooked.
- Both Today and My Week use the existing guarded agenda/plan refresh. The API,
  authentication, mutation verification, and private-origin boundaries are unchanged.
- `ASIMUT_PHONE_OUT_DIR` supports isolated static builds before promotion into the
  existing private runtime. Keep old hashed assets during promotion for open clients.
- Validation: phone TypeScript, lint, 19 unit tests, static/offline-shell checks,
  vinext build, and isolated browser navigation at 320/390/1024px passed. Sample
  screens were visually reviewed; this does not establish physical-phone behavior.

## 2026-09-08 Quiet Focus Desktop and Private Release

- `AsimutBookerGUI` uses the presentation adapter in `quiet_focus_gui.py` and
  widgets in `quiet_focus.py`. Today is the default; sidebar navigation exposes
  Today, My Week, Calendar, Assistant, and Settings. Detailed preferences, system
  controls, and activity remain reachable from Settings. Calendar embeds the
  booking-day editor directly; Plan my practice opens the same page.
- Today/My Week read validated display snapshots. Local minute refreshes reload
  those snapshots; Refresh bookings invokes only the existing bounded agenda-only
  path. Booked sessions, college events, stale evidence, and planned extensions
  are explicitly distinguished. These new widgets never authorize a mutation.
- Native booking details and preference shortcuts only prepare assistant drafts,
  preserving existing composer text. The same live identity and persistence
  checks still govern requests when the user sends them.
- Validation: all 734 offline Python tests pass. Isolated native Tk construction,
  live-snapshot display reads, and all four navigation pages passed at 1040x740;
  no physical desktop/phone interaction is claimed by these checks.
- Private phone build `b434755cc944` is deployed and the exact task, loopback
  process, assets, anonymous rejection, and Tailnet route passed
  `verify_phone_deployment.ps1`. Prior hashed assets were retained for open clients.
  Deployment also refreshed a removed Codex executable path in the private config
  and reloaded only the verified idle phone task. Origin and login were preserved.

## 2026-09-08 Private Phone Address Recovery

- The formerly configured `lox-pc.tail89d19b.ts.net` stopped resolving. Verify
  Tailscale's live `Self.DNSName` before relying on a saved hostname; the local
  Tailnet identity currently reports `windows-t8v5137.tail89d19b.ts.net`.
- Static builds derive the exact private origin from `ASIMUT_PHONE_ORIGIN` or
  the private runtime config, embed that origin in the client gate, and record
  it in build-info. Setup supplies the fresh Tailscale origin before building.
  Never weaken the exact-origin gate or server identity checks to fix a rename.
- Deployment verification now rejects hostname/build/config mismatches and
  requests health over the actual HTTPS address. Prior loopback and Serve-text
  checks alone did not prove DNS, TLS, or phone-path reachability.
- Initial connection failures use bounded retries and a ten-second timeout;
  network recovery or returning to the app retries a disconnected session.
  Access rejection is distinguished from inability to reach the server, and
  recovery never resubmits booking actions or draft messages.
- Validation: 47 phone Python tests, 19 phone state tests, TypeScript/lint, and
  isolated built-UI checks pass. `tools/check_phone_connection_ui.py --dist ...`
  checks wrong-origin rejection, four bounded session attempts, access errors,
  and network recovery without contacting live APIs.
- Build `6aad895fab8d` is deployed at
  `https://windows-t8v5137.tail89d19b.ts.net:10443/`. Verification passed the
  current DNS/config/build match, TLS health, loopback ownership, static assets,
  anonymous API rejection, and Tailnet-only route. A real HTTPS session request
  through Tailscale returned 200 and Booker bootstrap data without any booking
  request. Both deployment scripts also passed PowerShell parser checks.
- An installed shortcut on a hostname that no longer resolves cannot be updated
  by that server. Open the current private URL in Safari and replace the old
  home-screen shortcut; do not claim this migration was performed on the phone.

When modifying this codebase:
- Notebook tab styles explicitly map selected padding to their normal padding;
  Clam's inherited selected inset otherwise shrinks the active tab. Verified
  both styles through isolated Tk style lookups, including focus and hover.
- **Always update `AGENTS.md`** when adding features, changing behavior, or modifying architecture
- Keep the "Key Functions" sections current with new/changed functions
- Document any new booking rules or constraints
- Update the Files Overview table if adding new files

## 2026-09-08 Dedicated Desktop Calendar

- Calendar has its own sidebar destination with month, fortnight, week, three-day,
  and plan views, booking-day selection, reservations/events, and event refresh.
  Settings no longer contains the Practice days shortcut.
- The editor is a persistent notebook page, created on first use. Navigation
  preserves unsaved selections; Save changes resets the comparison baseline and
  keeps the page open, while Discard changes reloads saved preferences. Existing
  calendar shortcuts select this same page without opening a modal.
- The full offline suite passed 734 tests with one obsolete tab-order assertion;
  after updating that assertion, all ten affected integration/Quiet Focus tests
  passed. Focused GUI checks cover navigation, absence of a modal grab, retaining
  edits across tabs, and saving without destroying the page. These checks do not
  constitute physical desktop interaction or phone deployment.

## 2026-09-08 Calendar Tab Selection Repair

- Lazy Calendar construction is handled by `<<NotebookTabChanged>>`, so native
  tabs and keyboard selection initialize the same editor as sidebar shortcuts.
  Sidebar-only initialization previously left the native Calendar tab empty.
- The isolated Tk regression selects the notebook directly, drains the event
  loop, asserts populated calendar content, and verifies revisiting the tab
  does not rebuild it or start a duplicate initial agenda scan.
- Validation: all ten focused Quiet Focus/assistant integration tests passed.
  Full-suite verification was incomplete: a bounded diagnostic run timed out
  after 60 seconds in a Playwright agenda DOM test, outside this UI change.

## 2026-09-08 Assistant Codex Discovery

- The assistant preserves explicit executable/command overrides and PATH
  precedence, then checks `%LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe` on
  Windows. Desktop-launched Python may lack the Codex app's injected PATH.
  Discovery selects the newest executable by modification time, ignores
  incomplete releases, and resolves afresh without pinning a release hash.
- All 15 discovery and protocol tests passed. A live controller started with
  only System32 on PATH and passed the handshake and Terra/medium catalog
  verification without sending a prompt or invoking booking tools.
- Existing desktop/server processes need reopening to load this source change;
  the active GUI was preserved to avoid losing unsaved calendar edits.

## 2026-09-08 Direct Phone Practice Settings

- Your practice now opens inline editors for the daily goal, exact practice dates
  and date targets, preferred times/strict mode, and room order/exclusions. These
  controls previously only prepared Assistant drafts. Save and Cancel remain in
  Settings; a successful save refreshes the displayed preference summary.
- `phone_preferences.py` reuses the existing BookerToolSurface preference
  validators and atomic settings update. GET/POST `/api/v1/preferences` retains
  the private identity/session boundary and requires CSRF for saves. Saves are
  serialized against phone assistant submissions and rejected while it is busy.
  Revision comparison occurs under the settings file lock, rejecting stale forms
  without overwriting PC edits. Unrelated settings are preserved; validation
  errors roll back the entire update. No booking/cancellation is invoked.
- `tools/check_phone_settings_ui.py` exercises all four editors, saved-value
  reloads, Cancel, and stale-save recovery using temporary files and intercepted
  APIs in mobile Chromium and WebKit. Both passed; 119 focused Python tests,
  19 phone state tests, TypeScript, lint, and the private static build passed.
  This is isolated browser proof, not physical iPhone interaction.
- Build `232b0ba9c5b2` is deployed on the current private phone origin. The
  verified idle phone task was reloaded, prior hashed assets were retained, and
  deployment verification passed. An authenticated HTTPS preferences read
  returned 200 with all four sections; no live preference save or booking action
  was performed. Existing phone pages need a reload to load the editors.

## 2026-09-08 Practice Settings Tap Targets

- Daily target and Preferred time summary cards are now real labelled edit
  buttons. The Your practice sliders icon opens a combined editor for goal,
  times, optional date overrides, and room preferences, with one atomic Save.
- Editors focus and scroll their heading into view after loading. Save/Cancel
  stay above the phone navigation while editing long room lists. Daily hours
  retain the input string while typing so clearing does not force a zero.
- Mobile Chromium and WebKit checks now tap both summary cards and the sliders
  icon, clear/retype the daily goal, and save/reload combined goal/time edits.
  All prior editor checks, TypeScript, lint, and the static build passed.
- Build `a2e9f7da5139` is deployed and private deployment verification passed.
  Prior hashed assets were retained; no server restart or live preference write
  was needed. Reload an already-open phone page to receive the new tap targets.

## 2026-09-08 Room Targeting Repair

- Asimut's room legend and SVG booking grid scroll separately. Scrolling the
  legend left lower rooms outside the viewport. Normal and horizon creates now
  use the same grid-scrolling, geometry-derived, unobstructed target; arbitrary
  offset clicks are removed. The legacy renderer scrolls its actual day surface.
- DOM regressions click all 31 fixture rows across three scales, different row
  heights/hour spacing, reordered rooms, and nested scrolling; they also reject
  blockers, mismatched location IDs, ambiguous rows, and missing time geometry.
  All 80 focused renderer, targeting, snipe, and Save-safety tests passed.
- The completed live read-only audit opened exact unsaved forms for all 31
  eligible rooms, including Weston Gallery, Corus Recital Room, and the other
  AHC rooms. Two transient category-menu failures succeeded on a fresh check;
  bounded menu retries now handle that pre-Save condition in both create paths.
  `tools/manual_live/check_room_targeting.py --open-forms` repeats the audit under
  the Booker lock and blocks Save requests. This is form-opening evidence, not a
  claim that any reservation was saved. Scheduled processes load the new source
  on their next run; no phone UI build is required for this backend change.

## 2026-09-08 Same-Time Room Fallback and Clear Readiness

- Normal and horizon creates share `attempt_booking_with_room_fallback`.
  Following a confirmed failure it reopens the canonical overview, proves the
  requested date and complete grid, and ranks untried eligible rooms covering
  exactly the same interval. Each room is attempted once per chain, with a
  three-minute elapsed limit and the existing horizon boundary limit.
- Replacements retain room exclusions, explicit room scope, strict times,
  minimum duration, conflicts, same-room gaps, fragmentation, quotas, and held
  extension capacity. Any pending/unreadable journal or verification uncertainty
  stops fallback. A successful replacement retains its own exact receipt and
  extension identity; normal booking then replans the remaining day.
- `enter_new_booking_form` shares bounded menu handling across both create
  paths. It can retry the visible category item while still on the overview,
  handling an ignored initial click without repeating the grid click or Save.
- Readiness now says "Ready to book: no better room is worth waiting for."
  The internal threshold count is no longer shown as a booking requirement.
  Fallback logs explain the failed room, fresh scan, and next room/time.
- The full offline suite passed all 758 tests, including fresh backup races,
  exhaustion, scopes/budgets, uncertain Save refusal, delayed menus, and actual
  DOM clicks across layout variants. Read-only phone plan refreshes run a new
  backend process and receive the new wording without a phone server restart.
  A completed live `--plan-only` refresh verified the new readiness wording in
  the published plan. No test reservations were created; existing automatic
  scheduling remains enabled and loads these changes on its next pass.

## 2026-09-08 Soft Preferred-Time Distance

- Soft preferences apply the deterministic interval weight
  `2 ** -(hours_outside_window ** 2)`. A session needs at least 15 weighted
  minutes to justify booking; the planner can leave the daily target unfilled.
  With a noon start, 11:00 retains half weight and a 30-minute fallback remains
  eligible; 08:00 retains only 1/65536 and even a two-hour session is rejected.
  These are suitability scores, never random chances retried by the scheduler.
- The defect was target maximization accepting an early non-peak fragment after
  foresight had held capacity for later sessions. Opportunity enumeration,
  portfolio truncation, actual unlocked duration, normal and horizon creates,
  same-time fallback, extensions, and extension capacity now share the policy.
  Time-weighted quality precedes room priority, including cross-date horizon
  ordering; early discovery stops only when an untested room cannot improve it.
- Soft preference selection still enumerates interior gap starts when foresight
  is disabled. Strict windows and disabled time preferences retain their separate
  meanings. Saved preferences and existing reservations were not changed.
- All 773 offline tests passed using `.venv/Scripts/python.exe`. The scheduled
  task and batch launcher were verified to load this canonical checkout; new
  scheduled or read-only refresh processes receive the fix without a server
  restart or phone build. This milestone did not create or cancel reservations.

## 2026-09-08 Booking Selection Edge Audit

- Soft-time scoring now considers the larger of an early start and a late end.
  Enumeration retains useful shorter late sessions. Existing extension targets
  and their capacity holds stop at the best useful end rather than extending
  simply because the site permits it. Strict normal creates also recheck the
  actual bounded interval before entering the form.
- When penalized intervals compete, the day portfolio maximizes weighted useful
  time with a 7.5-weighted-minute cost per session to discourage needless extra
  visits. Filling the daily target cannot override that objective. Search retains
  a feasible preference-ranked fallback and is bounded to 50,000 states/250,000
  transitions; it does not claim global optimality on every large grid. With no
  soft-time tradeoff, the existing exact coverage/session-count behavior remains.
- `resize_opportunity` recalculates overlap evidence in both portfolio selection
  and runtime conversion; weekend sessions never acquire weekday peak costs.
  A day portfolio rejects mixed dates. Extension holds filter removed rooms,
  disabled dates, and expired dates before asking for their live horizons.
- Six failing audit scenarios were reproduced before repair. All 790 offline
  tests passed afterward, including 35 exhaustive small-portfolio comparisons.
  A 990-opportunity grid selected a valid three-hour plan in 0.183 seconds.
  No reservations or preferences were changed by the audit; new runtime
  processes load these backend changes from the canonical checkout.

## 2026-09-08 Preference Changes During Prepared Bookings

- `booking_preferences_guard.py` snapshots user controls for the production CLI
  run. Normal creates, horizon creates, and extensions re-read them under the
  same interprocess settings lock used by preference editors, holding that lock
  only across receipt creation and the Save click. Remote verification and
  runtime progress updates happen after releasing it.
- Changed, removed, or unreadable controls stop before the next Save and return
  exit code 3. This pre-Save stop propagates through room fallback and extension
  handling; it is not labelled as an uncertain remote mutation. Runtime-owned
  extension progress and display-cache updates do not invalidate the snapshot.
  Successful saves preceding a preference edit remain real reservations.
- All 798 offline tests passed, including a preference change after horizon
  form preparation, blocked stale-room fallback, and a concurrent preference
  writer waiting until the Save boundary releases the shared lock. No live
  booking, cancellation, preference write, or service restart was used to test it.

## 2026-09-08 Extension Validation Timing

- Extension edits commit the end-time control with Tab, await the exact trusted
  PATCH `event/event_id=N;type=check` response for that event/date/start/new end,
  finish reading the response, and allow up to five seconds for Save to enable.
  Unrelated or stale checks, failed responses, and a persistently disabled Save
  stop before any receipt. Existing form, warning, identity, preference, and
  persisted-result checks still gate the mutation.
- A read-only live reproduction observed Save disabled at the old check point
  and enabled 250 ms later after validation completed. The repaired path reached
  the pre-receipt boundary with all remote writes blocked except no-Save checks.
  All 804 offline tests passed, including delayed enable, stale response, timeout,
  failed response body, and no-receipt failure regressions. New CLI processes
  load the fix from this checkout without restarting the phone companion.

## 2026-09-08 Extension Recovery and Contention

- Scheduled runs wait up to 180 seconds for an occupied runtime, retaining the
  single-instance lock. After acquiring it they reload and validate preferences
  before fresh site discovery. An exhausted queue returns exit code 6 instead
  of reporting success; invalid settings after waiting return exit code 3.
- Modern and legacy day selection reserve pending extensions' daily and peak
  minutes before selecting new sessions, across horizon, read-only, and normal
  runtime planning. Attaching extension progress therefore does not spend those
  minutes twice or invalidate the display plan by exceeding its daily target.
- All 809 offline tests passed. A bounded extension-only recovery reached the
  existing two-hour target and verified the persisted event before removing its
  completed extension intent. No new reservation was created by that recovery.
