"""Retired configuration surface from the former alternate runtime.

Room lists and booking horizons are site-owned. Importers must use the
authoritative ``book_week`` runtime plus ``room_catalog``/``live_room_policy``;
this module deliberately cannot construct the stale legacy configuration.
"""


class Config:
    """Fail-closed compatibility name for callers of the retired runtime."""

    def __init__(self, *_args, **_kwargs):
        raise RuntimeError(
            "src.config is retired because it cannot own room horizons; "
            "use book_week.py and its rules-only config instead"
        )
