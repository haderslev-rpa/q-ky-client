"""Integrationstest af det nye KY-launch-flow.

Testen:
- opretter en BrowserSession,
- starter browseren,
- opretter en ny Page,
- kalder launch_ky(),
- sender kun navnet paa credential-posten,
- lader login_via_faelles_kommunal_idp hente credentials fra Automation Server,
- kontrollerer KY-URL og JSESSIONID,
- og lukker BrowserSession i finally.

Der laeses ikke brugernavn eller adgangskode fra .env.

Koer testen med:
    pytest tests/test_launch_ky.py -s -vv
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
    wait_for_ky_ready,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]


# Navnet paa credential-posten i Automation Server.
# Dette er ikke et brugernavn eller en adgangskode.
KY_CREDENTIAL_NAME = "DIRXFLX"

PAGE_TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT_MS = 120_000


async def test_launch_ky() -> None:
    """Launch KY med credential-opslag via Automation Server."""

    session = BrowserSession(
        headless=False,
        debug=True,
        video=False,
    )

    page: Page | None = None

    try:
        print()
        print("=" * 70)
        print("STARTER BROWSERSESSION")
        print("=" * 70)

        await session.start()
        page = await session.new_page()

        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

        recorder = getattr(
            session,
            "recorder",
            None,
        )

        set_page = getattr(
            recorder,
            "set_page",
            None,
        )

        if callable(set_page):
            set_page(page)

        print()
        print("=" * 70)
        print("LAUNCHER KY")
        print("=" * 70)
        print(f"Credential-post i Automation Server: {KY_CREDENTIAL_NAME}")

        await launch_ky(
            page=page,
            session=session,
            credential_name=KY_CREDENTIAL_NAME,
        )

        # launch_ky() venter selv paa KY, men denne kontrol goer
        # testens forventning eksplicit.
        await wait_for_ky_ready(
            page=page,
            timeout_ms=NAVIGATION_TIMEOUT_MS,
        )

        assert not page.is_closed(), "Playwright-siden blev lukket under KY-launch."

        assert not is_ky_error_url(page), (
            f"KY viste fejlsiden efter launch. Aktuel URL: {page.url}"
        )

        assert is_ky_url(page), (
            f"Siden er ikke en gyldig KY-side efter launch. Aktuel URL: {page.url}"
        )

        assert await has_jsessionid(page), (
            "KY blev aabnet, men JSESSIONID blev ikke fundet."
        )

        print()
        print("=" * 70)
        print("KY ER KLAR")
        print("=" * 70)
        print("KY-URL er gyldig.")
        print("JSESSIONID blev fundet.")
        print(f"Aktuel URL: {page.url}")

        # Kort pause, saa slutresultatet kan ses i en synlig browser.
        await page.wait_for_timeout(5_000)

    finally:
        print()
        print("Lukker BrowserSession.")
        await session.close()
