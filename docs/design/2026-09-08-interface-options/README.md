# Asimut Booker interface options

Design exploration only. No application implementation or live booking changes.

## Deliverables

Open `index.html` to compare three directions, switch between desktop and phone,
and view the next step. Each direction includes a 1120 × 760 desktop screen and
a 390 × 844 phone screen, plus a matching follow-up screen for each platform.
The six paired review boards are 1720 × 1080. All booking content is fictional.

| Option | Starting point | Follow-up screen | Best suited to |
| --- | --- | --- | --- |
| 1. Quiet Focus | Today, next practice booking, weekly progress | Booking details | Calm everyday use |
| 2. Week at a Glance | Weekly calendar and phone daily agenda | Practice preferences | Planning around classes |
| 3. Personal Assistant | Conversation, actionable plan card, next booking | Completed preference change | Natural-language control |

Recommendation for selection: Quiet Focus as the visual foundation; the calendar
and assistant concepts can also serve as its other tabs. This is a proposal,
not an approved implementation decision.

## Figma status

The Figma connector created this blank file:
https://www.figma.com/design/T00qfnzqnYqRhI88VUUuBT

The first `use_figma` read was rejected with the Starter-plan MCP tool-call
limit. No frames, components, variables, or prototype links were created there.
The original SVG files in this directory can be imported into Figma as vector
artwork. They are not equivalent to native auto-layout components or a working
Figma prototype. Once connector access is available, build the selected direction
as native Figma screens and verify the result before claiming Figma completion.

## Interaction and copy direction

- Use Today, My Week, Assistant, and Settings consistently across devices.
- Put the user's next practice session and relevant action ahead of diagnostics.
- Move system details, activity logs, advanced booking strategy, and login
  diagnostics into Settings. Surface a short actionable problem when needed.
- Replace Run in Background with Find a room; booking strategy with advanced
  preferences; potential plan with Planned; horizon terminology with available
  booking dates; assistant model names and raw tool events with plain progress.
- Booked means a persisted reservation. Planned always says it is not booked yet;
  calendar plans use a dashed border as well as a text label.
- Keep cancellation, changes, and manual college Wi-Fi reconfirmation separate.
  Reconfirmation advice never disables booking management.
- Show freshness explicitly and preserve the last readable agenda after refresh
  failure. Production error, empty, offline, loading, and keyboard-focus states
  still need implementation design after a direction is chosen.
- Daily goal, days, strict preferred times, and favourite rooms remain visible
  preferences. Advanced controls must stay reachable, not be removed.
- The assistant result's Undo is only a proposal for reversing a date preference.
  It must not promise that a cancelled reservation can be recovered.
- Any future implementation must keep the existing typed actions, fresh live
  checks, exact booking identity, mutation receipts, and runtime coordination.

## Validation

All six paired boards were rendered directly from SVG and visually inspected.
The comparison gallery was checked in the in-app browser: direction selection,
follow-up selection, device selection, correct SVG links, and narrow-screen fit.
Primary and detail screens are visual concepts, not interactive booking UI.
No app regression tests or deployment checks apply to this design-only change.

## Rebuild

Run `python build_designs.py` to regenerate SVGs and theme metadata. Run
`node render_designs.cjs` with the `sharp` package available to render the six PNG
boards. Both scripts operate only on files in this directory. The gallery works
directly as a local HTML file; `render.html` additionally needs a local HTTP
server because it fetches inline SVG for browser inspection.
