# Desktop capabilities on the phone

Design review, 8 September 2026. **Option 1 selected and implemented.** All
screen values, bookings, health readings and operation results are invented examples.
The SVGs remain design examples. Implementation and private publication evidence
is recorded in the repository AGENTS.md; the examples are not live account data.

## Three navigation choices

1. **Desktop companion** — Today, My Week, Calendar, Assistant, Settings. Matches
   the desktop destinations; settings open individual pages. Recommended because
   the request is to make desktop capabilities equally discoverable on the phone.
2. **Combined calendar** — Today, Calendar, Assistant, Settings. My Week becomes
   Calendar's agenda view; settings use expandable groups. Fewer tabs, one extra
   view switch when moving between the agenda and month planning.
3. **Calendar home** — Calendar, Assistant, Manage. Opens directly into a date
   workspace with the next booking, agenda and date controls together. Manage
   separates Practice, System and Activity. Fastest for date planning, but moves
   Today into the calendar and changes familiar navigation most.

All three retain the current Quiet Focus blue, white surfaces, system font,
rounded controls and compact rows from `phone/app/globals.css` and
`quiet_focus_gui.py`. These are information-layout alternatives, not new brands.
Each option has an overview SVG and a complete SVG atlas. Individual screens are
editable SVGs under `screens/`. `build_prototypes.py` regenerates the artwork.

## Capability audit and proposed destination

Source inspected: `gui.py` (menus, calendar, preference editors, activity, event
management, scan results), `quiet_focus_gui.py`, `system_details_ui.py`,
`phone/app/page.tsx`, `phone/components/practice-settings.tsx`,
`phone/components/quiet-focus.tsx`, `phone_preferences.py`, `phone_server.py`,
`room_preferences.py`, and `booking_strategy.py`.

| Desktop capability | Current phone | Proposed phone equivalent / atlas screen |
| --- | --- | --- |
| Today, next reservation, weekly hours, other events | Present | Preserve Today and exact booking detail (02, 09) |
| My Week with reservations, other events, potential sessions and closures | Present as agenda list | Retain agenda; add view switching (03–05) |
| Calendar month, fortnight, week, three-day and plan views | Full calendar absent | First-class Calendar with all five modes plus agenda (03–07) |
| Previous, next, today, arbitrary future planning dates | Partial date editor | Calendar navigation; dates outside live horizon remain planning only (03, 06) |
| Enable/disable dates, select/deselect visible dates, per-date targets | Individual dates present | Multi-date selection, scoped bulk controls, sticky Save/Discard (06) |
| Agenda refresh and plan refresh | Present | Separate action status, cached content retained, stale plan labelled (05, 07, 22) |
| Daily target enabled/default and date overrides | Present | Dedicated target/time editor; date overrides in calendar (06, 11) |
| Preferred-time on/off, presets, custom interval, strict mode | Core controls present | Preserve all controls and show scope (11) |
| Room order and exclusions | Present | Touch/keyboard Up/Down, inclusion, search over fresh catalog (12) |
| Instrument tags, room-type tags, required features | Missing controls; shared validator exists | Full room requirements editor (13) |
| Minimum useful block; allow fragmented sessions | Missing controls | Room/session requirements (13) |
| Reverse date order | Missing control | Book furthest dates first (15) |
| Daily planning enabled, preferred peak interval, desired peak block | Missing direct editor | Booking strategy (14) |
| Hold early peak edge, lookahead, required later choices, fallback lead | Missing direct editor | Booking strategy and waiting details (14–15) |
| After-peak mode and time/room priority | Missing direct editor | Named choices with adjacent help (15) |
| Automatic schedule list, next run, refresh, install/repair/remove | Read-only health summary | Typed host schedule controls, exact status and removal confirmation (16, 26) |
| Run in background / with browser, live progress, Stop | Assistant booking flow only | Owned host job, two explicit execution modes and stop state (17) |
| Six health checks, details and refresh | Present | Preserve independent evidence; unknown stays unknown (16, 18) |
| Check/repair login | No direct button | Invoke deterministic host login workflow; secure credential setup remains PC-local (18) |
| Find available rooms: select dates, scan, stop, date/room/duration filters, CSV | No dedicated scan UI | Read-only scan job and filtered results/download (19) |
| Activity stream; clear displayed output | No dedicated screen | Sanitized activity, clear view only (20) |
| Booking history, refresh, details, clear history | Assistant context only | History browser and separate destructive confirmation (20, 26) |
| Log list, today's log, refresh, open folder | No dedicated screen | Sanitized log list/detail/download replaces Explorer (20, 27) |
| Manage events, scan agenda, respect/ignore individual or visible events | Assistant typed operations | Exact event controls; visible warning that ignoring permits conflicts (21) |
| Config file, old-file cleanup, About | No equivalent screen | Validated supported config fields, scoped cleanup preview, About (27–28) |
| Assistant send/stream/Stop/new chat, intentions and cancellation memory | Present | Preserve draft, same-request retry, progress and explicit reopening of cancelled time (08, 24) |
| Reservation cancellation and Open in Asimut | Present | Exact detail + confirmation, progress, verified result/uncertainty (09, 24) |
| Manual reconfirmation | Asimut handoff | Open in Asimut; still requires RWCMD Wi-Fi when available (09) |
| Exit app / opening Windows Task Scheduler, Explorer or a PC browser window | OS actions | Close phone tab; typed schedule/log views replace OS windows; visible-browser mode explicitly runs on PC (17, 27) |

