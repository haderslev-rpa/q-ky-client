"""Integrationstest og hjælpefunktioner til KY Borgeroverblik.

Modulet bruger den BrowserSession og Page, som oprettes af fixtures i
``tests/conftest.py``. Modulet starter derfor ikke selv en ny browser og læser
ikke credentials eller CPR fra miljøvariabler.

Den genanvendelige funktion ``gaa_til_borgeroverblik()`` er beregnet til det
samlede flow:

1. Opgavebakken åbner en PDF i en ny browserfane.
2. PDF-fanen sendes ind som ``current_page``.
3. PDF-fanen lukkes, når ``luk_aktuel_browserfane=True``.
4. Playwright finder selv en tilbageværende KY-fane via BrowserContext.pages.
5. KY-fanen bringes frem.
6. Der klikkes på Overblik.
7. Funktionen venter på, at Borgeroverblik er synligt.

Der skal ikke sendes en URL fra opgavebakken som input.
"""

from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import Locator, Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import has_jsessionid, launch_ky


pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

KY_DOMAIN = "kommunernesydelsessystem.dk"
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


async def gaa_til_borgeroverblik(
    session: BrowserSession,
    current_page: Page,
    luk_aktuel_browserfane: bool,
) -> Page:
    """Luk eventuelt den aktuelle browserfane og vis Borgeroverblik.

    Args:
        session:
            Den eksisterende BrowserSession fra ``automation_session``.
        current_page:
            Den browserfane, flowet aktuelt står på. Det vil normalt være
            PDF-fanen fra opgavebakken.
        luk_aktuel_browserfane:
            Hvis True, lukkes ``current_page`` før faneskiftet. Hvis False,
            beholdes fanen, men funktionen skifter stadig til en KY-fane.

    Returns:
        Den eksisterende KY-side, som viser Borgeroverblik.
    """

    ky_page = await skift_til_ky_fane(
        session=session,
        current_page=current_page,
        luk_aktuel_browserfane=luk_aktuel_browserfane,
    )

    await _aabn_overblik(ky_page)
    await _vent_paa_borgeroverblik(ky_page)
    await ky_page.bring_to_front()
    _set_recorder_page(session, ky_page)

    print("Borgeroverblik er synligt.")
    print(f"Aktiv KY-URL: {ky_page.url}")

    return ky_page


async def skift_til_ky_fane(
    session: BrowserSession,
    current_page: Page,
    luk_aktuel_browserfane: bool,
) -> Page:
    """Find en eksisterende KY-fane gennem BrowserContext.pages.

    Funktionen prioriterer den første tilbageværende fane på KY-domænet.
    Hvis der ikke findes en KY-fane, fejler funktionen tydeligt i stedet for
    at vælge en vilkårlig browserfane.
    """

    if current_page.is_closed():
        raise AssertionError("Den aktuelle browserfane er allerede lukket.")

    context = current_page.context

    if luk_aktuel_browserfane:
        other_pages = [
            page
            for page in context.pages
            if page != current_page and not page.is_closed()
        ]

        if not other_pages:
            raise AssertionError(
                "Den aktuelle browserfane kan ikke lukkes, fordi der ikke "
                "findes en anden åben browserfane."
            )

        await current_page.close()
        await _vent_paa_lukket_fane(
            page=current_page,
            timeout_ms=ACTION_TIMEOUT_MS,
        )
        print("Den aktuelle browserfane blev lukket.")

    ky_pages = [
        page
        for page in context.pages
        if not page.is_closed() and _er_ky_fane(page)
    ]

    if not ky_pages:
        open_urls = [
            page.url
            for page in context.pages
            if not page.is_closed()
        ]
        raise AssertionError(
            "Der blev ikke fundet en åben KY-browserfane. "
            f"Åbne faner: {open_urls}"
        )

    # Den første KY-fane svarer normalt til den oprindelige side, som
    # BrowserSession oprettede før PDF-fanen blev åbnet.
    ky_page = ky_pages[0]
    await ky_page.bring_to_front()
    _set_recorder_page(session, ky_page)

    print(f"Skiftede til KY-browserfanen: {ky_page.url}")

    return ky_page


