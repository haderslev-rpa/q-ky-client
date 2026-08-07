"""Integrationstest: launch KY og vis Borgeroverblik for test_cpr.

Testen bruger fixtures fra tests/conftest.py:
- automation_session
- ky_page
- ky_credential_name
- test_cpr

Koer eksempelvis:
    pytest tests/test_launch_og_borgeroverblik.py -s -vv \
        --test-cpr 0101011234
"""

from __future__ import annotations

import re

import pytest
from playwright.async_api import Locator, Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

ACTION_TIMEOUT_MS = 30_000
BORGEROVERBLIK_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250

TOPSEARCH_SELECTORS = (
    "input#topSearch",
    "input#topsearch",
    "input[name='topSearch']",
    "input[name='topsearch']",
    "input[id*='topsearch' i]",
    "input[name*='topsearch' i]",
    "input[placeholder*='CPR' i]",
    "input[aria-label*='CPR' i]",
    "input[type='search']",
    "[role='searchbox']",
)

OVERBLIK_NAV_SELECTORS = (
    '[data-textkey="fagsystem.person.navigation.person_overblik"]',
    '[data-textkey*="person.navigation.person_overblik"]',
    "a:has-text('Overblik')",
    "button:has-text('Overblik')",
)

OVERBLIK_CONTENT_SELECTORS = (
    "table#person-oplysninger",
    "table#sagsoversigt",
    "table#person-overblik-livssituation",
)


async def test_launch_og_borgeroverblik(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
    test_cpr: str,
) -> None:
    """Launch KY, fremsog test_cpr og vent paa Borgeroverblik."""

    session = automation_session
    page = ky_page
    cpr = _validate_cpr(test_cpr)

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(BORGEROVERBLIK_TIMEOUT_MS)
    _set_recorder_page(session, page)

    print()
    print("=" * 70)
    print("TRIN 1: LAUNCHER KY")
    print("=" * 70)
    print(f"Credential-post: {ky_credential_name}")

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    assert not page.is_closed(), (
        "Playwright-siden blev lukket under KY-launch."
    )
    assert not is_ky_error_url(page), (
        f"KY viste fejlsiden efter launch: {page.url}"
    )
    assert is_ky_url(page), (
        f"Siden er ikke en gyldig KY-side: {page.url}"
    )
    assert await has_jsessionid(page), (
        "KY blev aabnet, men JSESSIONID mangler."
    )

    print("KY er klar, og JSESSIONID blev fundet.")

    print()
    print("=" * 70)
    print("TRIN 2: FREMSOEGER BORGER")
    print("=" * 70)
    print(f"Test-CPR: {cpr}")

    await _fremsoeg_borger(
        page=page,
        cpr=cpr,
    )

    print("Borgeren blev fremsogt.")

    print()
    print("=" * 70)
    print("TRIN 3: AABNER BORGEROVERBLIK")
    print("=" * 70)

    await _aabn_overblik(page)
    overblik_element = await _vent_paa_borgeroverblik(page)

    assert await overblik_element.is_visible(), (
        "Borgeroverblik-elementet blev fundet, men er ikke synligt."
    )

    await page.bring_to_front()
    _set_recorder_page(session, page)

    print()
    print("=" * 70)
    print("BORGEROVERBLIK ER KLAR")
    print("=" * 70)
    print(f"Aktuel URL: {page.url}")

    await session.screenshot(
        page=page,
        name="STEP_launch_og_borgeroverblik_klar",
    )


async def _fremsoeg_borger(
    page: Page,
    cpr: str,
) -> None:
    """Indtast CPR i KY's topsearch og vent paa borgersiden."""

    search_input = await _vent_paa_input(
        page=page,
        selectors=TOPSEARCH_SELECTORS,
        timeout_ms=BORGEROVERBLIK_TIMEOUT_MS,
    )

    await search_input.scroll_into_view_if_needed()
    await search_input.click(timeout=ACTION_TIMEOUT_MS)

    try:
        await search_input.fill("")
        await search_input.fill(cpr)
    except Exception:
        await search_input.press("Control+A")
        await search_input.press("Backspace")
        await search_input.press_sequentially(cpr, delay=75)

    actual_value = _normalise_cpr(
        await search_input.input_value()
    )

    if actual_value != cpr:
        await search_input.click(timeout=ACTION_TIMEOUT_MS)
        await search_input.press("Control+A")
        await search_input.press("Backspace")
        await search_input.press_sequentially(cpr, delay=75)
        actual_value = _normalise_cpr(
            await search_input.input_value()
        )

    if actual_value != cpr:
        raise AssertionError(
            "CPR blev ikke indsat korrekt i topsearch. "
            f"Forventet: {cpr}. Faktisk: {actual_value}."
        )

    await search_input.press("Enter")

    # Det foerste Enter kan kun vaelge autocomplete-resultatet.
    try:
        await _vent_paa_borgerside(
            page=page,
            timeout_ms=15_000,
        )
    except AssertionError:
        await search_input.press("Enter")

    await _vent_paa_borgerside(
        page=page,
        timeout_ms=BORGEROVERBLIK_TIMEOUT_MS,
    )


