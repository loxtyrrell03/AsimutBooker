import unittest
from datetime import date, datetime, timezone
from unittest import mock

import book_week


class HorizonSnipeFlowTests(unittest.TestCase):
    NOW = datetime(2026, 8, 30, 10, 0, 0)
    TARGET_DATE = date(2026, 9, 4)
    EVENT_URL = "https://rwcmd.asimut.net/arrangement?eventId=5150"
    HORIZON_MINUTES = 5 * 24 * 60
    HORIZON_OBSERVED_AT = datetime(2026, 8, 30, 9, 55, tzinfo=timezone.utc)

    def setUp(self):
        self.live_policy = mock.patch.multiple(
            book_week,
            ACTIVE_ROOM_POLICY=mock.Mock(observed_at=self.HORIZON_OBSERVED_AT),
            ROOM_HORIZON_MINUTES={"B0.29": self.HORIZON_MINUTES},
            MINIMUM_BLOCK_MINUTES=30,
            SITE_CLOCK_OFFSET_BOUNDS=(0.05, 0.05),
        )
        self.live_policy.start()
        self.addCleanup(self.live_policy.stop)
        self.fresh_validation = mock.patch.object(
            book_week,
            "refresh_new_booking_validation",
            return_value=(True, "fresh"),
        )
        self.fresh_validation.start()
        self.addCleanup(self.fresh_validation.stop)

    def build_page(self, *, changed_before_save=False):
        page = mock.MagicMock()
        page.url = "https://rwcmd.asimut.net/event?eventId=0"

        start_input = mock.MagicMock()
        start_input.first = start_input
        start_input.count.return_value = 1
        start_input.is_visible.return_value = True
        start_input.input_value.side_effect = (
            ["10:00", "10:15"]
            if changed_before_save
            else ["10:00", "10:00"]
        )

        end_input = mock.MagicMock()
        end_input.first = end_input
        end_input.count.return_value = 1
        end_input.is_visible.return_value = True
        end_input.input_value.side_effect = ["10:30", "10:30"]

        save_button = mock.MagicMock()
        save_button.first = save_button
        save_button.count.return_value = 1
        save_button.is_visible.return_value = True
        save_button.is_enabled.return_value = True
        save_button.bounding_box.return_value = {
            "x": 100,
            "y": 200,
            "width": 80,
            "height": 40,
        }
        button_locator = mock.MagicMock()
        button_locator.filter.return_value.first = save_button

        def locator(selector):
            if "time-range-start-time" in selector:
                return start_input
            if "time-range-end-time" in selector:
                return end_input
            if selector == "button":
                return button_locator
            return mock.MagicMock()

        page.locator.side_effect = locator
        page.start_input = start_input
        page.end_input = end_input
        page.save_button = save_button
        return page

    def slot(self):
        return {
            "room": "B0.29",
            "start_hour": 10.0,
            "end_hour": 12.0,
            "duration": 120,
            "bookable_from": self.NOW,
            "horizon_minutes": self.HORIZON_MINUTES,
            "booking_minutes": 30,
        }

    def tracker(self):
        tracker = mock.MagicMock()
        tracker.can_book.return_value = (True, "allowed")
        tracker.get_remaining_quota_hours.return_value = 28.0
        return tracker

    def find_candidate_at(self, now):
        frozen_now = now

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        available = [
            {
                "room": "B0.29",
                "slots": [
                    {
                        "startHour": 9.5,
                        "endHour": 11.5,
                        "clickX": 20,
                        "clickY": 30,
                    }
                ],
            }
        ]
        tracker = self.tracker()
        with mock.patch.object(book_week, "datetime", FrozenDateTime):
            return book_week.find_horizon_snipe_candidate(
                available,
                self.TARGET_DATE,
                tracker,
                {"enabled": False, "strict_mode": False},
            )

    def patches(self, page):
        frozen_now = self.NOW

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        def confirm_create(*_args, **_kwargs):
            page.url = self.EVENT_URL
            return True

        return (
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(
                book_week,
                "get_room_slot_coordinates",
                return_value={"x": 20, "y": 30},
            ),
            mock.patch.object(book_week, "wait_for_student_booking_option", return_value=None),
            mock.patch.object(book_week, "wait_for_new_booking_form", return_value=True),
            mock.patch.object(
                book_week,
                "page_booking_snapshot",
                return_value={
                    "room": "B0.29",
                    "date": self.TARGET_DATE.isoformat(),
                    "start": "10:00",
                    "end": "10:30",
                },
            ),
            mock.patch.object(book_week, "_visible_save_rejection", return_value=None),
            mock.patch.object(
                book_week,
                "record_pending_create",
                return_value={"id": "create-receipt"},
            ),
            mock.patch.object(
                book_week,
                "wait_for_created_booking_outcome",
                side_effect=confirm_create,
            ),
            mock.patch.object(book_week, "save_extendable_booking"),
            mock.patch.object(book_week.time, "sleep"),
            mock.patch.object(book_week, "go_back"),
        )

    def test_exact_plus_thirty_creates_minimum_and_tracks_two_hour_target(self):
        page = self.build_page()
        tracker = self.tracker()
        patches = self.patches(page)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as receipt, patches[7], patches[8] as save_extension, patches[9], patches[10]:
            result = book_week.try_horizon_snipe(
                page,
                self.slot(),
                self.TARGET_DATE,
                tracker,
                days_ahead=5,
                remaining_daily_hours=2.0,
                time_prefs={"enabled": False, "strict_mode": False},
            )

        self.assertTrue(result)
        receipt.assert_called_once_with(
            room="B0.29",
            booking_date=self.TARGET_DATE.isoformat(),
            start="10:00",
            end="10:30",
        )
        tracker.add_booking.assert_called_once_with(
            "B0.29",
            self.TARGET_DATE,
            10.0,
            10.5,
        )
        save_extension.assert_called_once_with(
            "B0.29",
            self.TARGET_DATE,
            10.0,
            10.5,
            12.0,
            event_url=self.EVENT_URL,
        )

    def test_finder_derives_initial_unlock_at_horizon_plus_thirty(self):
        horizon_edge = datetime(2026, 8, 30, 9, 30)
        expected_unlock = horizon_edge.replace(hour=10, minute=0)

        candidate, wait_seconds = self.find_candidate_at(
            horizon_edge.replace(hour=9, minute=59, second=59)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(wait_seconds, 1.0)
        self.assertEqual(candidate["bookable_from"], expected_unlock)
        self.assertEqual(candidate["horizon_minutes"], self.HORIZON_MINUTES)
        self.assertEqual(candidate["booking_minutes"], 30)

        candidate, wait_seconds = self.find_candidate_at(expected_unlock)
        self.assertIsNotNone(candidate)
        self.assertEqual(wait_seconds, 0.0)
        self.assertEqual(candidate["bookable_from"], expected_unlock)

        too_early, _ = self.find_candidate_at(
            horizon_edge.replace(hour=9, minute=56, second=59)
        )
        too_late, _ = self.find_candidate_at(
            horizon_edge.replace(hour=10, minute=3, second=1)
        )
        self.assertIsNone(too_early)
        self.assertIsNone(too_late)

    def test_changed_form_aborts_before_receipt_or_save(self):
        page = self.build_page(changed_before_save=True)
        tracker = self.tracker()
        patches = self.patches(page)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as receipt, patches[7] as outcome, patches[8], patches[9], patches[10]:
            result = book_week.try_horizon_snipe(
                page,
                self.slot(),
                self.TARGET_DATE,
                tracker,
                days_ahead=5,
                remaining_daily_hours=2.0,
                time_prefs={"enabled": False, "strict_mode": False},
            )

        self.assertFalse(result)
        receipt.assert_not_called()
        outcome.assert_not_called()
        tracker.add_booking.assert_not_called()


class BoundaryDrivenCandidateTests(unittest.TestCase):
    TODAY = date(2026, 8, 30)
    BOUNDARY = datetime(2026, 8, 30, 13, 45)

    def policy_patch(self, *, minimum=30, horizons=None, rooms=None):
        rooms = rooms or ["B0.29"]
        horizons = horizons or {"B0.29": 5 * 24 * 60}
        live_dates = tuple(
            self.TODAY.replace() + book_week.timedelta(days=offset)
            for offset in range(8)
        )
        return mock.patch.multiple(
            book_week,
            ACTIVE_ROOM_POLICY=mock.Mock(),
            PRIORITY_ROOMS=list(rooms),
            ROOM_HORIZON_MINUTES=dict(horizons),
            BOOKING_WINDOW_DATES=live_dates,
            MINIMUM_BLOCK_MINUTES=minimum,
        )

    @staticmethod
    def room_gap(room, start, end):
        return {
            "room": room,
            "slots": [
                {
                    "startHour": start,
                    "endHour": end,
                    "clickX": 20,
                    "clickY": 30,
                }
            ],
        }

    def test_interior_edge_is_found_inside_a_full_day_gap(self):
        with self.policy_patch():
            plan = book_week.plan_horizon_edge_slots(
                self.BOUNDARY,
                today=self.TODAY,
                only_room="B0.29",
            )[0]
            candidate = book_week._candidate_for_planned_edge(
                self.room_gap("B0.29", 7.25, 22.25),
                plan,
            )

        self.assertEqual(plan["target_date"], date(2026, 9, 4))
        self.assertEqual(plan["start_hour"], 13.25)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["booking_minutes"], 30)
        self.assertEqual(candidate["duration"], 540)

    def test_edge_requires_the_complete_minimum_block_inside_the_gap(self):
        with self.policy_patch():
            plan = book_week.plan_horizon_edge_slots(
                self.BOUNDARY,
                today=self.TODAY,
                only_room="B0.29",
            )[0]
            starts_too_late = book_week._candidate_for_planned_edge(
                self.room_gap("B0.29", 13.5, 22.25),
                plan,
            )
            ends_too_early = book_week._candidate_for_planned_edge(
                self.room_gap("B0.29", 7.25, 13.5),
                plan,
            )

        self.assertIsNone(starts_too_late)
        self.assertIsNone(ends_too_early)

    def test_selected_minimum_and_arbitrary_horizon_shift_the_exact_edge(self):
        with self.policy_patch(
            minimum=60,
            horizons={"B0.29": 3 * 24 * 60 + 15},
        ):
            plan = book_week.plan_horizon_edge_slots(
                self.BOUNDARY,
                today=self.TODAY,
                only_room="B0.29",
            )[0]

        self.assertEqual(plan["target_date"], date(2026, 9, 2))
        self.assertEqual(plan["start_hour"], 13.0)
        self.assertEqual(plan["end_hour"], 14.0)

    def test_room_and_date_scopes_are_applied_before_navigation_planning(self):
        with self.policy_patch(
            rooms=["B0.29", "B1.09"],
            horizons={"B0.29": 5 * 24 * 60, "B1.09": 3 * 24 * 60},
        ):
            room_scoped = book_week.plan_horizon_edge_slots(
                self.BOUNDARY,
                today=self.TODAY,
                only_room="B1.09",
            )
            mismatched_date = book_week.plan_horizon_edge_slots(
                self.BOUNDARY,
                today=self.TODAY,
                only_room="B1.09",
                only_date=date(2026, 9, 4),
            )

        self.assertEqual([item["room"] for item in room_scoped], ["B1.09"])
        self.assertEqual(room_scoped[0]["days_ahead"], 3)
        self.assertEqual(mismatched_date, [])

    def test_candidate_order_uses_gui_room_priority_before_date_distance(self):
        tracker = mock.MagicMock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.can_book.return_value = (True, "")
        page = mock.MagicMock()
        navigated = []

        def navigate(_page, target, _current, **_kwargs):
            navigated.append(target)
            return target

        def available(_page):
            return [
                self.room_gap("Preferred", 7.25, 22.25),
                self.room_gap("Fallback", 7.25, 22.25),
            ]

        frozen_now = self.BOUNDARY - book_week.timedelta(minutes=2)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        with (
            self.policy_patch(
                rooms=["Preferred", "Fallback"],
                horizons={
                    "Preferred": 3 * 24 * 60,
                    "Fallback": 5 * 24 * 60,
                },
            ),
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(book_week, "navigate_to_day", side_effect=navigate),
            mock.patch.object(book_week, "get_available_slots", side_effect=available),
            mock.patch.object(book_week, "load_extendable_bookings", return_value=[]),
        ):
            candidates, _ = book_week.find_all_snipe_candidates_multi_day(
                page,
                tracker,
                {"enabled": False, "strict_mode": False},
                0,
                target_boundary=self.BOUNDARY,
            )

        self.assertEqual(navigated, [3, 5])
        self.assertEqual(
            [candidate["room"] for candidate in candidates],
            ["Preferred", "Fallback"],
        )

    def test_site_clock_lower_bound_drives_a_safe_early_local_deadline(self):
        with mock.patch.object(
            book_week,
            "SITE_CLOCK_OFFSET_BOUNDS",
            (1.20, 1.32),
        ):
            deadline, bounds = book_week.site_aligned_save_deadline(self.BOUNDARY)
            latency = book_week.estimated_site_latency_bounds(
                deadline,
                self.BOUNDARY,
            )

        self.assertEqual(bounds, (1.20, 1.32))
        self.assertAlmostEqual(
            (deadline - self.BOUNDARY).total_seconds(),
            -1.15,
            places=6,
        )
        self.assertAlmostEqual(latency[0], 0.05, places=6)
        self.assertAlmostEqual(latency[1], 0.17, places=6)

    def test_single_candidate_mode_stops_after_top_room_date_is_free(self):
        tracker = mock.MagicMock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.can_book.return_value = (True, "")
        page = mock.MagicMock()
        navigated = []
        frozen_now = self.BOUNDARY - book_week.timedelta(minutes=2)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        def navigate(_page, target, _current, **_kwargs):
            navigated.append(target)
            return target

        with (
            self.policy_patch(
                rooms=["Preferred", "Fallback"],
                horizons={
                    "Preferred": 3 * 24 * 60,
                    "Fallback": 5 * 24 * 60,
                },
            ),
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(book_week, "navigate_to_day", side_effect=navigate),
            mock.patch.object(
                book_week,
                "get_available_slots",
                return_value=[self.room_gap("Preferred", 7.25, 22.25)],
            ),
            mock.patch.object(book_week, "load_extendable_bookings", return_value=[]),
        ):
            candidates, _ = book_week.find_all_snipe_candidates_multi_day(
                page,
                tracker,
                {"enabled": False, "strict_mode": False},
                0,
                target_boundary=self.BOUNDARY,
                candidate_limit=1,
            )

        self.assertEqual(navigated, [3])
        self.assertEqual([item["room"] for item in candidates], ["Preferred"])


