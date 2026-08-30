"""Launch-flow for Kommunernes Ydelsessystem, KY.

Modulet bruger en eksisterende asynkron Playwright-side og den fælles
BrowserSession fra q-haderslev-vbo. Login gennemføres via den
Fælleskommunale IDP-funktion.
"""

from __future__ import annotations

import re

from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    Locator,
    Page,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from q_haderslev_vbo.playwright.browser_session import BrowserSession
from q_haderslev_vbo.playwright.faelles_kommunal_login_idp import (
    login_via_faelles_kommunal_idp,
)

from ky_client.selectors import KYSelectors

KY_URL = "https://fs0510.fs.kommunernesydelsessystem.dk/ky-fagsystem"

KY_MUNICIPALITY = "Haderslev Kommune"

KY_DOMAIN = "kommunernesydelsessystem.dk"
KY_PATH = "/ky-fagsystem"
KY_ERROR_PATH = "/ky-fagsystem/errors"

AUTH_DROPDOWN = "select#SelectedAuthenticationUrl"

# Kun relevante OK/Vælg-knapper. Der vælges efterfølgende kun en knap,
# som både er synlig og aktiv.
OK_BUTTON = (
    "input[type='button'][value='OK'], "
    "input[type='submit'][value='OK'], "
    "input[type='button'][value='Vælg'], "
    "input[type='submit'][value='Vælg'], "
    "button:has-text('OK'), "
    "button:has-text('Vælg')"
)

# Faste standardtider i millisekunder.
# Værdierne kan ikke længere overskrives via .env.
NAVIGATION_TIMEOUT_MS = 120_000
ACTION_TIMEOUT_MS = 30_000
LOGIN_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 500


class KyLaunchError(RuntimeError):
    """Fejl under launch eller login i KY."""


async def optional_screenshot(
    session: BrowserSession,
    page: Page,
    name: str,
) -> None:
    """Tag et valgfrit screenshot gennem BrowserSession.

    BrowserSession er den offentlige grænseflade. Recorderens screenshot-
    metode kaldes derfor ikke direkte. Screenshot-fejl må ikke stoppe
    launch-flowet.
    """

    screenshot_method = getattr(session, "screenshot", None)

    if not callable(screenshot_method):
        return

    try:
        await screenshot_method(
            page=page,
            name=name,
        )
    except Exception as error:
        print(f"Valgfrit skærmbillede fejlede: {type(error).__name__}: {error}")


async def wait_for_page_ready(
    page: Page,
    timeout_ms: int = NAVIGATION_TIMEOUT_MS,
) -> None:
    """Vent på et tilgængeligt DOM-dokument."""

    if page.is_closed():
        raise KyLaunchError("Browserens side er lukket.")

    try:
        await page.wait_for_load_state(
            "domcontentloaded",
            timeout=timeout_ms,
        )
        return
    except PlaywrightTimeoutError:
        pass

    try:
        ready = await page.evaluate(
            "() => Boolean(document && document.documentElement)"
        )
    except PlaywrightError as error:
        raise KyLaunchError("Siden blev ikke klar.") from error

    if not ready:
        raise KyLaunchError("Siden blev ikke klar.")


def is_ky_url(page: Page) -> bool:
    """Returnér True, hvis siden er en gyldig KY-side."""

    if page.is_closed():
        return False

    url = page.url.casefold()

    return (
        KY_DOMAIN.casefold() in url
        and KY_PATH.casefold() in url
        and KY_ERROR_PATH.casefold() not in url
    )


def is_ky_error_url(page: Page) -> bool:
    """Returnér True, hvis KY viser fejlsiden."""

    return not page.is_closed() and KY_ERROR_PATH.casefold() in page.url.casefold()


async def has_jsessionid(page: Page) -> bool:
    """Kontrollér om browser-contexten har en aktiv JSESSIONID."""

    if page.is_closed():
        return False

    try:
        cookies = await page.context.cookies()
    except PlaywrightError:
        return False

    return any(
        cookie.get("name", "").casefold() == "jsessionid" and bool(cookie.get("value"))
        for cookie in cookies
    )


async def is_logged_in(page: Page) -> bool:
    """Kontrollér om der er en aktiv KY-session."""

    return is_ky_url(page) and await has_jsessionid(page)


async def raise_if_ky_error(
    page: Page,
    stage: str,
) -> None:
    """Rejs en tydelig fejl, hvis KY viser sin fejlside."""

    if not is_ky_error_url(page):
        return

    body_text = ""

    try:
        body_text = re.sub(
            r"\s+",
            " ",
            await page.locator("body").inner_text(timeout=5_000),
        ).strip()
    except (PlaywrightError, PlaywrightTimeoutError):
        pass

    message = f"KY viste en fejlside under '{stage}'. URL: {page.url}"

    if body_text:
        message += f". Fejltekst: {body_text[:500]}"

    raise KyLaunchError(message)


