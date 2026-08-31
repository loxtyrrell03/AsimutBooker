import unittest
from datetime import datetime, timezone

from assistant_plans import (
    AssistantPlanError,
    apply_future_practice_plan,
    load_assistant_plans,
)


class AssistantFuturePlanTests(unittest.TestCase):
    def test_complete_week_becomes_runtime_targets_and_explainable_intent(self):
        settings = {
            "unrelated": "preserved",
            "practice_plan": {
                "enabled": False,
                "default_hours": 1.5,
                "date_overrides": {"2026-09-20": 2.0},
            },
            "disabled_dates": ["2026-09-21"],
        }
        targets = [
            {"date": f"2026-09-{day:02d}", "hours": 2.0 if day <= 11 else 4.0}
            for day in range(7, 14)
        ]

        record = apply_future_practice_plan(
            settings,
            title="Busy week with weekend focus",
            intent_summary="Cap weekdays at two hours and put the extra practice on the weekend.",
            start_date="2026-09-07",
            end_date="2026-09-13",
            daily_targets=targets,
            replace_overlapping=False,
            now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )

        self.assertTrue(settings["practice_plan"]["enabled"])
        self.assertEqual(settings["practice_plan"]["date_overrides"]["2026-09-07"], 2.0)
        self.assertEqual(settings["practice_plan"]["date_overrides"]["2026-09-13"], 4.0)
        self.assertEqual(settings["practice_plan"]["date_overrides"]["2026-09-20"], 2.0)
        self.assertEqual(settings["disabled_dates"], ["2026-09-21"])
        self.assertEqual(settings["unrelated"], "preserved")
        self.assertEqual(load_assistant_plans(settings)[0], record)

    def test_zero_hours_turns_day_off_and_removes_old_override(self):
        settings = {
            "practice_plan": {
                "enabled": True,
                "default_hours": 2.0,
                "date_overrides": {"2026-09-08": 3.0},
            },
            "disabled_dates": [],
        }

        apply_future_practice_plan(
            settings,
            title="Rest day",
            intent_summary="Keep Tuesday free.",
            start_date="2026-09-08",
            end_date="2026-09-08",
            daily_targets=[{"date": "2026-09-08", "hours": 0}],
            replace_overlapping=False,
        )

        self.assertNotIn("2026-09-08", settings["practice_plan"]["date_overrides"])
        self.assertEqual(settings["disabled_dates"], ["2026-09-08"])

    def test_missing_date_is_rejected_before_settings_change(self):
        settings = {"sentinel": {"preserve": True}}
        original = {"sentinel": {"preserve": True}}

        with self.assertRaisesRegex(AssistantPlanError, "missing"):
            apply_future_practice_plan(
                settings,
                title="Incomplete",
                intent_summary="This must not be applied.",
                start_date="2026-09-07",
                end_date="2026-09-09",
                daily_targets=[
                    {"date": "2026-09-07", "hours": 2},
                    {"date": "2026-09-09", "hours": 2},
                ],
                replace_overlapping=False,
            )

        self.assertEqual(settings, original)

    def test_overlap_requires_explicit_replacement(self):
        settings = {}
        common = dict(
            title="First",
            intent_summary="Original plan.",
            start_date="2026-09-07",
            end_date="2026-09-08",
            daily_targets=[
                {"date": "2026-09-07", "hours": 2},
                {"date": "2026-09-08", "hours": 2},
            ],
        )
        apply_future_practice_plan(settings, replace_overlapping=False, **common)

        with self.assertRaisesRegex(AssistantPlanError, "overlaps"):
            apply_future_practice_plan(settings, replace_overlapping=False, **common)

        replacement = dict(common)
        replacement["title"] = "Revised"
        apply_future_practice_plan(settings, replace_overlapping=True, **replacement)
        self.assertEqual(len(load_assistant_plans(settings)), 1)
        self.assertEqual(load_assistant_plans(settings)[0]["title"], "Revised")

    def test_replacement_requires_the_complete_existing_range(self):
        settings = {}
        apply_future_practice_plan(
            settings,
            title="Whole week",
            intent_summary="Two hours daily.",
            start_date="2026-09-07",
            end_date="2026-09-13",
            daily_targets=[
                {"date": f"2026-09-{day:02d}", "hours": 2}
                for day in range(7, 14)
            ],
            replace_overlapping=False,
        )

        with self.assertRaisesRegex(AssistantPlanError, "complete existing date range"):
            apply_future_practice_plan(
                settings,
                title="Partial replacement",
                intent_summary="Three hours for part of the week.",
                start_date="2026-09-09",
                end_date="2026-09-11",
                daily_targets=[
                    {"date": f"2026-09-{day:02d}", "hours": 3}
                    for day in range(9, 12)
                ],
                replace_overlapping=True,
            )


if __name__ == "__main__":
    unittest.main()
