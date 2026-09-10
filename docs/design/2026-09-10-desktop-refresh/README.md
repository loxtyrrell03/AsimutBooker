# Phone-inspired desktop refresh

Design review, 10 September 2026. **Awaiting the user's choice; do not implement
these proposals without that choice.** All bookings, dates, preferences, health
readings and results in the artwork are invented examples.

## Three options

| Option | Layout | Tradeoff |
| --- | --- | --- |
| A · Quiet desktop | Light, labelled sidebar; next booking beside today's agenda; compact grouped settings | Closest to the phone and existing navigation. Recommended starting point. |
| B · Open canvas | Horizontal navigation; centred content; full-width details and editors | Most open and visually quiet; less simultaneous context. |
| C · Week workspace | Compact labelled rail; calendar home; contextual details beside the schedule | Best for arranging practice around classes; busier than A or B. |

All three retain Today, My Week, Calendar, Assistant and Settings. These are
complete alternative desktop shells, not three new colour palettes. C changes
the opening destination to Calendar. A and B open Today. The phone is unchanged.

## Source audit and design rules

Inspected the current phone CSS and components (`phone/app/globals.css`,
`phone/components/quiet-focus.tsx`), desktop shell and widgets
(`quiet_focus_gui.py`, `quiet_focus.py`), desktop controls (`gui.py`,
`calendar_preferences_ui.py`, `system_details_ui.py`), and existing design maps.
Also inspected a saved isolated phone My Week render and the desktop System
details design render. These are reference evidence, not a new capture of a
running app. No Computer Use or Chrome control was used.

- Reuse the phone's blue `#0868D9`, pale blue `#EAF3FF`, ink `#1D2430`, muted
  `#667080`, background `#F8FAFC`, border `#E1E6EE`, success `#2B805B`, and
  error `#B73332`. Green and red carry meaning, not decoration.
- Use Segoe UI on Windows, matching the phone's system-font fallback. Keep
  crisp line icons, 16px body copy, compact 14px labels, 32px page titles,
  40px desktop actions, 44px touch actions, and 16–24px rounded surfaces.
- The next booking gets one pale-blue feature panel. Routine rows use white
  space and separators. Avoid nested decorative cards and duplicate controls.
- Settings retain six compact groups and dedicated direct editors. They never
  launch or prefill Assistant. System and activity remain available from Settings.
- A number always has a unit and scope. Booked hours exclude classes and
  planned sessions. Unavailable evidence is labelled, never rendered as zero.
- Solid booking blocks are persisted reservations. Dashed planned blocks say
  “Not booked yet”. Booking-off dates are neutral and crossed out; confirmed
  whole-day room closures are red and explicitly labelled. Both retain events.
- Existing draft retention, exact-date overrides, request identity, freshness,
  verified mutation, Stop and no-rebook protections are behavior requirements.
  A visual refresh must not replace or weaken them.

## Affected surfaces and states (mapped before drawing)

Every option receives the following editable SVG screens. Main and secondary
navigation links lead to static example frames; the artwork makes no API calls.

