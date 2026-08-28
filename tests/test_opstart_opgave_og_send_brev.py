"""Integrationstest for det fulde Send brev-flow uden afsendelse.

Den faste rækkefølge er:

1. Launch KY.
2. Fremsøg borgeren.
3. Kald ``borgere.opstart_opgave``.
4. Overdrag den allerede åbne opgave til ``udfyld_send_brev``.

``opstart_opgave`` afgør, om en tidligere opgave skal genoptages, eller om en
ny opgave skal oprettes. ``udfyld_send_brev`` åbner aldrig selv en opgave.

Kør:
    uv run pytest tests/test_opstart_opgave_og_send_brev.py -s -vv

Testen bruger ``test=True`` og klikker derfor ikke på Godkend.
"""

from __future__ import annotations

import os
from pprint import pprint
from time import perf_counter
from typing import Any, TypedDict

import pytest
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import naviger_til_borger, opstart_opgave
from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)
from ky_client.functionality.send_brev import send_brev
from ky_client.selectors import KYSelectors

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
LUK_BROWSER_EFTER_MS = 5_000

SEND_BREV_MENU_STI = (
    "Administration",
    "Send brev",
)

ENV_TIDLIGERE_OPGAVE_ID = "TIDLIGERE_OPGAVE_ID"

SAG_SOEGNING = "RESJR-IIKVOV"
MEDTAG_AKTIVE_SAGER = False
MEDTAG_PASSIVE_SAGER = True

BREVSKABELON_STI = (
    "FLX",
    "Afgørelse",
    "Afgørelse efter opfølgning - fleks gl. ord. selv",
)

STANDARD_BILAG_TITEL = "Oplysningspligt Hjælp til forsørgelse"
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
    """Åbn eller genoptag Send brev, udfyld, men afsend ikke brevet."""

    page = ky_page
    session = automation_session

    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
    _set_recorder_page(session=session, page=page)

    _print_step("STARTER TIMER OG LAUNCHER KY")
    start_tid = perf_counter()

    assert ky_credential_name.strip(), "ky_credential_name er tomt."

    print(f"URL før launch: {page.url}", flush=True)
    print(f"Er KY-URL før launch: {is_ky_url(page)}", flush=True)
    print(
        f"Har JSESSIONID før launch: {await has_jsessionid(page)}",
        flush=True,
    )
    print(
        f"Credential-post anvendt: {ky_credential_name!r}",
        flush=True,
    )

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    print(f"URL efter launch: {page.url}", flush=True)
    print(f"Er KY-URL efter launch: {is_ky_url(page)}", flush=True)
    print(
        f"Har JSESSIONID efter launch: {await has_jsessionid(page)}",
        flush=True,
    )

    assert not page.is_closed(), "KY-siden blev lukket under launch."
    assert not is_ky_error_url(page), f"KY viste fejlsiden: {page.url}"
    assert is_ky_url(page), f"Siden er ikke en gyldig KY-side: {page.url}"
    assert await has_jsessionid(page), "KY-sessionen mangler JSESSIONID."

    _print_step("FREMSØGER BORGER")

    borger_url = await naviger_til_borger(
        page=page,
        cpr=test_cpr,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert borger_url, "Borgeropslaget returnerede ingen URL."

    tidligere_opgave_id = _hent_valgfrit_opgave_id()
    item_data: dict[str, Any] = {"box": {}}

    if tidligere_opgave_id:
        print(
            f"Forsøger at genoptage Send brev-opgave {tidligere_opgave_id!r}.",
            flush=True,
        )
    else:
        print(
            "Intet tidligere opgave-ID. opstart_opgave opretter en ny opgave.",
            flush=True,
        )

    _print_step("STARTER ELLER GENOPTAGER SEND BREV VIA OPSTART_OPGAVE")

    checkpoint = await opstart_opgave(
        page=page,
        menu_sti=SEND_BREV_MENU_STI,
        item_data=item_data,
        opgave_id=tidligere_opgave_id,
        timeout=PAGE_TIMEOUT_MS,
    )

    _kontroller_checkpoint(
        checkpoint=checkpoint,
        item_data=item_data,
        borger_url=borger_url,
        tidligere_opgave_id=tidligere_opgave_id,
    )

    print("Opgavecheckpoint:")
    pprint(checkpoint, sort_dicts=False)

    _print_step("OVERDRAGER DEN ÅBNE OPGAVE TIL UDFYLD_SEND_BREV")

    resultat = await send_brev(
        page=page,
        checkpoint=checkpoint,
        sag=SAG_SOEGNING,
        skabelon_sti=BREVSKABELON_STI,
        bilag_titel=STANDARD_BILAG_TITEL,
        fysisk_post=SEND_SOM_FYSISK_POST,
        aktive=MEDTAG_AKTIVE_SAGER,
        passive=MEDTAG_PASSIVE_SAGER,
        test=True,
        timeout=PAGE_TIMEOUT_MS,
    )

    assert resultat["opgave_id"] == checkpoint["opgave_id"]
    assert resultat["opgave_navn"].casefold() == "Send brev".casefold()
    assert resultat["sag_id"], "Den valgte sag mangler sag-ID."
    assert resultat["brevskabelon"].casefold() == BREVSKABELON_STI[-1].casefold()
    assert resultat["bilag_titel"].casefold() == STANDARD_BILAG_TITEL.casefold()
    assert resultat["bilag_noegle"], "Standardbilaget mangler nøgle."
    assert resultat["fysisk_post"] is SEND_SOM_FYSISK_POST
    assert resultat["test"] is True
    assert resultat["sendt"] is False

    # udfyld_send_brev sætter fysisk post som sidste formularændring. Herefter
    # foretager testen kun read-only kontrol, screenshot og tidsmåling.
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
            ("borger_url", resultat["borger_url"]),
            ("opgave_id", resultat["opgave_id"]),
            ("opgave_url", resultat["opgave_url"]),
            ("opgave_navn", resultat["opgave_navn"]),
            ("genoptaget", resultat["genoptaget"]),
            ("kilde", resultat["kilde"]),
            ("valgt_sag_id", resultat["sag_id"]),
            ("valgt_brevskabelon", resultat["brevskabelon"]),
            ("valgt_standard_bilag", resultat["bilag_titel"]),
            ("valgt_standard_bilag_noegle", resultat["bilag_noegle"]),
            ("fysisk_post", resultat["fysisk_post"]),
            ("brevtype_display", brevtype_status["display"]),
            ("brevtype_value", brevtype_status["value"]),
            ("brevtype_text", brevtype_status["text"]),
            ("samlet_tid", samlet_tid),
            ("samlet_tid_sekunder", round(samlet_tid_sekunder, 3)),
        ]
    )

    _print_step("TESTEN ER FÆRDIG UDEN AFSENDELSE")
    print(f"Samlet køretid fra launch til mål: {samlet_tid}")
    print(f"Genoptaget: {resultat['genoptaget']}")
    print(f"Kilde: {resultat['kilde']}")
    print("Godkend-knappen er ikke klikket.")
    print("Brevet er ikke sendt.")
    print("Browseren lukker automatisk om 5 sekunder.")

    await page.wait_for_timeout(LUK_BROWSER_EFTER_MS)


