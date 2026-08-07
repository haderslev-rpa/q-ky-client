import logging
from urllib.parse import urljoin

import httpx
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    Page,
    Playwright,
    sync_playwright,
)

from .hooks import create_response_logging_hook
from .selectors import KYSelectors


class KYClient:
    @property
    def page(self) -> Page:
        """Get the current active Playwright page."""
        return self._page

    def __init__(
        self,
        username: str,
        password: str,
        idp: str,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        response_hook = create_response_logging_hook(logger=self.logger)
        hooks = {"response": [response_hook]}

        self._username = username
        self._password = password
        self._idp = idp
        self._base_url = "https://fs0510.fs.kommunernesydelsessystem.dk/ky-fagsystem"
        self._timeout = 30
        self._client = httpx.Client(
            timeout=self._timeout,
            event_hooks=hooks,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
            },
        )

        self._playwright: Playwright = sync_playwright().start()
        browser_args = ["--window-size=1920,1080", "--lang=da-DK"]
        self._browser = self._playwright.chromium.launch(
            headless=False,
            args=browser_args,
        )
        self._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            accept_downloads=False,
            ignore_https_errors=True,
            locale="da-DK",
        )
        self._page: Page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        self.login()

    def login(self) -> None:
        self._page.goto(self._base_url)

        try:
            self._page.select_option(KYSelectors.Login.MUNICIPALITY_SELECT, self._idp)
            self._page.click(KYSelectors.Login.OK_BUTTON)
        except Exception:
            self.logger.debug("Skipping municipality select")

        try:
            self._page.wait_for_selector(KYSelectors.Login.USERNAME, timeout=5000)
            self._page.fill(KYSelectors.Login.USERNAME, self._username)
            self._page.click(KYSelectors.Login.SUBMIT_BUTTON)
        except Exception:
            self.logger.debug("Skipping username entry")

        try:
            self._page.wait_for_selector(KYSelectors.Login.PASSWORD, timeout=5000)
            self._page.fill(KYSelectors.Login.PASSWORD, self._password)
            self._page.click(KYSelectors.Login.SUBMIT_BUTTON)
        except Exception:
            self.logger.debug("Skipping password entry")

        # Wait for page to load before checking for logo
        try:
            self._page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightError:
            self.logger.debug("Page did not reach networkidle state, continuing anyway")

        # Wait for logo with error handling
        try:
            self._page.wait_for_selector(KYSelectors.Main.LOGO, timeout=30000)
        except PlaywrightError as e:
            self.logger.error(f"Failed to wait for logo: {e}")
            # Check if page is still open before retrying
            if self._page.is_closed():
                raise RuntimeError("Page was closed during login") from e
            # Retry once more with fresh page state
            self._page.wait_for_selector(KYSelectors.Main.LOGO, timeout=30000)

    def close(self) -> None:
        """Close the browser and stop Playwright."""
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def _normalize_url(self, endpoint: str) -> str:
        """Ensure the URL is absolute, handling relative URLs."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return urljoin(self._base_url, endpoint)

    def get(self, endpoint: str, **kwargs) -> httpx.Response:
        url = self._normalize_url(endpoint)
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def post(self, endpoint: str, json: dict | None = None, **kwargs) -> httpx.Response:
        url = self._normalize_url(endpoint)
        response = self._client.post(url, json=json, **kwargs)
        response.raise_for_status()
        return response

    def put(self, endpoint: str, json: dict | None = None, **kwargs) -> httpx.Response:
        url = self._normalize_url(endpoint)
        response = self._client.put(url, json=json, **kwargs)
        response.raise_for_status()
        return response

    def patch(
        self, endpoint: str, json: dict | None = None, **kwargs
    ) -> httpx.Response:
        url = self._normalize_url(endpoint)
        response = self._client.patch(url, json=json, **kwargs)
        response.raise_for_status()
        return response

    def delete(self, endpoint: str, **kwargs) -> httpx.Response:
        url = self._normalize_url(endpoint)
        response = self._client.delete(url, **kwargs)
        response.raise_for_status()
        return response
