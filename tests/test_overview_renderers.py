import contextlib
import copy
import io
import unittest
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import book_week


def _install_test_live_policy(today, *, window_days):
    """Install a valid non-default live window for run_booking integration."""

    observed_at = datetime.combine(today, time(12), tzinfo=timezone.utc)
    final_date = today + timedelta(days=window_days - 1)
    room_names = ("B0.29", "B1.09")
    room_location_ids = {"B0.29": 96, "B1.09": 87}
    policy = book_week.LiveRoomPolicy(
        observed_at=observed_at,
        booking_horizon=datetime.combine(
            final_date,
            time(12),
            tzinfo=timezone.utc,
        ),
        global_horizon_minutes=(window_days - 1) * 24 * 60,
        room_order=("B0.29", "B1.09"),
        room_horizon_minutes={"B0.29": 3 * 24 * 60, "B1.09": 2 * 24 * 60},
        room_metadata={
            "B0.29": {
                "instrument_tags": [],
                "room_type_tags": [],
                "features": [],
            },
            "B1.09": {
                "instrument_tags": [],
                "room_type_tags": [],
                "features": [],
            },
        },
        site_minimum_booking_minutes=30,
        site_maximum_booking_minutes=120,
        site_minimum_booking_gap_minutes=60,
        minimum_block_minutes=30,
        allow_fragmented_sessions=True,
        all_room_names=room_names,
        all_room_location_ids=room_location_ids,
        overview_url=book_week.canonical_overview_url(
            room_names,
            room_location_ids,
        ),
    )
    return book_week.install_live_room_policy(policy, today=today)


class _SnapshotPage:
    def __init__(self, snapshot):
        self.url = book_week.ASIMUT_OVERVIEW_URL
        self.snapshot = snapshot
        self.scripts = []
        self.arguments = []

    def evaluate(self, script, argument=None):
        self.scripts.append(script)
        self.arguments.append(argument)
        return copy.deepcopy(self.snapshot)

    def wait_for_timeout(self, _milliseconds):
        return None


class _SequenceSnapshotPage(_SnapshotPage):
    def __init__(self, snapshots):
        super().__init__(snapshots[-1])
        self.snapshots = list(snapshots)

    def evaluate(self, script, argument=None):
        self.scripts.append(script)
        self.arguments.append(argument)
        if len(self.snapshots) > 1:
            return copy.deepcopy(self.snapshots.pop(0))
        return copy.deepcopy(self.snapshots[0])


class _DelayedFormControl:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return self.page.wait_count >= 1


class _DelayedFormPage:
    def __init__(self):
        self.url = (
            "https://rwcmd.asimut.net/event?eventId=0&prefillTime=true&"
            "start=2026-09-01T11%3A00%3A00.000%2B01%3A00&categoryId=56&locationId=85"
        )
        self.wait_count = 0

    def locator(self, _selector):
        return _DelayedFormControl(self)

    def wait_for_timeout(self, _milliseconds):
        self.wait_count += 1


