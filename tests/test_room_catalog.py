import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import room_catalog
from room_catalog import (
    CATALOG_VERSION,
    LocationMeta,
    SITE_TIMEZONE,
    RoomCatalogError,
    build_catalog,
    load_cached_catalog,
    merge_location_groups,
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


class RequestClockTests(unittest.TestCase):
    def test_server_date_minute_overrides_local_boundary_envelope(self):
        started = OBSERVED.replace(second=59)
        finished = started + timedelta(seconds=2)
        server_time = OBSERVED + timedelta(minutes=1, seconds=1)

        candidates = room_catalog._request_observation_candidates(
            started,
            finished,
            server_time,
        )

        self.assertEqual(candidates, (OBSERVED + timedelta(minutes=1),))

    def test_server_date_uncertainty_includes_adjacent_rollover_minute(self):
        candidates = room_catalog._request_observation_candidates(
            OBSERVED.replace(second=59, microsecond=900000),
            OBSERVED + timedelta(minutes=1, microseconds=100000),
            OBSERVED + timedelta(minutes=1),
        )

        self.assertEqual(
            candidates,
            (OBSERVED, OBSERVED + timedelta(minutes=1)),
        )

    def test_exact_global_horizon_selects_one_rollover_minute(self):
        candidates = room_catalog._request_observation_candidates(
            OBSERVED.replace(second=59, microsecond=900000),
            OBSERVED + timedelta(minutes=1, microseconds=100000),
            OBSERVED + timedelta(minutes=1),
        )

        matching = []
        for candidate in candidates:
            try:
                parse_horizon_issues(
                    check_payload("B0.11"),
                    "B0.11",
                    GLOBAL_CUTOFF,
                    candidate,
                    expected_global_horizon_minutes=7 * 24 * 60,
                )
            except RoomCatalogError:
                continue
            matching.append(candidate)

        self.assertEqual(matching, [OBSERVED])

    def test_date_uncertainty_does_not_accept_a_non_rollover_stale_minute(self):
        candidates = room_catalog._request_observation_candidates(
            OBSERVED + timedelta(minutes=1, seconds=29),
            OBSERVED + timedelta(minutes=1, seconds=31),
            OBSERVED + timedelta(minutes=1, seconds=30),
        )

        self.assertEqual(candidates, (OBSERVED + timedelta(minutes=1),))
        with self.assertRaises(RoomCatalogError):
            parse_horizon_issues(
                check_payload("B0.11"),
                "B0.11",
                GLOBAL_CUTOFF,
                candidates[0],
                expected_global_horizon_minutes=7 * 24 * 60,
            )

    def test_missing_server_date_retains_bounded_local_minute_candidates(self):
        started = OBSERVED.replace(second=59)
        finished = started + timedelta(seconds=2)

        candidates = room_catalog._request_observation_candidates(
            started,
            finished,
            None,
        )

        self.assertEqual(
            candidates,
            (OBSERVED, OBSERVED + timedelta(minutes=1)),
        )

    def test_malformed_server_date_fails_closed(self):
        response = type(
            "Response",
            (),
            {"headers": {"date": "not an HTTP date"}},
        )()

        with self.assertRaisesRegex(RoomCatalogError, "Date header is unreadable"):
            room_catalog._response_server_time(response, "session context")

    def test_clock_bounds_intersect_across_existing_live_requests(self):
        first = room_catalog._server_clock_offset_bounds(
            OBSERVED + timedelta(seconds=10.10),
            OBSERVED + timedelta(seconds=10.20),
            OBSERVED + timedelta(seconds=11),
        )
        second = room_catalog._server_clock_offset_bounds(
            OBSERVED + timedelta(seconds=10.95),
            OBSERVED + timedelta(seconds=11.05),
            OBSERVED + timedelta(seconds=12),
        )

        combined = room_catalog._intersect_clock_offset_bounds((first, second))

        self.assertAlmostEqual(combined[0], -0.05, places=6)
        self.assertAlmostEqual(combined[1], 1.90, places=6)

    def test_inconsistent_proxy_clocks_are_not_used_for_edge_timing(self):
        combined = room_catalog._intersect_clock_offset_bounds(
            ((1.0, 1.1), (1.2, 1.3))
        )

        self.assertIsNone(combined)


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
PROMOTED_ROOMS = (
    (201, "Weston Gallery", "Performance Space"),
    (202, "Corus Recital Room", "Recital Room"),
)
ALL_LOCATION_ROOMS = (
    ROOMS[1],
    (999, "Other Auditorium", "Performance Space"),
    PROMOTED_ROOMS[1],
    ROOMS[0],
    PROMOTED_ROOMS[0],
    ROOMS[2],
)
MERGED_ROOMS = ROOMS + PROMOTED_ROOMS


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


def complete_union_checks():
    return {
        **complete_checks(),
        201: check_payload("Weston Gallery", "1/9/26 06:25"),
        202: check_payload("Corus Recital Room", "4/9/26 06:25"),
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


class ClosureCalendarTests(unittest.TestCase):
    def catalog_with_closures(self):
        payload = meta_payload()
        for room in payload["response"]["locations"]:
            room["closed_hours"] = [
                {"st": "2026-08-31T07:00:00+01:00", "en": "2026-08-31T12:00:00+01:00"},
                {"st": "2026-08-31T12:00:00+01:00", "en": "2026-08-31T23:00:00+01:00"},
            ]
        return build_catalog(session_payload=session_payload(), location_meta_payload=payload,
                             location_info_payload=info_payload(), check_payloads=complete_checks(),
                             observed_at=OBSERVED, booking_category_id=56)

    def test_all_rooms_must_explicitly_cover_the_whole_day(self):
        catalog = self.catalog_with_closures()
        self.assertEqual(room_catalog.closed_practice_dates(catalog, now=OBSERVED), ("2026-08-31",))
        for intervals in ((), ((OBSERVED.replace(day=31, hour=8), OBSERVED.replace(day=31, hour=23)),)):
            partial = replace(catalog, rooms=(replace(catalog.rooms[0], closed_hours=intervals),) + catalog.rooms[1:])
            self.assertEqual(room_catalog.closed_practice_dates(partial, now=OBSERVED), ())
        self.assertEqual(room_catalog.closed_practice_dates(catalog, now=OBSERVED + timedelta(hours=25)), ())
        self.assertEqual(room_catalog.closed_practice_dates(None, now=OBSERVED), ())

    def test_closures_round_trip_and_legacy_caches_remain_readable(self):
        catalog = self.catalog_with_closures()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            save_catalog(catalog, path)
            restored = load_cached_catalog(path)
            self.assertEqual(room_catalog.closed_practice_dates(restored, now=OBSERVED), ("2026-08-31",))
            raw = json.loads(path.read_text())
            for room in raw["rooms"]:
                del room["closed_hours"]
            path.write_text(json.dumps(raw))
            self.assertEqual(room_catalog.closed_practice_dates(load_cached_catalog(path), now=OBSERVED), ())

    def test_metadata_request_selects_the_exact_date(self):
        path = room_catalog._group_meta_path(GLOBAL_CUTOFF, group_id=10)
        self.assertIn("current_date=2026-09-06T00:00:00.000+01:00", path)

    def real_weekend(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "college_closed_weekend.json").read_text())
        observed = datetime.fromisoformat(fixture["observed_at"])
        base = complete_catalog()
        template = base.rooms[0]
        rooms = tuple(replace(
            template, location_id=row["id"], name=row["name"],
            booking_cutoff=observed + timedelta(minutes=template.horizon_minutes),
            closed_hours=tuple((datetime.fromisoformat(h["st"]), datetime.fromisoformat(h["en"]))
                               for h in row["closed_hours"]),
        ) for row in fixture["rooms"])
        catalog = replace(base, observed_at=observed,
                          booking_horizon=observed + timedelta(minutes=base.global_horizon_minutes), rooms=rooms)
        return fixture, catalog

    def test_actual_weekend_closure_events_complete_opening_hours_and_survive_cache(self):
        fixture, catalog = self.real_weekend()
        self.assertEqual(room_catalog.closed_practice_dates(catalog, now=catalog.observed_at), ())
        closed = room_catalog.with_closure_events(catalog, fixture["agenda"], fixture["categories"])
        expected = ("2026-09-12", "2026-09-13")
        self.assertEqual(room_catalog.closed_practice_dates(closed, now=catalog.observed_at), expected)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            save_catalog(closed, path)
            cached = load_cached_catalog(path)
            self.assertEqual(room_catalog.closed_practice_dates(cached, now=catalog.observed_at), expected)
            self.assertFalse(cached.fresh)
            enriched = room_catalog.with_closure_events(cached, fixture["agenda"], fixture["categories"])
            self.assertFalse(enriched.fresh)
            self.assertEqual(enriched.observed_at, cached.observed_at)
            self.assertEqual(room_catalog.closed_practice_dates(enriched, now=catalog.observed_at + timedelta(hours=25)), ())
            self.assertNotIn("College Closed", path.read_text())

    def test_missing_room_partial_day_and_ordinary_red_events_do_not_imply_closure(self):
        for variant in ("missing_room", "partial", "reservation", "cancelled", "hidden"):
            with self.subTest(variant=variant):
                fixture, catalog = self.real_weekend()
                events = fixture["agenda"]["response"]["arrangements"][0]["events"]
                saturday = [e for e in events if e["st"].startswith("2026-09-12")]
                if variant == "missing_room":
                    events.remove(saturday[0])
                elif variant == "partial":
                    saturday[0]["en"] = "2026-09-12T12:00:00+01:00"
                elif variant == "hidden":
                    saturday[0]["vi"] = "hidden"
                else:
                    cat = fixture["categories"]["response"]["categories"][0]
                    if variant == "reservation":
                        cat["name"] = "Reservation"  # Keep red colour and College Closed event title.
                    else:
                        cat["eventstatus"] = "cancelled"
                closed = room_catalog.with_closure_events(catalog, fixture["agenda"], fixture["categories"])
                self.assertNotIn("2026-09-12", room_catalog.closed_practice_dates(closed, now=catalog.observed_at))

    def test_closure_category_identity_is_discovered_and_bad_evidence_is_rejected(self):
        fixture, catalog = self.real_weekend()
        fixture["categories"]["response"]["categories"][0]["id"] = 9048
        events = fixture["agenda"]["response"]["arrangements"][0]["events"]
        for event in events:
            event["ca"] = 9048
        closed = room_catalog.with_closure_events(catalog, fixture["agenda"], fixture["categories"])
        self.assertEqual(room_catalog.closed_practice_dates(closed, now=catalog.observed_at), ("2026-09-12", "2026-09-13"))
        for field, value in (("en", "2026-09-11T00:00:00+01:00"), ("rs", [{"id": 999999}])):
            bad = copy_json(fixture["agenda"])
            bad["response"]["arrangements"][0]["events"][0][field] = value
            with self.assertRaises(RoomCatalogError):
                room_catalog.with_closure_events(catalog, bad, fixture["categories"])


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

    def test_all_locations_promotions_append_after_live_ahc_in_priority_order(self):
        core = parse_location_group_meta(meta_payload())
        all_locations = parse_location_group_meta(
            meta_payload(ALL_LOCATION_ROOMS),
            group_id=2,
            allow_empty=True,
        )

        merged = merge_location_groups(core, all_locations)

        self.assertEqual(
            tuple(item.name for item in merged),
            (
                "B0.11",
                "B0.29",
                "B1.09",
                "Weston Gallery",
                "Corus Recital Room",
            ),
        )
        self.assertNotIn("Other Auditorium", tuple(item.name for item in merged))

    def test_complete_all_locations_absence_omits_only_missing_promotions(self):
        core = parse_location_group_meta(meta_payload())
        all_locations = parse_location_group_meta(
            meta_payload(ROOMS + (PROMOTED_ROOMS[1],)),
            group_id=2,
            allow_empty=True,
        )

        merged = merge_location_groups(core, all_locations)

        self.assertEqual(
            tuple(item.name for item in merged),
            ("B0.11", "B0.29", "B1.09", "Corus Recital Room"),
        )

    def test_cross_group_identity_conflicts_fail_closed(self):
        core = parse_location_group_meta(meta_payload())
        conflicts = (
            (LocationMeta(11, "Weston Gallery", "Performance Space"),),
            (LocationMeta(201, "B0.11", "Music Practice Room"),),
            (
                LocationMeta(201, "Weston Gallery", "Performance Space"),
                LocationMeta(201, "Corus Recital Room", "Recital Room"),
            ),
        )
        for conflict in conflicts:
            with self.subTest(conflict=conflict):
                with self.assertRaises(RoomCatalogError):
                    merge_location_groups(core, conflict)

        malformed = meta_payload(())
        malformed["response"].pop("locations")
        with self.assertRaises(RoomCatalogError):
            parse_location_group_meta(malformed, group_id=2, allow_empty=True)

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

    def test_builder_uses_live_promoted_horizons_and_exact_merged_check_set(self):
        catalog = build_catalog(
            session_payload=session_payload(),
            location_meta_payload=meta_payload(),
            all_locations_meta_payload=meta_payload(ALL_LOCATION_ROOMS),
            location_info_payload=info_payload(MERGED_ROOMS),
            check_payloads=complete_union_checks(),
            observed_at=OBSERVED,
            booking_category_id=56,
        )

        self.assertEqual(catalog.room_names, tuple(row[1] for row in MERGED_ROOMS))
        self.assertEqual(catalog.rooms[3].horizon_minutes, 2 * 24 * 60)
        self.assertEqual(catalog.rooms[4].horizon_minutes, 5 * 24 * 60)

        changed_checks = complete_union_checks()
        changed_checks[201] = check_payload("Weston Gallery", "2/9/26 06:25")
        changed = build_catalog(
            session_payload=session_payload(),
            location_meta_payload=meta_payload(),
            all_locations_meta_payload=meta_payload(ALL_LOCATION_ROOMS),
            location_info_payload=info_payload(MERGED_ROOMS),
            check_payloads=changed_checks,
            observed_at=OBSERVED,
            booking_category_id=56,
        )
        self.assertEqual(changed.rooms[3].horizon_minutes, 3 * 24 * 60)

        with self.assertRaises(RoomCatalogError):
            build_catalog(
                session_payload=session_payload(),
                location_meta_payload=meta_payload(),
                all_locations_meta_payload=meta_payload(ALL_LOCATION_ROOMS),
                location_info_payload=info_payload(MERGED_ROOMS),
                check_payloads=complete_checks(),
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

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, None))
        path = urlsplit(url).path
        if path == "/services/v2/session-context/me":
            payload = session_payload()
        elif path == "/services/v2/categories":
            payload = category_payload()
        elif "location_group_ids=10" in path and path.endswith("/meta"):
            payload = meta_payload()
        elif "location_group_ids=2" in path and path.endswith("/meta"):
            payload = meta_payload(ALL_LOCATION_ROOMS)
        elif "/services/v2/locations/location_ids=" in path and path.endswith("/info"):
            id_text = path.split("location_ids=", 1)[1].split(";", 1)[0]
            requested_ids = tuple(int(value) for value in id_text.split(","))
            available_rooms = {row[0]: row for row in ROOMS + PROMOTED_ROOMS}
            try:
                requested_rooms = tuple(available_rooms[value] for value in requested_ids)
            except KeyError as exc:
                raise AssertionError(f"unexpected info identity {exc.args[0]}") from exc
            payload = info_payload(requested_rooms)
        elif "/services/v2/locations/location_ids=" in path and path.endswith("/agenda"):
            payload = {"response": {"success": True, "arrangements": []}}
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
        payload = complete_union_checks()[location_id]
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
    def test_normal_refresh_fetches_closure_events_and_publishes_them_in_cache(self):
        page = _FakePage()
        original_get = page.request.get
        event_requests = []
        fixture, _ = ClosureCalendarTests().real_weekend()
        category = fixture["categories"]["response"]["categories"][0]

        def get(url, **kwargs):
            if url.endswith("/agenda"):
                event_requests.append((url, kwargs))
                events = [{"id": room[0], "ca": category["id"], "vi": "visible",
                           "st": "2026-08-31T00:00:00+01:00", "en": "2026-08-31T23:55:00+01:00",
                           "rs": [{"id": room[0]}]} for room in ROOMS + PROMOTED_ROOMS]
                return _FakeResponse(url, {"response": {"success": True, "arrangements": [{"events": events}]}})
            response = original_get(url, **kwargs)
            if url.endswith("/categories"):
                response._payload["response"]["categories"].append(category)
            return response

        with tempfile.TemporaryDirectory() as directory, patch.object(page.request, "get", side_effect=get):
            path = Path(directory) / "catalog.json"
            catalog = refresh_from_site(page, path=path, observed_at=OBSERVED)
            self.assertEqual(room_catalog.closed_practice_dates(catalog, now=OBSERVED), ("2026-08-31",))
            self.assertEqual(room_catalog.closed_practice_dates(load_cached_catalog(path), now=OBSERVED), ("2026-08-31",))
        self.assertEqual(len(event_requests), 1)
        url, kwargs = event_requests[0]
        self.assertIn("location_ids=11,12,13,201,202", url)
        self.assertIn("start_at=2026-08-30T00:00:00.000+01:00", url)
        self.assertIn("end_at=2026-09-06T23:00:00.000+01:00", url)
        self.assertEqual(kwargs["timeout"], 3000)

    def test_unavailable_closure_events_do_not_block_booking_policy_refresh(self):
        with patch("room_catalog.refresh_closure_events", side_effect=RoomCatalogError("Unavailable")):
            catalog = refresh_from_site(_FakePage(), observed_at=OBSERVED, save=False)
        self.assertTrue(catalog.fresh)
        self.assertEqual(room_catalog.closed_practice_dates(catalog, now=OBSERVED), ())

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
            self.assertEqual(page.checked_ids, [11, 12, 13, 201, 202])
            self.assertEqual(
                catalog.room_names,
                ("B0.11", "B0.29", "B1.09", "Weston Gallery", "Corus Recital Room"),
            )
            self.assertTrue(all(page.participants_preserved))
            self.assertEqual(page.goto_wait_until, "domcontentloaded")
            methods_and_paths = [
                (method, urlsplit(url).path)
                for method, url, _body in page.request.calls
            ]
            self.assertIn(("GET", "/services/v2/session-context/me"), methods_and_paths)
            self.assertIn(("GET", "/services/v2/categories"), methods_and_paths)
            self.assertEqual(
                sum("location_group_ids=2" in path_value for _method, path_value in methods_and_paths),
                8,
            )
            self.assertEqual(
                sum("location_group_ids=10" in path_value for _method, path_value in methods_and_paths),
                8,
            )
            info_calls = [
                path_value
                for method, path_value in methods_and_paths
                if method == "GET" and path_value.endswith("/info")
            ]
            self.assertEqual(len(info_calls), 1)
            self.assertIn("location_ids=11,12,13,201,202", info_calls[0])
            self.assertNotIn("999", info_calls[0])
            self.assertEqual(
                sum(path_value.endswith("/type=check") for _method, path_value in methods_and_paths),
                5,
            )
            self.assertFalse(any("type=save" in path_value for _method, path_value in methods_and_paths))

            cached_text = path.read_text(encoding="utf-8")
            self.assertNotIn("PARTICIPANT_SECRET", cached_text)
            cached = load_cached_catalog(path)
            self.assertFalse(cached.fresh)
            self.assertEqual(cached.room_names, catalog.room_names)


if __name__ == "__main__":
    unittest.main()
