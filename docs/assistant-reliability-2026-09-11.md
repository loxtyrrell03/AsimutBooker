# Assistant reliability and model comparison — 11 September 2026

## Follow-up: empty weekday must not select a neighboring booking

A real incident after the initial evaluation exposed a missing scenario: an
empty requested Sunday was confused with a Monday reservation at the same time.
The existing identity/receipt guards verified the chosen booking, while the
model's date interpretation was wrong. The original 54-case result did not prove
this behavior safe. Four added scenarios bring the model suite to 58.

The host now supplies calendar-computed weekday labels and freshness-filtered
practice-room closure dates directly in agenda context. A separate code check
vetoes cancellation targets that contradict explicitly named weekdays before
any cancellation worker or rebooking-protection write. It applies to exact-date,
ID and mixed-batch selections. This is a limited contradiction check, not a
replacement natural-language parser or sufficient mutation authorization.

All four final Terra/medium model checks passed: empty Sunday with a Monday
alternative, empty Wednesday with a Thursday alternative, a conflicting named
weekday/date, and a real Sunday reservation despite practice-room closure.
The empty-day responses explain closure and ask before cancelling the alternative.
The first model check already avoided substitution but omitted the closure and
question; a later Wednesday variant showed that a separate closure lookup could
be skipped. Putting closure facts in agenda context addressed that omission.
One interim score also wrongly rejected the word “closure”; the grader now
accepts it. Earlier trials remain in `calendar-*.json`, including original scores.

Production-handler tests cover all 49 requested/selected weekday combinations,
range/list/exclusion variants, rollover dates, empty-day calendar facts, catalog
failure, and blocking a mixed batch before even its valid first item executes.
All 923 offline tests passed after the final change (61.9 seconds).
Tests use temporary settings and synthetic reservations; live booking state and
running hosts were preserved. Reopen/reload hosts to activate the guard.

## Scope

The real Codex controller and production tool declarations are exercised with
synthetic agenda data and in-memory mutations. A production-effect guard prevents
the evaluation from launching Booker commands or touching live Booker settings.
Separate execution tests exercise real tool handlers, validators and the
read-only scan worker against temporary files and mocked site access.

The expanded suite covers 54 requests: schedule questions; exact, daypart,
range, whole-agenda and separated-day cancellation; default, explicit, relative
and corrected durations; dated time windows; deferred goals; future plans;
availability by time, room and duration; ambiguity; quoted instructions;
untrusted event text; failed refreshes; pending receipts; zero-action runs;
uncertain saves; and clarification follow-ups.

All dates, rooms and reservations in evaluation reports are invented fixtures.
The clock is fixed to Monday 31 August 2026 at 10:00 Europe/London. These tests
do not establish physical-phone behavior or verify a live reservation mutation.

## Changes

- Expose existing exact-date time preferences to the assistant. A dated booking
  request now preserves global preferred times and unrelated dates. The shared
  calendar validator validates the entire update atomically.
- Add `find_availability` over the existing read-only room-grid scan. It accepts
  rolling or dated windows, optional room/minimum-duration filters, and reports
  missing date coverage separately from a scanned empty result. Results are
  observed room gaps, not a guarantee of personal booking eligibility.
- Use the established `minimum_block_minutes` name for its duration filter.
  Both models confused the initial `minimum_minutes` name with the existing
  preference field. Aligning the name avoids a second term for the same concept;
  there is no permissive alias or bypass of validation.
- Add read-only `--check-dates` scoping to avoid visiting every room-grid date
  for a narrow query. Complete agenda discovery and booking safeguards remain.
- Clarify cancellation of separated days as one union of exact reservation IDs,
  preserving the existing single-batch and verified-absence safeguards.
- Expand the evaluator with measured turn latency and explicit model, reasoning
  and requested service-tier options. Simulated dated preference updates use
  production validators and survive subsequent reads. Progress commentary is
  excluded from final-answer scoring.

## Method and limits

Timing starts when the user turn is submitted and ends when the final response
arrives. It includes model reasoning, simulated tool calls and output generation;
it excludes thread startup and real Asimut network/scan/Save latency. Model runs
use independent clean conversations on the same PC/account. Concurrent benchmark
runs and normal provider variability mean differences of a few seconds are not
conclusive. Fast is the requested Codex service tier; this controller does not
provide per-response billing or a served-tier receipt.

Correctness checks primarily examine tool scope, dates, targets, sequencing and
outcomes, with selected final-answer checks. Human review distinguishes actual
execution mistakes from overly rigid wording checks. A finite suite cannot prove
identical quality for every future request, especially dates, wording and history
not represented here.

The original 24-case Terra/medium suite passed before this work. That suite did
not expose the missing availability tool or global-versus-dated time-window gap.
The final full offline suite passed 917 tests in 64.3 seconds, including
controller, runtime, tool validation, scan worker and evaluation checks.

## Initial model comparison

| Configuration | Reviewed contracts passed | Median turn time |
| --- | ---: | ---: |
| Terra, medium, standard | 46 / 48 | 15.6 seconds |
| Luna, medium, Fast requested | 44 / 48 | 16.1 seconds |
| Terra, medium, six failure/clarification cases | 6 / 6 | 17.3 seconds |
| Luna, medium, Fast, same six failure/clarification cases | 6 / 6 | 17.5 seconds |

Both main runs initially scored 42/48 under overly restrictive checks. Review
accepted equivalent safe argument combinations, redundant read-only queries,
and correctly filtering a broader availability result. It did not forgive
incorrect reservation scope, missing rebooking protection or timeouts.