async def test_borgeroverblik(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
    test_cpr: str,
) -> None:
    """Selvstændig test af faneskift og Borgeroverblik.

    Den selvstændige test launcher KY, fremsøger borgeren og opretter en
    midlertidig ekstra browserfane. Den ekstra fane lukkes med True-inputtet,
    hvorefter testen skifter tilbage til KY og åbner Borgeroverblik.

    I det samlede opgavebakkeflow skal ``gaa_til_borgeroverblik()`` kaldes
    direkte med den rigtige PDF-fane i stedet for denne testfunktion.
    """

    session = automation_session
    page = ky_page

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(BORGEROVERBLIK_TIMEOUT_MS)
    _set_recorder_page(session, page)

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    assert await has_jsessionid(page), (
        "KY-login blev gennemført, men JSESSIONID mangler."
    )

    await _fremsoeg_borger(
        page=page,
        cpr=test_cpr,
    )
    await _vent_paa_borgerside(page)

    # Simuler den ekstra browserfane, som opgavebakken opretter ved åbning
    # af PDF-dokumentet. Funktionen skal lukke denne fane igen.
    current_page = await page.context.new_page()
    await current_page.goto("about:blank")
    await current_page.bring_to_front()

    overblik_page = await gaa_til_borgeroverblik(
        session=session,
        current_page=current_page,
        luk_aktuel_browserfane=True,
    )

    assert current_page.is_closed(), (
        "Den aktuelle browserfane blev ikke lukket."
    )
    assert overblik_page == page, (
        "Funktionen skiftede ikke tilbage til den oprindelige KY-fane."
    )
    assert _er_ky_fane(overblik_page), (
        f"Den aktive fane er ikke en KY-fane: {overblik_page.url}"
    )

    await session.screenshot(
        page=overblik_page,
        name="STEP_borgeroverblik_klar",
    )

    print("Test af Borgeroverblik blev gennemført.")


async def _fremsoeg_borger(
    page: Page,
    cpr: str,
) -> None:
    """Fremsøg borger via KY's topsearch-felt."""

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

    actual_value = _normaliser_cpr(
        await search_input.input_value()
    )

    if actual_value != cpr:
        raise AssertionError(
            "CPR blev ikke indsat korrekt i topsearch. "
            f"Forventet: {cpr}. Faktisk: {actual_value}."
        )

    await search_input.press("Enter")

    # Første Enter kan kun vælge et autocomplete-resultat.
    try:
        await _vent_paa_borgerside(
            page=page,
            timeout_ms=15_000,
        )
    except AssertionError:
        await search_input.press("Enter")

    await _vent_paa_borgerside(page)


async def _aabn_overblik(page: Page) -> None:
    """Klik på Overblik, hvis Borgeroverblik ikke allerede er synligt."""

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
    """Vent på et kendt og synligt element på Borgeroverblik."""

    return await _vent_paa_synlig_locator(
        page=page,
        selectors=OVERBLIK_CONTENT_SELECTORS,
        timeout_ms=BORGEROVERBLIK_TIMEOUT_MS,
    )


async def _vent_paa_borgerside(
    page: Page,
    timeout_ms: int = BORGEROVERBLIK_TIMEOUT_MS,
) -> Locator:
    """Vent på borgernavigation eller et kendt borgeroverblik-element."""

    return await _vent_paa_synlig_locator(
        page=page,
        selectors=(
            *OVERBLIK_NAV_SELECTORS,
            *OVERBLIK_CONTENT_SELECTORS,
        ),
        timeout_ms=timeout_ms,
    )


async def _overblik_er_synligt(page: Page) -> bool:
    """Kontrollér om Borgeroverblik allerede er synligt."""

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
    """Vent på første synlige locator på siden eller i et iframe."""

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
    """Vent på et synligt, aktivt og redigerbart inputfelt."""

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


async def _vent_paa_lukket_fane(
    page: Page,
    timeout_ms: int,
) -> None:
    """Vent på, at den aktuelle browserfane er lukket."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            return

        await asyncio.sleep(POLL_INTERVAL_MS / 1000)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "Browserfanen blev ikke lukket inden for "
        f"{timeout_ms / 1000:.0f} sekunder."
    )


def _er_ky_fane(page: Page) -> bool:
    """Kontrollér om browserfanen tilhører KY-domænet."""

    return (
        not page.is_closed()
        and KY_DOMAIN in page.url.casefold()
    )


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Opdatér BrowserSessions recorder til den aktive browserfane."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)

    if callable(set_page):
        set_page(page)


def _normaliser_cpr(value: str) -> str:
    """Fjern bindestreg og mellemrum fra CPR."""

    return value.replace("-", "").replace(" ", "").strip()
