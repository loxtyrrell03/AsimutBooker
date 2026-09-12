# Focused assistant robustness follow-up — 12 September 2026

Production is configured for **GPT-5.6 Terra, medium reasoning, Fast**. The
evaluation CLI defaults to **GPT-5.6 Luna, high reasoning, standard service**.
The service tier is explicit in both paths; standard evaluations disable the
Fast feature even when production enables it. No fallback model was added.

## Focus and cost control

Added 14 distinct scenarios, bringing the reusable suite to 72. This run used
12 initial Luna turns and six final Luna turns (four retests plus two new
variants), all at standard service. No Terra inference turn was run. A separate
real app-server startup probe accepted the production model, reasoning and Fast
configuration without submitting a user message. This proves configuration
acceptance, not a served-tier receipt or a measured production speedup.

| Luna high, standard | Passed | Median turn time |
| --- | ---: | ---: |
| Initial focused set | 12/12 | 17.8 seconds |
| Final guard checks and held-out variants | 6/6 | 19.4 seconds |

The requests cover whole-day exclusions, “keep” clauses, ranges with exclusions,
AM/PM ambiguity, overlapping versus exact starts, explicit no-action requests,
negated dayparts, corrected durations, impossible duration/window combinations,
hypotheticals, quoted examples followed by a real instruction, unresolved amounts,
the same weekday in different weeks, and preserving one session within a day.

These use the real controller and tool declarations with invented bookings and
in-memory mutations. The clock is fixed to 31 August 2026 in Europe/London.
Timing excludes thread startup and real site operations. This focused result
does not overturn the broader 11 September model comparison or prove identical
quality for all future language. Terra Fast remains the production choice the
user requested; Luna is promising but is not promoted on this small sample.

## Code changes demonstrated by tests

The cancellation contradiction veto previously allowed wrong target weekdays
for plurals, “not Friday”, “keep Wednesday”, an exclusion within a named range,
an exclusion after an open-ended boundary, a negated cancellation followed by a
positive one, and a quoted weekday followed by the real request. Seven failing
offline variants now pass. Whole-day exclusions stay in force across ranges,
and quoted examples do not supply weekday constraints.

The guard also avoids incorrectly treating “not next Wednesday” or “except
Friday at noon” as exclusions of every Wednesday/Friday. Finer date and session
selection remains with the model and exact reservation tools. The helper remains
a bounded contradiction veto, never an authorization parser or booking selector.
No new service, model router, retry layer or general language-parsing framework
was introduced.

All 926 offline tests passed after the final changes (65.8 seconds), including
production tool handlers and start/resume configuration checks.

## Evidence and reproduction

Compact reports are in [evaluations/2026-09-12](evaluations/2026-09-12/). They retain
synthetic prompts, final answers, tool arguments/outcomes and timing; repeated
context and reasoning summaries are omitted. The configuration probe is also
included. Tests use temporary settings and synthetic reservations.