class CalendarNavigationTests(unittest.TestCase):
    def test_semantic_date_button_ignores_hidden_generic_chevron(self):
        page = mock.MagicMock()
        button = mock.MagicMock()
        button.is_visible.return_value = True
        button.is_enabled.return_value = True
        primary = mock.MagicMock()
        primary.count.return_value = 1
        primary.nth.return_value = button
        absent = mock.MagicMock()
        absent.count.return_value = 0

        def locator(selector):
            if selector == "button[data-cy='decrement-date-chevron']":
                return primary
            return absent

        page.locator.side_effect = locator

        result = book_week._calendar_navigation_button(page, -1)

        self.assertIs(result, button)
        page.locator.assert_called_once_with(
            "button[data-cy='decrement-date-chevron']"
        )

    def test_ambiguous_visible_date_buttons_fail_closed(self):
        page = mock.MagicMock()
        first = mock.MagicMock()
        second = mock.MagicMock()
        first.is_visible.return_value = True
        second.is_visible.return_value = True
        locator = mock.MagicMock()
        locator.count.return_value = 2
        locator.nth.side_effect = [first, second]
        page.locator.return_value = locator

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            book_week._calendar_navigation_button(page, 1)

    def test_transient_grid_timeout_does_not_double_click_date_control(self):
        today = datetime.now().date()
        next_date = today + timedelta(days=1)
        page = mock.MagicMock()
        button = mock.MagicMock()

        with (
            mock.patch.object(book_week, "assert_calendar_date"),
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(
                book_week,
                "get_visible_calendar_dates",
                side_effect=[{today}, {next_date}],
            ),
            mock.patch.object(
                book_week,
                "_calendar_navigation_button",
                return_value=button,
            ),
            mock.patch.object(
                book_week,
                "wait_for_practice_room_grid",
                side_effect=[RuntimeError("transient grid"), None],
            ) as wait_grid,
        ):
            result = book_week._walk_calendar_days(
                page,
                1,
                0,
                today=today,
            )

        self.assertEqual(result, 1)
        button.click.assert_called_once_with(timeout=5000)
        self.assertEqual(wait_grid.call_count, 2)

    def test_navigation_retries_once_from_canonical_today_grid(self):
        page = mock.MagicMock()
        with (
            mock.patch.object(
                book_week,
                "_walk_calendar_days",
                side_effect=[RuntimeError("stale date bar"), 6],
            ) as walk,
            mock.patch.object(book_week, "open_practice_room_overview") as reopen,
        ):
            result = book_week.navigate_to_day(page, 6, 7)

        self.assertEqual(result, 6)
        today = datetime.now().date()
        self.assertEqual(walk.call_args_list[0].args[:3], (page, 6, 7))
        self.assertEqual(walk.call_args_list[1].args[:3], (page, 6, 0))
        reopen.assert_called_once_with(page, today)


