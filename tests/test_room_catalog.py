import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from room_catalog import (
    CATALOG_VERSION,
    SITE_TIMEZONE,
    RoomCatalogError,
    build_catalog,
    load_cached_catalog,
    normalize_html,
    normalize_html_items,
    parse_booking_category,
    parse_description_horizon_hint,
    parse_horizon_issues,
    parse_location_group_meta,
    parse_location_info,
    parse_session_context,
    refresh_from_site,
    save_catalog,
)


LONDON = SITE_TIMEZONE
OBSERVED = datetime(2026, 8, 30, 6, 25, tzinfo=LONDON)
GLOBAL_CUTOFF = datetime(2026, 9, 6, 6, 25, tzinfo=LONDON)


def session_payload(**me_updates):
    me = {
        "booking_horizon": GLOBAL_CUTOFF.isoformat(),
        "minimum_booking_length": 30,
        "maximum_booking_length": 120,
        "minimum_booking_gap": 60,
        "visible_horizon": "2027-01-01T00:00:00+00:00",
    }
    me.update(me_updates)
    return {"response": {"session_context": {"me": me}}}


def category_payload():
    return {
        "response": {
            "success": True,
            "categories": [
                {
                    "id": 56,
                    "name": "Student booking provisional",
                    "my_category": True,
                    "publicevent_usehorizons": "true",
                    "publicevent_usequotas": "true",
                },
                {
                    "id": 77,
                    "name": "Chamber provisional",
                    "my_category": True,
                    "publicevent_usehorizons": "true",
                    "publicevent_usequotas": "false",
                },
            ],
        }
    }


ROOMS = (
    (11, "B0.11", "Music Practice Room"),
    (12, "B0.29", "Piano Practice Room"),
    (13, "B1.09", "Music Practice Room"),
)


def meta_payload(rooms=ROOMS):
    return {
        "response": {
            "success": True,
            "locations": [
                {
                    "id": location_id,
                    "name": name,
                    "secondary_name": secondary,
                    "closed_hours": [],
                }
                for location_id, name, secondary in rooms
            ],
        }
    }


def info_payload(rooms=ROOMS):
    descriptions = {
        "B0.11": "<p>Large room &amp; natural light.</p>",
        "B0.29": "<p>Can only be booked 4.5 days in advance.</p><p>Adjustable stool</p>",
        "B1.09": "<div>Can only be booked three days ahead.</div>",
    }
    inventories = {
        "B0.11": "<ul><li>1 x Music stand</li></ul>",
        "B0.29": "<ul><li>1 x Steinway Grand Piano</li><li>2 × Music stands</li></ul>",
        "B1.09": "<p>1 x Upright Piano</p>",
    }
    return {
        "response": {
            "success": True,
            "locationsInfo": [
                {
                    "id": location_id,
                    "name": name,
                    "secondary_name": secondary,
                    "description": descriptions.get(name, ""),
                    "inventory": inventories.get(name, ""),
                    "default_opening_hours": [],
                    "exceptional_opening_hours": [],
                }
                for location_id, name, secondary in rooms
            ],
        }
    }


def issue(text, *, issue_class="message-warning", issue_type="date-time"):
    return {"class": issue_class, "text": text, "type": issue_type}


def global_issue():
    return issue(
        "You are not allowed to create or modify bookings "
        "that end later than 6/9/26 06:25"
    )


def check_payload(room, room_cutoff=None, *, additional=()):
    issues = [global_issue()]
    if room_cutoff is not None:
        issues.append(
            issue(
                "You are not allowed to create or modify bookings "
                f"in {room} that end later than {room_cutoff}"
            )
        )
    issues.extend(additional)
    return {
        "response": {
            "success": False,
            "bookingrules": {"issues": issues},
        }
    }


def complete_checks():
    return {
        11: check_payload("B0.11"),
        12: check_payload("B0.29", "3/9/26 18:25"),
        13: check_payload("B1.09", "2/9/26 06:25"),
    }


def complete_catalog():
    return build_catalog(
        session_payload=session_payload(),
        location_meta_payload=meta_payload(),
        location_info_payload=info_payload(),
        check_payloads=complete_checks(),
        observed_at=OBSERVED,
        booking_category_id=56,
    )