## Complete screen and state map

Every option includes these numbered screens. Secondary screens inherit that
option's navigation and return to their originating date, settings group or job.

01 connection/setup; 02 Today; 03 month; 04 week timeline; 05 agenda;
06 multi-date editing; 07 plan; 08 Assistant; 09 reservation detail;
10 settings root; 11 daily target and times; 12 room ordering;
13 room requirements; 14 strategy; 15 strategy details;
16 automatic schedule; 17 manual run/progress/Stop; 18 health/login;
19 availability scan/results; 20 activity/history; 21 event conflict management;
22 loading/empty/offline; 23 validation/stale/uncertain save;
24 cancellation/reopen/stop; 25 help at 320px; 26 destructive confirmations;
27 logs/config/cleanup; 28 secure setup/About; 29 fortnight; 30 three-day.

At 320px, the month retains seven 40px-wide date cells; five-tab navigation has
at least 56px per target. Timeline modes retain legible columns via labelled
horizontal scrolling, with a single-day detail beneath. Headers and action rows
wrap, editors scroll above persistent Save/Discard controls, and back navigation
retains drafts. The fortnight overview is a date grid; its selected day opens a
readable timeline rather than squeezing fourteen columns into a phone.

Help is an adjacent question-mark button: hover/focus opens, touch toggles,
Escape/outside tap dismisses, and content stays within the viewport. Error and
destructive consequences remain visible. A reusable phone help control needs to
be added using the existing theme; the inspected phone code has no shared help
popover. SVGs depict its open state; they do not implement interaction.

## Delivery architecture and acceptance

- Calendar display may range beyond the live booking horizon. Only fresh
  `LiveRoomPolicy` eligibility authorizes scans or bookings. A target is not a
  reservation; confirmed, potential, closed, off and unknown are distinct.
- Extend the versioned preferences document with shared room, strategy and
  event validators. Compare revisions inside the existing settings lock; keep
  unrelated fields and all desktop/phone drafts. Do not overwrite stale edits.
- System controls need new authenticated, CSRF-protected, allow-listed host
  endpoints and durable operation IDs. Use structured arguments, never a remote
  shell or arbitrary file browser. Persist job status across disconnect/reload.
- Own the specific subprocess and coordinate with all existing runtime/mutation
  locks. Stop must be acknowledged and reconcile any possible completed Save;
  disconnecting the phone must not replay or silently cancel accepted work.
- Schedule repair/removal runs the existing scoped Windows helper, with fresh
  registered-task verification. Removing the schedule does not cancel bookings.
- Reuse deterministic login recovery. Changing stored credentials continues to
  require the masked PC setup; do not invent a phone password/OTP collection flow.
  Waking a powered-off PC or bypassing campus reconfirmation is not promised.
- Logs/config/cleanup are the largest unresolved backend surfaces. Define a
  sanitized log schema, validated supported config schema and explicit file
  allow-list with preview/revision checks before exposing any writes/downloads.
  Raw logs, private YAML, auth state and active files must never be served.
- Verify each interaction using temporary settings and intercepted APIs in
  mobile Chromium/WebKit at 320/390px and landscape, including keyboard/touch
  help, date drafts, stale saves, job reconnect, duplicate taps, cancellation,
  stop during Save, busy host, denied access, unknown health and cleanup scope.
  Source/browser checks do not constitute physical phone or live booking proof.
- Build outside served `phone/dist-phone`; publish only the chosen, completed
  implementation. Preserve older hashed assets and restart only a proven idle
  owned phone process. Verify actual private HTTPS origin separately.

Implementation order after design selection: calendar + complete preference
parity; read-only activity/scan/health; guarded system jobs and maintenance;
integrated mobile regression checks and private deployment. Scope remains all
audited capabilities, with explicit PC/campus boundaries above.

## Verification for this design milestone

Render the generated SVGs with the repository Playwright installation using
`verify_prototypes.py`. It checks text bounds and creates three overview PNGs
plus the 320px help PNG for visual inspection. These are static design checks;
no product controls are wired and no product test/deployment claim is made.
