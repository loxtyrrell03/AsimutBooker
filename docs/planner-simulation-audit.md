# Planner simulation audit — 20 September 2026

All schedules below are invented fixtures. The tests use no real account,
network or reservation writes.

## Independent comparisons

The reference searches enumerate legal alternatives directly, without calling
the production optimizer or its feasibility helpers.

| Mock line-ups | Comparison | Result |
| --- | --- | --- |
| 160 daily grids | Best weighted useful practice, then fewest sessions; varied peak credit, room gaps, preferred-time fit and fragmentation | Matches exhaustive search |
| 120 small weeks | Best fair daily coverage under one shared quota, with existing practice and different room horizons | Matches exhaustive search |
| 120 competing upgrade sets | Best compatible room improvements, preserving identity, spacing and the 60-minute daily peak cap | Matches exhaustive search |
| 428 progressive openings | Best available legal prefix under three-/five-day horizons; preserve total practice and minimum fallback duration | Matches independent prefix enumeration |

These 828 comparisons establish the stated optima for those finite fixtures.
They do not predict future cancellations or establish a global optimum for all
possible real-world room grids. Large soft-preference searches remain bounded.

## Behavioural and lifecycle cases

| Situation | Verified behaviour |
| --- | --- |
| Same room has 12:00 and 16:00; only 30, 45 or 60 advance minutes available | Chooses the saved preferred peak period; a saved two-hour aspiration no longer disqualifies the feasible shorter block |
| Only Weston 18:00-19:00 remains outside a soft 12:00-18:00 preference | Keeps the useful fallback; an explicitly strict window still rejects it |
| A useful soft-time block is already booked | Counts it toward coverage and allocates the next credit to an uncovered date |
| One date's booking is definitively refused before Save | Keeps its allocation reserved, tries other dates, and does not retry that date in the same pass |
| Definitive failures are slow, or earlier scheduled work consumed the available time | Publishes the plan, leaves time for subsequent worker phases, and continues remaining dates on a later run |
| Quota refusal or uncertain Save | Stops immediately; no attempt on another date |
| Corus is open at seven days; equally suitable Weston opens at five | Secures Corus, forecasts Weston, then permits the same-ID/duration upgrade only after Weston opens |
| A new daily anchor and a room-only transfer compete | Existing extension work and new advance blocks go first; interrupted transactions still recover before unrelated work |
| One peak hour has already finished; ASIMUT reports peak credit again | Refuses another peak booking, including a session crossing 16:00; permits fully off-peak practice |
| Two different rooms hold 30 peak minutes each | Further peak creates are refused across all rooms |
| Full peak usage followed by a free-window worker run | Chooses 16:00-18:00 rather than a second peak session |
| Upgrade would move off-peak practice into a used peak hour | Rejects that upgrade |

The existing extension, consolidation, interrupted-transaction, crowded-calendar
and browser-form regressions also remain part of the full suite. Extensions
cannot skip occupied/forbidden peak time to reach an off-peak tail; their whole
edited interval must satisfy the rules.

Run the independent comparisons and named scenarios:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_planning_oracles tests.test_smart_booking_scenarios
```

Run the complete suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
