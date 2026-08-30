"""Retired mutation engine from the former alternate runtime.

The canonical ``book_week`` implementation is the only booking engine because
it installs a complete room policy freshly observed from Asimut before any
mutation. Leaving this older engine callable would reintroduce static windows.
"""


class AsimutBooker:
    """Fail closed for direct callers of the retired mutation engine."""

    def __init__(self, *_args, **_kwargs):
        raise RuntimeError(
            "src.booker is retired; invoke book_week.py so live room policy "
            "is refreshed before booking"
        )