| Frame | Surface | Important states and controls |
| --- | --- | --- |
| today | Today | Next booking, other events, booked week total, freshness |
| week | My Week | Chronological bookings and unbooked plans, closures and off dates |
| calendar | Month | Selected date, off/closed days, scoped edits, explicit legend |
| timeline | Week | Booked/class/planned blocks, contextual day selection |
| fortnight | Fortnight | Two-week date selection plus selected-day schedule |
| three-day | Three days | Legible time axis and adjacent booking detail |
| plan | Practice plan | Daily target, multiple waiting sessions and alternatives |
| dates | Date editor | Multi-date scope, booking toggle, target/time overrides, draft Save/Discard |
| booking | Booking details | Exact date/room/time, manual reconfirmation, change/cancel |
| settings | Settings | Six compact groups, direct editors, System and Activity |
| practice | Target and times | Default hours, custom interval, strict preference help |
| rooms | Rooms | Ordering, include/exclude, features, instrument/type, block length |
| strategy | Strategy | Foresight, peak preference, fallback, ordering, full supported details |
| system | System | Schedule status/actions, manual runs, health and login repair |
| run | Run progress | Actual named stages, Stop, safe stop explanation, completed booking retained |
| scan | Availability | Date/room/duration filters, scan progress, last results, CSV |
| activity | Activity and history | Curated events, exact booking history, log access, clear-view distinction |
| events | Event conflicts and protected time | Explicit ignore consequence and separate reopening |
| tools | Advanced tools | Supported config, logs, scoped cleanup, setup/about |
| setup | First use / sign-in | Session check, secure PC setup, repair and retry |
| assistant | Assistant | Conversation, one progress surface, composer, Stop and draft retention |
| cancel | Cancellation confirmation | Exact booking, no-rebook consequence, keep/cancel actions |
| cancelling | Cancellation progress | Check → cancel → verify, safe leaving, uncertain-result review |
| feedback | Loading / empty / failure | Separate conditional examples, retained old content, action beside error |
| recovery | Edit and request recovery | Validation, stale edit, uncertain delivery, Stop requested, retained draft |
| confirmations | Other confirmations | Schedule removal, history clearing, cleanup; bounded consequences |
| narrow | Narrow desktop with help | 760px width, bounded help, keyboard focus and retained Save/Discard |
| narrow-settings | Narrow settings | Six groups remain compact at 760px; one-column fallback specified |
| minimum-settings | Minimum desktop window | Compact 1040×740 Settings with all six groups and support links visible |

## Interaction contract

- Native SVG links allow reviewing main navigation, calendar view switching,
  settings drill-down, booking detail and the cancellation sequence. The gallery
  lists every frame. Links change mockups only; buttons do not execute bookings.
- Help opens on hover/focus; click/touch toggles; Escape/outside click dismisses.
  Reuse the phone help pattern with a 44px hit area and viewport bounds. The
  `narrow` frame shows its open and keyboard-focused state; static SVG does not
  execute hover/focus behavior.
- Settings Save applies only edited fields to its stated scope. Cancel/Discard
  is local. Navigating away retains drafts. A revision conflict offers review
  before Save; uncertain delivery requires reading the result, never blind retry.
- Cancellation states retain exact identity and block duplicates. Success
  requires persisted absence. Reopening protected time is a separate decision.
- Stop prevents the next Save. If Save has begun, show “Finishing verification”
  until the verified result is known. Never imply that Stop reverses a booking.
- The 1040×740 Settings frames fit the six groups without scrolling; other long
  pages must scroll within their content region when implemented. At 760px, A collapses to the compact labelled rail,
  B wraps navigation, C replaces the inspector with a detail page. Settings
  retain two columns where labels fit; below 700px they stack, keeping drafts and
  Save/Discard visible. Timeline views may scroll horizontally with a labelled
  selected-day alternative. This is a desktop resizing proposal, not a phone redesign.

## Review and regeneration

Open `index.html` for the comparison and complete frame list. `option-a.svg`,
`option-b.svg` and `option-c.svg` are the main editable mockups. `screens/`
contains the full state set. `comparison.svg` places the three home screens on
one review board. `build_prototypes.py` generates the artwork and validates text
bounds, overlap, palette, screen coverage and local link targets. Use the bundled
Python runtime with Pillow. `render.cjs` uses Sharp via the bundled Node packages
to render the SVGs directly, without opening or controlling a browser.

All 87 individual SVG frames passed text bounds/overlap, control-obstruction,
palette and local-link checks, and all rendered through Sharp. The three home
screens, comparison board, multi-screen contact sheet and representative
settings, narrow-help, calendar, scan and strategy renders were visually inspected.
The native SVG links and gallery references are checked as local files; no
browser interaction, editable form behavior or hover help execution is claimed.
These are design references, not implemented application interaction or deployed
desktop/phone verification. No application source, live preferences, bookings,
runtime sessions or deployment files are part of this milestone.