```powershell
# Defaults are Luna/high/standard; each --case selects one focused scenario.
.\.venv\Scripts\python.exe tools\evaluate_assistant.py --case trick_cancel_qualified_week --case trick_cancel_partial_exclusion --output .tmp\focused-luna.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Existing desktop/phone hosts and live booking state were preserved. Reopen or
reload hosts to use the updated guard and Terra Fast configuration. This work
does not claim a live booking/cancellation test or physical-device verification.

The Fast configuration follows the official
[Codex speed documentation](https://learn.chatgpt.com/docs/agent-configuration/speed).
No account-wide settings or billing credits were changed.

## Conversation continuity follow-up

Added eleven scenarios, bringing the suite to 83, covering focused answers,
withdrawal, date corrections, saved-target adjustments, bare acknowledgements,
singular versus plural cancellation, comparative selectors, and replacement
booking failures. This follow-up used Luna/high/standard only: 14 model turns in
eight initial conversations, then seven turns in five final cases. It did not
rerun the entire model suite or use Terra inference.

The initial run exposed a real unsafe interpretation: after "Cancel one of my
bookings tomorrow afternoon", Luna selected and called cancellation for both
afternoon reservations, then asked which one was intended in its final text.
The follow-up "The 4pm one" was correct, but could not undo the setup error.
The evaluator now checks mutations in clarification/setup turns as well as the
final turn. The prompt's unconditional instruction to cancel a fresh non-empty
selection has been replaced with a requirement to check the intended set and
its size first. An unresolved singular request must ask which booking; explicit
plural and comparative requests still proceed.

The initial machine score was 5/8. Reviewing the full tool traces showed 7/8
behavioral passes: the two replacement failures preserved the original and
correctly reported "No move was made" and "could not be verified". These were
false negatives in the wording check, now covered by an offline grader test.
The failure evidence retains both the original score and this review.

All five final cancellation cases passed: the original clarification case,
withdrawal, a separate singular ambiguity, the earlier of two bookings, and
both bookings. Their median final-message turn was 13.1 seconds; the initial
set's was 13.7 seconds. These figures exclude setup messages, thread startup
and real Asimut operations, so they are not end-to-end conversation latency.
They are not a paired model speed comparison or evidence of equal Luna quality.

The conversation tests also exposed simulator fidelity gaps. Future plans now
use the production pure validator and persist in memory. Follow-up adjustments
and zero-action remainders use saved targets rather than a fixed two-hour
baseline. Cancelled reservations remain absent in later agenda reads, and
opaque selections expire across turns even when the user repeats identical
text. A failed combined-edit rollback check already passed; no production
rollback change was necessary. These are simulator improvements, not claims
that production had those state bugs.

All 933 offline tests passed in 71.4 seconds. The new evidence files are
`evaluations/2026-09-12/conversations-luna-initial.json` and
`evaluations/2026-09-12/conversations-luna-final.json`. Model mutations remain
synthetic. The prompt fix reduces the demonstrated ambiguity failure; it does
not make natural-language interpretation deterministic. No transaction engine,
model router or extra production retry layer was added. Production remains
Terra/medium/Fast and existing hosts were not restarted.

## Availability boundaries and final coverage audit

Production time-handling tests reproduced two more gaps. A rolling 24-hour query
covered 23/25 elapsed hours across the spring/autumn clock changes, and scan
rows on the following date could inherit the wrong offset. Rolling arithmetic
and clipping now use UTC instants; each date's wall times use the London zone.
The adapter refuses skipped/repeated local boundaries whose offset is unknown.
This is deterministic time handling, not another model instruction for calendar
arithmetic.

A short query that expired during the scan previously returned an ordinary empty
list. The result now includes `window_elapsed`, and the assistant explains the
expired interval without claiming current rooms are fully booked or free. Both
the elapsed-window and ordinary-empty-window Luna/high/standard checks passed
(9.6 and 11.7 seconds), each using exactly one availability query and no mutations.
Evidence: `evaluations/2026-09-12/availability-boundaries-luna.json`.

The reusable model suite now contains 84 scenarios. All 938 offline tests passed
after the final production changes (68.1 seconds). The following audit inspected
the saved traces and current execution tests; it does not imply another full
84-case model run was performed.

| Requested behavior | Inspected evidence |
| --- | --- |
| Book a specified duration tomorrow afternoon/evening while preserving usual preferences | `audit_book_temporary`, `audit_book_combined`, `audit_book_exact` in the full/matched reports; actual atomic date-window handlers in `test_assistant_request_scope.py` |
| Use saved duration for “book me practice this afternoon/evening” | Both default-daypart cases pass in the Terra and Luna high full reports; dated/default target and existing-booking arithmetic tests |
| Cancel Wednesday and Friday without touching intervening days | `audit_cancel_separated` passes in final matched/full runs; exact-ID selection, complete coverage and batch-veto production tests |
| Report next-hour room availability | `audit_availability_hour` passes in both models' aligned runs; production read-only worker, clipping, minimum-duration, unknown coverage and clock-boundary tests |
| Avoid the empty-Sunday/Monday substitution | Four final calendar scenarios pass; all 49 weekday-pair veto tests and mixed-batch pre-mutation checks |
| Handle trick wording and subsequent corrections | Exclusions, quotations, qualified weeks and session exceptions in the trick reports; clarification, withdrawal, target adjustment and date correction in the conversation reports |
| Report failures without claiming success or retrying uncertain writes | Failed-prerequisite, pending-receipt, zero-action and uncertain-Save cases; replacement-failure cases retain the original |
| Keep dated times effective through execution | `test_date_time_preferences.py` checks actual planner filtering, horizon Save, extension rejection, and preference changes stopping a prepared Save |
| Compare cheaper/faster models and reasoning settings | Saved paired 22-case Terra medium/Luna high/Luna xhigh runs, complete 54-case Terra/Luna high runs, and the earlier low/medium probes; timings and failures remain recorded |
| Keep testing bounded and configuration scoped | Latest follow-up uses two Luna standard turns only; CLI overrides are process-local; current production constants are Terra/medium/Fast |

The model decision remains unchanged: the measured Luna configurations did not
show equal reliability with lower latency. Higher reasoning did not consistently
improve results. Requested Fast tiers in older experiments are not proof of
served-tier latency, and these runs do not measure monetary savings on the user's
Codex account. No billing claim or automatic model switch is justified.

This completes the source/evaluation audit of the requested behavior. It does
not prove every possible utterance correct. The tests deliberately use synthetic
state and isolated production-handler fixtures; no real reservation was created
or cancelled for testing, and no host was restarted or deployed. Existing hosts
must reload Python to receive the final prompt/tools and explicit Fast setting.
