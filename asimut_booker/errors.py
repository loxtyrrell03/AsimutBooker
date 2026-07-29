"""Typed failures used to drive safe recovery behavior."""

from __future__ import annotations


class AsimutBookerError(Exception):
    """Base class for failures that should be shown to the user."""


class ConfigurationError(AsimutBookerError):
    """The configuration is invalid or unsafe."""


class AuthenticationRequired(AsimutBookerError):
    """The saved Asimut session is absent or no longer authenticated."""


class PageContractError(AsimutBookerError):
    """The live HTML no longer satisfies the parser contract.

    This is intentionally fail-closed.  Treating an unfamiliar or incomplete
    page as an empty calendar could create a conflicting booking.
    """


class NavigationError(AsimutBookerError):
    """Asimut did not navigate to the exact requested date or page."""


class BookingRejected(AsimutBookerError):
    """Asimut positively rejected a create or edit request."""


class AmbiguousBookingResult(AsimutBookerError):
    """A write may have reached Asimut but could not be reconciled safely."""


class ConcurrentRunError(AsimutBookerError):
    """Another coordinator already owns the application lock."""
