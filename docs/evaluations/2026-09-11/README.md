# Synthetic assistant evaluation evidence

See [the comparison report](../../assistant-reliability-2026-09-11.md) for
methodology, changes and the model decision.

These reports contain invented bookings and simulated tool effects. They are
not receipts for live booking or cancellation. Repeated full context and model
reasoning summaries are omitted; request text, final answers, tool arguments,
outcomes, elapsed turn time and deterministic issues remain.

- `terra-reviewed` and `luna-fast-reviewed`: initial 48-case comparison,
  before the daypart/rolling-exclusion prompt repairs.
- `*-failures`: six failure and clarification scenarios at medium reasoning.
- `*-refined`: five focused retests after those repairs.
- `*-matched`: the same 22 cases using the final prompt, comparing Terra medium
  with Luna high and xhigh. Luna runs request Fast; Terra requests default tier.
- `*-full`: all 54 cases with the final prompt, assembled from disjoint matched
  and remaining-case runs. Every case appears once; medians use all 54 turns.
- `*-aligned`: focused retests after aligning the availability duration filter
  to the existing `minimum_block_minutes` vocabulary. Earlier reports retain the
  former field name as evidence of the tested contract at that time.

Where manual review accepted equivalent safe behavior, `original_issues`
preserves the earlier score and `issues` contains the reviewed score. Review
does not excuse wrong mutation scope, a failed requested action or false success.
JSON files are UTF-8. Timings exclude thread startup and real site latency.
