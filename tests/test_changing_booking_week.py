"""Full changing weeks with real planning functions and independent Save checks."""
import unittest
from tests.booking_week_simulation import WeekSimulation


class ChangingWeekTests(unittest.TestCase):
    def test_contested_week_fills_daily_practice_and_keeps_good_rooms(self):
        world=WeekSimulation(seed=1)
        result=world.run()
        self.assertEqual(result['checks'],168)
        self.assertGreaterEqual(min(result['daily_minutes']),150)
        self.assertGreaterEqual(result['premium_minutes']/result['total_minutes'],.85)
        self.assertGreater(result['races'],0)
        self.assertGreater(result['free_creates'],0)
        self.assertGreater(result['extensions'],0)
        self.assertGreater(result['upgrades'],0)
        self.assertEqual(result['remaining_intents'],0)
        # Every original survives ordinary same-ID extensions and upgrades.
        created={row['id'] for row in world.journal if row['kind']=='creates'}
        self.assertEqual(created,set(world.events))

    def test_24_messy_weeks_with_sparse_rooms_races_and_optional_comfort(self):
        for seed in range(1,13):
            for comfort in (False,True):
                with self.subTest(seed=seed,comfort=comfort):
                    world=WeekSimulation(seed,scarcity=(.5,.7,.85)[seed%3],
                        comfort=comfort,race_every=3 if seed%2 else 7,target=240)
                    result=world.run()
                    self.assertTrue(all(0 <= minutes <= 240 for minutes in result['daily_minutes']))
                    self.assertGreater(result['total_minutes'],0)
                    self.assertLess(world.attempts,400)
                    self.assertTrue(all(row['room'] in ('Weston','Corus','Practice A','Practice B')
                                        for row in world.journal))


if __name__=='__main__':
    unittest.main()