class FreshBoundaryValidationTests(unittest.TestCase):
    def test_exact_event_check_is_required_after_refilling_end_time(self):
        page = mock.MagicMock()
        end_input = mock.MagicMock()
        end_input.first = end_input
        end_input.count.return_value = 1
        end_input.is_visible.return_value = True
        page.locator.return_value = end_input

        response = mock.MagicMock()
        response.url = "https://rwcmd.asimut.net/services/v2/event/type=check"
        response.ok = True
        pending = mock.MagicMock()
        pending.value = response
        response_context = mock.MagicMock()
        response_context.__enter__.return_value = pending
        page.expect_response.return_value = response_context

        success, detail = book_week.refresh_new_booking_validation(page, "14:00")

        self.assertTrue(success, detail)
        end_input.fill.assert_called_once_with("14:00")
        end_input.press.assert_called_once_with("Tab")
        page.wait_for_timeout.assert_called_once_with(300)

    def test_missing_fresh_event_check_aborts_validation(self):
        page = mock.MagicMock()
        end_input = mock.MagicMock()
        end_input.first = end_input
        end_input.count.return_value = 1
        end_input.is_visible.return_value = True
        page.locator.return_value = end_input
        page.expect_response.side_effect = TimeoutError("no response")

        success, detail = book_week.refresh_new_booking_validation(page, "14:00")

        self.assertFalse(success)
        self.assertIn("did not complete", detail)


if __name__ == "__main__":
    unittest.main()
