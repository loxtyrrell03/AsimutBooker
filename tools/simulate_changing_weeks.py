"""Report invented, changing practice weeks without accessing a real account."""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.booking_week_simulation import WeekSimulation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    started = perf_counter()
    rows = []
    for name, options in (
        ('contested', dict(seed=1, scarcity=.65, target=180)),
        ('scarce', dict(seed=5, scarcity=.85, target=240, race_every=3)),
        ('changing with missed runs', dict(seed=8, scarcity=.75, target=240,
                                          race_every=3, competitor_waves=True, miss_every=4)),
    ):
        for comfort in (False, True):
            world = WeekSimulation(comfort=comfort, **options)
            rows.append(dict(scenario=name, comfort=comfort, **world.run()))
    report = dict(synthetic=True, browser_or_live_booking_proof=False,
                  elapsed_seconds=round(perf_counter()-started, 3), weeks=rows)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
