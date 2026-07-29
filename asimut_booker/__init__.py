"""Reliable automation for the RWCMD Asimut room-booking system.

The package deliberately separates:

* pure booking policy and planning;
* validated HTML observations;
* browser interactions;
* transactional persistence; and
* the CLI / control-panel entry points.

Asimut remains the source of truth.  A booking or extension is never reported
as successful until it has been observed again with the expected event ID,
date, room and times.
"""

from __future__ import annotations

__version__ = "2.0.0"
