"""Integrationstest af opgaveindbakke og Modtag post-dokument i KY.

Testen udfører følgende:

1. Launcher KY.
2. Henter opgaver fra den valgte opgavepakke.
3. Vælger en opgave ud fra RAEKKE_INDEX.
4. Kontrollerer, at opgavens URL indeholder /ky-fagsystem.
5. Kalder modtag_post_dokument() med opgavens URL.
6. modtag_post_dokument() navigerer selv til opgaven.
7. modtag_post_dokument() åbner Dokumenter-toggle.
8. modtag_post_dokument() læser dokumentrækkerne.
9. Dokumentet åbnes kun, når AABEN_DOKUMENT=True.
10. Testen kontrollerer og printer resultatet.

Testen bruger ikke Journalnotater/Dokumenter.

Kør testen med:

    uv run pytest \
        tests/test_opgaveindbakke_og_modtag_post.py \
        -s -vv

Vigtigt:
    Testens output kan indeholde personoplysninger og dokumentoplysninger.
    Terminaloutput og CI-log skal derfor behandles fortroligt.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import (
    ModtagPostDokumentResultat,
    modtag_post_dokument,
)
from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
    raise_if_ky_error,
    wait_for_page_ready,
)
from ky_client.functionality.opgaveindbakke import (
    OpgaveindbakkeClient,
    OpgaveindbakkeError,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]


# ---------------------------------------------------------------------------
# TESTKONFIGURATION
# ---------------------------------------------------------------------------

OPGAVEPAKKE = "NT - Modtaget Post ( HTF, RESS, REVA )"

# Nulbaseret indeks:
#
# 0 = første opgave
# 1 = anden opgave
# 2 = tredje opgave
RAEKKE_INDEX = 0

# Vilkårlig tekst, som skal findes i dokumentrækken.
DOKUMENT = "p10"


# False:
#   Find dokumentrækken og returnér resultatet uden at åbne PDF'en.
#
# True:
#   Find dokumentrækken og åbn det entydige PDF-link.
AABEN_DOKUMENT = False

ACTION_TIMEOUT_MS = 30_000
PAGE_READY_TIMEOUT_MS = 120_000
DOKUMENT_TIMEOUT_MS = 120_000
WAIT_BEFORE_TEST_END_MS = 5_000

FORVENTET_OPGAVE_URL_START = (
    "https://fs0510.fs.kommunernesydelsessystem.dk"
    "/ky-fagsystem/entitet/overblik"
)


async def test_opgaveindbakke_og_modtag_post_dokument(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
) -> None:
    """Hent en opgave og kontroller dens Modtag post-dokumenter."""

    session = automation_session
    page = ky_page

    page.set_default_timeout(
        ACTION_TIMEOUT_MS
    )

    page.set_default_navigation_timeout(
        DOKUMENT_TIMEOUT_MS
    )

    _set_recorder_page(
        session=session,
        page=page,
    )

    # ------------------------------------------------------------------
    # TRIN 1: LAUNCH KY
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 1: LAUNCHER KY"
    )

    print(
        f"Credential-navn: {ky_credential_name!r}",
        flush=True,
    )

    print(
        f"Opgavepakke: {OPGAVEPAKKE!r}",
        flush=True,
    )

    print(
        f"Valgt rækkeindeks: {RAEKKE_INDEX}",
        flush=True,
    )

    print(
        f"Dokumentkriterium: {DOKUMENT!r}",
        flush=True,
    )

    print(
        f"Åbn dokument: {AABEN_DOKUMENT}",
        flush=True,
    )

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    await wait_for_page_ready(
        page=page,
        timeout_ms=PAGE_READY_TIMEOUT_MS,
    )

    await raise_if_ky_error(
        page=page,
        stage="launch før hentning af opgaveindbakke",
    )

    _kontroller_ky_session(
        page
    )

    if not await has_jsessionid(page):
        raise AssertionError(
            "KY blev åbnet, men browser-contexten mangler JSESSIONID."
        )

    print(
        f"KY er åbnet på: {page.url}",
        flush=True,
    )

    await session.screenshot(
        page=page,
        name="01_ky_launch_faerdig",
        always=True,
    )

    # ------------------------------------------------------------------
    # TRIN 2: HENT OPGAVER FRA OPGAVEINDBAKKEN
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 2: HENTER OPGAVER"
    )

    opgaveindbakke = OpgaveindbakkeClient(
        page=page,
    )

    try:
        opgaver = await opgaveindbakke.hent_opgaver(
            opgavepakke=OPGAVEPAKKE,
        )

    except OpgaveindbakkeError as error:
        await session.screenshot(
            page=page,
            name="FEJL_opgaveindbakke",
            always=True,
        )

        raise AssertionError(
            "Opgaveindbakken kunne ikke hentes. "
            f"Fejltype={type(error).__name__}, "
            f"fejl={error}, "
            f"aktuel URL={page.url!r}."
        ) from error

    except Exception as error:
        await session.screenshot(
            page=page,
            name="FEJL_uventet_opgaveindbakke",
            always=True,
        )

        raise AssertionError(
            "Der opstod en uventet fejl under hentning af "
            "opgaveindbakken. "
            f"Fejltype={type(error).__name__}, "
            f"fejl={error}, "
            f"aktuel URL={page.url!r}."
        ) from error

    await raise_if_ky_error(
        page=page,
        stage="hentning af opgaveindbakke",
    )

    if not isinstance(opgaver, list):
        raise TypeError(
            "hent_opgaver() returnerede ikke en liste. "
            f"Type={type(opgaver).__name__}."
        )

    print(
        "Valgt opgavepakke: "
        f"{opgaveindbakke.valgt_opgavepakke!r}",
        flush=True,
    )

    print(
        "Forventet antal fra dropdown: "
        f"{opgaveindbakke.forventet_antal!r}",
        flush=True,
    )

    print(
        f"Antal hentede opgaver: {len(opgaver)}",
        flush=True,
    )

    if not opgaver:
        pytest.skip(
            "Den valgte opgavepakke indeholder ingen opgaver."
        )

    _print_opgaver(
        opgaver
    )

    # ------------------------------------------------------------------
    # TRIN 3: VÆLG OPGAVE
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 3: VÆLGER OPGAVE"
    )

    valgt_opgave = _hent_opgave_fra_index(
        opgaver=opgaver,
        raekke_index=RAEKKE_INDEX,
    )

    print(
        json.dumps(
            valgt_opgave,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )

    opgave_id = _hent_tekst(
        data=valgt_opgave,
        key="Opgave-Id",
    )

    original_url = _hent_tekst(
        data=valgt_opgave,
        key="Original URL",
    )

    opgave_url = _hent_tekst(
        data=valgt_opgave,
        key="URL",
    )

    if not opgave_url:
        raise AssertionError(
            "Den valgte opgave mangler feltet 'URL'. "
            f"Opgave-Id={opgave_id!r}, "
            f"tilgængelige felter={list(valgt_opgave)!r}."
        )

    print(flush=True)

    print(
        f"Opgave-Id: {opgave_id or '(mangler)'}",
        flush=True,
    )

    print(
        f"Original URL: {original_url or '(mangler)'}",
        flush=True,
    )

    print(
        f"Opgave-URL: {opgave_url}",
        flush=True,
    )

    # Testen reparerer ikke URL'en.
    #
    # OpgaveindbakkeClient skal allerede have tilføjet /ky-fagsystem.
    _kontroller_opgave_url(
        opgave_url
    )

    # ------------------------------------------------------------------
    # TRIN 4: KALD MODTAG_POST_DOKUMENT
    #
    # modtag_post_dokument() udfører selv:
    #
    # - page.goto(opgave_url)
    # - kontrol af Modtag post
    # - åbning af Dokumenter-toggle
    # - læsning af dokumentrækker
    # - søgning efter dokumentet
    # - eventuel åbning af PDF-linket
    #
    # Testen må derfor ikke kalde page.goto(opgave_url) først.
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 4: KONTROLLERER MODTAG POST-DOKUMENT"
    )

    print(
        f"URL sendt til modtag_post_dokument(): {opgave_url}",
        flush=True,
    )

    print(
        f"Dokumentkriterium: {DOKUMENT!r}",
        flush=True,
    )

    print(
        f"Åbn dokument: {AABEN_DOKUMENT}",
        flush=True,
    )

    try:
        dokument_resultat = await modtag_post_dokument(
            page=page,
            opgave_url=opgave_url,
            aaben_dokument=AABEN_DOKUMENT,
            dokument=DOKUMENT,
            timeout=DOKUMENT_TIMEOUT_MS,
        )

    except PlaywrightTimeoutError as error:
        await session.screenshot(
            page=page,
            name="FEJL_timeout_modtag_post_dokument",
            always=True,
        )

        raise AssertionError(
            "modtag_post_dokument() fik timeout. "
            f"Opgave-Id={opgave_id!r}, "
            f"dokument={DOKUMENT!r}, "
            f"opgave-URL={opgave_url!r}, "
            f"aktuel URL={page.url!r}, "
            f"fejl={error}."
        ) from error

    except Exception as error:
        await session.screenshot(
            page=page,
            name="FEJL_modtag_post_dokument",
            always=True,
        )

        raise AssertionError(
            "modtag_post_dokument() kunne ikke gennemføres. "
            f"Opgave-Id={opgave_id!r}, "
            f"dokument={DOKUMENT!r}, "
            f"opgave-URL={opgave_url!r}, "
            f"aktuel URL={page.url!r}, "
            f"fejltype={type(error).__name__}, "
            f"fejl={error}."
        ) from error

    await raise_if_ky_error(
        page=page,
        stage="kontrol af dokument på Modtag post-opgave",
    )

    # ------------------------------------------------------------------
    # TRIN 5: KONTROLLER RESULTATKONTRAKTEN
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 5: KONTROLLERER RESULTATET"
    )

    _kontroller_dokument_resultat(
        resultat=dokument_resultat,
        forventet_dokument=DOKUMENT,
        forventet_aaben_dokument=AABEN_DOKUMENT,
    )

    _print_dokument_resultat(
        dokument_resultat
    )

    screenshot_name = (
        "02_dokument_aabnet"
        if dokument_resultat["dokument_aabnet"]
        else "02_dokument_kontrolleret"
    )

    await session.screenshot(
        page=page,
        name=screenshot_name,
        always=True,
    )

    # ------------------------------------------------------------------
    # TRIN 6: TESTRESULTAT
    # ------------------------------------------------------------------

    _print_step(
        "TRIN 6: TESTEN ER FÆRDIG"
    )

    print(
        f"Antal opgaver i pakken: {len(opgaver)}",
        flush=True,
    )

    print(
        f"Valgt rækkeindeks: {RAEKKE_INDEX}",
        flush=True,
    )

    print(
        f"Valgt opgave-ID: {opgave_id or '(mangler)'}",
        flush=True,
    )

    print(
        f"URL sendt som funktionsargument: {opgave_url}",
        flush=True,
    )

    print(
        f"Browserens aktuelle URL: {page.url}",
        flush=True,
    )

    print(
        f"Dokumentkriterium: {DOKUMENT!r}",
        flush=True,
    )

    print(
        f"Dokument fundet: {dokument_resultat['fundet']}",
        flush=True,
    )

    print(
        f"Dokument åbnet: {dokument_resultat['dokument_aabnet']}",
        flush=True,
    )

    print(
        "Dokumentrække: "
        f"{dokument_resultat['dokumenttekst'] or '(ingen)'}",
        flush=True,
    )

    print(
        "Dokument-URL: "
        f"{dokument_resultat['dokument_url'] or '(ingen)'}",
        flush=True,
    )

    print(
        "Åbnet URL: "
        f"{dokument_resultat['aabnet_url'] or '(ingen)'}",
        flush=True,
    )

    if not AABEN_DOKUMENT:
        print(
            "PDF-filen blev ikke åbnet, fordi "
            "AABEN_DOKUMENT=False.",
            flush=True,
        )

    print(
        "Opgaven er ikke gemt, ændret eller afsluttet.",
        flush=True,
    )

    await page.wait_for_timeout(
        WAIT_BEFORE_TEST_END_MS
    )


def _kontroller_ky_session(
    page: Page,
) -> None:
    """Kontrollér KY-sessionen efter launch."""

    if page.is_closed():
        raise AssertionError(
            "Playwright-siden blev lukket under KY-launch."
        )

    if is_ky_error_url(page):
        raise AssertionError(
            "KY viser sin fejlside efter launch. "
            f"Aktuel URL={page.url!r}."
        )

    if not is_ky_url(page):
        raise AssertionError(
            "Siden er ikke en gyldig KY-side efter launch. "
            f"Aktuel URL={page.url!r}."
        )


def _hent_opgave_fra_index(
    opgaver: list[dict[str, Any]],
    raekke_index: int,
) -> dict[str, Any]:
    """Returnér opgaven på det nulbaserede rækkeindeks."""

    if not isinstance(raekke_index, int):
        raise TypeError(
            "RAEKKE_INDEX skal være et heltal."
        )

    if raekke_index < 0:
        raise ValueError(
            "RAEKKE_INDEX må ikke være negativ."
        )

    if raekke_index >= len(opgaver):
        raise IndexError(
            "Den ønskede opgaverække findes ikke. "
            f"RAEKKE_INDEX={raekke_index}, "
            f"antal opgaver={len(opgaver)}."
        )

    opgave = opgaver[
        raekke_index
    ]

    if not isinstance(opgave, dict):
        raise TypeError(
            "Den valgte opgaverække er ikke en dictionary. "
            f"Type={type(opgave).__name__}."
        )

    return opgave


def _kontroller_opgave_url(
    opgave_url: str,
) -> None:
    """Kontrollér URL'en fra OpgaveindbakkeClient."""

    if not isinstance(opgave_url, str):
        raise TypeError(
            "opgave_url skal være en tekststreng."
        )

    opgave_url = opgave_url.strip()

    if not opgave_url:
        raise ValueError(
            "opgave_url må ikke være tom."
        )

    if not opgave_url.casefold().startswith(
        FORVENTET_OPGAVE_URL_START.casefold()
    ):
        raise AssertionError(
            "hent_opgaver() returnerede en forkert opgave-URL. "
            f"Forventet start={FORVENTET_OPGAVE_URL_START!r}, "
            f"modtaget URL={opgave_url!r}. "
            "URL'en skal rettes i opgaveindbakke.py og ikke i testen."
        )


