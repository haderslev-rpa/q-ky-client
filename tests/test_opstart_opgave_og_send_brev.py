"""Integrationstest for det fulde Send brev-flow uden afsendelse.

Ansvarsfordeling:
- ``borgere.opstart_opgave`` åbner den generelle KY-opgave og returnerer
  opgavecheckpointet.
- ``send_brev`` udfører alle Send brev-specifikke handlinger.
- ``saet_fysisk_post`` bruger JavaScript og kaldes som den sidste ændring af
  formularen.
- ``test_cpr`` leveres af ``tests/conftest.py`` fra ``TEST_CPR`` i .env eller
  fra ``--test-cpr``.

Kør:
    uv run pytest tests/test_opstart_opgave_og_send_brev.py -s -vv

Testen klikker ikke på Godkend, og brevet bliver ikke sendt.
"""

from __future__ import annotations

from pprint import pprint
from time import perf_counter
from typing import TypedDict

import pytest
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import (
    naviger_til_borger_async,
    opstart_opgave,
)
from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)
from ky_client.selectors import KYSelectors
from ky_client.functionality.send_brev import (
    saet_fysisk_post,
    vaelg_brevskabelon_fra_sti,
    vaelg_sag_til_brev,
    vaelg_standard_bilag,
    vent_efter_bilag_dropdown_er_minimeret,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
LUK_BROWSER_EFTER_MS = 5_000

SEND_BREV_MENU_STI = (
    "Administration",
    "Send brev",
)

SAG_SOEGNING = "RESJR-IIKVOV"
MEDTAG_AKTIVE_SAGER = False
MEDTAG_PASSIVE_SAGER = True

BREVSKABELON_STI = (
    "FLX",
    "Afgørelse",
    "Afgørelse efter opfølgning - fleks gl. ord. selv",
)

STANDARD_BILAG_TITEL = (
    "Oplysningspligt Hjælp til forsørgelse"
)

SEND_SOM_FYSISK_POST = True


class BrevtypeStatus(TypedDict):
    """Read-only status for Brevtype-rækken."""

    synlig: bool
    display: str
    value: str
    text: str


async def test_opstart_opgave_og_send_brev(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
    test_cpr: str,
    request: pytest.FixtureRequest,
) -> None:
    """Klargør Send brev og sæt fysisk post sidst uden at sende brevet."""

    page = ky_page
    session = automation_session

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
    _set_recorder_page(session=session, page=page)

    _print_step("STARTER TIMER OG LAUNCHER KY")
    start_tid = perf_counter()

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    assert not page.is_closed(), "KY-siden blev lukket under launch."
    assert not is_ky_error_url(page), f"KY viste fejlsiden: {page.url}"
    assert is_ky_url(page), f"Siden er ikke en gyldig KY-side: {page.url}"
    assert await has_jsessionid(page), "KY-sessionen mangler JSESSIONID."

    _print_step("FREMSØGER BORGER")

    borger_url = await naviger_til_borger_async(
        page=page,
        cpr=test_cpr,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert borger_url, "Borgeropslaget returnerede ingen URL."

    _print_step("STARTER SEND BREV VIA BORGERE.OPSTART_OPGAVE")

    checkpoint = await opstart_opgave(
        page=page,
        menu_sti=SEND_BREV_MENU_STI,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert checkpoint["opgave_id"], "Checkpointet mangler opgave-ID."
    assert checkpoint["opgave_url"], "Checkpointet mangler opgave-URL."
    assert checkpoint["opgave_navn"], "Checkpointet mangler opgavenavn."
    assert checkpoint["borger_url"] == borger_url
    assert checkpoint["opgave_id"] in checkpoint["opgave_url"]

    print("Opgavecheckpoint:")
    pprint(checkpoint, sort_dicts=False)

    request.node.user_properties.extend(
        [
            ("borger_url", checkpoint["borger_url"]),
            ("opgave_id", checkpoint["opgave_id"]),
            ("opgave_url", checkpoint["opgave_url"]),
            ("opgave_navn", checkpoint["opgave_navn"]),
        ]
    )

    _print_step("VÆLGER SAG")

    valgt_sag = await vaelg_sag_til_brev(
        page=page,
        soegevaerdi=SAG_SOEGNING,
        aktive=MEDTAG_AKTIVE_SAGER,
        passive=MEDTAG_PASSIVE_SAGER,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert valgt_sag["sag_id"], "Den valgte sag mangler sag-ID."

    _print_step("VÆLGER BREVSKABELON VIA FLX > AFGØRELSE")

    valgt_skabelon = await vaelg_brevskabelon_fra_sti(
        page=page,
        skabelon_sti=BREVSKABELON_STI,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert valgt_skabelon.casefold() == BREVSKABELON_STI[-1].casefold(), (
        "Den valgte brevskabelon matcher ikke sidste element i mappestien."
    )

    _print_step("VÆLGER OG TILFØJER STANDARD BILAG")

    valgt_bilag = await vaelg_standard_bilag(
        page=page,
        bilag_titel=STANDARD_BILAG_TITEL,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert valgt_bilag["titel"].casefold() == (
        STANDARD_BILAG_TITEL.casefold()
    )
    assert valgt_bilag["noegle"], "Standardbilaget mangler nøgle."

    _print_step("VENTER PÅ MINIMERET BILAG-DROPDOWN")

    await vent_efter_bilag_dropdown_er_minimeret(
        page=page,
        timeout=PAGE_TIMEOUT_MS,
        wait_after_closed_ms=1_500,
    )

    _print_step("SÆTTER FYSISK POST SOM SIDSTE FORMULARÆNDRING")

    # Dette er med vilje den sidste funktionelle ændring af formularen.
    # send_brev.saet_fysisk_post bruger den fungerende JavaScript-metode.
    fysisk_post = await saet_fysisk_post(
        page=page,
        fysisk_post=SEND_SOM_FYSISK_POST,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert fysisk_post is SEND_SOM_FYSISK_POST, (
        "Fysisk post fik ikke den ønskede boolske tilstand."
    )

    # Herefter udføres kun read-only kontrol, screenshot og tidsmåling.
    brevtype_status = await _laes_brevtype_status(page)

    assert brevtype_status["synlig"] is SEND_SOM_FYSISK_POST

    if SEND_SOM_FYSISK_POST:
        assert brevtype_status["display"] == "table-row"
        assert brevtype_status["value"] == "1"
        assert brevtype_status["text"].casefold() == "b-post"

    await session.screenshot(
        page=page,
        name="TEST_opstart_opgave_og_send_brev_fysisk_post_sidst",
        always=True,
    )

    samlet_tid_sekunder = perf_counter() - start_tid
    samlet_tid = _format_varighed(samlet_tid_sekunder)

    request.node.user_properties.extend(
        [
            ("valgt_sag_id", valgt_sag["sag_id"]),
            ("valgt_brevskabelon", valgt_skabelon),
            ("valgt_standard_bilag", valgt_bilag["titel"]),
            ("valgt_standard_bilag_noegle", valgt_bilag["noegle"]),
            ("fysisk_post", fysisk_post),
            ("brevtype_display", brevtype_status["display"]),
            ("brevtype_value", brevtype_status["value"]),
            ("brevtype_text", brevtype_status["text"]),
            ("samlet_tid", samlet_tid),
            ("samlet_tid_sekunder", round(samlet_tid_sekunder, 3)),
        ]
    )

    _print_step("TESTEN ER FÆRDIG UDEN AFSENDELSE")
    print(f"Samlet køretid fra launch til mål: {samlet_tid}")
    print("Godkend-knappen er ikke klikket.")
    print("Brevet er ikke sendt.")
    print("Browseren lukker automatisk om 5 sekunder.")

    # Timeren er allerede stoppet; de fem sekunder indgår ikke i køretiden.
    await page.wait_for_timeout(LUK_BROWSER_EFTER_MS)


async def _laes_brevtype_status(page: Page) -> BrevtypeStatus:
    """Læs Brevtype-status uden at ændre formularen."""

    container = page.locator(
        KYSelectors.Borgere.SEND_BREV_POSTAGE_CONTAINER
    ).last
    postage_select = page.locator(
        KYSelectors.Borgere.SEND_BREV_POSTAGE_TYPE
    ).last

    synlig = await container.is_visible()
    display = ""
    value = ""
    text = ""

    if await container.count() > 0:
        display = await container.evaluate(
            "element => window.getComputedStyle(element).display"
        )

    if synlig:
        await postage_select.wait_for(
            state="visible",
            timeout=ACTION_TIMEOUT_MS,
        )
        value = await postage_select.input_value()
        text = (
            await postage_select.locator("option:checked").inner_text()
        ).strip()

    status: BrevtypeStatus = {
        "synlig": synlig,
        "display": display,
        "value": value,
        "text": text,
    }
    print(f"Brevtype-status: {status}")
    return status


def _format_varighed(total_seconds: float) -> str:
    """Formatér en varighed som HH:MM:SS.mmm."""

    total_milliseconds = max(0, round(total_seconds * 1_000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _set_recorder_page(session: BrowserSession, page: Page) -> None:
    """Knyt BrowserSession-recorderen til testens aktive side."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)
    if callable(set_page):
        set_page(page)


def _print_step(title: str) -> None:
    """Skriv en tydelig trinoverskrift i terminalen."""

    print()
    print("=" * 70)
    print(title)
    print("=" * 70)
