"""Integrationstest af ``opstart_opgave`` og ``opret_opfoelgningsopgave``.

Testen følger den fælles opgavestruktur:

1. Launcher KY.
2. Fremsøger og validerer TEST_CPR_1 fra projektets .env.
3. Kalder ``opstart_opgave`` først.
4. ``opstart_opgave`` genoptager en eksisterende opgave, hvis der findes et
   opgave-id, ellers oprettes en ny opfølgningsopgave.
5. Sender checkpointet til ``opret_opfoelgningsopgave``.
6. Formularen udfyldes igen, uanset om opgaven er ny eller genoptaget.
7. Kører med test=True, så der ikke klikkes på Gem.
8. Venter 10 sekunder med den udfyldte formular før browseren lukkes.

Påkrævede værdier i projektets .env:
    TEST_CPR_1=DDMMYYXXXX
    OPFOELGNINGSTYPE=Brugerdefineret
    OPFOELGNINGSDATO=01-09-2026
    OPFOELGNING_SAGSBEHANDLER=Navn på sagsbehandler

Ved opfølgningstypen Brugerdefineret kræves også:
    OPFOELGNING_TITEL=Kontrol
    OPFOELGNING_FREKVENS=En gang
    OPFOELGNING_BESKRIVELSE=Følg op på sagen

Valgfrit:
    OPFOELGNING_HAENDELSESTYPE=
    OPFOELGNING_VAELG_SAGSBEHANDLER=false
    OPFOELGNING_OPGAVE_ID=

Hvis OPFOELGNING_OPGAVE_ID er et gyldigt UUID, forsøger opstart_opgave at
finde og genoptage opgaven fra Ubehandlede opgaver. Hvis variablen er tom,
opretter opstart_opgave en ny opgave via Handlinger-menuen.

Kør:
    uv run pytest tests/test_opret_opfoelgningsopgave.py -s -vv
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

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
from ky_client.functionality.opfoelgningsopgave import (
    opret_opfoelgningsopgave,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
WAIT_AFTER_FILLED_MS = 10_000

MENU_STI = (
    "Administration",
    "Opret opfølgningsopgave",
)

ENV_CPR = "TEST_CPR_1"
ENV_OPGAVE_ID = "OPFOELGNING_OPGAVE_ID"
ENV_TYPE = "OPFOELGNINGSTYPE"
ENV_DATO = "OPFOELGNINGSDATO"
ENV_SAGSBEHANDLER = "OPFOELGNING_SAGSBEHANDLER"
ENV_TITEL = "OPFOELGNING_TITEL"
ENV_FREKVENS = "OPFOELGNING_FREKVENS"
ENV_HAENDELSESTYPE = "OPFOELGNING_HAENDELSESTYPE"
ENV_BESKRIVELSE = "OPFOELGNING_BESKRIVELSE"
ENV_VAELG_SAGSBEHANDLER = "OPFOELGNING_VAELG_SAGSBEHANDLER"


async def test_opret_opfoelgningsopgave_uden_at_gemme(
    ky_credential_name: str,
) -> None:
    """Start/genoptag opgaven, udfyld formularen og gem den ikke."""

    cpr = _hent_test_cpr_1()
    opgave_id = _optional_uuid_env(ENV_OPGAVE_ID)
    opfoelgningstype = _required_env(ENV_TYPE)
    opfoelgningsdato = _required_env(ENV_DATO)
    sagsbehandler = _required_env(ENV_SAGSBEHANDLER)

    titel = _optional_env(ENV_TITEL)
    frekvens = _optional_env(ENV_FREKVENS)
    haendelsestype = _optional_env(ENV_HAENDELSESTYPE)
    beskrivelse = _optional_env(ENV_BESKRIVELSE)
    vaelg_sagsbehandler = _bool_env(
        ENV_VAELG_SAGSBEHANDLER,
        standard=False,
    )

    if _er_brugerdefineret_input(opfoelgningstype):
        _krav_til_brugerdefineret(
            titel=titel,
            frekvens=frekvens,
            beskrivelse=beskrivelse,
        )

    session = BrowserSession(
        headless=False,
        debug=True,
        video=False,
    )
    page: Page | None = None
    borgere: BorgereClient | None = None
    faner_foer_test: set[str] = set()

    # opstart_opgave gemmer checkpointet her. Data kan efter et crash
    # gemmes af robotten og sendes ind igen ved næste kørsel.
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
        assert is_ky_url(page), f"Ugyldig KY-URL: {page.url}"
        assert await has_jsessionid(page), "KY mangler JSESSIONID."

        _print_step("FREMSØGER TEST_CPR_1")
        borgere = BorgereClient(page)
        faner_foer_test = await borgere.hent_aabne_borger_ids()

        borger = await borgere.hent_borger(
            cpr=cpr,
            timeout=PAGE_TIMEOUT_MS,
            max_forsog=3,
        )

        print(f"CPR: {_masker_cpr(cpr)}", flush=True)
        print(f"pId: {borger['pId']}", flush=True)
        print(f"Borger-URL: {borger['borger_url']}", flush=True)

        _print_step("STARTER ELLER GENOPTAGER OPFØLGNINGSOPGAVE")
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
            forventet_opgave_id=opgave_id,
        )

        print(f"Kilde: {checkpoint['kilde']}", flush=True)
        print(f"Genoptaget: {checkpoint['genoptaget']}", flush=True)
        print(f"Opgave-id: {checkpoint['opgave_id']}", flush=True)
        print(f"Opgavenavn: {checkpoint['opgave_navn']}", flush=True)
        print(f"Opgave-URL: {checkpoint['opgave_url']}", flush=True)

        _print_step("UDFYLDER DEN ALLEREDE ÅBNE OPFØLGNINGSOPGAVE")
        print(f"Opfølgningstype: {opfoelgningstype}", flush=True)
        print(f"Opfølgningsdato: {opfoelgningsdato}", flush=True)
        print(f"Sagsbehandler: {sagsbehandler}", flush=True)
        print(f"Titel: {titel or '(ikke angivet)'}", flush=True)
        print(f"Frekvens: {frekvens or '(ikke angivet)'}", flush=True)
        print(
            f"Hændelsestype: {haendelsestype or '(ikke angivet)'}",
            flush=True,
        )
        print(f"Beskrivelse: {beskrivelse or '(ikke angivet)'}", flush=True)
        print(f"Vælg sagsbehandlerforslag: {vaelg_sagsbehandler}", flush=True)
        print("Test: True", flush=True)

        resultat = await opret_opfoelgningsopgave(
            page=borgere.page,
            checkpoint=checkpoint,
            opfoelgningstype=opfoelgningstype,
            opfoelgningsdato=opfoelgningsdato,
            sagsbehandler=sagsbehandler,
            titel=titel,
            frekvens=frekvens,
            haendelsestype=haendelsestype,
            beskrivelse=beskrivelse,
            vaelg_sagsbehandler_fra_typeahead=vaelg_sagsbehandler,
            test=True,
            timeout=PAGE_TIMEOUT_MS,
        )

        _kontroller_resultat(
            resultat=resultat,
            checkpoint=checkpoint,
            opfoelgningstype=opfoelgningstype,
            opfoelgningsdato=opfoelgningsdato,
            sagsbehandler=sagsbehandler,
            titel=titel,
            frekvens=frekvens,
            haendelsestype=haendelsestype,
            beskrivelse=beskrivelse,
            vaelg_sagsbehandler=vaelg_sagsbehandler,
        )

        _print_step("OPFØLGNINGSOPGAVEN ER UDFYLDT")
        print(f"Opgave-id: {resultat['opgave_id']}", flush=True)
        print(f"Opgavenavn: {resultat['opgave_navn']}", flush=True)
        print(f"Opgave-URL: {resultat['opgave_url']}", flush=True)
        print(f"Test: {resultat['test']}", flush=True)
        print(f"Gemt: {resultat['gemt']}", flush=True)
        print("Der er ikke klikket på Gem.", flush=True)

        _print_step("VENTER 10 SEKUNDER TIL VISUEL KONTROL")
        await page.wait_for_timeout(WAIT_AFTER_FILLED_MS)

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

    assert checkpoint["opgave_id"], "Checkpointets opgave_id er tomt."
    assert checkpoint["opgave_navn"].casefold() == MENU_STI[-1].casefold()
    assert checkpoint["opgave_url"], "Checkpointets opgave_url er tomt."
    assert checkpoint["borger_url"], "Checkpointets borger_url er tomt."
    assert tuple(checkpoint["menu_sti"]) == MENU_STI

    if forventet_opgave_id is not None:
        assert checkpoint["opgave_id"].casefold() == (
            forventet_opgave_id.casefold()
        )
        assert checkpoint["genoptaget"] is True
        assert checkpoint["kilde"] == "ubehandlede_opgaver"
    else:
        assert checkpoint["genoptaget"] is False
        assert checkpoint["kilde"] == "ny_opgave"

    box = item_data.get("box")
    assert isinstance(box, dict), "item_data['box'] mangler."
    assert box.get("Aktiv Opgave-Id") == checkpoint["opgave_id"]
    assert box.get("Aktiv Opgave URL") == checkpoint["opgave_url"]
    assert box.get("Aktiv Opgavenavn") == checkpoint["opgave_navn"]


def _kontroller_resultat(
    resultat: dict[str, Any],
    checkpoint: dict[str, Any],
    opfoelgningstype: str,
    opfoelgningsdato: str,
    sagsbehandler: str,
    titel: str | None,
    frekvens: str | None,
    haendelsestype: str | None,
    beskrivelse: str | None,
    vaelg_sagsbehandler: bool,
) -> None:
    """Kontrollér checkpoint, formularværdier og sikker testtilstand."""

    required = {
        "opgave_id",
        "opgave_navn",
        "opgave_url",
        "borger_url",
        "menu_sti",
        "opfoelgningstype",
        "opfoelgningsdato",
        "sagsbehandler",
        "titel",
        "frekvens",
        "haendelsestype",
        "beskrivelse",
        "test",
        "gemt",
    }
    missing = required - set(resultat)
    assert not missing, f"Resultatet mangler felter: {sorted(missing)}"

    assert resultat["opgave_id"] == checkpoint["opgave_id"]
    assert resultat["opgave_navn"] == checkpoint["opgave_navn"]
    assert resultat["opgave_url"] == checkpoint["opgave_url"]
    assert resultat["borger_url"] == checkpoint["borger_url"]
    assert tuple(resultat["menu_sti"]) == MENU_STI

    # Dropdownfunktionerne kan returnere den synlige label. Sammenligning
    # gøres derfor normaliseret og uden forskel på store/små bogstaver.
    assert _samme_tekst(resultat["opfoelgningstype"], opfoelgningstype)
    assert resultat["opfoelgningsdato"] == opfoelgningsdato

    if vaelg_sagsbehandler:
        assert _normaliser(resultat["sagsbehandler"]).casefold().startswith(
            _normaliser(sagsbehandler).casefold()
        )
    else:
        assert resultat["sagsbehandler"] == sagsbehandler

    if _er_brugerdefineret_input(opfoelgningstype):
        assert resultat["titel"] == (titel or "")
        assert _samme_tekst(resultat["frekvens"], frekvens or "")
        assert resultat["beskrivelse"] == (beskrivelse or "")
        if haendelsestype:
            assert _samme_tekst(
                resultat["haendelsestype"],
                haendelsestype,
            )
        else:
            assert resultat["haendelsestype"] == ""
    else:
        assert resultat["titel"] == ""
        assert resultat["frekvens"] == ""
        assert resultat["haendelsestype"] == ""
        assert resultat["beskrivelse"] == ""

    assert resultat["test"] is True, "Testtilstanden skal være aktiv."
    assert resultat["gemt"] is False, (
        "Opfølgningsopgaven må ikke gemmes, når test=True."
    )


def _hent_test_cpr_1() -> str:
    """Hent og validér TEST_CPR_1 fra projektets .env-fil."""

    raw = os.getenv(ENV_CPR, "").strip()
    match = re.fullmatch(r"\s*(\d{6})[\s-]?(\d{4})\s*", raw)

    if not match:
        pytest.fail(
            f"{ENV_CPR} mangler eller er ugyldigt i {ENV_FILE}.",
            pytrace=False,
        )

    return match.group(1) + match.group(2)


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


def _required_env(name: str) -> str:
    """Hent en obligatorisk miljøvariabel."""

    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(
            f"{name} mangler eller er tom i {ENV_FILE}.",
            pytrace=False,
        )
    return value


def _optional_env(name: str) -> str | None:
    """Hent en valgfri miljøvariabel."""

    value = os.getenv(name, "").strip()
    return value or None


def _bool_env(name: str, standard: bool) -> bool:
    """Læs true/false fra en valgfri miljøvariabel."""

    value = os.getenv(name, "").strip().casefold()
    if not value:
        return standard
    if value in {"1", "true", "ja", "yes"}:
        return True
    if value in {"0", "false", "nej", "no"}:
        return False

    pytest.fail(
        f"{name} skal være true eller false i {ENV_FILE}.",
        pytrace=False,
    )


def _er_brugerdefineret_input(value: str) -> bool:
    """Returnér True for kendte Brugerdefineret-input."""

    return _normaliser(value).casefold() in {
        "brugerdefineret",
        "manuel",
    }


def _krav_til_brugerdefineret(
    titel: str | None,
    frekvens: str | None,
    beskrivelse: str | None,
) -> None:
    """Kontrollér testdata til Brugerdefineret."""

    missing = []
    if not titel:
        missing.append(ENV_TITEL)
    if not frekvens:
        missing.append(ENV_FREKVENS)
    if not beskrivelse:
        missing.append(ENV_BESKRIVELSE)

    if missing:
        pytest.fail(
            "Brugerdefineret kræver miljøvariablerne: "
            f"{', '.join(missing)}.",
            pytrace=False,
        )


def _samme_tekst(actual: object, expected: object) -> bool:
    """Sammenlign normaliseret tekst uden forskel på store/små bogstaver."""

    return _normaliser(actual).casefold() == _normaliser(expected).casefold()


def _normaliser(value: object) -> str:
    """Saml whitespace og trim tekst."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def _masker_cpr(cpr: str) -> str:
    """Maskér CPR i terminaloutput."""

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