def _hent_valgfrit_opgave_id() -> str | None:
    """Læs et valgfrit tidligere opgave-ID fra miljøet."""

    value = os.getenv(ENV_TIDLIGERE_OPGAVE_ID, "").strip()
    if value.casefold() in {"", "null", "none", "nul"}:
        return None
    return value


def _kontroller_checkpoint(
    checkpoint: dict[str, Any],
    item_data: dict[str, Any],
    borger_url: str,
    tidligere_opgave_id: str | None,
) -> None:
    """Kontrollér checkpointet for både ny og genoptaget opgave."""

    assert checkpoint["opgave_id"], "Checkpointet mangler opgave-ID."
    assert checkpoint["opgave_url"], "Checkpointet mangler opgave-URL."
    assert checkpoint["opgave_navn"], "Checkpointet mangler opgavenavn."
    assert checkpoint["borger_url"] == borger_url
    assert checkpoint["opgave_id"] in checkpoint["opgave_url"]
    assert checkpoint["opgave_navn"].casefold() == "Send brev".casefold()
    assert tuple(checkpoint["menu_sti"]) == SEND_BREV_MENU_STI
    assert checkpoint["genoptaget"] in {True, False}
    assert checkpoint["kilde"] in {"ny_opgave", "ubehandlede_opgaver"}

    if checkpoint["genoptaget"]:
        assert tidligere_opgave_id is not None
        assert checkpoint["opgave_id"].casefold() == tidligere_opgave_id.casefold()
        assert checkpoint["kilde"] == "ubehandlede_opgaver"
    else:
        assert checkpoint["kilde"] == "ny_opgave"

    box = item_data["box"]
    assert box["Aktiv Opgave-Id"] == checkpoint["opgave_id"]
    assert box["Aktiv Opgave URL"] == checkpoint["opgave_url"]
    assert box["Aktiv Opgavenavn"] == checkpoint["opgave_navn"]


async def _laes_brevtype_status(page: Page) -> BrevtypeStatus:
    """Læs Brevtype-status uden at ændre formularen."""

    container = page.locator(KYSelectors.Borgere.SEND_BREV_POSTAGE_CONTAINER).last
    postage_select = page.locator(KYSelectors.Borgere.SEND_BREV_POSTAGE_TYPE).last

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
        text = (await postage_select.locator("option:checked").inner_text()).strip()

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
