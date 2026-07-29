# Runtime data

This directory is intentionally local-only. AsimutBooker creates its SQLite
database, process lock, booking history, health state, and Playwright browser
session here.

Never commit files from this directory: they may contain session credentials or
personal calendar and room-booking information.
