"""Integrationstest: fremsøg to borgere, print personoplysninger og luk faner.

Flow:

1. Start BrowserSession.
2. Launch KY.
3. Hent TEST_CPR_1 og TEST_CPR_2 fra projektets .env.
4. Fremsøg TEST_CPR_1.
5. Print den synlige Personoplysninger-tabel.
6. Vent 3 sekunder.
7. Fremsøg TEST_CPR_2.
8. Print den synlige Personoplysninger-tabel.
9. Luk begge PERSON-faner.
10. Vent 10 sekunder.
11. Luk browseren.

Der foretages ingen sammenligning mellem det fremsøgte CPR og CPR-værdien
i Personoplysninger-tabellen. Den synlige tabel læses og printes, uanset
hvilket CPR-nummer den indeholder.

Kør:

    uv run pytest \
        tests/test_fremsoeg_to_borgere_print_og_luk.py \
        -s -vv

Projektets .env skal indeholde:

    TEST_CPR_1=0101011234
    TEST_CPR_2=0202025678
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv
from playwright.async_api import Frame, Locator, Page
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


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250

WAIT_BETWEEN_CITIZENS_MS = 3_000
WAIT_BEFORE_BROWSER_CLOSE_MS = 10_000
WAIT_AFTER_TAB_CLOSE_MS = 1_000
MAX_CLOSE_ATTEMPTS = 3

load_dotenv(
    dotenv_path=ENV_FILE,
    override=False,
)


# ---------------------------------------------------------------------------
# PERSON-faner
# ---------------------------------------------------------------------------

PERSON_TAB_SELECTOR = (
    "li.tab.topmenu-tab"
    "[data-tab-target-id='PERSON']"
)

PERSON_CLOSE_BUTTON_SELECTOR = (
    "li.tab.topmenu-tab"
    "[data-tab-target-id='PERSON'] "
    ".navigation-close-tab"
    "[data-entity-type='PERSON']"
)

ACTIVE_PERSON_TAB_SELECTOR = (
    "li.tab.topmenu-tab.active"
    "[data-tab-target-id='PERSON']"
)

ACTIVE_PERSON_CLOSE_BUTTON_SELECTOR = (
    f"{ACTIVE_PERSON_TAB_SELECTOR} "
    ".navigation-close-tab"
    "[data-entity-type='PERSON']"
)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

async def test_fremsoeg_to_borgere_print_og_luk(
    ky_credential_name: str,
) -> None:
    """Fremsøg to borgere, print tabellerne og luk begge PERSON-faner."""

    test_cpr_1 = _hent_test_cpr("TEST_CPR_1")
    test_cpr_2 = _hent_test_cpr("TEST_CPR_2")

    if test_cpr_1 == test_cpr_2:
        pytest.fail(
            "TEST_CPR_1 og TEST_CPR_2 skal være forskellige.",
            pytrace=False,
        )

    session = BrowserSession(
        headless=False,
        debug=True,
        video=False,
    )

    page: Page | None = None

    try:
        # ------------------------------------------------------------------
        # Start browser
        # ------------------------------------------------------------------

        _print_step("STARTER BROWSERSESSION")

        await session.start()
        page = await session.new_page()

        page.set_default_timeout(ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        _set_recorder_page(
            session=session,
            page=page,
        )

        # ------------------------------------------------------------------
        # Launch KY
        # ------------------------------------------------------------------

        _print_step("LAUNCHER KY")

        await launch_ky(
            page=page,
            session=session,
            credential_name=ky_credential_name,
        )

        assert not page.is_closed(), (
            "KY-siden blev lukket under launch."
        )

        assert not is_ky_error_url(page), (
            "KY viste fejlsiden efter launch. "
            f"Aktuel URL: {page.url}"
        )

        assert is_ky_url(page), (
            "Siden er ikke en gyldig KY-side efter launch. "
            f"Aktuel URL: {page.url}"
        )

        assert await has_jsessionid(page), (
            "KY-sessionen mangler JSESSIONID."
        )

        print(
            "KY blev startet korrekt.",
            flush=True,
        )
        print(
            f"Aktuel URL: {page.url}",
            flush=True,
        )

        # Gem de faner, der eventuelt allerede var åbne.
        tabs_before_test = set(
            await _hent_person_tab_ids(page)
        )

        print(
            "PERSON-faner før testen: "
            f"{len(tabs_before_test)}",
            flush=True,
        )

        # ------------------------------------------------------------------
        # Fremsøg TEST_CPR_1
        # ------------------------------------------------------------------

        _print_step("FREMSØGER TEST_CPR_1")

        print(
            "TEST_CPR_1 blev hentet fra .env: "
            f"{_masker_cpr(test_cpr_1)}",
            flush=True,
        )

        tabs_before_first_search = set(
            await _hent_person_tab_ids(page)
        )

        first_table = await _fremsoeg_borger_og_find_tabel(
            page=page,
            cpr=test_cpr_1,
            minimum_tab_count=len(tabs_before_first_search) + 1,
            timeout_ms=PAGE_TIMEOUT_MS,
        )

        tabs_after_first_search = set(
            await _hent_person_tab_ids(page)
        )

        first_new_tabs = (
            tabs_after_first_search
            - tabs_before_first_search
        )

        print(
            "Nye PERSON-faner efter TEST_CPR_1: "
            f"{len(first_new_tabs)}",
            flush=True,
        )

        _print_step("PERSONOPLYSNINGER FOR FØRSTE OPSLAG")

        first_rows = await _laes_personoplysninger(
            table=first_table,
        )

        _print_personoplysninger(
            rows=first_rows,
            heading="FØRSTE BORGER",
        )

        # ------------------------------------------------------------------
        # Vent tre sekunder
        # ------------------------------------------------------------------

        _print_step("VENTER 3 SEKUNDER FØR NÆSTE BORGEROPSLAG")

        await page.wait_for_timeout(
            WAIT_BETWEEN_CITIZENS_MS
        )

        print(
            "Ventetiden på 3 sekunder er afsluttet.",
            flush=True,
        )

        # ------------------------------------------------------------------
        # Fremsøg TEST_CPR_2
        # ------------------------------------------------------------------

        _print_step("FREMSØGER TEST_CPR_2")

        print(
            "TEST_CPR_2 blev hentet fra .env: "
            f"{_masker_cpr(test_cpr_2)}",
            flush=True,
        )

        tabs_before_second_search = set(
            await _hent_person_tab_ids(page)
        )

        second_table = await _fremsoeg_borger_og_find_tabel(
            page=page,
            cpr=test_cpr_2,
            minimum_tab_count=len(tabs_before_second_search) + 1,
            timeout_ms=PAGE_TIMEOUT_MS,
        )

        tabs_after_second_search = set(
            await _hent_person_tab_ids(page)
        )

        second_new_tabs = (
            tabs_after_second_search
            - tabs_before_second_search
        )

        print(
            "Nye PERSON-faner efter TEST_CPR_2: "
            f"{len(second_new_tabs)}",
            flush=True,
        )

        _print_step("PERSONOPLYSNINGER FOR ANDET OPSLAG")

        second_rows = await _laes_personoplysninger(
            table=second_table,
        )

        _print_personoplysninger(
            rows=second_rows,
            heading="ANDEN BORGER",
        )

        # ------------------------------------------------------------------
        # Find fanerne, som er åbnet af testen
        # ------------------------------------------------------------------

        _print_step("FINDER BORGERFANER ÅBNET AF TESTEN")

        tabs_after_both_searches = set(
            await _hent_person_tab_ids(page)
        )

        tabs_opened_by_test = list(
            tabs_after_both_searches - tabs_before_test
        )

        print(
            "PERSON-faner åbnet af testen: "
            f"{len(tabs_opened_by_test)}",
            flush=True,
        )

        for entity_id in tabs_opened_by_test:
            print(
                f"PERSON-fane: {entity_id}",
                flush=True,
            )

        assert len(tabs_opened_by_test) >= 2, (
            "Forventede mindst to nye PERSON-faner efter opslag "
            "af TEST_CPR_1 og TEST_CPR_2, men fandt "
            f"{len(tabs_opened_by_test)}."
        )

        # ------------------------------------------------------------------
        # Luk begge faner
        # ------------------------------------------------------------------

        _print_step("LUKKER BEGGE BORGERFANER")

        await _luk_person_faner(
            page=page,
            entity_ids=tabs_opened_by_test,
        )

        # ------------------------------------------------------------------
        # Verificér lukning
        # ------------------------------------------------------------------

        _print_step("VERIFICERER AT BORGERFANERNE ER LUKKET")

        remaining_ids = set(
            await _hent_person_tab_ids(page)
        )

        tabs_still_open = [
            entity_id
            for entity_id in tabs_opened_by_test
            if entity_id in remaining_ids
        ]

        assert not tabs_still_open, (
            "Følgende PERSON-faner er stadig åbne: "
            f"{tabs_still_open}"
        )

        print(
            "Begge borgerfaner blev lukket og lukningen "
            "blev verificeret.",
            flush=True,
        )

        # ------------------------------------------------------------------
        # Vent 10 sekunder
        # ------------------------------------------------------------------

        _print_step("VENTER 10 SEKUNDER FØR BROWSEREN LUKKES")

        await page.wait_for_timeout(
            WAIT_BEFORE_BROWSER_CLOSE_MS
        )

        print(
            "Ventetiden på 10 sekunder er afsluttet.",
            flush=True,
        )

    finally:
        # ------------------------------------------------------------------
        # Luk browser
        # ------------------------------------------------------------------

        _print_step("LUKKER BROWSERSESSION")

        await session.close()

        print(
            "BrowserSession og browseren er lukket.",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Borgeropslag
# ---------------------------------------------------------------------------

async def _fremsoeg_borger_og_find_tabel(
    page: Page,
    cpr: str,
    minimum_tab_count: int,
    timeout_ms: int,
) -> Locator:
    """Fremsøg borgeren og returnér den synlige Personoplysninger-tabel.

    Hele CPR-værdien indsættes på én gang med ``fill()``.

    Der foretages ingen sammenligning mellem det fremsøgte CPR og CPR i
    tabellen. Borgeropslaget anses for udført, når:

    1. En ny PERSON-fane er åbnet.
    2. En synlig Personoplysninger-tabel har mindst én læsbar række.
    """

    print(
        "Borgeropslag: leder efter aktivt topSearch-felt.",
        flush=True,
    )

    _, search_input = await _find_aktivt_topsearch(
        page=page,
        timeout_ms=timeout_ms,
    )

    print(
        "Borgeropslag: aktivt topSearch-felt blev fundet.",
        flush=True,
    )

    await search_input.scroll_into_view_if_needed()

    await search_input.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    # Indsæt hele CPR-værdien på én gang.
    await search_input.fill(cpr)

    actual_value = _normaliser_cpr_input(
        await search_input.input_value()
    )

    if actual_value != cpr:
        raise RuntimeError(
            "CPR blev ikke indsat korrekt i topSearch. "
            f"Forventede {_masker_cpr(cpr)}, men feltet "
            f"indeholder {_masker_cpr(actual_value)}."
        )

    print(
        "Borgeropslag: hele CPR-værdien blev indsat korrekt.",
        flush=True,
    )

    # fill() udsender input-event. Change-eventen sendes også, da KY
    # i nogle flows kan lytte specifikt efter change.
    await search_input.dispatch_event("change")

    await search_input.press("Enter")

    print(
        "Borgeropslag: første Enter blev sendt.",
        flush=True,
    )

    try:
        await _vent_paa_minimum_person_tabs(
            page=page,
            minimum_count=minimum_tab_count,
            timeout_ms=15_000,
        )

    except PlaywrightTimeoutError:
        print(
            "Borgeropslag: ny PERSON-fane blev ikke åbnet efter "
            "første Enter. Prøver andet Enter.",
            flush=True,
        )

        _, search_input = await _find_aktivt_topsearch(
            page=page,
            timeout_ms=ACTION_TIMEOUT_MS,
        )

        current_value = _normaliser_cpr_input(
            await search_input.input_value()
        )

        if current_value != cpr:
            await search_input.fill(cpr)
            await search_input.dispatch_event("change")

        await search_input.press("Enter")

        print(
            "Borgeropslag: andet Enter blev sendt.",
            flush=True,
        )

        try:
            await _vent_paa_minimum_person_tabs(
                page=page,
                minimum_count=minimum_tab_count,
                timeout_ms=15_000,
            )

        except PlaywrightTimeoutError:
            print(
                "Borgeropslag: ny PERSON-fane blev ikke åbnet "
                "efter andet Enter. Prøver KY's søgeknap.",
                flush=True,
            )

            clicked = await _klik_topsearch_knap(
                page=page,
            )

            if not clicked:
                raise RuntimeError(
                    "En ny PERSON-fane blev ikke åbnet efter Enter, "
                    "og KY's søgeknap kunne ikke findes."
                )

            await _vent_paa_minimum_person_tabs(
                page=page,
                minimum_count=minimum_tab_count,
                timeout_ms=timeout_ms,
            )

    print(
        "Borgeropslag: ny PERSON-fane blev registreret.",
        flush=True,
    )

    table = await _vent_paa_synlig_personoplysninger(
        page=page,
        timeout_ms=timeout_ms,
    )

    print(
        "Borgeropslag: synlig Personoplysninger-tabel blev fundet.",
        flush=True,
    )

    return table


# ---------------------------------------------------------------------------
# TopSearch
# ---------------------------------------------------------------------------

async def _find_aktivt_topsearch(
    page: Page,
    timeout_ms: int,
) -> tuple[Frame, Locator]:
    """Find et synligt, aktivt og redigerbart topSearch-felt."""

    selectors = (
        KYSelectors.Main.TOP_SEARCH,
        "input#topsearch",
        "input[name='topSearch']",
        "input[name='topsearch']",
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

    elapsed_ms = 0
    last_diagnostic: list[dict[str, object]] = []

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under søgningen efter topSearch."
            )

        diagnostic: list[dict[str, object]] = []

        for frame in page.frames:
            for selector in selectors:
                try:
                    candidates = frame.locator(selector)
                    candidate_count = await candidates.count()

                    if candidate_count:
                        diagnostic.append(
                            {
                                "frame": frame.url,
                                "selector": selector,
                                "antal": candidate_count,
                            }
                        )

                    for index in range(candidate_count):
                        candidate = candidates.nth(index)

                        if not await candidate.is_visible():
                            continue

                        if not await candidate.is_enabled():
                            continue

                        if not await candidate.is_editable():
                            continue

                        return frame, candidate

                except Exception:
                    continue

        last_diagnostic = diagnostic

        if elapsed_ms % 2_000 == 0:
            print(
                "Borgeropslag: venter på aktivt topSearch. "
                f"Diagnose: {diagnostic}",
                flush=True,
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Et synligt, aktivt og redigerbart topSearch-felt blev "
        f"ikke fundet inden for {timeout_ms / 1000:.0f} sekunder. "
        f"URL: {page.url}. Diagnose: {last_diagnostic!r}"
    )


async def _klik_topsearch_knap(
    page: Page,
) -> bool:
    """Find og klik en synlig KY-søgeknap."""

    selectors = (
        KYSelectors.Main.TOP_SEARCH_BUTTON,
        "div#topSearchBtn",
        "button#topSearchBtn",
        "#topSearchBtn button",
        "#topSearchBtn a",
        "button[aria-label*='Søg' i]",
        "button[title*='Søg' i]",
    )

    for frame in page.frames:
        for selector in selectors:
            try:
                buttons = frame.locator(selector)

                for index in range(await buttons.count()):
                    button = buttons.nth(index)

                    if not await button.is_visible():
                        continue

                    if not await button.is_enabled():
                        continue

                    await button.scroll_into_view_if_needed()

                    await button.click(
                        timeout=ACTION_TIMEOUT_MS,
                    )

                    print(
                        "Borgeropslag: KY's søgeknap blev klikket "
                        f"via selector {selector!r}.",
                        flush=True,
                    )

                    return True

            except Exception:
                continue

    return False


# ---------------------------------------------------------------------------
# Personoplysninger
# ---------------------------------------------------------------------------

async def _vent_paa_synlig_personoplysninger(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Returnér den første synlige tabel med læsbare rækker.

    Der foretages ingen CPR-kontrol.
    """

    elapsed_ms = 0
    last_diagnostic: list[dict[str, object]] = []

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under ventetiden på "
                "Personoplysninger."
            )

        diagnostic: list[dict[str, object]] = []

        for frame in page.frames:
            try:
                tables = frame.locator(
                    KYSelectors.Borgere.PERSON_OPLYSNINGER
                )

                table_count = await tables.count()

                frame_diagnostic: dict[str, object] = {
                    "frame": frame.url,
                    "antal_tabeller": table_count,
                }

                diagnostic.append(frame_diagnostic)

                for index in range(table_count):
                    table = tables.nth(index)

                    if not await table.is_visible():
                        continue

                    readable_rows = await _antal_laesbare_person_rows(
                        table
                    )

                    frame_diagnostic["synlig_tabel"] = True
                    frame_diagnostic["laesbare_raekker"] = readable_rows

                    if readable_rows > 0:
                        return table

            except Exception as error:
                diagnostic.append(
                    {
                        "frame": frame.url,
                        "fejl": (
                            f"{type(error).__name__}: {error}"
                        ),
                    }
                )

        last_diagnostic = diagnostic

        if elapsed_ms % 2_000 == 0:
            print(
                "Venter på synlig Personoplysninger-tabel: "
                f"{diagnostic}",
                flush=True,
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "En synlig Personoplysninger-tabel med mindst én læsbar "
        f"række blev ikke fundet inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"URL: {page.url}. Diagnose: {last_diagnostic!r}"
    )