class SessionPolicyTests(unittest.TestCase):
    def test_exact_live_limits_and_arbitrary_global_horizon(self):
        policy = parse_session_context(session_payload(), OBSERVED)

        self.assertEqual(policy.observed_at, OBSERVED)
        self.assertEqual(policy.booking_horizon, GLOBAL_CUTOFF)
        self.assertEqual(policy.global_horizon_minutes, 7 * 24 * 60)
        self.assertEqual(policy.minimum_booking_minutes, 30)
        self.assertEqual(policy.maximum_booking_minutes, 120)
        self.assertEqual(policy.minimum_booking_gap_minutes, 60)

    def test_observed_seconds_are_floored_to_match_site_minute_evidence(self):
        observed = OBSERVED.replace(second=59, microsecond=999999)
        policy = parse_session_context(session_payload(), observed)
        self.assertEqual(policy.observed_at, OBSERVED)
        self.assertEqual(policy.global_horizon_minutes, 10080)

    def test_naive_or_non_quarter_hour_policy_fails_closed(self):
        with self.assertRaises(RoomCatalogError):
            parse_session_context(session_payload(), OBSERVED.replace(tzinfo=None))
        with self.assertRaises(RoomCatalogError):
            parse_session_context(
                session_payload(booking_horizon="2026-09-06T06:26:00+01:00"),
                OBSERVED,
            )
        with self.assertRaises(RoomCatalogError):
            parse_session_context(session_payload(minimum_booking_length=31), OBSERVED)
        with self.assertRaises(RoomCatalogError):
            parse_session_context(
                session_payload(minimum_booking_length=120, maximum_booking_length=30),
                OBSERVED,
            )

    def test_dst_crossing_uses_local_policy_duration(self):
        observed = datetime(2026, 10, 20, 6, 25, tzinfo=LONDON)
        horizon = datetime(2026, 10, 27, 6, 25, tzinfo=LONDON)
        policy = parse_session_context(
            session_payload(booking_horizon=horizon.isoformat()), observed
        )
        self.assertEqual(policy.global_horizon_minutes, 7 * 24 * 60)


class LiveResponseParserTests(unittest.TestCase):
    def test_dynamic_booking_category_is_selected_without_id_hardcode(self):
        payload = category_payload()
        payload["response"]["categories"][0]["id"] = 904
        self.assertEqual(parse_booking_category(payload), 904)

        payload["response"]["categories"][1]["publicevent_usequotas"] = "true"
        with self.assertRaises(RoomCatalogError):
            parse_booking_category(payload)

    def test_group_and_combined_info_cross_check_every_identity(self):
        locations = parse_location_group_meta(meta_payload())
        details = parse_location_info(info_payload(), locations)

        self.assertEqual(tuple(item.name for item in locations), tuple(row[1] for row in ROOMS))
        self.assertEqual(set(details), {11, 12, 13})
        self.assertEqual(details[12].description_items[0], "Can only be booked 4.5 days in advance.")
        self.assertEqual(details[12].inventory_items[0], "1 x Steinway Grand Piano")

    def test_missing_duplicate_or_mismatched_locations_fail_closed(self):
        locations = parse_location_group_meta(meta_payload())
        missing = info_payload()
        missing["response"]["locationsInfo"].pop()
        with self.assertRaises(RoomCatalogError):
            parse_location_info(missing, locations)

        duplicate = meta_payload()
        duplicate["response"]["locations"].append(
            dict(duplicate["response"]["locations"][0])
        )
        with self.assertRaises(RoomCatalogError):
            parse_location_group_meta(duplicate)

        mismatch = info_payload()
        mismatch["response"]["locationsInfo"][0]["name"] = "B0.12"
        with self.assertRaises(RoomCatalogError):
            parse_location_info(mismatch, locations)

    def test_html_normalization_decodes_entities_and_ignores_active_content(self):
        html = (
            "<p>Steinway &amp; Sons&nbsp;B</p><script>secret()</script>"
            "<ul><li>Adjustable stool</li><li>Adjustable   stool</li></ul>"
        )
        self.assertEqual(
            normalize_html_items(html),
            ("Steinway & Sons B", "Adjustable stool"),
        )
        self.assertEqual(normalize_html(html), "Steinway & Sons B\nAdjustable stool")

    def test_explicit_description_hints_support_words_and_fractional_days(self):
        self.assertEqual(
            parse_description_horizon_hint("Can only be booked three days ahead."),
            3 * 24 * 60,
        )
        self.assertEqual(
            parse_description_horizon_hint("Booking horizon: 4.5 days in advance"),
            int(4.5 * 24 * 60),
        )
        self.assertEqual(
            parse_description_horizon_hint(
                "This room may only be booked thirty-one days ahead"
            ),
            31 * 24 * 60,
        )
        self.assertIsNone(parse_description_horizon_hint("A bright piano room."))
        with self.assertRaises(RoomCatalogError):
            parse_description_horizon_hint(
                "Can only be booked three days ahead; can only be booked five days ahead."
            )


