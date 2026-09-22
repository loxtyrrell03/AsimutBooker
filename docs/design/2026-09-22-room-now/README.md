# Find me a room now — design review

Status: three proposals for selection; application behavior is not implemented.
All rooms, availability, times and outcomes in the mockups are invented examples.

Open `index.html` for the gallery. Each editable `option-*.svg` shows phone and
desktop placement; `states.svg` completes **all three** proposals with their
shared setup, detail, settings/help, loading, progress, cancellation, empty,
error and confirmation states. `narrow.svg` shows the controls at 320px and the
desktop at 760px. PNGs are review previews of the SVG sources.

## Options

- **A — Today panel (recommended):** mode, duration and action at the top of
  Today, above the next reservation. One press from the default screen.
- **B — Persistent action bar:** compact duration selector and action remain
  available on Today, My Week and Calendar; expanding the selector shows the
  same duration editor. Reserve layout space above phone navigation and below
  desktop content. Hide the bar while editing, in booking details and in
  Assistant so it does not crowd existing editors or the composer.
- **C — Global button and booking sheet:** a header button opens a focused
  phone sheet / desktop dialog from any primary view. Choose the duration and
  start there; closing preserves the draft and returns focus to the launcher.

## Accepted intent

- On 22 September the user chose **start soonest; get as close to my duration
  as possible** over waiting for a full-duration match.
- The user also requested **Longest possible**, with an explicit maximum
  such as two hours. Show two modes: **Preferred duration** and **Longest
  possible**. The second relabels the duration control **Maximum duration**.
- Proposed presets: 30 min, 1 hour, 1½ hours and 2 hours, with a Custom control
  for legal intermediate durations. Initial example: Preferred / 1 hour;
  Longest / maximum 2 hours. These are prototype defaults, not saved settings.
- A press starts one immediate search-and-book attempt. Clearly state beside
  the action that pressing books a room. There is no routine second approval.
- Find the earliest personally eligible start today. At that start, prefer
  the closest legal duration no longer than the requested duration, or the
  longest legal duration no longer than the maximum; then use saved room rank.
  Both modes may produce the same result at the same bound. Do not invent an
  unexplained quality score or deliberately wait for a nicer room.
- Search from now through today's remaining eligible hours. If the earliest
  start is later, show the exact start and wait in progress and the result.
  Do not invent an arbitrary waiting cutoff or silently search another date.
  If nothing eligible remains today, explain that and offer another check or
  an edit to the duration.
- One contiguous session; no automatic split, cancellation, relocation or
  background watching. A shorter result names requested and booked durations.
  Duration is a ceiling; never overshoot it. Do not return a token-length
  booking below fresh legal and applicable user minimums.

## Shared state and preference contract

The one-off mode/duration draft is separate from daily goals, advance allocation
and automatic-booking settings. Preserve it across local navigation and retain
it after a failed attempt. Returning to a running request shows its current
status; it does not submit again. Per-device last-used controls may be retained
without queuing the preference-save dispatcher.

Use existing room order, exclusions and instrument requirements. Keep conflicts,
strict preferred-time limits, disabled dates, daily limits, manual cancellation
blackouts and manual-edit pins authoritative. If one prevents the requested
session, name it and link its existing editor; do not silently rewrite it.
Soft time preferences must not delay an eligible near-now start. Automatic
booking Off remains Off; an explicit one-off action does not enable scheduling.
If no room setup is usable, open the existing Rooms editor rather than a new
onboarding flow.

Show a help question mark beside duration: “Starts as soon as possible. Books
as close to this duration as it can, without going over.” In Longest mode:
“Starts as soon as possible. Takes the longest available session, up to your
maximum.” Use the existing help components with hover, keyboard and touch,
Escape/outside dismissal, focus return and viewport bounds.

Success requires a verified receipt and newer complete agenda containing the
exact reservation. Show room, date, start/end and actual duration; booking
details retains its existing Asimut/reconfirmation/cancellation controls.
Uncertain Save outcomes expose **Check booking status**, not another Book
button. Stop prevents further Saves but allows in-flight verification to finish;
it never cancels a reservation already saved. A definite no-Save rejection can
try a bounded next candidate within the same request scope.

## Current-source findings and implementation handoff

- `phone/components/quiet-focus.tsx` and `quiet_focus_gui.py` currently route
  Find a room into an assistant draft. The new action needs a deterministic
  shared create-only path, not a reformulated assistant prompt.
- `assistant_tools.py: run_booker` selects a plan action and may upgrade or
  extend; its date/room/duration flags cannot implement this contract alone.
- Reuse fresh policy/catalog, complete agenda, room-grid discovery and the
  guarded exact-create/receipt engine in `book_week.py`. Pure ranking should
  receive an explicit clock and the one-off request, not mutate global settings.
- Reuse assistant/runtime ownership, durable request IDs and cooperative Stop
  from the existing phone/desktop operation infrastructure. Revalidate after
  waiting for another worker. An old request must expire rather than quietly
  book a now-stale session. Preserve uncertain receipts and recover before a
  new mutation; double taps, reloads and reconnects must not duplicate Saves.
- Phone: authenticated CSRF-checked operation submission and status polling;
  Today and global shell share the same operation state. Desktop: normal
  subprocess worker and thread-safe UI updates. No new service or scheduler.
- Current source declares a 30-minute site minimum and two-hour maximum, but
  every live action must verify fresh policy, access, quotas, booking increments
  and applicable minimum length. Prototype selectors are not site authority.
- Keep in-flight settings/composer drafts and existing agenda/details behavior.
  Native PC topbar already wraps at narrow width. Phone bar/dialog adaptations
  must preserve the contained Assistant layout and safe-area navigation.

## Verification before delivery

For this design milestone: parse/render SVGs, inspect text bounds and all boards,
and verify gallery at 390px and 1040px. No live booking calls are needed.

After selection: isolated ranking fixtures for earliest-start precedence,
duration modes, room order, shortfall, expiry, no available room, existing
bookings, strict preferences, disabled dates, protected intervals and quotas;
intercepted Save/receipt/agenda fixtures for success, refusals, uncertain results,
stop and duplicate delivery. Exercise both UI paths at phone 320/390px and a
reduced-height viewport, plus PC 760/1040px. Source, activated service, private
HTTPS and physical-phone evidence must be reported separately.
