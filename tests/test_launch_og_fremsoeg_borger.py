"""Integrationstest: launch KY, fremsøg borger og validér CPR.

Testen bruger fixtures fra tests/conftest.py:
- automation_session
- ky_page
- ky_credential_name
- test_cpr

Kør:
    uv run pytest tests/test_launch_og_fremsoeg_borger.py -s -vv \
        --test-cpr ""
"""

from __future__ import annotations

import re

import pytest
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)
from ky_client.selectors import KYSelectors

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

ACTION_TIMEOUT_MS = 30_000
BORGER_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250


async def test_launch_og_htopsearc_borger(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
    test_cpr: str,
) -> None:
    """Launch KY, fremsøg borgeren og sammenlign CPR-værdierne."""

    page = ky_page
    session = automation_session
    cpr = _normaliser_og_valider_cpr(test_cpr)

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(BORGER_TIMEOUT_MS)
    _set_recorder_page(session=session, page=page)

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
        f"Siden er ikke en gyldig KY-side efter launch: {page.url}"
    )
    assert await has_jsessionid(page), (
        "KY-sessionen mangler JSESSIONID efter launch."
    )

    print("KY er klar.")
    print(f"Aktuel URL: {page.url}")

    print()
    print("=" * 70)
    print("TRIN 2: FREMSØGER BORGER VIA TOPSEARCH")
    print("=" * 70)
    print(f"Fremsøgt CPR: {_masker_cpr(cpr)}")

    await naviger_til_borger_async(
        page=page,
        cpr=cpr,
        timeout=BORGER_TIMEOUT_MS,
    )

    print()
    print("=" * 70)
    print("TRIN 3: VALIDERER CPR FRA PERSONOPLYSNINGER")
    print("=" * 70)

    cpr_fra_personoplysninger = await _hent_cpr_fra_personoplysninger(
        page=page,
        timeout_ms=BORGER_TIMEOUT_MS,
    )

    assert cpr_fra_personoplysninger == cpr, (
        "Den viste borger matcher ikke det fremsøgte CPR. "
        f"Fremsøgt: {_masker_cpr(cpr)}. "
        f"Vist: {_masker_cpr(cpr_fra_personoplysninger)}."
    )

    print("CPR-værdierne matcher.")
    print(f"Fremsøgt CPR: {_masker_cpr(cpr)}")
    print(
        "CPR i Personoplysninger: "
        f"{_masker_cpr(cpr_fra_personoplysninger)}"
    )
    print(f"Aktuel URL: {page.url}")

    await session.screenshot(
        page=page,
        name="TEST_fremsoeg_korrekt_borger_valideret",
        always=True,
    )


async def naviger_til_borger_async(
    page: Page,
    cpr: str,
    timeout: int = 30_000,
) -> None:
    """Fremsøg CPR og vent på den synlige Personoplysninger-tabel."""

    cpr = _normaliser_og_valider_cpr(cpr)

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før borgeropslaget.")

    search_input = page.locator(KYSelectors.Main.TOP_SEARCH).first
    await search_input.wait_for(state="visible", timeout=timeout)
    await search_input.scroll_into_view_if_needed()
    await search_input.click(timeout=ACTION_TIMEOUT_MS)

    # fill() erstatter automatisk en eksisterende værdi.
    await search_input.fill(cpr)

    actual_cpr = _normaliser_cpr(await search_input.input_value())
    if actual_cpr != cpr:
        raise RuntimeError(
            "CPR blev ikke indsat korrekt i topsearch. "
            f"Forventet: {_masker_cpr(cpr)}. "
            f"Faktisk: {_masker_cpr(actual_cpr)}."
        )

    await search_input.press("Enter")

    try:
        await _vent_paa_personoplysninger(
            page=page,
            timeout_ms=15_000,
        )
        return
    except AssertionError:
        # Første Enter kan kun have valgt autocomplete-resultatet.
        pass

    await search_input.press("Enter")

    await _vent_paa_personoplysninger(
        page=page,
        timeout_ms=timeout,
    )


