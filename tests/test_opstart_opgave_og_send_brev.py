"""Integrationstest for det fulde Send brev-flow uden afsendelse.

Rækkefølge:
1. Launch KY.
2. Fremsøg borgeren.
3. Start eller genoptag opgaven med opstart_opgave().
4. Find og kontrollér den konkrete HTF-sag.
5. Udfyld brevet med send_brev(test=True).

Kør:
    uv run pytest tests/test_opstart_opgave_og_send_brev.py -s -vv

TEST_HTF_SAG_ID skal indeholde det viste SagsID for testborgerens aktive sag.
"""

from __future__ import annotations

import os
import re
from pprint import pprint
from time import perf_counter
from typing import Any, TypedDict

import pytest
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import (
    BorgereClient,
    naviger_til_borger,
    opstart_opgave,
)
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

MEDTAG_AKTIVE_SAGER = True
MEDTAG_PASSIVE_SAGER = False

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

    forventet_sags_id = _hent_forventet_sags_id()
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

    personoplysninger = await BorgereClient(page).hent_personoplysninger(
        cpr=test_cpr,
        timeout=PAGE_TIMEOUT_MS,
    )
    navne = [
        str(oplysning["vaerdi"]).strip()
        for oplysning in personoplysninger
        if _normaliser_ky_tekst(
            str(oplysning["felt"]).rstrip(":")
        ) == "navn"
        and str(oplysning["vaerdi"]).strip()
    ]
    if len(navne) != 1:
        raise RuntimeError(
            "Det CPR-validerede borgeropslag gav ikke præcis ét Navn. "
            "Ingen sag vælges."
        )

    # Personoplysninger viser her et afsluttende visningssuffiks,
    # som ikke står i sagsvælgerens kolonne Vedrører.
    forventet_vedroerer = _fjern_visningssuffiks_fra_navn(navne[0])
    if not forventet_vedroerer:
        raise RuntimeError(
            "Navnet fra Personoplysninger blev tomt efter oprydning. "
            "Ingen sag vælges."
        )

    tidligere_opgave_id = _hent_valgfrit_opgave_id()
    item_data: dict[str, Any] = {"box": {}}

    if tidligere_opgave_id:
        print(
            "Forsøger at genoptage en eksisterende Send brev-opgave.",
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
        checkpoint_type="send_brev",
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

    _print_step("IDENTIFICERER DEN KONKRETE HTF-SAG I KY-RÆKKEN")

    fundet_sags_id = await _find_sags_id_fra_ky_raekke(
        page=page,
        forventet_sags_id=forventet_sags_id,
        forventet_vedroerer=forventet_vedroerer,
        aktive=MEDTAG_AKTIVE_SAGER,
        passive=MEDTAG_PASSIVE_SAGER,
    )
    print(f"SagsID fra KY-rækken: {fundet_sags_id}", flush=True)

    _print_step("OVERDRAGER DEN ÅBNE OPGAVE TIL SEND_BREV")

    resultat = await send_brev(
        page=page,
        checkpoint=checkpoint,
        sag=fundet_sags_id,
        forventet_vedroerer=forventet_vedroerer,
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
    assert resultat["sag_id"], "Den valgte sag mangler teknisk sag-id."
    # data-id er KY-rækkens tekniske id, ikke nødvendigvis vist SagsID.
    assert (
        resultat["brevskabelon"].casefold()
        == BREVSKABELON_STI[-1].casefold()
    )
    assert (
        resultat["bilag_titel"].casefold()
        == STANDARD_BILAG_TITEL.casefold()
    )
    assert resultat["bilag_noegle"], "Standardbilaget mangler nøgle."
    assert resultat["fysisk_post"] is SEND_SOM_FYSISK_POST
    assert resultat["test"] is True
    assert resultat["sendt"] is False

    # Efter fysisk post foretages kun læsende kontrol og screenshot.
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
            ("valgt_sagsnummer", fundet_sags_id),
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


# START: _find_sags_id_fra_ky_raekke()
async def _find_sags_id_fra_ky_raekke(
    page: Page,
    forventet_sags_id: str,
    forventet_vedroerer: str,
    aktive: bool,
    passive: bool,
) -> str:
    """Validér én sag uden at vælge den, og lad dropdownen stå åben."""
    if not aktive and not passive:
        raise ValueError("Mindst én sagsstatus skal vælges.")

    roots = page.locator(
        f"{KYSelectors.Borgere.SEND_BREV_SAGSVAELGER}:visible"
    )
    await roots.first.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )
    if await roots.count() != 1:
        raise RuntimeError("Forventede præcis én synlig sagsvælger.")

    root = roots.first
    menu = root.locator(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_MENU
    ).first
    toggle = root.locator(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_TOGGLE
    ).first

    if not await menu.is_visible():
        await toggle.click(timeout=ACTION_TIMEOUT_MS)

    await menu.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await root.locator(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_AKTIVE
    ).first.set_checked(aktive)
    await root.locator(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_PASSIVE
    ).first.set_checked(passive)

    table = menu.locator(
        "table[id^='brevSagsvaelgerTable']:visible"
    )
    await table.first.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )
    if await table.count() != 1:
        raise RuntimeError("Forventede præcis én synlig sagstabel.")

    table = table.first
    header_cells = table.locator("thead th")
    headers = [
        _normaliser_ky_tekst(
            await header_cells.nth(i).inner_text()
        )
        for i in range(await header_cells.count())
    ]

    def kolonne(navn: str) -> int:
        matches = [
            i
            for i, tekst in enumerate(headers)
            if tekst == _normaliser_ky_tekst(navn)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"KY-tabellen mangler entydig kolonne {navn!r}: "
                f"{headers!r}"
            )
        return matches[0]

    sags_id_index = kolonne("SagsID")
    vedroerer_index = kolonne("Vedrører")
    rows = table.locator("tbody > tr.table-row")

    forventet_id = _normaliser_ky_tekst(forventet_sags_id)
    forventet_navn = _normaliser_ky_tekst(forventet_vedroerer)
    if not forventet_navn:
        raise ValueError("forventet_vedroerer er tom.")

    for _ in range(PAGE_TIMEOUT_MS // 250):
        fundne: list[str] = []

        for i in range(await rows.count()):
            row = rows.nth(i)
            if not await row.is_visible():
                continue

            cells = row.locator("td")
            if await cells.count() != len(headers):
                raise RuntimeError(
                    "KY-rækkens kolonner matcher ikke overskrifterne."
                )

            sags_id = (
                await cells.nth(sags_id_index).inner_text()
            ).strip()
            if _normaliser_ky_tekst(sags_id) != forventet_id:
                continue

            status = _normaliser_ky_tekst(
                await row.get_attribute("data-tilstand")
            )
            if status not in {"aktiv", "passiv"}:
                raise RuntimeError(
                    "Sagen har ukendt status. Ingen sag vælges."
                )
            if (
                (status == "aktiv" and not aktive)
                or (status == "passiv" and not passive)
            ):
                raise RuntimeError(
                    "Sagen har fravalgt status. Ingen sag vælges."
                )

            navn = _normaliser_ky_tekst(
                await cells.nth(vedroerer_index).inner_text()
            )
            if navn != forventet_navn:
                raise RuntimeError(
                    "SagsID blev fundet, men Vedrører matcher ikke "
                    "det forventede borgernavn. Ingen sag vælges."
                )

            fundne.append(sags_id)

        if len(fundne) > 1:
            raise RuntimeError(
                "Flere synlige rækker har samme SagsID. "
                "Ingen sag vælges."
            )

        if len(fundne) == 1:
            # VIGTIGT: Ingen toggle.click() her.
            # send_brev() overtager den åbne dropdown.
            return fundne[0]

        await page.wait_for_timeout(250)

    raise RuntimeError(
        f"Fandt ikke synlig sag med SagsID "
        f"{forventet_sags_id!r}. Ingen sag vælges."
    )
# SLUT: _find_sags_id_fra_ky_raekke()

def _normaliser_ky_tekst(value: str | None) -> str:
    """Normalisér whitespace og store/små bogstaver til sammenligning."""
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _fjern_visningssuffiks_fra_navn(value: str) -> str:
    """Fjern kun det afsluttende '(Mand)', som er set i Personoplysninger."""
    return re.sub(
        r"\s+\(Mand\)\s*$",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip()


def _hent_forventet_sags_id() -> str:
    """Læs testens konkrete, viste HTF-SagsID fra miljøet."""
    sags_id = os.getenv("TEST_HTF_SAG_ID", "").strip()
    if not sags_id.upper().startswith("HTF-"):
        raise ValueError(
            "Angiv et konkret HTF-SagsID i TEST_HTF_SAG_ID."
        )
    return sags_id


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
    assert checkpoint["kilde"] in {
        "ny_opgave",
        "ubehandlede_opgaver",
    }

    if checkpoint["genoptaget"]:
        assert tidligere_opgave_id is not None
        assert (
            checkpoint["opgave_id"].casefold()
            == tidligere_opgave_id.casefold()
        )
        assert checkpoint["kilde"] == "ubehandlede_opgaver"
    else:
        assert checkpoint["kilde"] == "ny_opgave"

    box = item_data["box"]
    assert box["Send brev Opgave-Id"] == checkpoint["opgave_id"]
    assert box["Send brev Opgave URL"] == checkpoint["opgave_url"]
    assert box["Send brev Opgavenavn"] == checkpoint["opgave_navn"]


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
            await postage_select.locator(
                "option:checked"
            ).inner_text()
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
    return (
        f"{hours:02d}:{minutes:02d}:"
        f"{seconds:02d}.{milliseconds:03d}"
    )


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
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