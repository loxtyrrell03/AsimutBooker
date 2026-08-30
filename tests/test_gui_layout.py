import unittest

from gui import automation_health_summary
from health_status import HealthItem


def item(key, state="ok", headline="Ready"):
    return HealthItem(key, key.replace("_", " ").title(), state, headline)


def ready_items():
    return {
        "next_run": item("next_run", headline="Next automatic run: 17:13"),
        "saved_session": item("saved_session", headline="Saved state is available"),
        "auth_cooldown": item("auth_cooldown", headline="Login recovery is ready"),
        "pending_mutations": item(
            "pending_mutations",
            headline="No mutations need reconciliation",
        ),
    }


class AutomationHealthSummaryTests(unittest.TestCase):
    def test_incomplete_evidence_stays_in_checking_state(self):
        state, headline, detail = automation_health_summary(
            {"next_run": item("next_run")}
        )

        self.assertEqual(state, "unknown")
        self.assertEqual(headline, "Checking automation")
        self.assertIn("schedule", detail)

    def test_error_takes_priority_and_names_the_affected_area(self):
        items = ready_items()
        items["pending_mutations"] = item(
            "pending_mutations",
            "error",
            "One pending mutation needs reconciliation",
        )

        state, headline, detail = automation_health_summary(items)

        self.assertEqual((state, headline), ("error", "Action required"))
        self.assertIn("Booking safety", detail)
        self.assertIn("reconciliation", detail)

    def test_warning_is_not_flattened_into_a_green_summary(self):
        items = ready_items()
        items["auth_cooldown"] = item(
            "auth_cooldown",
            "warn",
            "Authentication retry cooldown is active",
        )

        state, headline, detail = automation_health_summary(items)

        self.assertEqual((state, headline), ("warn", "Check recommended"))
        self.assertIn("Login recovery", detail)

    def test_unproven_physical_wake_is_disclosed_without_claiming_failure(self):
        items = ready_items()
        items["physical_wake"] = item(
            "physical_wake",
            "unknown",
            "Physical wake has not been proven",
        )

        state, headline, detail = automation_health_summary(items)

        self.assertEqual((state, headline), ("ok", "Automation is ready"))
        self.assertIn("not yet been physically proven", detail)


if __name__ == "__main__":
    unittest.main()
