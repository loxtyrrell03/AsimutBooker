import unittest
from datetime import datetime, timedelta
from unittest import mock

import book_week


class HorizonExtensionTimelineTests(unittest.TestCase):
    """Exact fake-clock coverage for a five-day horizon-edge booking."""

    room = "B0.29"
    slot_date = "2026-09-05"
    start_hour = 10.0
    horizon_edge = datetime(2026, 8, 31, 10, 0, 0)
    booking_timestamp = "2026-08-31T10:30:00"

    def calculate(self, elapsed_seconds, current_end, target_end=12.0):
        return book_week.calculate_max_extension(
            self.room,
            self.slot_date,
            self.start_hour,
            current_end,
            self.booking_timestamp,
            target_end,
            now=self.horizon_edge + timedelta(seconds=elapsed_seconds),
        )

    def assert_not_extendable(self, elapsed_seconds, current_end, target_end=12.0):
        can_extend, max_end, reason = self.calculate(
            elapsed_seconds,
            current_end,
            target_end,
        )
        self.assertFalse(can_extend, reason)
        self.assertAlmostEqual(max_end, current_end)

    def assert_extendable_to(
        self,
        elapsed_seconds,
        current_end,
        expected_end,
        target_end=12.0,
    ):
        can_extend, max_end, reason = self.calculate(
            elapsed_seconds,
            current_end,
            target_end,
        )
        self.assertTrue(can_extend, reason)
        self.assertAlmostEqual(max_end, expected_end)

    def test_boundary_reached_during_preparation_still_gets_settle_allowance(self):
        boundary = datetime(2026, 8, 31, 10, 45, 0)

        for current_time in (boundary, boundary + timedelta(milliseconds=1)):
            with (
                self.subTest(current_time=current_time),
                mock.patch.object(book_week, "datetime", wraps=datetime) as clock,
                mock.patch.object(book_week.time, "sleep") as sleep,
            ):
                clock.now.return_value = current_time

                initial_wait = book_week.wait_until_datetime(
                    boundary,
                    label="EXTEND",
                )

            self.assertEqual(initial_wait, 0.0)
            sleep.assert_called_once_with(
                book_week.HORIZON_SAVE_SETTLE_SECONDS
            )

    def test_minimum_booking_and_first_extension_unlock_at_exact_boundaries(self):
        # A 30-minute booking cannot exist before +30, and it has no extension
        # available until the next complete 15-minute boundary at +45.
        self.assert_not_extendable(29 * 60 + 59, 10.5)
        self.assert_not_extendable(30 * 60, 10.5)
        self.assert_not_extendable(44 * 60 + 59, 10.5)
        self.assert_extendable_to(45 * 60, 10.5, 10.75)

    def test_planner_exposes_the_exact_upcoming_boundary_for_preparation(self):
        plan = book_week.plan_horizon_extension(
            self.room,
            self.slot_date,
            self.start_hour,
            10.5,
            12.0,
            now=self.horizon_edge + timedelta(minutes=44, seconds=59),
        )

        self.assertFalse(plan.can_extend)
        self.assertAlmostEqual(plan.max_end_hour, 10.5)
        self.assertAlmostEqual(plan.next_end_hour, 10.75)
        self.assertEqual(
            plan.next_unlock_at,
            self.horizon_edge + timedelta(minutes=45),
        )
        self.assertEqual(plan.wait_seconds, 1.0)

    def test_each_quarter_hour_unlocks_one_step_through_two_hours(self):
        cases = (
            (45, 10.5, 10.75),
            (60, 10.75, 11.0),
            (75, 11.0, 11.25),
            (90, 11.25, 11.5),
            (105, 11.5, 11.75),
            (120, 11.75, 12.0),
        )
        for elapsed_minutes, current_end, expected_end in cases:
            with self.subTest(elapsed_minutes=elapsed_minutes):
                self.assert_not_extendable(
                    elapsed_minutes * 60 - 1,
                    current_end,
                )
                self.assert_extendable_to(
                    elapsed_minutes * 60,
                    current_end,
                    expected_end,
                )

    def test_missed_runs_catch_up_to_latest_complete_boundary(self):
        # If one or more scheduled runs were missed, a single edit should catch
        # up to the newest unlocked quarter-hour rather than replaying each step.
        self.assert_extendable_to(76 * 60, 10.5, 11.25)
        self.assert_extendable_to(104 * 60 + 59, 10.5, 11.5)
        self.assert_extendable_to(119 * 60 + 59, 10.5, 11.75)
        self.assert_extendable_to(180 * 60, 10.5, 12.0)

    def test_target_end_caps_extension_and_completed_state_stays_closed(self):
        self.assert_extendable_to(120 * 60, 11.0, 11.25, target_end=11.25)
        self.assert_not_extendable(120 * 60, 11.25, target_end=11.25)

    def test_two_hour_maximum_cannot_be_exceeded(self):
        self.assert_extendable_to(180 * 60, 11.75, 12.0, target_end=13.0)
        self.assert_not_extendable(180 * 60, 12.0, target_end=13.0)

    def test_upcoming_boundary_prepares_exact_edit_but_does_not_save_early(self):
        booking = {
            "room": self.room,
            "date": self.slot_date,
            "startTime": "10:00",
            "endTime": "10:30",
            "target_end": "12:00",
            "created_at": self.booking_timestamp,
            "horizon_days": 5,
            "eventId": 4242,
            "event_url": "https://rwcmd.asimut.net/arrangement?eventId=4242",
        }
        boundary = self.horizon_edge + timedelta(minutes=45)
        with (
            mock.patch.object(
                book_week,
                "edit_reservation_end_time",
                return_value=True,
            ) as edit,
            mock.patch.object(
                book_week,
                "update_extendable_booking_end_time",
            ),
        ):
            success, new_end, message = book_week.try_extend_booking(
                object(),
                booking,
                now=boundary - timedelta(seconds=1),
            )

        self.assertTrue(success, message)
        self.assertEqual(new_end, "10:45")
        edit.assert_called_once_with(
            mock.ANY,
            booking,
            "10:45",
            save_not_before=boundary,
        )

    def test_more_than_three_minutes_before_boundary_does_no_browser_work(self):
        booking = {
            "room": self.room,
            "date": self.slot_date,
            "startTime": "10:00",
            "endTime": "10:30",
            "target_end": "12:00",
            "created_at": self.booking_timestamp,
            "horizon_days": 5,
            "eventId": 4242,
            "event_url": "https://rwcmd.asimut.net/arrangement?eventId=4242",
        }
        with mock.patch.object(book_week, "edit_reservation_end_time") as edit:
            success, new_end, _ = book_week.try_extend_booking(
                object(),
                booking,
                now=self.horizon_edge + timedelta(minutes=40),
            )

        self.assertFalse(success)
        self.assertIsNone(new_end)
        edit.assert_not_called()

    def test_missed_run_uses_one_edit_and_one_state_update_to_catch_up(self):
        booking = {
            "room": self.room,
            "date": self.slot_date,
            "startTime": "10:00",
            "endTime": "10:30",
            "target_end": "12:00",
            "created_at": self.booking_timestamp,
            "horizon_days": 5,
            "eventId": 4242,
            "event_url": "https://rwcmd.asimut.net/arrangement?eventId=4242",
        }
        with (
            mock.patch.object(
                book_week,
                "edit_reservation_end_time",
                return_value=True,
            ) as edit,
            mock.patch.object(
                book_week,
                "update_extendable_booking_end_time",
            ) as update,
        ):
            success, new_end, message = book_week.try_extend_booking(
                object(),
                booking,
                now=self.horizon_edge + timedelta(minutes=76),
            )

        self.assertTrue(success, message)
        self.assertEqual(new_end, "11:15")
        edit.assert_called_once_with(mock.ANY, booking, "11:15")
        update.assert_called_once_with(
            self.room,
            self.slot_date,
            "10:00",
            "11:15",
        )

    def test_snipe_grace_keeps_only_imminent_or_just_opened_edges(self):
        for seconds in (-180, -1, 0, 1, 180):
            with self.subTest(seconds=seconds):
                self.assertTrue(
                    book_week.is_horizon_snipe_timing_candidate(seconds)
                )
        for seconds in (-181, 181):
            with self.subTest(seconds=seconds):
                self.assertFalse(
                    book_week.is_horizon_snipe_timing_candidate(seconds)
                )

    def test_extension_queue_orders_due_then_imminent_then_future(self):
        now = self.horizon_edge + timedelta(minutes=74)

        def booking(end_time):
            return {
                "room": self.room,
                "date": self.slot_date,
                "startTime": "10:00",
                "endTime": end_time,
                "target_end": "12:00",
            }

        due = booking("10:30")
        imminent = booking("11:00")
        future = booking("11:15")
        ordered = sorted(
            [future, imminent, due],
            key=lambda item: book_week.extension_processing_sort_key(
                item,
                now=now,
            ),
        )
        self.assertEqual(ordered, [due, imminent, future])

    def test_due_extensions_use_room_priority_as_the_stable_tie_break(self):
        now = datetime(2026, 9, 1, 12, 0)
        low_priority = {
            "room": "B1.09",
            "date": "2026-09-04",
            "startTime": "10:00",
            "endTime": "10:30",
            "target_end": "12:00",
        }
        high_priority = dict(low_priority, room="B0.29", date="2026-09-06")
        ordered = sorted(
            [low_priority, high_priority],
            key=lambda item: book_week.extension_processing_sort_key(
                item,
                now=now,
            ),
        )
        self.assertEqual(
            [item["room"] for item in ordered],
            ["B0.29", "B1.09"],
        )


if __name__ == "__main__":
    unittest.main()