def _kontroller_dokument_resultat(
    resultat: ModtagPostDokumentResultat,
    forventet_dokument: str,
    forventet_aaben_dokument: bool,
) -> None:
    """Kontrollér resultatet fra modtag_post_dokument()."""

    if not isinstance(resultat, dict):
        raise TypeError(
            "modtag_post_dokument() returnerede ikke en dictionary. "
            f"Type={type(resultat).__name__}."
        )

    forventede_felter = {
        "dokument",
        "fundet",
        "aaben_dokument",
        "dokument_aabnet",
        "dokumenttekst",
        "dokument_url",
        "aabnet_url",
    }

    manglende_felter = (
        forventede_felter
        - set(resultat)
    )

    if manglende_felter:
        raise AssertionError(
            "Resultatet fra modtag_post_dokument() mangler felter. "
            f"Manglende felter={sorted(manglende_felter)!r}, "
            f"resultat={resultat!r}."
        )

    if resultat["dokument"] != forventet_dokument:
        raise AssertionError(
            "Resultatets dokumentkriterium matcher ikke testens input. "
            f"Forventet={forventet_dokument!r}, "
            f"faktisk={resultat['dokument']!r}."
        )

    if resultat["aaben_dokument"] is not forventet_aaben_dokument:
        raise AssertionError(
            "Resultatets aaben_dokument matcher ikke testens valg. "
            f"Forventet={forventet_aaben_dokument}, "
            f"faktisk={resultat['aaben_dokument']}."
        )

    if not isinstance(resultat["fundet"], bool):
        raise TypeError(
            "Resultatfeltet 'fundet' skal være bool. "
            f"Type={type(resultat['fundet']).__name__}."
        )

    if not isinstance(resultat["dokument_aabnet"], bool):
        raise TypeError(
            "Resultatfeltet 'dokument_aabnet' skal være bool. "
            f"Type={type(resultat['dokument_aabnet']).__name__}."
        )

    if not resultat["fundet"]:
        if resultat["dokument_aabnet"]:
            raise AssertionError(
                "Dokumentet blev ikke fundet, men "
                "dokument_aabnet er True."
            )

        if resultat["dokumenttekst"]:
            raise AssertionError(
                "Dokumentet blev ikke fundet, men "
                "dokumenttekst er udfyldt."
            )

        if resultat["dokument_url"]:
            raise AssertionError(
                "Dokumentet blev ikke fundet, men "
                "dokument_url er udfyldt."
            )

        if resultat["aabnet_url"]:
            raise AssertionError(
                "Dokumentet blev ikke fundet, men "
                "aabnet_url er udfyldt."
            )

        return

    if not resultat["dokumenttekst"]:
        raise AssertionError(
            "Dokumentet blev fundet, men dokumenttekst er tom."
        )

    if not resultat["dokument_url"]:
        raise AssertionError(
            "Dokumentet blev fundet, men dokument_url er tom."
        )

    if forventet_aaben_dokument:
        if not resultat["dokument_aabnet"]:
            raise AssertionError(
                "Dokumentet blev fundet, og AABEN_DOKUMENT=True, "
                "men dokumentet blev ikke åbnet. "
                f"Resultat={resultat!r}."
            )

        if not resultat["aabnet_url"]:
            raise AssertionError(
                "Dokumentet blev åbnet, men aabnet_url er tom."
            )

    else:
        if resultat["dokument_aabnet"]:
            raise AssertionError(
                "AABEN_DOKUMENT=False, men dokumentet blev åbnet."
            )

        if resultat["aabnet_url"]:
            raise AssertionError(
                "AABEN_DOKUMENT=False, men aabnet_url er udfyldt."
            )