class OverviewRendererTests(unittest.TestCase):
    def setUp(self):
        # Renderer unit tests that do not explicitly install a live policy must
        # not inherit another test class's process-global run policy.
        saved_policy = book_week.ACTIVE_ROOM_POLICY
        saved_url = book_week.ASIMUT_OVERVIEW_URL
        self.addCleanup(setattr, book_week, "ACTIVE_ROOM_POLICY", saved_policy)
        self.addCleanup(setattr, book_week, "ASIMUT_OVERVIEW_URL", saved_url)
        book_week.ACTIVE_ROOM_POLICY = None
        book_week.ASIMUT_OVERVIEW_URL = f"{book_week.ASIMUT_BASE_URL}/overview"

    def svg_snapshot(self):
        return {
            "renderer": "svg",
            "rooms": [
                {
                    "room": "B1.09\u00a0(Practice: Grand Piano)",
                    "blockedRanges": [
                        {"startHour": 7.0, "endHour": 8.0, "closed": True},
                        {"startHour": 9.0, "endHour": 10.0, "closed": False},
                        {"startHour": 22.0, "endHour": 23.0, "closed": True},
                    ],
                    "clickOriginX": 100.0,
                    "clickPixelsPerHour": 60.0,
                    "clickY": 45.0,
                }
            ],
        }

    def test_svg_room_labels_are_reduced_to_configured_room_tokens(self):
        page = _SnapshotPage(self.svg_snapshot())

        snapshot = book_week.get_practice_room_grid_snapshot(page)

        self.assertEqual(snapshot["rooms"][0]["room"], "B1.09")
        script = page.scripts[0]
        self.assertIn("app-overview-svg", script)
        self.assertIn("a[data-location-id]", script)
        self.assertIn("rect.closed-hours, rect.event-overlay", script)
        self.assertIn("7 + item.x / 60", script)
        self.assertIn("rowIndex: uniqueLabels.length", script)
        self.assertIn("const rowTop = 30 * rowIndex", script)

    def test_site_named_room_is_not_truncated_to_its_first_word(self):
        snapshot = self.svg_snapshot()
        snapshot["rooms"][0]["room"] = "The Hopkins Studio"
        page = _SnapshotPage(snapshot)

        result = book_week.get_practice_room_grid_snapshot(
            page,
            configured_rooms=("The Hopkins Studio",),
        )

        self.assertEqual(result["rooms"][0]["room"], "The Hopkins Studio")
        self.assertEqual(page.arguments[0], ["The Hopkins Studio"])

    def test_overlapping_room_names_choose_the_unique_longest_match(self):
        configured = ("Studio", "Studio 1")

        self.assertEqual(
            book_week._normalize_practice_room_name(
                "Studio 1 (Practice room)", configured
            ),
            "Studio 1",
        )
        self.assertEqual(
            book_week._normalize_practice_room_name("Studio", configured),
            "Studio",
        )

    def test_svg_blockers_produce_free_intervals_and_screen_coordinates(self):
        page = _SnapshotPage(self.svg_snapshot())

        rooms = book_week.get_available_slots(page)

        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["room"], "B1.09")
        self.assertEqual(
            [(slot["startHour"], slot["endHour"]) for slot in rooms[0]["slots"]],
            [(8.0, 9.0), (10.0, 22.0)],
        )
        self.assertEqual(rooms[0]["slots"][0]["clickX"], 190.0)
        self.assertEqual(rooms[0]["slots"][0]["clickY"], 45.0)

    def test_coordinate_lookup_scrolls_svg_label_before_refreshing_transform(self):
        page = _SnapshotPage({"x": 640.0, "y": 75.0, "renderer": "svg"})

        with mock.patch.object(book_week, "LIVE_CATALOG_ROOM_NAMES", ["B1.09"]):
            coordinates = book_week.get_room_slot_coordinates(
                page,
                "B1.09\u00a0(Practice: Grand Piano)",
                16.0,
                17.0,
            )

        self.assertEqual(coordinates["renderer"], "svg")
        self.assertEqual(page.arguments[0], ["B1.09", 16.5, ["B1.09"]])
        script = page.scripts[0]
        self.assertIn("configuredRoomFromText(item.textContent) === roomName", script)
        self.assertLess(script.index("label.scrollIntoView"), script.index("svg.getScreenCTM"))
        self.assertIn("point.x = 60 * (centerHour - 7)", script)
        self.assertIn("point.y = 30 * rowIndex + 15", script)
        self.assertIn(".location-day", script)

    def test_grid_readiness_accepts_svg_without_waiting_on_legacy_class(self):
        page = _SnapshotPage(self.svg_snapshot())
        expected_date = datetime.now().date()

        with (
            mock.patch.object(
                book_week,
                "LIVE_CATALOG_ROOM_NAMES",
                ["B1.09", "B1.10"],
            ),
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            snapshot = book_week.wait_for_practice_room_grid(
                page,
                expected_date,
                timeout_ms=100,
            )

        self.assertEqual(snapshot["renderer"], "svg")
        assert_date.assert_called_once()
        self.assertFalse(hasattr(page, "wait_for_selector"))

    def test_installed_policy_uses_exact_dynamic_all_locations_route(self):
        today = datetime.now().date()
        policy = _install_test_live_policy(today, window_days=4)

        self.assertEqual(book_week.ASIMUT_OVERVIEW_URL, policy.overview_url)
        self.assertTrue(
            book_week._is_exact_practice_room_overview_url(
                "https://rwcmd.asimut.net/overview?locationGroupId=0&"
                "locationIds=96,87"
            )
        )
        for stale_or_changed in (
            "https://rwcmd.asimut.net/overview?locationGroupId=10",
            "https://rwcmd.asimut.net/overview?locationGroupId=0&locationIds=87,96",
            "https://rwcmd.asimut.net/overview?locationGroupId=0&locationIds=96,87,93",
        ):
            with self.subTest(url=stale_or_changed):
                self.assertFalse(
                    book_week._is_exact_practice_room_overview_url(stale_or_changed)
                )

    def test_grid_waits_until_every_fresh_catalog_room_is_present(self):
        today = datetime.now().date()
        _install_test_live_policy(today, window_days=4)
        missing = self.svg_snapshot()
        complete = copy.deepcopy(missing)
        second = copy.deepcopy(missing["rooms"][0])
        second["room"] = "B0.29"
        complete["rooms"].append(second)
        page = _SequenceSnapshotPage([missing, complete, complete])

        with (
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            result = book_week.wait_for_practice_room_grid(
                page,
                today,
                timeout_ms=100,
            )

        self.assertEqual(
            {item["room"] for item in result["rooms"]},
            {"B0.29", "B1.09"},
        )
        self.assertEqual(len(page.scripts), 3)
        assert_date.assert_called_once()

    def test_missing_fresh_catalog_room_cannot_be_used_for_slots(self):
        today = datetime.now().date()
        _install_test_live_policy(today, window_days=4)
        page = _SnapshotPage(self.svg_snapshot())

        with self.assertRaisesRegex(RuntimeError, "missing one or more"):
            book_week.get_available_slots(page)

    def test_grid_readiness_waits_for_svg_event_overlays(self):
        complete = self.svg_snapshot()
        incomplete = copy.deepcopy(complete)
        incomplete["rooms"][0]["blockedRanges"] = [
            item
            for item in incomplete["rooms"][0]["blockedRanges"]
            if item["closed"]
        ]
        page = _SequenceSnapshotPage([incomplete, complete])

        with (
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            snapshot = book_week.wait_for_practice_room_grid(
                page,
                datetime.now().date(),
                timeout_ms=100,
            )

        self.assertEqual(snapshot["renderer"], "svg")
        self.assertEqual(snapshot["rooms"][0]["room"], "B1.09")
        self.assertTrue(
            any(
                item["closed"] is False
                for item in snapshot["rooms"][0]["blockedRanges"]
            )
        )
        self.assertEqual(len(page.scripts), 3)
        assert_date.assert_called_once()

    def test_grid_readiness_allows_empty_room_when_another_room_has_events(self):
        snapshot = self.svg_snapshot()
        empty_room = copy.deepcopy(snapshot["rooms"][0])
        empty_room["room"] = "B1.10"
        empty_room["blockedRanges"] = [
            item for item in empty_room["blockedRanges"] if item["closed"]
        ]
        snapshot["rooms"].append(empty_room)
        page = _SnapshotPage(snapshot)

        with (
            mock.patch.object(
                book_week,
                "LIVE_CATALOG_ROOM_NAMES",
                ["B1.09", "B1.10"],
            ),
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            result = book_week.wait_for_practice_room_grid(
                page,
                datetime.now().date(),
                timeout_ms=100,
            )

        self.assertEqual([room["room"] for room in result["rooms"]], ["B1.09", "B1.10"])
        self.assertEqual(len(page.scripts), 2)
        assert_date.assert_called_once()

    def test_grid_readiness_accepts_stably_empty_svg_day(self):
        empty = self.svg_snapshot()
        empty["rooms"][0]["blockedRanges"] = [
            item for item in empty["rooms"][0]["blockedRanges"] if item["closed"]
        ]
        page = _SnapshotPage(empty)

        with (
            mock.patch.object(book_week, "_SVG_EMPTY_GRID_STABLE_POLLS", 3),
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            result = book_week.wait_for_practice_room_grid(
                page,
                datetime.now().date(),
                timeout_ms=100,
            )

        self.assertEqual(result["renderer"], "svg")
        self.assertFalse(
            any(
                item["closed"] is False
                for item in result["rooms"][0]["blockedRanges"]
            )
        )
        self.assertEqual(len(page.scripts), 3)
        assert_date.assert_called_once()

    def test_labels_only_transient_cannot_win_before_overlays_stabilize(self):
        complete = self.svg_snapshot()
        labels_only = copy.deepcopy(complete)
        labels_only["rooms"][0]["blockedRanges"] = [
            item
            for item in labels_only["rooms"][0]["blockedRanges"]
            if item["closed"]
        ]
        page = _SequenceSnapshotPage(
            [labels_only, labels_only, complete, complete]
        )

        with (
            mock.patch.object(book_week, "_SVG_EMPTY_GRID_STABLE_POLLS", 3),
            mock.patch.object(book_week, "page_is_authenticated", return_value=True),
            mock.patch.object(book_week, "assert_calendar_date") as assert_date,
        ):
            result = book_week.wait_for_practice_room_grid(
                page,
                datetime.now().date(),
                timeout_ms=100,
            )

        self.assertTrue(
            any(
                item["closed"] is False
                for item in result["rooms"][0]["blockedRanges"]
            )
        )
        self.assertEqual(len(page.scripts), 4)
        assert_date.assert_called_once()

    def test_stable_grid_signature_rejects_ambiguous_or_invalid_rows(self):
        duplicate = self.svg_snapshot()
        duplicate["rooms"].append(copy.deepcopy(duplicate["rooms"][0]))
        invalid_range = self.svg_snapshot()
        invalid_range["rooms"][0]["blockedRanges"][0]["endHour"] = None

        for snapshot in (duplicate, invalid_range):
            with self.subTest(snapshot=snapshot):
                self.assertIsNone(
                    book_week._practice_room_grid_content_signature(snapshot)
                )

    def test_overview_open_retries_one_incomplete_spa_load(self):
        page = object()
        recovered = self.svg_snapshot()
        with (
            mock.patch.object(book_week, "safe_goto") as goto,
            mock.patch.object(
                book_week,
                "wait_for_practice_room_grid",
                side_effect=[RuntimeError("renderer empty"), recovered],
            ) as wait_grid,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = book_week.open_practice_room_overview(
                page,
                datetime.now().date(),
            )

        self.assertEqual(result, recovered)
        self.assertEqual(goto.call_count, 2)
        self.assertEqual(wait_grid.call_count, 2)

    def test_overview_refresh_reloads_today_then_restores_future_date(self):
        page = object()
        today = datetime.now().date()
        expected_date = today + timedelta(days=7)
        today_snapshot = self.svg_snapshot()
        recovered = copy.deepcopy(today_snapshot)
        recovered["rooms"][0]["blockedRanges"][1]["startHour"] = 10.0
        call_order = mock.Mock()
        with (
            mock.patch.object(book_week, "safe_reload") as reload_page,
            mock.patch.object(
                book_week,
                "wait_for_practice_room_grid",
                side_effect=[today_snapshot, recovered],
            ) as wait_grid,
            mock.patch.object(book_week, "navigate_to_day", return_value=7) as navigate,
        ):
            call_order.attach_mock(reload_page, "reload")
            call_order.attach_mock(wait_grid, "wait")
            call_order.attach_mock(navigate, "navigate")

            result = book_week.refresh_practice_room_overview(
                page,
                expected_date,
                base_date=today,
            )

        self.assertEqual(result, recovered)
        self.assertEqual(
            call_order.mock_calls,
            [
                mock.call.reload(page),
                mock.call.wait(page, today),
                mock.call.navigate(page, 7, 0, base_date=today),
                mock.call.wait(page, expected_date),
            ],
        )

    def test_overview_refresh_today_needs_no_calendar_navigation(self):
        page = object()
        today = datetime.now().date()
        recovered = self.svg_snapshot()
        with (
            mock.patch.object(book_week, "safe_reload") as reload_page,
            mock.patch.object(
                book_week,
                "wait_for_practice_room_grid",
                return_value=recovered,
            ) as wait_grid,
            mock.patch.object(book_week, "navigate_to_day") as navigate,
        ):
            result = book_week.refresh_practice_room_overview(
                page,
                today,
                base_date=today,
            )

        self.assertEqual(result, recovered)
        reload_page.assert_called_once_with(page)
        wait_grid.assert_called_once_with(page, today)
        navigate.assert_not_called()

    def test_overview_refresh_rejects_past_date_before_reload(self):
        page = object()
        today = datetime.now().date()
        with (
            mock.patch.object(book_week, "safe_reload") as reload_page,
            self.assertRaisesRegex(RuntimeError, "before the run's base date"),
        ):
            book_week.refresh_practice_room_overview(
                page,
                today - timedelta(days=1),
                base_date=today,
            )

        reload_page.assert_not_called()

    def test_delayed_booking_menu_and_aria_labelled_form_are_supported(self):
        option = mock.MagicMock()
        option.count.return_value = 1
        option.is_visible.return_value = True
        locator = mock.MagicMock()
        locator.first = option
        page = mock.MagicMock()
        page.url = book_week.ASIMUT_OVERVIEW_URL
        page.locator.return_value = locator

        found = book_week.wait_for_student_booking_option(page)

        self.assertIs(found, option)
        page.locator.assert_called_once_with(
            "mat-list-item:has-text('Student booking provisional')"
        )
        page.evaluate.return_value = {
            "room": "B1.09",
            "date": "2026-08-31",
            "start": "09:00",
            "end": "10:00",
        }
        book_week.page_booking_snapshot(page)
        snapshot_script = page.evaluate.call_args.args[0]
        self.assertIn("input[aria-label='Start time']", snapshot_script)
        self.assertIn("input[aria-label='End time']", snapshot_script)
        self.assertIn("input[aria-label='Event date']", snapshot_script)
        self.assertIn("input[aria-label='Event location']", snapshot_script)
        self.assertIn("element.getAttribute && element.getAttribute('aria-label')", snapshot_script)

    def test_new_booking_form_waits_for_delayed_time_controls(self):
        page = _DelayedFormPage()

        self.assertTrue(book_week.wait_for_new_booking_form(page, timeout_ms=500))
        self.assertEqual(page.wait_count, 1)


class CheckOnlyRendererIntegrationTests(unittest.TestCase):
    def test_check_only_traverses_svg_grids_through_renderer_abstraction(self):
        args = SimpleNamespace(headless=True, check_only=True)
        expected_window_days = 5
        page = mock.MagicMock()
        context = mock.MagicMock()
        context.new_page.return_value = page
        browser = mock.MagicMock()
        browser.new_context.return_value = context
        playwright = mock.MagicMock()
        playwright.chromium.launch.return_value = browser
        playwright_context = mock.MagicMock()
        playwright_context.__enter__.return_value = playwright
        call_order = []

        def refresh_policy(_page, _preferences, *, today=None):
            call_order.append("refresh")
            return _install_test_live_policy(
                today,
                window_days=expected_window_days,
            )

        def scan_agenda(*_args, **_kwargs):
            call_order.append("scan")
            return 0, []

        refresh_policy_mock = mock.Mock(side_effect=refresh_policy)
        agenda_scan_mock = mock.Mock(side_effect=scan_agenda)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.multiple(
                book_week,
                refresh_live_room_policy=refresh_policy_mock,
                scan_agenda=agenda_scan_mock,
            ),
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(book_week, "load_ignored_events", return_value=[]),
            mock.patch.object(book_week, "open_practice_room_overview") as open_grid,
            mock.patch.object(
                book_week,
                "navigate_to_day",
                side_effect=lambda _page, target, _current, **_kwargs: target,
            ) as navigate,
            mock.patch.object(book_week, "wait_for_practice_room_grid") as wait_grid,
            mock.patch.object(
                book_week,
                "get_practice_room_names",
                return_value={"B0.29", "B1.09"},
            ) as room_names,
            mock.patch.object(
                book_week,
                "get_available_slots",
                return_value=[{"room": "B0.29", "slots": []}],
            ) as available,
            mock.patch.object(book_week, "persist_storage_state"),
        ):
            result = book_week.run_booking(args, {}, book_week.PracticePlan())

        self.assertEqual(result, 0)
        refresh_policy_mock.assert_called_once()
        agenda_scan_mock.assert_called_once()
        self.assertEqual(call_order, ["refresh", "scan"])
        self.assertEqual(
            len(agenda_scan_mock.call_args.kwargs["window_dates"]),
            expected_window_days,
        )
        open_grid.assert_called_once()
        self.assertEqual(navigate.call_count, expected_window_days - 1)
        self.assertEqual(wait_grid.call_count, expected_window_days)
        self.assertEqual(room_names.call_count, expected_window_days)
        self.assertEqual(available.call_count, expected_window_days)


if __name__ == "__main__":
    unittest.main()
