"""Authentication module for Microsoft 365 SSO with Asimut."""

import logging
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

logger = logging.getLogger(__name__)


class AsimutAuth:
    """Handles authentication to Asimut via Microsoft 365 SSO."""

    def __init__(self, asimut_url: str, browser_state_dir: Path, headed: bool = False):
        """
        Initialize the authentication handler.

        Args:
            asimut_url: The Asimut institution URL (e.g., https://rwcmd.asimut.net/)
            browser_state_dir: Directory to store persistent browser state
            headed: Whether to run browser in headed mode (visible)
        """
        self.asimut_url = asimut_url.rstrip("/")
        self.browser_state_dir = browser_state_dir
        self.state_file = browser_state_dir / "state.json"
        self.headed = headed

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def start(self) -> None:
        """Start the browser and load existing session if available."""
        self._playwright = sync_playwright().start()

        # Launch browser
        self._browser = self._playwright.chromium.launch(
            headless=not self.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )

        # Load existing state or create new context
        if self.state_file.exists():
            logger.info("Loading existing browser state...")
            self._context = self._browser.new_context(storage_state=str(self.state_file))
        else:
            logger.info("No existing state found, creating new browser context...")
            self._context = self._browser.new_context()

        self._page = self._context.new_page()

    def close(self) -> None:
        """Close browser and save state."""
        if self._context:
            # Save state before closing
            self._save_state()
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _save_state(self) -> None:
        """Save browser state (cookies, local storage) for future sessions."""
        if self._context:
            logger.debug("Saving browser state...")
            self._context.storage_state(path=str(self.state_file))

    @property
    def page(self) -> Page:
        """Get the current page."""
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    def is_logged_in(self) -> bool:
        """
        Check if we're currently logged in to Asimut.

        Returns:
            True if logged in, False otherwise.
        """
        try:
            # Navigate to Asimut main page
            self.page.goto(self.asimut_url, wait_until="networkidle", timeout=30000)

            # Check if we're redirected to login or if we see the main interface
            # Asimut shows user info when logged in - look for common elements
            current_url = self.page.url

            # If URL contains 'login', we're not logged in
            if "login" in current_url.lower():
                return False

            # Look for elements that indicate we're logged in
            # This might need adjustment based on actual Asimut page structure
            try:
                # Wait briefly for page to load
                self.page.wait_for_load_state("networkidle", timeout=5000)

                # Check for logout button or user menu - common indicators of being logged in
                logged_in_indicators = [
                    "text=Log out",
                    "text=Logout",
                    "text=Sign out",
                    "[class*='user']",
                    "[class*='logout']",
                    "text=Locations",  # From the screenshot, this appears when logged in
                ]

                for selector in logged_in_indicators:
                    if self.page.locator(selector).count() > 0:
                        logger.info("Login verified - found indicator: %s", selector)
                        return True

            except Exception:
                pass

            # If we got redirected away from login and are on main page, likely logged in
            if self.asimut_url in current_url and "login" not in current_url:
                logger.info("Login assumed - on main Asimut page")
                return True

            return False

        except Exception as e:
            logger.warning("Error checking login status: %s", e)
            return False

    def login_interactive(self) -> bool:
        """
        Perform interactive login - opens browser for user to complete SSO.

        Returns:
            True if login successful, False otherwise.
        """
        if not self.headed:
            logger.error("Interactive login requires headed mode (--headed flag)")
            return False

        logger.info("Starting interactive login...")
        logger.info("Please complete the Microsoft 365 SSO login in the browser window.")

        # Navigate to Asimut - will redirect to SSO if not logged in
        self.page.goto(self.asimut_url, wait_until="networkidle", timeout=60000)

        # If already logged in, we're done
        if self.is_logged_in():
            logger.info("Already logged in!")
            self._save_state()
            return True

        # Wait for user to complete login
        # We'll wait until we detect the main Asimut interface
        logger.info("Waiting for login to complete...")
        logger.info("(Complete the SSO login in the browser window)")

        try:
            # Wait for navigation away from login page
            # This gives the user time to complete SSO
            self.page.wait_for_url(
                lambda url: "login" not in url.lower() and self.asimut_url.split("//")[1] in url,
                timeout=300000,  # 5 minutes to complete login
            )

            # Wait a bit for page to fully load
            self.page.wait_for_load_state("networkidle", timeout=30000)

            # Verify login was successful
            if self.is_logged_in():
                logger.info("Login successful!")
                self._save_state()
                return True
            else:
                logger.warning("Login may not have completed successfully")
                return False

        except Exception as e:
            logger.error("Login timed out or failed: %s", e)
            return False

    def ensure_logged_in(self) -> bool:
        """
        Ensure we're logged in, attempting automatic session restore first.

        Returns:
            True if logged in (or login successful), False otherwise.
        """
        # Try existing session first
        if self.is_logged_in():
            logger.info("Using existing session")
            return True

        # If headed mode, try interactive login
        if self.headed:
            return self.login_interactive()

        # Headless mode with no valid session
        logger.error(
            "No valid session found. Please run with --setup --headed first "
            "to complete the initial login."
        )
        return False
