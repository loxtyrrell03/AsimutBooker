"""A room-only upgrade must not wait ahead of missing-target extension work."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from progressive_runtime import missing_time_extension_has_priority


class ProgressivePriorityTests(unittest.TestCase):
    def context(self, timing, *, held=True):
        booking = dict(room='Best', date='2030-01-07', startTime='12:00', endTime='12:30', target_end='14:00')
        engine = SimpleNamespace(load_extendable_bookings=lambda: [booking],
            calculate_extension_capacity_holds=Mock(return_value=({}, {}, (booking,) if held else ())),
            load_disabled_dates=lambda settings: [], load_time_preferences=lambda settings: {},
            plan_horizon_extension=Mock(return_value=timing))
        return SimpleNamespace(engine=engine, tracker=object(), practice_plan=object(), settings={})

    def test_already_available_held_extension_has_priority(self):
        ctx = self.context(SimpleNamespace(can_extend=True))
        self.assertTrue(missing_time_extension_has_priority(ctx, datetime.now(timezone.utc)))

    def test_same_boundary_held_extension_has_priority(self):
        edge = datetime.now(timezone.utc) + timedelta(seconds=90)
        ctx = self.context(SimpleNamespace(can_extend=False, next_unlock_at=edge))
        self.assertTrue(missing_time_extension_has_priority(ctx, edge))

    def test_later_extension_does_not_delay_current_upgrade(self):
        edge = datetime.now(timezone.utc) + timedelta(seconds=90)
        ctx = self.context(SimpleNamespace(can_extend=False, next_unlock_at=edge + timedelta(minutes=15)))
        self.assertFalse(missing_time_extension_has_priority(ctx, edge))

    def test_target_met_or_invalid_extension_has_no_hold(self):
        ctx = self.context(SimpleNamespace(can_extend=True), held=False)
        self.assertFalse(missing_time_extension_has_priority(ctx, datetime.now(timezone.utc)))
        ctx.engine.plan_horizon_extension.assert_not_called()
