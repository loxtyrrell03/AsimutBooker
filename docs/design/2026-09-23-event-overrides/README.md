# Event overrides: design review

Status: proposed, awaiting a design choice. These are editable SVG prototypes,
not a deployed feature. All event names, dates, rooms and results shown are
invented examples. No settings, reservations or services were changed.

## Proposed behavior

- An event switch reads **Allow practice during this event**. Saving it applies
  to that exact dated occurrence for ordinary automatic booking and room-now.
  The event remains visible in Today, My Week and Calendar, with a **Practice
  allowed** label. This does not cancel the event or notify its organiser.
- A clearly named **Event overrides** entry sits in practice Settings. It offers
  date/search filters and lets the user restore an event as a conflict. Nothing
  is selected by default; existing saved choices remain visible. No title-based
  recurring rule or automatic selection of future lessons is proposed.
- **Find me a room now** keeps its approved duration controls. An **Override
  events** toggle reveals a picker of today's eligible events. Its choices apply
  to **this search only**, and are shown immediately above the booking button.
  The toggle alone selects nothing. It resets after completion, cancellation,
  expiry or reload; an in-flight request retains its exact submitted selection.
- Saved overrides already apply, and are shown separately even when the one-off
  toggle is off. Turning it off clears only the extra choices for this request.
  A saved override is edited through its event details or the manager.
- New controls offer lessons and other college events, never reservations or
  college closures. Room availability, genuine site refusals, limits, protected
  time and existing reservations continue to constrain a booking.
- Saving persistent choices can queue the existing automatic check when
  automatic booking is enabled. The save row says this before submission. Off
  remains Off. One-off choices never save preferences or enable scheduling.
- A successful saved choice is labelled **Saved**, distinct from a verified
  **Booked** outcome. Restoring a conflict affects future decisions; it does not
  cancel an already booked practice session. Show this consequence inline if
  there is an overlapping reservation.

## Three alternatives

| Option | Main interaction | Room-now selection | Tradeoff |
| --- | --- | --- | --- |
| A: Switches beside events (recommended) | Short inline switches on schedule event cards; sticky Save/Cancel for edits | Inline checklist beneath the toggle | Fewest steps for individual lessons; more controls in a long schedule |
| B: Select on the calendar | A selection mode with checkboxes and a selected-events review bar | Toggle opens an event-selection sheet with an Apply button | Efficient for a batch across days; one extra mode |
| C: Event manager | Searchable dedicated page reached from Settings and event details | Toggle opens the same picker in a clearly labelled one-search scope | Best for a long event list; more navigation for a single event |

All three reuse the app's blue actions, white surfaces, amber college-event
cards, blue reservations and dashed planned sessions. The shared state board
applies to all options; each option also has a 320px schedule example. The
desktop board represents the existing PC application, not a new web service.

## Surface and state map

| Surface | Required states and actions |
| --- | --- |
| Today / Also today | Open event details; show saved Practice allowed badge; retain exact date/time/room |
| My Week / Calendar | College events versus bookings and plans; selection controls for the chosen option; multi-date draft; saved badge |
| Event details | Full title/date/time/room; Allow practice switch; Save/Cancel; restore conflict consequence |
| Settings / Event overrides | First use; date/search filter; selected list; unchanged/dirty/saving/saved; automatic-booking state |
| Find me a room now | Toggle off/on; zero selections; saved versus extra choices; collapsed selection summary; duration preserved |
| Help | Adjacent question mark; short explanation; hover, focus, touch and Escape; viewport-bounded dismissible popover |
| Agenda refresh | Loading; unavailable; stale; no eligible events; search with no matches; retain draft when a refresh fails |
| Submission | Changed event/revision; permission/busy failure; retry only after a known non-write; selection review before booking |
| Search progress | Selection locked; Finding / Stop / Stopping; request-specific scope visible |
| Results | Verified booking and actual ignored overlaps; no room; stopped; lost delivery; uncertain result and Check booking status |
| Restore | Stop treating a saved lesson as ignored; existing overlapping reservations remain; no implicit cancellation |
| Narrow / short height | 320/390px phone; 760/1040px PC; contained sheet scrolling; reachable Save/Cancel and navigation |