Terra eventually recovered from guessed daypart argument names and preserved the
whole afternoon's rebooking protection. Luna cancelled the right two example
reservations but lost the requested full-daypart protection after falling back
to an ID selection. Both included a reservation 15 minutes beyond the rolling
seven-day boundary when excluding Friday. Luna additionally omitted a remainder
explanation once and timed out before completing the separated-day cancellation.

A three-case Luna/low/Fast probe took 6.6 seconds for rolling availability,
20.8 seconds for an evening booking and 14.0 seconds for separated-day
cancellation. The cancellation selected the correct IDs but copied an invalid
selection token, which the host rejected without mutation. This is a small probe,
not an aggregate latency comparison.

After the general prompt repairs, Terra passed five focused retests: daypart
cancellation, rolling exclusions, empty availability, an uncertain reservation
Save, and a failed prerequisite. Luna's corresponding cancellation retests also
succeeded. The initial results alone do not justify changing the production model.

## Final-prompt reasoning comparison

The same 22-case subset was run with the repaired prompt on all three
configurations. It includes complex cancellation, default/relative targets,
dated windows, untrusted text, ambiguity and uncertain/blocked mutations.

| Configuration | Reviewed contracts passed | Median turn time |
| --- | ---: | ---: |
| Terra, medium, standard | 22 / 22 | 14.6 seconds |
| Luna, high, Fast requested | 22 / 22 | 16.8 seconds |
| Luna, xhigh, Fast requested | 21 / 22 | 20.9 seconds |

All three used the correct tool actions and scopes in these 22 cases. Xhigh's
failure was an omitted explanation that subsequent recurring runs would pursue
the unbooked remainder. Its answer correctly described the three-hour goal,
multiple sessions and the one-action limit, but left the follow-through unclear.
Higher reasoning was not monotonically better or faster.

The raw scores were 20/22 for Terra and 21/22 for Luna high. Manual review
accepted an explicit 07:00–12:00 restriction as equivalent to saying “morning”,
and two known one-hour reservation intervals as equivalent to stating two hours
already booked. No action/scope failure was reclassified as a pass.

Compact synthetic evidence, including original issues where reviewed, is in
[`evaluations/2026-09-11`](evaluations/2026-09-11/). Repeated full context and
reasoning summaries are omitted; prompts, final answers, exact tool arguments,
outcomes, timing and scoring remain.

Luna high's full 54-case run passed 52 contracts, with a median turn time of
18.6 seconds. Both exceptions completed the correct underlying task: one answer
omitted how recurring runs would pursue the remaining practice time; another
first guessed an invalid duration-filter parameter, received a validation error,
then recovered and correctly reported the qualifying evening room. The latter
still fails the clean tool-use contract, consistently with the initial Terra
daypart recovery. Neither exception caused a wrong reservation mutation.
Manual review also accepted “3 total hours”, which the initial wording check
incorrectly rejected. This does not establish a drop-in replacement without any
reliability tradeoff.

## Default model decision

Keep Terra at medium reasoning. On the complete 54-case comparison, Terra passed
53 contracts with a 16.0-second median; Luna high passed 52 with an 18.6-second
median. Both completed the right underlying tasks, including the single recovered
availability-parameter error described above. Luna also omitted one remainder
explanation. This comparison precedes the final duration-filter naming alignment;
its focused validation is recorded separately rather than replacing failed trials.
After alignment, all three availability retests passed on both models. Luna high
also passed a fresh three-hour booking retest, including the remainder explanation.
This recovery is encouraging but does not erase the earlier inconsistency.

Luna high is a plausible budget option, but these measurements do not justify
claiming the same reliability with better speed. Medium and low Luna showed
more material problems; xhigh added latency without improving the matched result.
No automatic model-routing layer or fallback system was added.

The shared configuration supports explicit model, reasoning and tier choices,
and desktop/phone labels derive from it. Production remains Terra/medium with
its inherited service tier. Existing desktop and phone hosts were preserved;
they need to reload Python to receive the new tools and prompt. No live booking,
cancellation or preference mutation was used as a test, and no deployment or
physical-device verification is claimed.

## Reproduce

From the repository root:

```powershell
.\.venv\Scripts\python.exe tools\evaluate_assistant.py --model gpt-5.6-terra --effort medium --service-tier default --json
.\.venv\Scripts\python.exe tools\evaluate_assistant.py --model gpt-5.6-luna --effort medium --service-tier fast --json
.\.venv\Scripts\python.exe tools\evaluate_assistant.py --model gpt-5.6-luna --effort high --service-tier fast --output .tmp/luna-high.json
.\.venv\Scripts\python.exe tools\evaluate_assistant.py --model gpt-5.6-luna --effort xhigh --service-tier fast --output .tmp/luna-xhigh.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Use repeated `--case` options for a focused subset. Model and service-tier
overrides apply only inside the evaluation process; they do not alter an open
desktop or phone assistant.

## Cost context

OpenAI lists Luna standard API rates at $0.20 input / $1.20 output per million
tokens, compared with Terra at $2 / $12. This application uses Codex account
access, so API prices are context rather than a measured bill or an exact Codex
quota-saving estimate. Fast mode has separate pricing and availability.

Sources: [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[Fast mode](https://developers.openai.com/api/docs/guides/fast-mode),
[Codex service tiers](https://learn.chatgpt.com/docs/config-file/config-reference).