def _print_opgaver(
    opgaver: list[dict[str, Any]],
) -> None:
    """Print alle hentede opgaver."""

    _print_step(
        "RESULTAT FRA OPGAVEINDBAKKEN"
    )

    for nummer, opgave in enumerate(
        opgaver,
        start=1,
    ):
        print(flush=True)

        print(
            "-" * 70,
            flush=True,
        )

        print(
            f"OPGAVE {nummer} AF {len(opgaver)}",
            flush=True,
        )

        print(
            "-" * 70,
            flush=True,
        )

        print(
            json.dumps(
                opgave,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            flush=True,
        )


def _print_dokument_resultat(
    resultat: ModtagPostDokumentResultat,
) -> None:
    """Print resultatet fra modtag_post_dokument()."""

    print(
        json.dumps(
            resultat,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )

    print(flush=True)

    print(
        f"Dokument: {resultat['dokument']!r}",
        flush=True,
    )

    print(
        f"Fundet: {resultat['fundet']}",
        flush=True,
    )

    print(
        f"Skulle åbnes: {resultat['aaben_dokument']}",
        flush=True,
    )

    print(
        f"Faktisk åbnet: {resultat['dokument_aabnet']}",
        flush=True,
    )

    print(
        "Dokumentrække: "
        f"{resultat['dokumenttekst'] or '(ingen)'}",
        flush=True,
    )

    print(
        "Dokumentlink: "
        f"{resultat['dokument_url'] or '(ingen)'}",
        flush=True,
    )

    print(
        "Åbnet URL: "
        f"{resultat['aabnet_url'] or '(ikke åbnet)'}",
        flush=True,
    )


def _hent_tekst(
    data: dict[str, Any],
    key: str,
) -> str:
    """Hent en dictionary-værdi som trimmet tekst."""

    value = data.get(
        key
    )

    if value is None:
        return ""

    return str(
        value
    ).strip()


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSession-recorderen til siden."""

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
        set_page(
            page
        )


def _print_step(
    title: str,
) -> None:
    """Print en sektionsoverskrift."""

    print(flush=True)

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