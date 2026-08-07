"""Integrationstest: launch KY, log ind og slå en borger op."""

from __future__ import annotations

import os
from typing import Any

import pytest
from playwright.async_api import (
    Frame,
    Locator,
    Page,
    async_playwright,
)

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_url,
    is_logged_in,
    launch_ky,
    optional_screenshot,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

SEARCH_TIMEOUT_MS = int(os.getenv("KY_CPR_SEARCH_TIMEOUT_MS", "30000"))
WAIT_AFTER_CPR_INPUT_MS = int(os.getenv("KY_WAIT_AFTER_CPR_INPUT_MS", "1000"))
WAIT_AFTER_CPR_SEARCH_MS = int(os.getenv("KY_WAIT_AFTER_CPR_SEARCH_MS", "3000"))

# Selektorerne er ordnet fra mest til mindst specifik.
TOPSEARCH_SELECTORS = (
    "input[id*='topsearch' i]",
    "input[name*='topsearch' i]",
    "input[class*='topsearch' i]",
    "input[placeholder*='CPR' i]",
    "input[aria-label*='CPR' i]",
    "input[placeholder*='Søg' i]",
    "input[aria-label*='Søg' i]",
    "input[type='search']",
    "[role='searchbox']",
)

PERSON_OVERVIEW_SELECTORS = (
    "[data-textkey*='fagsystem.person.navigation']",
    "[data-textkey*='person.navigation']",
    "[data-textkey*='person.oplysninger']",
    "[id*='personoplysninger' i]",
    "[id*='borgeroplysninger' i]",
    "text=Personoplysninger",
    "text=Borgeroplysninger",
    "text=Sagsoversigt",
)


async def test_launch_login_og_borgeropslag(
    automation_session: Any,
    ky_user: str,
    test_cpr: str,
) -> None:
    """Start Chromium, launch KY, log ind og fremsøg TEST_CPR."""

    headless = _env_bool("KY_HEADLESS", default=False)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            slow_mo=0 if headless else 100,
        )

        try:
            context = await browser.new_context(
                locale="da-DK",
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )

            try:
                page = await context.new_page()
                page.set_default_timeout(30_000)
                page.set_default_navigation_timeout(60_000)

                # IDP-modulet forventer session.recorder. Recorderen afgør
                # selv via KY_SCREENSHOTS, om billeder faktisk skal gemmes.
                automation_session.recorder.set_page(page)

                print("\nStarter KY-launch og login.")
                print(
                    "Skærmbilleder: "
                    + (
                        "aktiveret"
                        if automation_session.recorder.enabled
                        else "deaktiveret"
                    )
                )

                await launch_ky(
                    page=page,
                    session=automation_session,
                    credential_name=ky_user,
                )

                assert not page.is_closed(), "Siden blev lukket under launch."
                assert is_ky_url(page), f"Ugyldig KY-URL efter login: {page.url}"
                assert await has_jsessionid(page), "JSESSIONID blev ikke fundet."
                assert await is_logged_in(page), (
                    "KY-sessionen blev ikke identificeret som logget ind."
                )

                print("KY-launch og login blev gennemført.")
                print(f"Aktuel URL efter login: {page.url}")

                await optional_screenshot(
                    automation_session,
                    "06_foer_borgeropslag",
                )

                print("Fremsøger borgeren fra TEST_CPR.")

                await naviger_til_borger_async(
                    page=page,
                    cpr=test_cpr,
                    timeout_ms=SEARCH_TIMEOUT_MS,
                )

                assert not page.is_closed(), "Siden blev lukket under borgeropslaget."

                print("Borgeren blev fremsøgt.")
                print(f"Aktuel URL efter borgeropslag: {page.url}")

                await optional_screenshot(
                    automation_session,
                    "07_borger_fremsogt",
                )

                if not headless:
                    await page.wait_for_timeout(3_000)

            finally:
                await context.close()

        finally:
            await browser.close()


