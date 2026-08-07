from __future__ import annotations
import os
import re

from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    Page,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from q_haderslev_vbo.playwright.faelles_kommunal_login_idp import (
    login_via_faelles_kommunal_idp,
)

KY_URL = os.getenv(
    "KY_URL",
    "https://fs0510.fs.kommunernesydelsessystem.dk/ky-fagsystem",
).strip()
KY_MUNICIPALITY = os.getenv(
    "KY_MUNICIPALITY",
    "Haderslev Kommune",
).strip()

KY_DOMAIN = "kommunernesydelsessystem.dk"
KY_PATH = "/ky-fagsystem"
KY_ERROR_PATH = "/ky-fagsystem/errors"
AUTH_DROPDOWN = "select#SelectedAuthenticationUrl"
OK_BUTTON = (
    "input[type='button'][value='OK'], "
    "input[type='submit'][value='OK'], "
    "input[type='button'][value='Vælg'], "
    "input[type='submit'][value='Vælg'], "
    "button:has-text('OK'), "
    "button:has-text('Vælg'), "
    "button[type='submit'], "
    "input[type='submit']"
)

NAVIGATION_TIMEOUT_MS = int(os.getenv("KY_NAVIGATION_TIMEOUT_MS", "60000"))
ACTION_TIMEOUT_MS = int(os.getenv("KY_ACTION_TIMEOUT_MS", "15000"))
WAIT_AFTER_GOTO_MS = int(os.getenv("KY_WAIT_AFTER_GOTO_MS", "1500"))
WAIT_AFTER_MUNICIPALITY_MS = int(os.getenv("KY_WAIT_AFTER_MUNICIPALITY_MS", "1000"))
WAIT_AFTER_CONTINUE_MS = int(os.getenv("KY_WAIT_AFTER_CONTINUE_MS", "2000"))
WAIT_BEFORE_IDP_LOGIN_MS = int(os.getenv("KY_WAIT_BEFORE_IDP_LOGIN_MS", "1500"))
WAIT_AFTER_IDP_LOGIN_MS = int(os.getenv("KY_WAIT_AFTER_IDP_LOGIN_MS", "3000"))


class KyLaunchError(RuntimeError):
    """Fejl under launch eller login i KY."""


async def optional_screenshot(
    session,
    name: str,
) -> None:
    """Anmod sessionens recorder om et billede, hvis aktivt.

    Recorderens egen konfiguration afgør, om der faktisk tages et billede.
    Fejl herfra må aldrig stoppe launch-flowet.
    """

    recorder = getattr(session, "recorder", None)

    if recorder is None:
        return

    screenshot_method = getattr(recorder, "screenshot", None)

    if not callable(screenshot_method):
        return

    try:
        await screenshot_method(name)
    except Exception as error:
        print(f"Valgfrit skærmbillede fejlede: {type(error).__name__}: {error}")


async def wait_between_steps(
    page: Page,
    delay_ms: int,
    stage: str,
) -> None:
    """Vent kontrolleret og kontrollér, at siden stadig er åben."""

    if page.is_closed():
        raise KyLaunchError(f"Siden blev lukket før trinnet '{stage}'.")

    if delay_ms > 0:
        await page.wait_for_timeout(delay_ms)

    if page.is_closed():
        raise KyLaunchError(f"Siden blev lukket under trinnet '{stage}'.")


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
    except PlaywrightTimeoutError:
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
    """Kontrollér om browser-contexten har JSESSIONID."""

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
    timeout_ms: int = NAVIGATION_TIMEOUT_MS,
) -> None:
    """Vent på gyldig KY-URL og aktiv JSESSIONID."""

    elapsed_ms = 0
    interval_ms = 500

    while elapsed_ms < timeout_ms:
        await raise_if_ky_error(page, "vent på KY-session")

        if await is_logged_in(page):
            await wait_for_page_ready(page, timeout_ms)
            return

        await page.wait_for_timeout(interval_ms)
        elapsed_ms += interval_ms

    raise KyLaunchError(f"KY blev ikke klar efter login. Aktuel URL: {page.url}")


async def select_municipality_and_continue(page: Page) -> None:
    """Vælg kommunen, hvis kommunevælgeren vises."""

    dropdown = page.locator(AUTH_DROPDOWN)

    try:
        await dropdown.wait_for(
            state="visible",
            timeout=ACTION_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        return

    try:
        await dropdown.select_option(label=KY_MUNICIPALITY)
    except PlaywrightError as error:
        raise KyLaunchError(
            f"Kommunen '{KY_MUNICIPALITY}' kunne ikke vælges."
        ) from error

    await wait_between_steps(
        page,
        WAIT_AFTER_MUNICIPALITY_MS,
        "efter valg af kommune",
    )

    buttons = page.locator(OK_BUTTON)
    selected_button = None

    for index in range(await buttons.count()):
        candidate = buttons.nth(index)

        if await candidate.is_visible() and await candidate.is_enabled():
            selected_button = candidate
            break

    if selected_button is None:
        raise KyLaunchError("En synlig OK/Vælg-knap blev ikke fundet.")

    await selected_button.click(timeout=ACTION_TIMEOUT_MS)

    await wait_between_steps(
        page,
        WAIT_AFTER_CONTINUE_MS,
        "efter klik på OK/Vælg",
    )

    await raise_if_ky_error(page, "valg af kommune")


async def launch_ky(
    page: Page,
    session,
    credential_name: str,
) -> None:
    """Åbn KY og gennemfør login via Fælleskommunal IDP.

    ``credential_name`` er navnet på credential-posten i Automation Server.
    Selve credential-opslaget håndteres af
    ``login_via_faelles_kommunal_idp`` på samme måde som i FASIT-launch.
    """

    if page.is_closed():
        raise KyLaunchError("Playwright-siden er lukket.")

    if not credential_name.strip():
        raise KyLaunchError("credential_name må ikke være tomt.")

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)

    if callable(set_page):
        set_page(page)

    try:
        if await is_logged_in(page):
            return

        await page.goto(
            KY_URL,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )

        await wait_between_steps(
            page,
            WAIT_AFTER_GOTO_MS,
            "efter åbning af KY",
        )
        await wait_for_page_ready(page)
        await raise_if_ky_error(page, "åbning af KY")
        await optional_screenshot(session, "01_ky_aabnet")

        if await is_logged_in(page):
            return

        await select_municipality_and_continue(page)
        await optional_screenshot(session, "02_kommune_valgt")

        if await is_logged_in(page):
            return

        await wait_between_steps(
            page,
            WAIT_BEFORE_IDP_LOGIN_MS,
            "før IDP-login",
        )
        await optional_screenshot(session, "03_foer_idp_login")

        await login_via_faelles_kommunal_idp(
            page=page,
            session=session,
            credential_name=credential_name,
        )

        await wait_between_steps(
            page,
            WAIT_AFTER_IDP_LOGIN_MS,
            "efter IDP-login",
        )
        await optional_screenshot(session, "04_efter_idp_login")

        await wait_for_ky_ready(page)
        await optional_screenshot(session, "05_ky_logget_ind")

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
