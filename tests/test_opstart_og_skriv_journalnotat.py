"""Integrationstest af ``opstart_opgave`` og ``skriv_journalnotat`` i KY.

Testen følger den fælles opgavestruktur:

1. Launcher KY.
2. Fremsøger og validerer TEST_CPR_1 fra projektets .env.
3. Kalder ``opstart_opgave`` først.
4. ``opstart_opgave`` genoptager en eksisterende opgave, hvis der findes et
   opgave-id, ellers oprettes en ny Skriv journalnotat-opgave.
5. Sender checkpointet til ``skriv_journalnotat``.
6. Journalnotatformularen udfyldes igen, uanset om opgaven er ny eller
   genoptaget.
7. Både Aktive sager og Passive sager sættes til True.
8. Kører med test=True, så der ikke klikkes på Gem eller Godkend.
9. Venter 10 sekunder med den udfyldte formular før browseren lukkes.

Påkrævede værdier i projektets .env:
    TEST_CPR_1=DDMMYYXXXX
    JOURNALNOTAT_SAG=RESJR-IIKVOV
    JOURNALNOTAT_SKABELON=Kontrol af bil

Valgfrit:
    JOURNALNOTAT_OPGAVE_ID=6517c6e4-bb8a-4af0-bc28-587a95d5a117

Hvis JOURNALNOTAT_OPGAVE_ID er et gyldigt UUID, forsøger opstart_opgave at
finde og genoptage opgaven fra Ubehandlede opgaver. Hvis variablen er tom,
opretter opstart_opgave en ny opgave via Handlinger-menuen.

Kør:
    uv run pytest tests/test_opstart_og_skriv_journalnotat.py -s -vv
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from dotenv import load_dotenv
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import (
    BorgereClient,
    opstart_opgave,
)
from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)
from ky_client.functionality.skriv_journalnotat import (
    opret_journalnotat,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
WAIT_AFTER_FILLED_MS = 10_000

OPGAVENAVN = "Skriv journalnotat"
MENU_STI = (
    "Administration",
    OPGAVENAVN,
)

ENV_CPR = "TEST_CPR_1"
ENV_OPGAVE_ID = "JOURNALNOTAT_OPGAVE_ID"
ENV_SAG = "JOURNALNOTAT_SAG"
ENV_SKABELON = "JOURNALNOTAT_SKABELON"


async def test_opstart_og_skriv_journalnotat(
    ky_credential_name: str,
) -> None:
    """Start/genoptag opgaven og udfyld journalnotatet uden afslutning."""

    cpr = _hent_test_cpr(ENV_CPR)
    opgave_id = _optional_uuid_env(ENV_OPGAVE_ID)
    journalnotat_sag = _hent_paakraevet_env(ENV_SAG)
    journalnotat_skabelon = _hent_paakraevet_env(ENV_SKABELON)

    session = BrowserSession(
        headless=False,
        debug=True,
        video=False,
    )
    page: Page | None = None
    borgere: BorgereClient | None = None
    faner_foer_test: set[str] = set()

    # opstart_opgave gemmer det aktive checkpoint her. Robotten kan gemme
    # box-data efter et crash og sende data ind igen ved næste kørsel.
    item_data: dict[str, Any] = {
        "box": {},
    }

    try:
        _print_step("STARTER BROWSERSESSION")
        await session.start()
        page = await session.new_page()
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        _set_recorder_page(session=session, page=page)

        _print_step("LAUNCHER KY")
        await launch_ky(
            page=page,
            session=session,
            credential_name=ky_credential_name,
        )

        assert not page.is_closed(), "KY-siden blev lukket under launch."
        assert not is_ky_error_url(page), f"KY viste fejlsiden: {page.url}"
        assert is_ky_url(page), (
            f"Siden er ikke en gyldig KY-side: {page.url}"
        )
        assert await has_jsessionid(page), (
            "KY-sessionen mangler JSESSIONID."
        )

        _print_step("FREMSØGER BORGER FRA TEST_CPR_1")
        borgere = BorgereClient(page)
        faner_foer_test = await borgere.hent_aabne_borger_ids()

        borger = await borgere.hent_borger(
            cpr=cpr,
            timeout=PAGE_TIMEOUT_MS,
            max_forsog=3,
        )

        person_id = str(borger["pId"] or "").strip()
        borger_url = str(borger["borger_url"] or "").strip()

        assert person_id, "Borgeropslaget returnerede intet pId."
        assert borger_url, "Borgeropslaget returnerede ingen URL."

        print(f"CPR: {_masker_cpr(cpr)}", flush=True)
        print(f"pId: {person_id}", flush=True)
        print(f"Borger-URL: {borger_url}", flush=True)

        _print_step("STARTER ELLER GENOPTAGER SKRIV JOURNALNOTAT")
        print(
            f"Opgave-id til genoptagelse: {opgave_id or '(intet)'}",
            flush=True,
        )

        checkpoint = await opstart_opgave(
            page=borgere.page,
            menu_sti=MENU_STI,
            item_data=item_data,
            opgave_id=opgave_id,
            timeout=PAGE_TIMEOUT_MS,
        )

        _kontroller_checkpoint(
            checkpoint=checkpoint,
            item_data=item_data,
            forventet_person_id=person_id,
            forventet_opgave_id=opgave_id,
        )

        print(f"Genoptaget: {checkpoint['genoptaget']}", flush=True)
        print(f"Kilde: {checkpoint['kilde']}", flush=True)
        print(f"Opgave-id: {checkpoint['opgave_id']}", flush=True)
        print(f"Opgavenavn: {checkpoint['opgave_navn']}", flush=True)
        print(f"Opgave-URL: {checkpoint['opgave_url']}", flush=True)

        _print_step("UDFYLDER DEN ALLEREDE ÅBNE JOURNALNOTATOPGAVE")
        resultat = await opret_journalnotat(
            page=borgere.page,
            checkpoint=checkpoint,
            sag=journalnotat_sag,
            journalnotatskabelon=journalnotat_skabelon,
            aktive=True,
            passive=True,
            test=True,
            timeout=PAGE_TIMEOUT_MS,
        )

        _kontroller_resultat(
            resultat=resultat,
            checkpoint=checkpoint,
            journalnotat_sag=journalnotat_sag,
            journalnotat_skabelon=journalnotat_skabelon,
        )

        _print_step("JOURNALNOTATET ER UDFYLDT")
        print(f"Opgave-id: {resultat['opgave_id']}", flush=True)
        print(f"Opgavenavn: {resultat['opgave_navn']}", flush=True)
        print(f"Opgave-URL: {resultat['opgave_url']}", flush=True)
        print(f"Valgt sag-id: {resultat['sag_id']}", flush=True)
        print(f"Valgt sag: {resultat['sagstekst']}", flush=True)
        print(
            f"Valgt skabelon: {resultat['skabelon_titel']}",
            flush=True,
        )
        print(
            f"Skabelonnøgle: {resultat['skabelon_noegle']}",
            flush=True,
        )
        print(f"Genoptaget: {resultat['genoptaget']}", flush=True)
        print(f"Kilde: {resultat['kilde']}", flush=True)
        print(f"Test: {resultat['test']}", flush=True)
        print(f"Afsluttet: {resultat['afsluttet']}", flush=True)
        print("Aktive sager: True", flush=True)
        print("Passive sager: True", flush=True)
        print("Der er ikke klikket på Gem eller Godkend.", flush=True)

        _print_step("VENTER 10 SEKUNDER TIL VISUEL KONTROL")
        await borgere.page.wait_for_timeout(WAIT_AFTER_FILLED_MS)

    finally:
        if borgere is not None and page is not None and not page.is_closed():
            try:
                faner_efter_test = await borgere.hent_aabne_borger_ids()
                testens_faner = sorted(
                    faner_efter_test - faner_foer_test
                )
                if testens_faner:
                    _print_step("LUKKER TESTENS BORGERFANER")
                    await borgere.luk_borgerfaner(
                        entity_ids=testens_faner,
                        timeout_ms=ACTION_TIMEOUT_MS,
                        maks_forsog=3,
                    )
            except Exception as error:
                print(
                    "Oprydning af borgerfaner fejlede: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        _print_step("LUKKER BROWSERSESSION")
        await session.close()


def _kontroller_checkpoint(
    checkpoint: dict[str, Any],
    item_data: dict[str, Any],
    forventet_person_id: str,
    forventet_opgave_id: str | None,
) -> None:
    """Kontrollér resultatet fra den fælles opstart_opgave."""

    required = {
        "opgave_id",
        "opgave_navn",
        "opgave_url",
        "borger_url",
        "menu_sti",
        "genoptaget",
        "kilde",
    }
    missing = required - set(checkpoint)
    assert not missing, f"Checkpointet mangler felter: {sorted(missing)}"

    faktisk_id = str(checkpoint["opgave_id"] or "").strip()
    faktisk_navn = str(checkpoint["opgave_navn"] or "").strip()
    faktisk_url = str(checkpoint["opgave_url"] or "").strip()

    assert faktisk_id, "opstart_opgave returnerede intet opgave-id."
    assert faktisk_navn.casefold() == OPGAVENAVN.casefold()
    assert faktisk_url, "opstart_opgave returnerede ingen opgave-URL."
    assert checkpoint["borger_url"], (
        "opstart_opgave returnerede ingen borger-URL."
    )
    assert tuple(checkpoint["menu_sti"]) == MENU_STI
    assert isinstance(checkpoint["genoptaget"], bool)

    if forventet_opgave_id is not None:
        assert checkpoint["genoptaget"] is True
        assert checkpoint["kilde"] == "ubehandlede_opgaver"
        assert faktisk_id.casefold() == forventet_opgave_id.casefold()
        assert _er_rigtig_opgave_url(
            url=faktisk_url,
            forventet_person_id=forventet_person_id,
            forventet_opgave_id=forventet_opgave_id,
        ), f"Forkert URL for genoptaget opgave: {faktisk_url!r}."
    else:
        assert checkpoint["genoptaget"] is False
        assert checkpoint["kilde"] == "ny_opgave"

    box = item_data.get("box")
    assert isinstance(box, dict), "item_data['box'] mangler."
    assert box.get("Aktiv Opgave-Id") == faktisk_id
    assert box.get("Aktiv Opgave URL") == faktisk_url
    assert box.get("Aktiv Opgavenavn") == faktisk_navn


def _kontroller_resultat(
    resultat: dict[str, Any],
    checkpoint: dict[str, Any],
    journalnotat_sag: str,
    journalnotat_skabelon: str,
) -> None:
    """Kontrollér checkpoint, formularvalg og sikker testtilstand."""

    required = {
        "opgave_id",
        "opgave_navn",
        "opgave_url",
        "borger_url",
        "menu_sti",
        "sag_id",
        "sagstekst",
        "skabelon_titel",
        "skabelon_noegle",
        "genoptaget",
        "kilde",
        "test",
        "afsluttet",
    }
    missing = required - set(resultat)
    assert not missing, f"Resultatet mangler felter: {sorted(missing)}"

    assert resultat["opgave_id"] == checkpoint["opgave_id"]
    assert resultat["opgave_navn"] == checkpoint["opgave_navn"]
    assert resultat["opgave_url"] == checkpoint["opgave_url"]
    assert resultat["borger_url"] == checkpoint["borger_url"]
    assert tuple(resultat["menu_sti"]) == MENU_STI
    assert resultat["genoptaget"] == checkpoint["genoptaget"]
    assert resultat["kilde"] == checkpoint["kilde"]

    assert resultat["sag_id"], "Ingen sag-id blev returneret."
    assert resultat["sagstekst"], "Ingen sag blev valgt."

    sag_input = _normaliser(journalnotat_sag).casefold()
    sag_id = _normaliser(resultat["sag_id"]).casefold()
    sagstekst = _normaliser(resultat["sagstekst"]).casefold()
    assert sag_input == sag_id or sag_input in sagstekst, (
        "Den valgte sag matcher ikke JOURNALNOTAT_SAG. "
        f"Input={journalnotat_sag!r}, "
        f"sag_id={resultat['sag_id']!r}, "
        f"sagstekst={resultat['sagstekst']!r}."
    )

    assert _samme_tekst(
        resultat["skabelon_titel"],
        journalnotat_skabelon,
    ), (
        "Forkert skabelon blev valgt. "
        f"Forventet={journalnotat_skabelon!r}, "
        f"faktisk={resultat['skabelon_titel']!r}."
    )
    assert resultat["skabelon_noegle"], (
        "Skabelonnøglen er tom efter udfyldningen."
    )
    assert resultat["test"] is True
    assert resultat["afsluttet"] is False, (
        "Journalnotatet må ikke afsluttes, når test=True."
    )


def _er_rigtig_opgave_url(
    url: str,
    forventet_person_id: str,
    forventet_opgave_id: str,
) -> bool:
    """Kontrollér overblik-URL med pId og opgaveId."""

    parsed = urlparse(str(url or "").strip())
    return (
        parsed.path.casefold().endswith(
            "/ky-fagsystem/entitet/overblik"
        )
        and _hent_query_parameter(url, "pId").casefold()
        == forventet_person_id.casefold()
        and _hent_query_parameter(url, "opgaveId").casefold()
        == forventet_opgave_id.casefold()
    )


def _hent_query_parameter(url: str, name: str) -> str:
    """Returnér første værdi for en queryparameter."""

    values = parse_qs(
        urlparse(str(url or "").strip()).query
    ).get(name, [])
    return str(values[0]).strip() if values else ""


def _optional_uuid_env(name: str) -> str | None:
    """Hent et valgfrit opgave-id og kræv et gyldigt UUID."""

    value = os.getenv(name, "").strip()
    if value.casefold() in {"", "null", "none", "nul"}:
        return None

    pattern = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{12}$"
    )
    if pattern.fullmatch(value) is None:
        pytest.fail(
            f"{name} skal være tom eller indeholde et gyldigt UUID.",
            pytrace=False,
        )

    return value


def _hent_test_cpr(variable_name: str) -> str:
    """Hent og validér CPR fra projektets .env-fil."""

    raw_value = os.getenv(variable_name, "").strip()
    match = re.fullmatch(
        r"\s*(\d{6})[\s-]?(\d{4})\s*",
        raw_value,
    )

    if not match:
        pytest.fail(
            f"{variable_name} mangler eller er ugyldigt i {ENV_FILE}. "
            "Angiv præcis 10 cifre eller DDMMÅÅ-NNNN.",
            pytrace=False,
        )

    return match.group(1) + match.group(2)


def _hent_paakraevet_env(variable_name: str) -> str:
    """Hent en obligatorisk tekstværdi fra projektets .env-fil."""

    value = os.getenv(variable_name, "").strip()
    if not value:
        pytest.fail(
            f"{variable_name} mangler eller er tom i {ENV_FILE}.",
            pytrace=False,
        )
    return value


def _samme_tekst(actual: object, expected: object) -> bool:
    """Sammenlign normaliseret tekst uden forskel på store/små bogstaver."""

    return _normaliser(actual).casefold() == _normaliser(expected).casefold()


def _normaliser(value: object) -> str:
    """Saml whitespace og trim tekst."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def _masker_cpr(cpr: str) -> str:
    """Maskér CPR i testens output."""

    digits = re.sub(r"\D", "", cpr)
    return (
        f"******{digits[-4:]}"
        if len(digits) == 10
        else "[ugyldigt CPR]"
    )


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSessions recorder til den aktive side."""

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)
    if callable(set_page):
        set_page(page)


def _print_step(title: str) -> None:
    """Print en tydelig sektionsoverskrift."""

    print("", flush=True)
    print("=" * 70, flush=True)
    print(title, flush=True)
    print("=" * 70, flush=True)
