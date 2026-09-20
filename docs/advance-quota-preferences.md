# Advance quota preferences

Settings → **Advance quota** is available on desktop and phone. It configures
ordinary advance creates under New/Custom College rules with a daily target and
daily planning enabled. The six hours are rolling credit, not a weekly reset.
Missing settings retain the previous defaults; opening an editor never writes
defaults. Save changes only this settings section. Existing reservations stay put.

## Choices

- **Rooms:** top N of the general room order (default 2), a separate ranked
  selection, or all eligible favourites. Optional fallback appends the remaining
  eligible rooms. General exclusions, instrument/access requirements and actual
  room horizons always apply. A custom rank never makes an excluded room eligible.
- **Periods:** up to 14 ranked, non-overlapping weekday/time intervals on the
  15-minute grid, each at least 30 minutes. Earlier rows rank higher. Prefer mode
  permits alternatives; Only mode admits advance sessions wholly within a listed
  interval and no advance sessions on unlisted weekdays. An empty list uses
  general Preferred times. Custom periods replace general *soft* hours on their
  weekdays; strict general/date hours remain mandatory. Room-first/time-first can
  override the general strategy for advance creates only.
- **Spread:** max-min coverage of useful daily blocks, then larger daily goals.
  The block aim defaults to 60 minutes. Six hours over seven equally eligible
  days yields four 45-minute and three 60-minute blocks, not seven guaranteed hours.
- **Day priorities:** the same fairness calculation with block aims scaled by
  weekday weights (1–10), then coverage relative to weighted daily goals. Hard
  goals and available slots can prevent an exact ratio. Weight 0 excludes new
  advance allocations on that weekday in every mode.
- **Group:** use feasible credit on fewer newly covered days, favouring longer
  daily coverage. Daily targets/caps and the two-hour individual booking maximum
  still apply. Existing practice dates are preferred when useful capacity remains.
- **Quality:** use feasible credit, then maximize the ranked room/time quality
  before daily spread. It can leave some days without an advance allocation.
- **Day limits:** weekday caps count already confirmed practice and extension
  holds. Zero inherits the daily target; it does not mean zero practice. These
  caps do not reduce existing bookings or stop last-minute target filling.
- **Session length:** inherit the general preference or request 30/60/90/120
  minutes as a tie preference. It never overrides peak, minimum, access or target
  constraints. The existing break and room-change preferences remain in effect.
- **Room opening waits:** include observed suitable future openings by default,
  including shorter room horizons. Off considers only already-open opportunities.
  Waiting allocations are plans, not reservations; every actual Save is rechecked.
- **Reserve:** subtract this many minutes from currently available advance credit
  before allocating. Last-minute practice can still consume it normally. This is
  not an ASIMUT exemption or a permanent separate credit account.
- **Last-minute release lead:** optional minutes before session start at which
  routine short-notice practice may spend remaining credit. Blank inherits the
  general strategy. When credit is exhausted, the actual five-hour free horizon
  controls eligibility instead; both booking endpoints must fit inside it.
- **Date order:** nearest/furthest resolves otherwise equal choices, or inherits
  the general booking order.

## Implementation and safeguards

`advance_preferences.py` validates the shared `advance_quota` settings section.
Desktop, phone and assistant use the same scoped validator. Phone revision checks
and desktop section-conflict checks reject overwriting changes made elsewhere.
Preferences participate in plan fingerprints and the final Save drift guard.
Assistant contract revision 9 refreshes stale assistant context.

The allocator compares a bounded menu of feasible daily portfolios under one
credit budget. Custom room/period quality is additive within each day; a bounded
search keeps a feasible fallback on dense calendars. This is a best-effort
planner, not a prediction of future competition or a global optimality guarantee
for arbitrarily dense grids. Existing exact identity, live quota, peak, receipt,
recovery, extension and upgrade boundaries remain authoritative.

Changes affect future advance creates. Extensions, upgrades and last-minute
practice continue to use the general room/time/comfort settings. Lowering a cap,
excluding a weekday or changing room rank never automatically cancels practice.
Scoped manual/legacy modes retain their existing behavior.

## Verification

The approved compact design is recorded in `design/2026-09-21-advance-quota/`.
Tests cover unchanged defaults, the four allocation strategies, room/period
tradeoffs, independent enumeration of 40 small custom-quality schedules, weekday
caps/weights, reserve, strict hours, shorter horizons, atomic persistence and
pre-Save preference drift. Browser fixtures exercise Save/reload/Cancel, invalid
overlapping periods, stale revisions and 320/390px layouts in Chromium/WebKit.
Desktop fixtures use isolated settings and check compact/expanded controls at
760/1040px. These tests make no live booking or settings changes.
