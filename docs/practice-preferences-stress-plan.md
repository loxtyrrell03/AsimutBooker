# Practice preferences and changing-week audit

The objective is useful daily practice in the saved rooms and times under real
rolling credit and room horizons. Optional comfort must never reduce an otherwise
feasible primary booking plan. The source audit below uses invented schedules;
production activation and account verification are recorded separately.

## Requirements and evidence

| Requirement | Implementation / required proof |
| --- | --- |
| Preferred block length and rest | Shared validated settings; desktop, phone and assistant controls; persistence and rendered interaction checks |
| Additional useful preference | Fewer room changes, only after primary time/room quality and coverage |
| No lost hours due to optional comfort | Baseline-preserving bounded refinement; independent exhaustive small-calendar comparisons |
| Existing bookings influence breaks | Pass confirmed practice into daily and advance allocation; retain full daily peak accounting |
| Extensions and upgrades remain effective | No new comfort veto on extensions or superior-room moves; bounded equal-quality upgrade refinement checks the full resulting day's breaks and conflicts |
| Fair advance allocation and fast free-window fills | Existing worker sequencing, fresh quota/agenda reads and room-specific horizons; evolving week simulations |
| Messy competing schedules | Deterministic independent simulated service, changing occupancy, cancellations, Save races, returned credit and shorter room horizons |
| Recovery and bounded runtime | Existing real receipt/browser/restart regressions plus bounded dense-grid tests and simulated interrupted runs |
| Production activation | Full relevant regression, built/verified phone controls, protected idle activation, fresh live read-only plan/agenda and unchanged preferences |

New controls default to Automatic / No preference / off. Existing saved choices
are not rewritten. The hard minimum useful block, same-room spacing, strict hours,
daily target, total/peak limits and fragmentation settings remain authoritative.

The comfort refinement keeps the baseline's exact total minutes. Primary
weighted time suitability, preferred peak fit and room quality precede comfort;
block fit, rest deficit, room changes and session count then break ties. It does
not invent availability or replace ready time with a later opening. Hitting its
search bound retains the original feasible plan.

## Verified scenarios

- 100 small independent exhaustive comparisons verify comfort below primary
  coverage/room quality. The earlier 828 independent daily, weekly, upgrade and
  progressive comparisons remain in the full regression suite.
- A dense 22-room, 1,100-opportunity fixture finds three one-hour blocks in the
  best room within a two-second test budget. Exact-length search may terminate
  early only when both primary quality and optional comfort attain proven bounds.
  General bounded search retains the known feasible baseline on exhaustion.
- 38 invented weeks cover three-/five-/seven-day rooms, six-hour rolling credit,
  completed-session credit, one whole-day peak hour, five-hour seeds/extensions,
  classes, 50-85% initial occupancy, competitors winning before Save, cancellations,
  new competitor bookings during the day, and missed runs. One fully occupied
  week makes zero impossible Save attempts. Every simulated write is checked by
  an independent service model; ordinary upgrades retain exact IDs and minutes.
- The actual journal/runtime suites separately cover lost trim/seed responses,
  restart after partial transfer, denied restoration, manual changes, preference
  drift and uncertain outcomes. The weekly service does not pretend to reproduce
  a browser or prove transaction recovery by itself.
- Prepared free-window tests require both interval endpoints, preserve peak
  limits and later extension targets, reject wrong boundaries, and retain the
  final preference/identity/receipt safeguards. Real Chromium with an intercepted
  server prepared before the opening and submitted exactly once after fresh exact
  approval in 163-181ms locally. Live ASIMUT latency/competition is unmeasured.
- Scheduler tests cover 190 five-minute launches, including 64 quarter-hour
  preparations three minutes early, through the final 23:00 edge. Routine upgrade
  and short-notice work use admission budgets; started Saves/recovery are never
  interrupted for speed. A long existing operation or network outage can still
  delay preparation; the worker does not bypass its exclusive mutation lock.

## Illustrative changing-week results

Run `python tools/simulate_changing_weeks.py --output artifacts/week-report.json`.
These are synthetic outcomes, not promised real practice hours.

| Scenario (comfort on) | Booked / requested minutes | Weston/Corus minutes | Extensions / upgrades |
| --- | ---: | ---: | ---: |
| Contested, three-hour daily target | 1,215 / 1,260 | 1,095 | 2 / 7 |
| 85% initial occupancy, four-hour target | 1,380 / 1,680 | 885 | 1 / 7 |
| New competing bookings and 42 missed checks | 1,290 / 1,680 | 960 | 2 / 6 |

The final case sees 75 new competing half-hours, 84 released slots and 16 lost
Save races. All three finish without stale extension intentions. Optional comfort
on/off can lead to different later outcomes as competitors react to availability;
the guarantee is no sacrificed primary value in the current feasible decision,
not global optimality against unknown future bookings. Unfilled targets remain
unfilled rather than being represented as reserved time.