## Source findings and implementation handoff

The current app already stores exact-occurrence `ignored_events`. Phone access
is buried in `phone/components/system-tools.tsx` (Event conflicts); desktop
access is `gui.py::show_events_dialog`. `phone_system.py::_event_document` and
`events_save` already provide revision checks, atomic preference writes, legacy
key migration and display-plan invalidation. The phone event response currently
omits reservation classification, which a shared picker will need.

`book_week.py::scan_agenda` resolves those settings using `event_identity.py`.
It removes ignored events from local conflict accounting, while preserving
ignored reservations for quota accounting. `room_now.py` uses the shared engine
and saved choices, but `validate_choices`, `RoomNowRequest` and `cli_flags`
currently accept only duration choices. There is no request-scoped event picker.

This is more than exposing a switch: the create editor's conflict-warning checks
in `book_week.py` reject generic conflict text. Explicit time edits independently
reject other agenda events in `booking_time_edits.py`. Inspect normal/prepared
creates, fallback, extensions, upgrades, transfers and explicit edits to make
saved choices consistent. Preserve every actual ASIMUT refusal. Only a proven
soft participant clash whose complete event list matches explicitly selected
events may be treated as an acknowledged clash; never suppress generic warnings,
room occupation, an unselected participant conflict or a disabled Save button.
Read-only live form inspection may be needed to establish the site's distinction.

Recommended implementation boundaries:

1. Share a typed event-choice document and UI model across phone, PC and
   assistant, with exact event IDs and complete identity tuples. V2 keys lack an
   event ID; two distinct events can share a tuple. Resolve legacy choices
   conservatively and require review for ambiguous matches. No fuzzy title match.
2. Add a request-scoped override context to room-now's durable request and worker,
   validated against a fresh complete agenda. The override must reach the tracker,
   exact validation, receipt evidence and recovery without writing preferences.
3. Preserve existing saved choices outside the submitted edit set. Do not silently
   erase legacy reservation overrides; new lesson controls must neither create
   such choices nor permit a duplicate reservation. Make any legacy-policy
   incompatibility explicit before implementation.
4. Revalidate selected identity, eligibility and scope before a prepared Save.
   A changed event, stale revision, cancellation, expiry or unreadable evidence
   stops before a write. Existing ownership, preference guards, site checks,
   receipts, complete-agenda confirmation and uncertain-outcome recovery remain.
5. Persistent event changes use the current preference-run dispatcher; one-off
   selections never queue automatic booking or persist across future searches.
6. Keep event badges/details coherent after reload on every view. Existing saved
   and temporary choices must not collapse into one misleading toggle state.

Future implementation verification should cover distinct simultaneous events,
changed/deleted events, reservation/closure exclusion, saved and temporary
choices together, clearing temporary choices, stale/busy writes, Stop, delivery
loss, exact site refusals, prepared-Save races and recovery. Use isolated fixtures
and intercepted forms, then Chromium/WebKit at 320/390px and PC renders at
760/1040px. This design milestone establishes layout only; it does not establish
real booking behavior, private deployment or physical-phone behavior.

## Files and reproduction

- `make_prototypes.py`: editable vector source and local review gallery generator.
- `option-a.svg`, `option-b.svg`, `option-c.svg`: main PC/phone alternatives.
- `states.svg`: shared complete flow and exception states.
- `narrow.svg`: each alternative at 320px plus 760px PC and short-height guidance.
- `render_prototypes.py`: headless local-only layout verification and PNG exports.
- `index.html`: responsive gallery with full-size SVG links.
- `verification.json`: generated bounds and gallery checks.

Run the two Python scripts with the repository virtualenv. They import no Booker
runtime modules, use no live state and make no booking or preference requests.

Verification: all five SVG boards passed text bounds checks and were visually
inspected. The local gallery loaded every board and full-size link without
horizontal overflow at 320, 390, 760 and 1040px. These checks do not exercise
the proposed app interactions.