async def wait_for_ky_ready(
    page: Page,
    timeout_ms: int = LOGIN_TIMEOUT_MS,
) -> None:
    """Vent på gyldig KY-URL og aktiv JSESSIONID."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise KyLaunchError("Browserfanen blev lukket under ventetiden på KY.")

        await raise_if_ky_error(
            page,
            "vent på KY-session",
        )

        if await is_logged_in(page):
            await wait_for_page_ready(
                page,
                timeout_ms,
            )
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise KyLaunchError(
        "KY blev ikke klar efter login inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"Aktuel URL: {page.url}"
    )


async def select_municipality_and_continue(page: Page) -> None:
    """Vælg kommunen og vent på, at kommunevælgeren forsvinder."""

    try:
        await page.wait_for_selector(
            AUTH_DROPDOWN,
            state="visible",
            timeout=ACTION_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        # Kommunevælgeren vises ikke ved en allerede aktiv session.
        return

    await page.select_option(
        AUTH_DROPDOWN,
        label=KY_MUNICIPALITY,
    )

    selected_button = await _find_visible_enabled_button(
        page=page,
        timeout_ms=ACTION_TIMEOUT_MS,
    )
    await selected_button.click(timeout=ACTION_TIMEOUT_MS)

    # Vent på et konkret DOM-skift i stedet for en fast pause.
    try:
        await page.wait_for_selector(
            AUTH_DROPDOWN,
            state="hidden",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as error:
        raise KyLaunchError(
            "Kommunevælgeren forsvandt ikke efter klik på OK/Vælg. "
            f"Aktuel URL: {page.url}"
        ) from error

    await wait_for_page_ready(page)
    await raise_if_ky_error(page, "valg af kommune")


async def launch_ky(
    page: Page,
    session: BrowserSession,
    credential_name: str,
) -> None:
    """Åbn KY og gennemfør login via Fælleskommunal IDP.

    ``credential_name`` er navnet på credential-posten i Automation Server.
    Selve credential-opslaget håndteres af
    ``login_via_faelles_kommunal_idp``.
    """

    if page.is_closed():
        raise KyLaunchError("Playwright-siden er lukket.")

    credential_name = credential_name.strip()

    if not credential_name:
        raise KyLaunchError("credential_name må ikke være tomt.")

    _set_session_page(
        session=session,
        page=page,
    )

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

    try:
        if await is_logged_in(page):
            return

        await page.goto(
            KY_URL,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )

        await wait_for_page_ready(page)
        await raise_if_ky_error(page, "åbning af KY")

        await optional_screenshot(
            session=session,
            page=page,
            name="01_ky_aabnet",
        )

        if await is_logged_in(page):
            return

        await select_municipality_and_continue(page)

        await optional_screenshot(
            session=session,
            page=page,
            name="02_kommune_valgt",
        )

        if await is_logged_in(page):
            return

        await optional_screenshot(
            session=session,
            page=page,
            name="03_foer_idp_login",
        )

        await login_via_faelles_kommunal_idp(
            page=page,
            session=session,
            credential_name=credential_name,
        )

        await wait_for_ky_ready(page)

        # Bekræft et konkret element fra den færdigindlæste KY-side.
        await page.wait_for_selector(
            KYSelectors.Main.LOGO,
            state="visible",
            timeout=LOGIN_TIMEOUT_MS,
        )

        await optional_screenshot(
            session=session,
            page=page,
            name="04_efter_idp_login",
        )

        await optional_screenshot(
            session=session,
            page=page,
            name="05_ky_logget_ind",
        )

    except KyLaunchError:
        raise

    except PlaywrightTimeoutError as error:
        raise KyLaunchError(
            "KY-launch overskred tidsgrænsen. "
            f"Underliggende fejl: {type(error).__name__}: {error}. "
            f"Aktuel URL: {page.url}"
        ) from error

    except PlaywrightError as error:
        raise KyLaunchError(
            "Playwright fejlede under KY-launch. "
            f"Underliggende fejl: {type(error).__name__}: {error}. "
            f"Aktuel URL: {page.url}"
        ) from error

    except Exception as error:
        raise KyLaunchError(
            "Login via Fælleskommunal IDP fejlede. "
            f"Underliggende fejl: {type(error).__name__}: {error}. "
            f"Aktuel URL: {page.url}"
        ) from error


async def _find_visible_enabled_button(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Find den første synlige og aktive OK/Vælg-knap."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        for frame in page.frames:
            try:
                buttons = frame.locator(OK_BUTTON)

                for index in range(await buttons.count()):
                    candidate = buttons.nth(index)

                    if not await candidate.is_visible():
                        continue

                    if not await candidate.is_enabled():
                        continue

                    return candidate
            except PlaywrightError:
                continue

        await page.wait_for_timeout(250)
        elapsed_ms += 250

    raise KyLaunchError("En synlig og aktiv OK/Vælg-knap blev ikke fundet.")


def _set_session_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSessions recorder til den aktive Playwright-side."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)

    if callable(set_page):
        set_page(page)
