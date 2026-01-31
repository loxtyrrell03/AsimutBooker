"""Scheduler module for periodic booking checks."""

import logging
import signal
import sys
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class BookingScheduler:
    """Manages scheduled booking checks."""

    def __init__(
        self,
        booking_func: Callable[[], None],
        check_interval_hours: int = 1,
        run_at_times: list[str] | None = None,
    ):
        """
        Initialize the scheduler.

        Args:
            booking_func: Function to call for each booking attempt.
            check_interval_hours: How often to check (if run_at_times not set).
            run_at_times: Specific times to run (e.g., ["00:05", "12:00"]).
        """
        self.booking_func = booking_func
        self.check_interval_hours = check_interval_hours
        self.run_at_times = run_at_times or []
        self.scheduler = BlockingScheduler()
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Set up graceful shutdown handlers."""
        def shutdown(signum, frame):
            logger.info("Received shutdown signal, stopping scheduler...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

    def _run_booking_job(self) -> None:
        """Wrapper for the booking function with error handling."""
        try:
            logger.info("=" * 50)
            logger.info("Running scheduled booking check at %s", datetime.now().isoformat())
            logger.info("=" * 50)
            self.booking_func()
        except Exception as e:
            logger.error("Error during scheduled booking: %s", e, exc_info=True)
        finally:
            logger.info("Booking check completed")
            logger.info("-" * 50)

    def start(self) -> None:
        """Start the scheduler."""
        logger.info("Starting booking scheduler...")

        if self.run_at_times:
            # Schedule for specific times
            for time_str in self.run_at_times:
                try:
                    hour, minute = time_str.split(":")
                    trigger = CronTrigger(hour=int(hour), minute=int(minute))
                    self.scheduler.add_job(
                        self._run_booking_job,
                        trigger,
                        id=f"booking_at_{time_str}",
                        name=f"Booking check at {time_str}",
                    )
                    logger.info("Scheduled booking check at %s daily", time_str)
                except ValueError as e:
                    logger.error("Invalid time format '%s': %s", time_str, e)
        else:
            # Schedule at regular intervals
            trigger = IntervalTrigger(hours=self.check_interval_hours)
            self.scheduler.add_job(
                self._run_booking_job,
                trigger,
                id="booking_interval",
                name=f"Booking check every {self.check_interval_hours} hour(s)",
            )
            logger.info("Scheduled booking check every %d hour(s)", self.check_interval_hours)

        # Run once immediately on start
        logger.info("Running initial booking check...")
        self._run_booking_job()

        # Start the scheduler (blocking)
        logger.info("Scheduler running. Press Ctrl+C to stop.")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")

    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")