async def _antal_laesbare_person_rows(
    table: Locator,
) -> int:
    """Returnér antallet af læsbare label/værdi-rækker."""

    return int(
        await table.evaluate(
            r"""
            table => {
                const normalize = value =>
                    (value || '')
                        .replace(/\s+/g, ' ')
                        .trim();

                const isVisible = element => {
                    const style = window.getComputedStyle(element);

                    return (
                        style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && element.getClientRects().length > 0
                    );
                };

                return Array.from(
                    table.querySelectorAll(
                        'tbody.datatable-tbody > tr.table-row'
                    )
                ).filter(row => {
                    if (!isVisible(row)) {
                        return false;
                    }

                    const cells = Array.from(
                        row.querySelectorAll(
                            ':scope > td:not(.handlinger)'
                        )
                    ).filter(isVisible);

                    return (
                        cells.length >= 2
                        && Boolean(normalize(cells[0].innerText))
                    );
                }).length;
            }
            """
        )
    )


async def _laes_personoplysninger(
    table: Locator,
) -> list[dict[str, str]]:
    """Læs alle synlige label/værdi-rækker.

    Tredje kolonne, ``td.handlinger``, ignoreres.
    """

    rows = await table.evaluate(
        r"""
        table => {
            const normalize = value =>
                (value || '')
                    .replace(/\s+/g, ' ')
                    .trim();

            const isVisible = element => {
                const style = window.getComputedStyle(element);

                return (
                    style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && element.getClientRects().length > 0
                );
            };

            return Array.from(
                table.querySelectorAll(
                    'tbody.datatable-tbody > tr.table-row'
                )
            )
                .filter(isVisible)
                .map(row => {
                    const cells = Array.from(
                        row.querySelectorAll(
                            ':scope > td:not(.handlinger)'
                        )
                    ).filter(isVisible);

                    return {
                        label: cells.length >= 1
                            ? normalize(cells[0].innerText)
                            : '',
                        value: cells.length >= 2
                            ? normalize(cells[1].innerText)
                            : '',
                        data_id:
                            row.getAttribute('data-id') || ''
                    };
                })
                .filter(row => row.label);
        }
        """
    )

    if not isinstance(rows, list):
        raise RuntimeError(
            "Personoplysninger blev ikke returneret som en liste."
        )

    result: list[dict[str, str]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        label = str(
            row.get("label", "")
        ).strip()

        if not label:
            continue

        result.append(
            {
                "label": label,
                "value": str(
                    row.get("value", "")
                ).strip(),
                "data_id": str(
                    row.get("data_id", "")
                ).strip(),
            }
        )

    return result


def _print_personoplysninger(
    rows: list[dict[str, str]],
    heading: str,
) -> None:
    """Print alle udtrukne personoplysninger."""

    assert rows, (
        f"Personoplysninger for {heading} indeholdt ingen læsbare rækker."
    )

    print(
        "",
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )
    print(
        heading,
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )

    for row_number, row in enumerate(
        rows,
        start=1,
    ):
        print(
            f"{row_number:02d}. "
            f"{row['label']}: {row['value']}",
            flush=True,
        )

    print(
        "-" * 70,
        flush=True,
    )
    print(
        f"Antal printede felter: {len(rows)}",
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )


# ---------------------------------------------------------------------------
# PERSON-faner
# ---------------------------------------------------------------------------

async def _hent_person_tab_ids(
    page: Page,
) -> list:

    if page.is_closed():
        return []

    close_buttons = page.locator(
        PERSON_CLOSE_BUTTON_SELECTOR
    )

    entity_ids: list[str] = []

    for index in range(await close_buttons.count()):
        try:
            entity_id = await close_buttons.nth(
                index
            ).get_attribute(
                "data-entity-id"
            )
        except Exception:
            continue

        if not entity_id:
            continue

        entity_id = entity_id.strip()

        if entity_id and entity_id not in entity_ids:
            entity_ids.append(entity_id)

    return entity_ids


async def _vent_paa_minimum_person_tabs(
    page: Page,
    minimum_count: int,
    timeout_ms: int,
) -> None:
    """Vent på mindst det angivne antal PERSON-faner."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        current_count = len(
            await _hent_person_tab_ids(page)
        )

        if current_count >= minimum_count:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    current_ids = await _hent_person_tab_ids(page)

    raise PlaywrightTimeoutError(
        "Det forventede antal PERSON-faner blev ikke åbnet. "
        f"Forventede mindst {minimum_count}, "
        f"men fandt {len(current_ids)}. "
        f"Fundne entity-id'er: {current_ids}"
    )


async def _luk_person_faner(
    page: Page,
    entity_ids: list[str],
) -> None:
    """Luk de angivne PERSON-faner med højst tre gennemløb."""

    wanted_ids = list(
        dict.fromkeys(entity_ids)
    )

    for attempt in range(
        1,
        MAX_CLOSE_ATTEMPTS + 1,
    ):
        open_ids = set(
            await _hent_person_tab_ids(page)
        )

        remaining_ids = [
            entity_id
            for entity_id in wanted_ids
            if entity_id in open_ids
        ]

        if not remaining_ids:
            print(
                "Alle borgerfaner er lukket.",
                flush=True,
            )
            return

        print(
            f"Lukkeforsøg {attempt}/{MAX_CLOSE_ATTEMPTS}. "
            f"Resterende faner: {len(remaining_ids)}",
            flush=True,
        )

        # Luk fanerne én ad gangen. Genfind knappen før hvert klik,
        # da KY genopbygger fanemenuen efter lukningen.
        for entity_id in remaining_ids:
            try:
                await _luk_person_fane(
                    page=page,
                    entity_id=entity_id,
                )
            except Exception as error:
                print(
                    "Kunne ikke lukke PERSON-fane "
                    f"{entity_id}: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        await page.wait_for_timeout(
            WAIT_AFTER_TAB_CLOSE_MS
        )

    open_ids = set(
        await _hent_person_tab_ids(page)
    )

    remaining_ids = [
        entity_id
        for entity_id in wanted_ids
        if entity_id in open_ids
    ]

    if remaining_ids:
        raise AssertionError(
            "Det lykkedes ikke at lukke alle borgerfaner. "
            f"Resterende entity-id'er: {remaining_ids}"
        )


async def _luk_person_fane(
    page: Page,
    entity_id: str,
) -> None:
    """Luk én PERSON-fane og kontrollér, at knappen forsvinder."""

    selector = (
        f"{PERSON_CLOSE_BUTTON_SELECTOR}"
        f"[data-entity-id='{entity_id}']"
    )

    close_button = page.locator(
        selector
    ).first

    if await close_button.count() == 0:
        print(
            f"PERSON-fanen er allerede lukket: {entity_id}",
            flush=True,
        )
        return

    print(
        f"Lukker PERSON-fane: {entity_id}",
        flush=True,
    )

    await close_button.scroll_into_view_if_needed()

    await close_button.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    # Ved almindelige borgeroverblik bør fanen forsvinde direkte.
    # Hvis KY viser en dialog om åbne opgaver, håndteres de kendte
    # "Afbryd og gem"-knapper.
    await _haandter_eventuel_lukke_dialog(
        page=page,
    )

    await _vent_paa_person_fane_lukket(
        page=page,
        entity_id=entity_id,
        timeout_ms=ACTION_TIMEOUT_MS,
    )

    print(
        f"PERSON-fanen blev lukket: {entity_id}",
        flush=True,
    )


async def _haandter_eventuel_lukke_dialog(
    page: Page,
) -> None:
    """Klik 'Afbryd og gem', hvis KY viser en dialog ved fanelukning."""

    selectors = (
        KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_GEM,
        KYSelectors.Borgere.LUK_ALLE_OPGAVER_AFBRYD_OG_GEM,
    )

    # Giv dialogen kort tid til at blive synlig. Hvis der ikke kommer
    # en dialog, fortsætter funktionen uden fejl.
    await page.wait_for_timeout(300)

    for selector in selectors:
        try:
            buttons = page.locator(
                f"{selector}:visible"
            )

            if await buttons.count() == 0:
                continue

            button = buttons.first

            if not await button.is_enabled():
                continue

            print(
                "KY viste en dialog ved fanelukning. "
                "Klikker på 'Afbryd og gem'.",
                flush=True,
            )

            await button.click(
                timeout=ACTION_TIMEOUT_MS,
            )

            return

        except Exception:
            continue


async def _vent_paa_person_fane_lukket(
    page: Page,
    entity_id: str,
    timeout_ms: int,
) -> None:
    """Vent på, at PERSON-fanens lukknap er fjernet eller skjult."""

    selector = (
        f"{PERSON_CLOSE_BUTTON_SELECTOR}"
        f"[data-entity-id='{entity_id}']"
    )

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        buttons = page.locator(selector)

        if await buttons.count() == 0:
            return

        visible = False

        for index in range(await buttons.count()):
            try:
                if await buttons.nth(index).is_visible():
                    visible = True
                    break
            except Exception:
                continue

        if not visible:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "PERSON-fanen blev ikke lukket inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"Entity-id: {entity_id}"
    )


# ---------------------------------------------------------------------------
# Miljøvariabler og hjælpefunktioner
# ---------------------------------------------------------------------------

def _hent_test_cpr(
    variable_name: str,
) -> str:
    """Hent og validér et test-CPR fra projektets .env."""

    raw_value = os.getenv(
        variable_name,
        "",
    ).strip()

    match = re.fullmatch(
        r"\s*(\d{6})[\s-]?(\d{4})\s*",
        raw_value,
    )

    if not match:
        pytest.fail(
            f"{variable_name} mangler eller er ugyldigt i "
            f"{ENV_FILE}. Angiv præcis 10 cifre eller "
            "formatet DDMMÅÅ-NNNN.",
            pytrace=False,
        )

    return match.group(1) + match.group(2)


def _normaliser_cpr_input(
    value: str,
) -> str:
    """Fjern alle ikke-cifre fra søgefeltets værdi."""

    return re.sub(
        r"\D",
        "",
        str(value or ""),
    )


def _masker_cpr(
    cpr: str,
) -> str:
    """Maskér CPR i testens statusbeskeder."""

    digits = re.sub(
        r"\D",
        "",
        str(cpr or ""),
    )

    if len(digits) < 4:
        return "****"

    return f"******{digits[-4:]}"


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSessions recorder til Playwright-siden."""

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


def _print_step(
    title: str,
) -> None:
    """Print en tydelig sektionsoverskrift."""

    print(
        "",
        flush=True,
    )
    print(
        "=" * 70,
        flush=True,
    )
    print(
        title,
        flush=True,
    )
    print(
        "=" * 70,
        flush=True,
    )