async def _vent_paa_personoplysninger(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Vent på en synlig Personoplysninger-tabel med læsbare rækker."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise AssertionError(
                "KY-browserfanen blev lukket under borgeropslaget."
            )

        if is_ky_error_url(page):
            raise AssertionError(
                "KY viste fejlsiden efter CPR-opslaget. "
                f"Aktuel URL: {page.url}"
            )

        for frame in page.frames:
            try:
                tables = frame.locator(
                    KYSelectors.Borgere.PERSON_OPLYSNINGER
                )

                for table_index in range(await tables.count()):
                    table = tables.nth(table_index)
                    if not await table.is_visible():
                        continue

                    rows = table.locator("tbody tr")
                    for row_index in range(await rows.count()):
                        row = rows.nth(row_index)
                        if not await row.is_visible():
                            continue
                        if (await row.inner_text()).strip():
                            return table
            except Exception:
                # KY kan genopbygge DOM'en under indlæsningen.
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "Personoplysninger blev ikke færdigindlæst inden for "
        f"{timeout_ms / 1000:.0f} sekunder. Aktuel URL: {page.url}"
    )


async def _hent_cpr_fra_personoplysninger(
    page: Page,
    timeout_ms: int = BORGER_TIMEOUT_MS,
) -> str:
    """Læs CPR-værdien fra den synlige Personoplysninger-tabel."""

    table = await _vent_paa_personoplysninger(
        page=page,
        timeout_ms=timeout_ms,
    )

    cpr_fra_tabel = await table.evaluate(
        r"""
        table => {
            const normalize = value =>
                (value || '').replace(/\s+/g, ' ').trim();

            const acceptedLabels = new Set([
                'cpr',
                'cpr-nummer',
                'cpr nummer',
                'personnummer'
            ]);

            const rows = Array.from(table.querySelectorAll('tbody tr'));

            for (const row of rows) {
                const cells = Array.from(
                    row.querySelectorAll('th, td:not(.handlinger)')
                ).map(cell => normalize(cell.innerText));

                for (let index = 0; index < cells.length - 1; index += 1) {
                    const label = cells[index]
                        .replace(/:$/, '')
                        .trim()
                        .toLocaleLowerCase('da-DK');

                    if (!acceptedLabels.has(label)) {
                        continue;
                    }

                    for (
                        let valueIndex = index + 1;
                        valueIndex < cells.length;
                        valueIndex += 1
                    ) {
                        if (cells[valueIndex]) {
                            return cells[valueIndex];
                        }
                    }
                }
            }

            return null;
        }
        """
    )

    if not cpr_fra_tabel:
        # Diagnostik uden at skrive hele CPR-værdier i testloggen.
        labels = await table.evaluate(
            r"""
            table => Array.from(table.querySelectorAll('tbody tr'))
                .map(row => {
                    const first = row.querySelector('th, td:not(.handlinger)');
                    return first
                        ? (first.innerText || '').replace(/\s+/g, ' ').trim()
                        : '';
                })
                .filter(Boolean)
        """
        )
        raise AssertionError(
            "Personoplysninger er synlig, men CPR-rækken blev ikke fundet. "
            f"Fundne rækkenavne: {labels}"
        )

    cpr = _normaliser_cpr(str(cpr_fra_tabel))

    if not cpr.isdigit() or len(cpr) != 10:
        raise AssertionError(
            "CPR-værdien fra Personoplysninger har ikke det forventede "
            "format med 10 cifre."
        )

    return cpr


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSessions recorder til testens aktive side."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)
    if callable(set_page):
        set_page(page)


def _normaliser_og_valider_cpr(value: str) -> str:
    """Normalisér og validér CPR til præcis ti cifre."""

    cpr = _normaliser_cpr(value)
    if not cpr.isdigit() or len(cpr) != 10:
        raise ValueError("CPR skal bestå af præcis 10 cifre.")
    return cpr


def _normaliser_cpr(value: str) -> str:
    """Fjern alle tegn, der ikke er cifre."""

    return re.sub(r"\D", "", value)


def _masker_cpr(cpr: str) -> str:
    """Maskér CPR i terminaloutput og fejlbeskeder."""

    if len(cpr) != 10:
        return "**********"
    return f"{cpr[:6]}-****"