async def _aabn_overblik(page: Page) -> None:
    """Klik paa Overblik, medmindre overblikket allerede er synligt."""

    if await _overblik_er_synligt(page):
        print("Borgeroverblik er allerede synligt.")
        return

    overblik_link = await _vent_paa_synlig_locator(
        page=page,
        selectors=OVERBLIK_NAV_SELECTORS,
        timeout_ms=BORGEROVERBLIK_TIMEOUT_MS,
        require_enabled=True,
    )

    await overblik_link.scroll_into_view_if_needed()
    await overblik_link.click(timeout=ACTION_TIMEOUT_MS)


async def _vent_paa_borgeroverblik(page: Page) -> Locator:
    """Vent paa et kendt og synligt element paa Borgeroverblik."""

    return await _vent_paa_synlig_locator(
        page=page,
        selectors=OVERBLIK_CONTENT_SELECTORS,
        timeout_ms=BORGEROVERBLIK_TIMEOUT_MS,
    )


async def _vent_paa_borgerside(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Vent paa borgernavigation eller kendt indhold paa borgersiden."""

    return await _vent_paa_synlig_locator(
        page=page,
        selectors=(
            *OVERBLIK_NAV_SELECTORS,
            *OVERBLIK_CONTENT_SELECTORS,
        ),
        timeout_ms=timeout_ms,
    )


async def _overblik_er_synligt(page: Page) -> bool:
    """Kontroller om Borgeroverblik allerede er synligt."""

    for frame in page.frames:
        for selector in OVERBLIK_CONTENT_SELECTORS:
            try:
                candidates = frame.locator(selector)

                for index in range(await candidates.count()):
                    if await candidates.nth(index).is_visible():
                        return True
            except Exception:
                continue

    return False


async def _vent_paa_synlig_locator(
    page: Page,
    selectors: tuple[str, ...],
    timeout_ms: int,
    require_enabled: bool = False,
) -> Locator:
    """Vent paa den foerste synlige locator paa siden eller i et iframe."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise AssertionError(
                "KY-browserfanen blev lukket under ventetiden."
            )

        for frame in page.frames:
            for selector in selectors:
                try:
                    candidates = frame.locator(selector)

                    for index in range(await candidates.count()):
                        candidate = candidates.nth(index)

                        if not await candidate.is_visible():
                            continue

                        if (
                            require_enabled
                            and not await candidate.is_enabled()
                        ):
                            continue

                        return candidate
                except Exception:
                    continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "Det forventede KY-element blev ikke synligt inden for "
        f"{timeout_ms / 1000:.0f} sekunder. URL: {page.url}"
    )


async def _vent_paa_input(
    page: Page,
    selectors: tuple[str, ...],
    timeout_ms: int,
) -> Locator:
    """Vent paa et synligt, aktivt og redigerbart inputfelt."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise AssertionError(
                "KY-browserfanen blev lukket under ventetiden."
            )

        for frame in page.frames:
            for selector in selectors:
                try:
                    candidates = frame.locator(selector)

                    for index in range(await candidates.count()):
                        candidate = candidates.nth(index)

                        if not await candidate.is_visible():
                            continue
                        if not await candidate.is_enabled():
                            continue
                        if not await candidate.is_editable():
                            continue

                        return candidate
                except Exception:
                    continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "KY's topsearch blev ikke klar inden for "
        f"{timeout_ms / 1000:.0f} sekunder."
    )


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Opdater BrowserSessions recorder til den aktive side."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)

    if callable(set_page):
        set_page(page)


def _validate_cpr(value: str) -> str:
    """Normaliser og valider test_cpr."""

    cpr = _normalise_cpr(value)

    if not cpr.isdigit() or len(cpr) != 10:
        raise ValueError(
            "test_cpr skal bestaa af praecis 10 cifre."
        )

    return cpr


def _normalise_cpr(value: str) -> str:
    """Fjern bindestreg og mellemrum fra CPR."""

    return re.sub(r"[-\s]", "", value).strip()