class HorizonEvidenceTests(unittest.TestCase):
    def test_global_only_and_arbitrary_room_specific_cutoffs(self):
        inherited = parse_horizon_issues(
            check_payload("B0.11"), "B0.11", GLOBAL_CUTOFF, OBSERVED
        )
        self.assertEqual(inherited.booking_cutoff, GLOBAL_CUTOFF)
        self.assertEqual(inherited.horizon_minutes, 10080)

        arbitrary = parse_horizon_issues(
            check_payload("B0.29", "3/9/26 18:25"),
            "B0.29",
            GLOBAL_CUTOFF,
            OBSERVED,
        )
        self.assertEqual(arbitrary.horizon_minutes, 4 * 24 * 60 + 12 * 60)

    def test_moving_global_cutoff_is_validated_by_duration_at_check_minute(self):
        shifted = check_payload("B0.11")
        shifted["response"]["bookingrules"]["issues"][0]["text"] = (
            "You are not allowed to create or modify bookings "
            "that end later than 6/9/26 06:26"
        )

        evidence = parse_horizon_issues(
            shifted,
            "B0.11",
            GLOBAL_CUTOFF,
            OBSERVED + timedelta(minutes=1),
            expected_global_horizon_minutes=7 * 24 * 60,
        )

        self.assertEqual(evidence.horizon_minutes, 7 * 24 * 60)
        self.assertEqual(
            evidence.booking_cutoff,
            GLOBAL_CUTOFF + timedelta(minutes=1),
        )

    def test_unrelated_general_information_is_ignored(self):
        evidence = parse_horizon_issues(
            check_payload(
                "B0.11",
                additional=(
                    issue(
                        "This booking will be provisional",
                        issue_class="message-info",
                        issue_type="general",
                    ),
                ),
            ),
            "B0.11",
            GLOBAL_CUTOFF,
            OBSERVED,
        )
        self.assertEqual(evidence.horizon_minutes, 10080)

    def test_incomplete_ambiguous_or_inconsistent_cutoff_evidence_fails_closed(self):
        bad_payloads = []
        bad_payloads.append(
            {"response": {"success": False, "bookingrules": {"issues": []}}}
        )
        bad_payloads.append(
            {
                "response": {
                    "success": False,
                    "bookingrules": {"issues": [global_issue(), global_issue()]},
                }
            }
        )
        bad_payloads.append(check_payload("B0.29", "7/9/26 06:25"))
        bad_payloads.append(check_payload("B1.09", "2/9/26 06:25"))
        bad_payloads.append(
            check_payload(
                "B0.29",
                additional=(
                    issue("An unfamiliar horizon warning", issue_type="date-time"),
                ),
            )
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(RoomCatalogError):
                    parse_horizon_issues(
                        payload, "B0.29", GLOBAL_CUTOFF, OBSERVED
                    )

        changed_global = check_payload("B0.29")
        changed_global["response"]["bookingrules"]["issues"][0]["text"] = (
            "You are not allowed to create or modify bookings "
            "that end later than 6/9/26 06:40"
        )
        with self.assertRaisesRegex(
            RoomCatalogError,
            r"check=2026-09-06T06:40\+01:00, session=2026-09-06T06:25\+01:00",
        ):
            parse_horizon_issues(changed_global, "B0.29", GLOBAL_CUTOFF, OBSERVED)


class CatalogBuilderAndCacheTests(unittest.TestCase):
    def test_complete_builder_exposes_ordered_rooms_rules_and_preference_metadata(self):
        catalog = complete_catalog()

        self.assertTrue(catalog.fresh)
        self.assertTrue(catalog.complete)
        self.assertEqual(catalog.source, "live")
        self.assertEqual(catalog.room_names, ("B0.11", "B0.29", "B1.09"))
        self.assertEqual(catalog.rooms[1].horizon_minutes, 4 * 24 * 60 + 12 * 60)
        self.assertEqual(catalog.rooms[1].horizon_days, 4.5)
        self.assertEqual(
            catalog.rooms[1].instrument_tags,
            ("Steinway Grand Piano", "Music stands"),
        )
        metadata = catalog.as_live_metadata()
        self.assertEqual(
            set(metadata["B0.29"]),
            {"instrument_tags", "room_type_tags", "features"},
        )
        self.assertIn("Piano Practice Room", metadata["B0.29"]["room_type_tags"])
        self.assertIn("Adjustable stool", metadata["B0.29"]["features"])

    def test_builder_requires_one_check_per_current_location(self):
        checks = complete_checks()
        checks.pop(13)
        with self.assertRaises(RoomCatalogError):
            build_catalog(
                session_payload=session_payload(),
                location_meta_payload=meta_payload(),
                location_info_payload=info_payload(),
                check_payloads=checks,
                observed_at=OBSERVED,
                booking_category_id=56,
            )

    def test_builder_normalizes_moving_check_cutoffs_to_session_snapshot(self):
        checks = complete_checks()
        replacements = {
            11: (("06:25", "06:26"),),
            12: (("06:25", "06:26"), ("18:25", "18:26")),
            13: (("06:25", "06:26"),),
        }
        for location_id, pairs in replacements.items():
            for issue_value in checks[location_id]["response"]["bookingrules"]["issues"]:
                for old, new in pairs:
                    issue_value["text"] = issue_value["text"].replace(old, new)

        catalog = build_catalog(
            session_payload=session_payload(),
            location_meta_payload=meta_payload(),
            location_info_payload=info_payload(),
            check_payloads=checks,
            observed_at=OBSERVED,
            booking_category_id=56,
            check_observed_ats={
                location_id: OBSERVED + timedelta(minutes=1)
                for location_id in checks
            },
        )

        self.assertEqual(catalog.rooms[0].booking_cutoff, GLOBAL_CUTOFF)
        self.assertEqual(
            catalog.rooms[1].booking_cutoff,
            datetime(2026, 9, 3, 18, 25, tzinfo=LONDON),
        )
        self.assertEqual(
            catalog.rooms[2].booking_cutoff,
            datetime(2026, 9, 2, 6, 25, tzinfo=LONDON),
        )

    def test_description_disagreement_with_authoritative_check_fails_closed(self):
        checks = complete_checks()
        checks[13] = check_payload("B1.09", "4/9/26 06:25")
        with self.assertRaises(RoomCatalogError):
            build_catalog(
                session_payload=session_payload(),
                location_meta_payload=meta_payload(),
                location_info_payload=info_payload(),
                check_payloads=checks,
                observed_at=OBSERVED,
                booking_category_id=56,
            )

    def test_locked_atomic_cache_round_trip_is_display_only(self):
        catalog = complete_catalog()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "room_catalog.json"
            save_catalog(catalog, path)
            cached = load_cached_catalog(path, missing_ok=False)

            self.assertIsNotNone(cached)
            self.assertEqual(cached.room_names, catalog.room_names)
            self.assertEqual(cached.observed_at, catalog.observed_at)
            self.assertFalse(cached.fresh)
            self.assertEqual(cached.source, "cache")
            with self.assertRaises(RoomCatalogError):
                cached.require_fresh()
            self.assertTrue(path.with_suffix(".json.lock").exists())

    def test_cache_schema_is_strict_and_missing_is_optional(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "room_catalog.json"
            self.assertIsNone(load_cached_catalog(path))
            with self.assertRaises(RoomCatalogError):
                load_cached_catalog(path, missing_ok=False)

            save_catalog(complete_catalog(), path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["version"] = CATALOG_VERSION + 1
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(RoomCatalogError):
                load_cached_catalog(path)


class _FakeResponse:
    def __init__(self, url, payload, *, ok=True):
        self.url = url
        self.ok = ok
        self._payload = payload

    def json(self):
        return copy_json(self._payload)


def copy_json(value):
    return json.loads(json.dumps(value))


class _FakeExpectedResponse:
    def __init__(self, page, predicate):
        self.page = page
        self.predicate = predicate
        self.value = None

    def __enter__(self):
        self.page._pending_response = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.page._pending_response = None
        if exc_type is None and self.value is None:
            raise AssertionError("expected response was not emitted")


class _FakeRequestContext:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def get(self, url):
        self.calls.append(("GET", url, None))
        path = urlsplit(url).path
        if path == "/services/v2/session-context/me":
            payload = session_payload()
        elif path == "/services/v2/categories":
            payload = category_payload()
        elif "location_group_ids=10" in path and path.endswith("/meta"):
            payload = meta_payload()
        elif "location_ids=11,12,13" in path and path.endswith("/info"):
            payload = info_payload()
        else:
            raise AssertionError(f"unexpected GET {url}")
        return _FakeResponse(url, payload)

    def post(self, url, data):
        self.calls.append(("POST", url, copy_json(data)))
        path = urlsplit(url).path
        if path != "/services/v2/event/type=check":
            raise AssertionError(f"unexpected POST {url}")
        event = data["event"]
        location_id = event["rs"][0]["id"]
        self.page.checked_ids.append(location_id)
        self.page.participants_preserved.append(
            event["pe"] == [{"id": 999, "dn": "PARTICIPANT_SECRET"}]
            and event["ps"] == [{"id": 998, "dn": "PARTICIPANT_SECRET_2"}]
        )
        payload = complete_checks()[location_id]
        return _FakeResponse(url, payload)


class _FakePage:
    def __init__(self):
        self.url = "https://rwcmd.asimut.net/overview?locationGroupId=10"
        self.request = _FakeRequestContext(self)
        self._pending_response = None
        self.checked_ids = []
        self.participants_preserved = []

    def expect_response(self, predicate, timeout):
        self.expect_timeout = timeout
        return _FakeExpectedResponse(self, predicate)

    def goto(self, url, wait_until):
        self.url = url
        self.goto_wait_until = wait_until
        query = parse_qs(urlsplit(url).query)
        start = query["start"][0]
        category_id = int(query["categoryId"][0])
        location_id = int(query["locationId"][0])
        payload = {
            "response": {
                "success": True,
                "eventdefault": {
                    "events": [
                        {
                            "id": 0,
                            "ca": category_id,
                            "st": start,
                            "en": start,
                            "rs": [{"id": location_id, "dn": "B0.11"}],
                            "pe": [{"id": 999, "dn": "PARTICIPANT_SECRET"}],
                            "ps": [{"id": 998, "dn": "PARTICIPANT_SECRET_2"}],
                        }
                    ]
                },
            }
        }
        response = _FakeResponse(
            "https://rwcmd.asimut.net/services/v2/eventdefault", payload
        )
        if self._pending_response and self._pending_response.predicate(response):
            self._pending_response.value = response


class RefreshWorkflowTests(unittest.TestCase):
    def test_authenticated_refresh_checks_every_live_room_and_never_saves(self):
        page = _FakePage()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "room_catalog.json"
            catalog = refresh_from_site(
                page,
                path=path,
                observed_at=OBSERVED,
            )

            self.assertTrue(catalog.fresh)
            self.assertEqual(page.checked_ids, [11, 12, 13])
            self.assertTrue(all(page.participants_preserved))
            self.assertEqual(page.goto_wait_until, "domcontentloaded")
            methods_and_paths = [
                (method, urlsplit(url).path)
                for method, url, _body in page.request.calls
            ]
            self.assertIn(("GET", "/services/v2/session-context/me"), methods_and_paths)
            self.assertIn(("GET", "/services/v2/categories"), methods_and_paths)
            self.assertEqual(
                sum(path_value.endswith("/type=check") for _method, path_value in methods_and_paths),
                3,
            )
            self.assertFalse(any("type=save" in path_value for _method, path_value in methods_and_paths))

            cached_text = path.read_text(encoding="utf-8")
            self.assertNotIn("PARTICIPANT_SECRET", cached_text)
            cached = load_cached_catalog(path)
            self.assertFalse(cached.fresh)
            self.assertEqual(cached.room_names, catalog.room_names)


if __name__ == "__main__":
    unittest.main()