async def naviger_til_borger_async(
    page: Page,
    cpr: str,
    timeout_ms: int = SEARCH_TIMEOUT_MS,
) -> None:
    """Søg efter en borger via KY's topsearch-felt.

    Funktionen undersøger hovedsiden og eventuelle iframes. Efter CPR er
    indtastet, trykkes Enter, hvorefter funktionen venter på borgerens
    oversigt eller en tydelig ændring væk fra opgaveindbakken.
    """

    cpr = _validate_cpr(cpr)
    search_input = await _wait_for_topsearch(page, timeout_ms)

    await search_input.scroll_into_view_if_needed()
    await search_input.click(timeout=timeout_ms)

    try:
        await search_input.fill("")
        await search_input.fill(cpr)
    except Exception:
        # Fallback til tastaturindtastning, hvis KY blokerer fill().
        await search_input.press("Control+A")
        await search_input.press("Backspace")
        await search_input.press_sequentially(cpr, delay=75)

    actual_value = _normalise_cpr(await search_input.input_value())

    if actual_value != cpr:
        # KY kan have JS-events, som kun udløses ved rigtig tastaturinput.
        await search_input.click()
        await search_input.press("Control+A")
        await search_input.press("Backspace")
        await search_input.press_sequentially(cpr, delay=75)

        actual_value = _normalise_cpr(await search_input.input_value())

    if actual_value != cpr:
        raise AssertionError("CPR blev ikke indsat korrekt i KY's søgefelt.")

    await page.wait_for_timeout(WAIT_AFTER_CPR_INPUT_MS)
    await search_input.press("Enter")
    await page.wait_for_timeout(WAIT_AFTER_CPR_SEARCH_MS)

    await _wait_for_person_overview(page, timeout_ms)


async def _wait_for_topsearch(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Vent på KY's synlige, aktive og redigerbare topsearch-felt."""

    elapsed_ms = 0
    interval_ms = 250

    while elapsed_ms < timeout_ms:
        found = await _find_visible_editable_input(page)

        if found is not None:
            return found

        await page.wait_for_timeout(interval_ms)
        elapsed_ms += interval_ms

    raise AssertionError("KY's aktive søgefelt blev ikke fundet inden for tidsgrænsen.")


async def _find_visible_editable_input(
    page: Page,
) -> Locator | None:
    """Find et synligt og redigerbart søgefelt i alle frames."""

    for frame in _all_frames(page):
        for selector in TOPSEARCH_SELECTORS:
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
                # Et utilgængeligt frame må ikke stoppe søgningen.
                continue

    return None


async def _wait_for_person_overview(
    page: Page,
    timeout_ms: int,
) -> None:
    """Vent på, at borgerens oversigt bliver synlig."""

    elapsed_ms = 0
    interval_ms = 500

    while elapsed_ms < timeout_ms:
        if await _person_overview_is_visible(page):
            return

        # Nogle KY-versioner viser borgeren uden de kendte selectors,
        # men ændrer URL'en væk fra opgaveindbakken.
        if (
            "kommunernesydelsessystem.dk" in page.url.casefold()
            and "/opgaveindbakke" not in page.url.casefold()
            and "/errors" not in page.url.casefold()
        ):
            await page.wait_for_timeout(1_000)

            if await _person_overview_is_visible(page):
                return

        await page.wait_for_timeout(interval_ms)
        elapsed_ms += interval_ms

    raise AssertionError(
        "CPR-søgningen blev udført, men borgeroversigten blev ikke "
        f"fundet inden for {timeout_ms} ms. Aktuel URL: {page.url}"
    )


async def _person_overview_is_visible(page: Page) -> bool:
    """Kontrollér alle frames for kendte elementer på borgeroversigten."""

    for frame in _all_frames(page):
        for selector in PERSON_OVERVIEW_SELECTORS:
            try:
                candidates = frame.locator(selector)

                for index in range(await candidates.count()):
                    if await candidates.nth(index).is_visible():
                        return True
            except Exception:
                continue

    return False


def _all_frames(page: Page) -> list[Frame]:
    """Returnér hovedframe og alle underframes uden dubletter."""

    return list(page.frames)


def _validate_cpr(cpr: str) -> str:
    """Normalisér og validér et dansk CPR-format med 10 cifre."""

    value = _normalise_cpr(cpr)

    if not value.isdigit() or len(value) != 10:
        raise ValueError("TEST_CPR skal bestå af præcis 10 cifre.")

    return value


def _normalise_cpr(value: str) -> str:
    """Fjern bindestreg og mellemrum fra et CPR-nummer."""

    return value.replace("-", "").replace(" ", "").strip()


def _env_bool(name: str, default: bool) -> bool:
    """Læs en boolsk miljøvariabel."""

    fallback = "true" if default else "false"
    value = os.getenv(name, fallback).strip().casefold()
    return value in {"1", "true", "yes", "ja"}
