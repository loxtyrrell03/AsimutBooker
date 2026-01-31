#!/usr/bin/env python3
"""
AsimutBooker - Automated practice room booking for RWCMD.

Usage:
    python -m src.main --setup --headed    # First-time setup (login)
    python -m src.main --once              # Run once
    python -m src.main --schedule          # Run on schedule
    python -m src.main --dry-run --headed  # Test without booking
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .auth import AsimutAuth
from .booker import AsimutBooker
from .config import Config
from .scheduler import BookingScheduler


def setup_logging(config: Config) -> None:
    """Configure logging to file and console."""
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)

    # Create log filename with date
    log_file = config.logs_dir / f"asimut_{datetime.now().strftime('%Y%m%d')}.log"

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def run_setup(config: Config) -> int:
    """Run interactive setup to save login session."""
    logger = logging.getLogger(__name__)
    logger.info("Starting setup mode...")

    with AsimutAuth(
        asimut_url=config.asimut_url,
        browser_state_dir=config.browser_state_dir,
        headed=True,  # Must be headed for interactive login
    ) as auth:
        if auth.login_interactive():
            logger.info("Setup complete! Browser session saved.")
            logger.info("You can now run the script without --setup and --headed")
            return 0
        else:
            logger.error("Setup failed. Please try again.")
            return 1


def run_once(config: Config, headed: bool = False, dry_run: bool = False) -> int:
    """Run a single booking cycle (checks all days in horizon)."""
    logger = logging.getLogger(__name__)
    logger.info("Running booking cycle...")

    with AsimutAuth(
        asimut_url=config.asimut_url,
        browser_state_dir=config.browser_state_dir,
        headed=headed,
    ) as auth:
        if not auth.ensure_logged_in():
            logger.error("Not logged in. Run with --setup --headed first.")
            return 1

        booker = AsimutBooker(auth.page, config)
        results = booker.run_booking_cycle(dry_run=dry_run)

        # Summarize results
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        logger.info("=" * 50)
        logger.info("BOOKING SUMMARY")
        logger.info("=" * 50)
        logger.info("Total attempts: %d", len(results))
        logger.info("Successful: %d", len(successful))
        logger.info("Failed: %d", len(failed))

        for result in successful:
            logger.info("  [OK] %s at %s on %s - %s",
                       result.room, result.start_time, result.date, result.message)

        for result in failed:
            logger.info("  [--] %s", result.message)

        return 0 if successful else 1


def run_schedule(config: Config, dry_run: bool = False) -> int:
    """Run the scheduler for periodic booking checks."""
    logger = logging.getLogger(__name__)
    logger.info("Starting scheduled booking mode...")

    def booking_job():
        """Job function for the scheduler."""
        with AsimutAuth(
            asimut_url=config.asimut_url,
            browser_state_dir=config.browser_state_dir,
            headed=False,  # Headless for scheduled runs
        ) as auth:
            if not auth.ensure_logged_in():
                logger.error("Session expired. Need to re-login with --setup --headed")
                return

            booker = AsimutBooker(auth.page, config)
            results = booker.run_booking_cycle(dry_run=dry_run)

            successful = [r for r in results if r.success]
            logger.info("Booking cycle complete: %d bookings made", len(successful))
            for r in successful:
                logger.info("  Booked: %s at %s on %s", r.room, r.start_time, r.date)

    scheduler = BookingScheduler(
        booking_func=booking_job,
        check_interval_hours=config.check_interval_hours,
        run_at_times=config.run_at_times if config.run_at_times else None,
    )

    scheduler.start()
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AsimutBooker - Automated practice room booking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First-time setup (opens browser for login):
    python -m src.main --setup --headed

  Run once (test your configuration):
    python -m src.main --once --headed

  Dry run (see what would be booked without actually booking):
    python -m src.main --once --dry-run --headed

  Start scheduler (runs every hour):
    python -m src.main --schedule
        """,
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run interactive setup to save login session",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single booking attempt",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Start the scheduler for periodic booking checks",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be booked without actually booking",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config file (default: config/config.yaml)",
    )

    args = parser.parse_args()

    # Validate arguments
    if not any([args.setup, args.once, args.schedule]):
        parser.print_help()
        print("\nError: Must specify one of --setup, --once, or --schedule")
        return 1

    if args.setup and not args.headed:
        print("Note: --setup requires --headed for interactive login")
        args.headed = True

    # Load configuration
    try:
        config = Config(config_path=args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nTo get started:")
        print("  1. Copy config/config.example.yaml to config/config.yaml")
        print("  2. Edit config/config.yaml with your preferences")
        print("  3. Run: python -m src.main --setup --headed")
        return 1

    # Setup logging
    setup_logging(config)
    logger = logging.getLogger(__name__)
    logger.info("AsimutBooker starting...")

    # Run appropriate mode
    if args.setup:
        return run_setup(config)
    elif args.once:
        return run_once(config, headed=args.headed, dry_run=args.dry_run)
    elif args.schedule:
        return run_schedule(config, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
