"""Udfyld og afslut en allerede åbnet Skriv journalnotat-opgave i KY."""

from __future__ import annotations

import os
import logging
import hashlib
from time import monotonic
from urllib.parse import urljoin, urlparse, parse_qs
import re
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, expect
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.functionality.borgere import (
    OPGAVE_TIMEOUT_MS,
    OpstartOpgaveCheckpoint,
)
from ky_client.selectors import KYSelectors


logger = logging.getLogger(__name__)

ACTION_TIMEOUT_MS = 30_000
POLL_INTERVAL_MS = 250

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

ENV_JOURNALNOTAT_SAG = "JOURNALNOTAT_SAG"
ENV_JOURNALNOTAT_SKABELON = "JOURNALNOTAT_SKABELON"


class JournalnotatResultat(TypedDict):
    """Resultat efter klargøring eller afslutning af journalnotatet."""

    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]
    sag_id: str
    sagstekst: str
    skabelon_titel: str
    skabelon_noegle: str
    genoptaget: bool
    kilde: str
    test: bool
    afsluttet: bool
    dokument_sti: str
    dokument_uploadet: bool
    dokument_id: str
    dokument_url: str
    dokument_sha256: str


class UdfyldtJournalnotat(TypedDict):
    """Felter valgt i en allerede åbnet journalnotatopgave."""

    sag_id: str
    sagstekst: str
    skabelon_titel: str
    skabelon_noegle: str


async def opret_journalnotat(
    page: Page,
    checkpoint: OpstartOpgaveCheckpoint,
    sag: str | None = None,
    primaerpart_navn: str | None = None,
    journalnotatskabelon: str | None = None,
    aktive: bool = True,
    passive: bool = True,
    test: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
    nummerplade: str | None = None,
    journalnotat_tekst: str | None = None,
    dokument_sti: str | Path | None = None,
) -> JournalnotatResultat:
    """Udfyld notatet og vedhaeft valgfrit en allerede hentet fil.

    dokument_sti skal pege paa det faktiske afsendte brev, ikke en ny PDF.
    Filen skal findes paa maskinen, hvor Python koerer (fx Ubuntu-serveren).
    test=True udfoerer hverken upload-Gem eller afsluttende Godkend.
    Funktionen sender eller downloader aldrig selv breve.
    """
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket.")
    if not isinstance(test, bool):
        raise TypeError("test skal vaere bool.")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout skal vaere et positivt heltal.")
    _journalnotat_valider_checkpoint(checkpoint)
    fil = _journalnotat_laes_uploadfil(dokument_sti) if dokument_sti is not None else None
    udfyldt = await udfyld_aabnet_journalnotat(
        page=page, sag=sag, primaerpart_navn=primaerpart_navn,
        journalnotatskabelon=journalnotatskabelon,
        aktive=aktive, passive=passive, timeout=timeout,
        nummerplade=nummerplade, journalnotat_tekst=journalnotat_tekst,
    )
    dokument = {"dokument_id": "", "dokument_url": "", "dokument_sha256": ""}
    if fil is not None and not test:
        dokument = await _journalnotat_upload_dokument(
            page=page, checkpoint=checkpoint, fil=fil,
            forventet_sag_id=udfyldt["sag_id"], timeout=timeout,
        )
    if test:
        logger.info("Testtilstand: ingen dokumentupload og ingen Godkend.")
    else:
        await _journalnotat_kontroller_tekst(
            page=page, journalnotat_tekst=journalnotat_tekst, timeout=timeout,
        )
        await _journalnotat_afslut_opgave(page=page, timeout=timeout)
    return {
        "opgave_id": checkpoint["opgave_id"],
        "opgave_navn": checkpoint["opgave_navn"],
        "opgave_url": checkpoint["opgave_url"],
        "borger_url": checkpoint["borger_url"],
        "menu_sti": checkpoint["menu_sti"],
        "sag_id": udfyldt["sag_id"], "sagstekst": udfyldt["sagstekst"],
        "skabelon_titel": udfyldt["skabelon_titel"],
        "skabelon_noegle": udfyldt["skabelon_noegle"],
        "genoptaget": bool(checkpoint.get("genoptaget", False)),
        "kilde": str(checkpoint.get("kilde", "ny_opgave")),
        "test": test, "afsluttet": not test,
        "dokument_sti": str(fil["sti"]) if fil else "",
        "dokument_uploadet": bool(dokument["dokument_id"]),
        **dokument,
    }


def _journalnotat_valider_checkpoint(
    checkpoint: OpstartOpgaveCheckpoint,
) -> None:
    """Kræv checkpoint fra opstart_opgave til Skriv journalnotat."""
    if not isinstance(checkpoint, dict):
        raise TypeError(
            "checkpoint skal komme fra opstart_opgave()."
        )

    for key in (
        "opgave_id",
        "opgave_navn",
        "opgave_url",
        "borger_url",
        "menu_sti",
    ):
        if not checkpoint.get(key):
            raise RuntimeError(
                f"Checkpointet mangler {key}."
            )

    if (
        str(checkpoint["opgave_navn"]).casefold()
        != "Skriv journalnotat".casefold()
    ):
        raise RuntimeError(
            "Checkpointet tilhører ikke Skriv journalnotat. "
            f"Faktisk opgavenavn={checkpoint['opgave_navn']!r}."
        )


async def udfyld_aabnet_journalnotat(
    page: Page,
    sag: str | None = None,
    primaerpart_navn: str | None = None,
    journalnotatskabelon: str | None = None,
    aktive: bool = True,
    passive: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
    nummerplade: str | None = None,
    journalnotat_tekst: str | None = None,
) -> UdfyldtJournalnotat:
    """Udfyld en journalnotatopgave, som allerede er åbnet.

    Sagen findes alene ud fra den angivne sagsnøgle/SagsID.
    Navnet på primærparten anvendes ikke til sagsvalget.
    """
    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket før journalnotatet udfyldes."
        )
    sag = _journalnotat_input_eller_env(
        value=sag,
        env_name=ENV_JOURNALNOTAT_SAG,
        feltnavn="sag",
    )
    journalnotatskabelon = _journalnotat_input_eller_env(
        value=journalnotatskabelon,
        env_name=ENV_JOURNALNOTAT_SKABELON,
        feltnavn="journalnotatskabelon",
    )
    del primaerpart_navn  # Beholdes midlertidigt for bagudkompatibilitet.
    del nummerplade  # Beholdes midlertidigt for bagudkompatibilitet.
    journalnotat_tekst = str(journalnotat_tekst or "").strip()
    if not journalnotat_tekst:
        raise ValueError(
            "journalnotat_tekst skal angives. "
            "Journalnotatet godkendes ikke."
        )
    await _journalnotat_vent_paa_formular(
        page=page,
        timeout=timeout,
    )
    await _journalnotat_nulstil_skabelon(page=page)
    valgt_sag = await _journalnotat_vaelg_sag(
        page=page,
        sag=sag,
        aktive=aktive,
        passive=passive,
        timeout=timeout,
    )
    valgt_skabelon = await _journalnotat_vaelg_skabelon(
        page=page,
        skabelon_titel=journalnotatskabelon,
        timeout=timeout,
    )
    await _journalnotat_skriv_tekst(
        page=page,
        journalnotat_tekst=journalnotat_tekst,
        timeout=timeout,
    )
    return {
        "sag_id": valgt_sag["sag_id"],
        "sagstekst": valgt_sag["sagstekst"],
        "skabelon_titel": valgt_skabelon["titel"],
        "skabelon_noegle": valgt_skabelon["noegle"],
    }
async def _journalnotat_vent_paa_formular(
    page: Page,
    timeout: int,
) -> None:
    """Vent på journalnotatets præcise sagsdropdown."""
    selector = (
        KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_DROPDOWN
    )
    dropdown = page.locator(selector).first
    await dropdown.wait_for(
        state="visible",
        timeout=timeout,
    )


async def _journalnotat_vaelg_sag(
    page: Page,
    sag: str,
    aktive: bool,
    passive: bool,
    timeout: int,
) -> dict[str, str]:
    """Vælg præcis én sag ud fra sagsnøglen/SagsID alene."""
    dropdown_selector = (
        KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_DROPDOWN
    )
    dropdown = page.locator(dropdown_selector).first
    await dropdown.wait_for(
        state="visible",
        timeout=timeout,
    )
    await dropdown.scroll_into_view_if_needed()
    if not await dropdown.is_enabled():
        raise RuntimeError(
            "Journalnotatets sagsdropdown er synlig, men ikke aktiv."
        )
    await _journalnotat_aabn_sagsvaelger(
        page=page,
        dropdown=dropdown,
        timeout=timeout,
    )
    await _journalnotat_nulstil_valgte_sager(
        page=page,
        timeout=timeout,
    )
    await _journalnotat_saet_checkbox(
        page=page,
        selector=KYSelectors.Borgere.JOURNALNOTAT_AKTIV_CHECKBOX,
        checked=aktive,
        feltnavn="Aktive sager",
        timeout=timeout,
    )
    await _journalnotat_saet_checkbox(
        page=page,
        selector=KYSelectors.Borgere.JOURNALNOTAT_PASSIV_CHECKBOX,
        checked=passive,
        feltnavn="Passive sager",
        timeout=timeout,
    )
    search = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG}:visible"
    ).last
    await search.wait_for(state="visible", timeout=timeout)
    await search.fill(sag)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")
    row = await _journalnotat_find_entydig_sag(
        page=page,
        sag=sag,
        aktive=aktive,
        passive=passive,
        timeout=timeout,
    )
    sag_id = (
        await row.get_attribute("data-id")
        or await row.get_attribute("data-sag-id")
        or await row.get_attribute("data-sagsid")
        or ""
    ).strip()
    if not sag_id:
        raise RuntimeError(
            "Den valgte sagsrække mangler et teknisk sag-id."
        )
    sagstekst = _journalnotat_normaliser_tekst(
        await row.inner_text()
    )
    await row.scroll_into_view_if_needed()
    await row.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
    await _journalnotat_vent_paa_en_sag_valgt(
        page=page,
        dropdown_selector=dropdown_selector,
        timeout=timeout,
    )
    await _journalnotat_minimer_sagsvaelger(
        page=page,
        dropdown_selector=dropdown_selector,
        timeout=timeout,
    )
    return {
        "sag_id": sag_id,
        "sagstekst": sagstekst,
    }
async def _journalnotat_aabn_sagsvaelger(
    page: Page,
    dropdown: Locator,
    timeout: int,
) -> None:
    """Åbn sagsvælgeren og vent på det interne søgefelt."""
    elapsed_ms = 0
    while elapsed_ms < timeout:
        searches = page.locator(
            f"{KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG}:visible"
        )
        if await searches.count() > 0:
            return

        try:
            await dropdown.click(
                timeout=min(ACTION_TIMEOUT_MS, timeout)
            )
        except Exception:
            pass

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsvælgerens søgefelt blev ikke synligt."
    )


async def _journalnotat_nulstil_valgte_sager(
    page: Page,
    timeout: int,
) -> None:
    """Fravælg tidligere valgte sager før det nye valg."""
    selector = (
        KYSelectors.Borgere.JOURNALNOTAT_VALGTE_SAGSRAEKKER
    )
    selected = page.locator(f"{selector}:visible")

    for index in reversed(range(await selected.count())):
        row = selected.nth(index)
        try:
            await row.click(
                timeout=min(ACTION_TIMEOUT_MS, timeout)
            )
        except Exception:
            continue


async def _journalnotat_saet_checkbox(
    page: Page,
    selector: str,
    checked: bool,
    feltnavn: str,
    timeout: int,
) -> None:
    """Sæt og kontrollér fluebenet for aktive eller passive sager."""
    checkboxes = page.locator(f"{selector}:visible")
    if await checkboxes.count() == 0:
        raise RuntimeError(
            f"Checkboxen {feltnavn!r} blev ikke fundet."
        )

    checkbox = checkboxes.last
    await checkbox.wait_for(
        state="visible",
        timeout=timeout,
    )

    if await checkbox.is_checked() != checked:
        await checkbox.set_checked(
            checked,
            timeout=min(ACTION_TIMEOUT_MS, timeout),
        )

    actual = await checkbox.is_checked()
    if actual != checked:
        raise RuntimeError(
            f"Checkboxen {feltnavn!r} fik ikke den ønskede tilstand. "
            f"Forventet={checked}, faktisk={actual}."
        )


async def _journalnotat_find_entydig_sag(
    page: Page,
    sag: str,
    aktive: bool,
    passive: bool,
    timeout: int,
) -> Locator:
    """Find præcis én sag med den angivne sagsnøgle/SagsID.

    Der matches kun på kolonnen SagsID. Navnet på primærparten indgår ikke.
    Kun sager med en tilladt status kan vælges.
    """
    wanted_sag = _journalnotat_normaliser_tekst(sag).casefold()
    if not wanted_sag:
        raise ValueError("sag må ikke være tom.")
    if not aktive and not passive:
        raise ValueError(
            "Mindst én af aktive eller passive sager skal medtages."
        )
    tables = page.locator("#sagsvaelgertable:visible")
    await tables.first.wait_for(state="visible", timeout=timeout)
    if await tables.count() != 1:
        raise RuntimeError(
            "Journalnotatet har ikke præcis én synlig sagstabel. "
            "Ingen sag vælges."
        )
    table = tables.first
    await table.wait_for(state="visible", timeout=timeout)
    header_cells = table.locator("thead th")
    headers = [
        _journalnotat_normaliser_overskrift(
            await header_cells.nth(index).inner_text()
        )
        for index in range(await header_cells.count())
    ]
    sagsid_indexes = [
        index
        for index, header in enumerate(headers)
        if header == "sagsid"
    ]
    if len(sagsid_indexes) != 1:
        raise RuntimeError(
            "Journalnotatets sagstabel mangler en entydig "
            "kolonne med overskriften 'SagsID'. "
            f"Fundne kolonner={headers!r}."
        )
    sagsid_index = sagsid_indexes[0]
    tilladte_tilstande: set[str] = set()
    if aktive:
        tilladte_tilstande.add("aktiv")
    if passive:
        tilladte_tilstande.add("passiv")
    elapsed_ms = 0
    diagnostic: list[dict[str, object]] = []
    while elapsed_ms < timeout:
        rows = table.locator("tbody tr.table-row:visible")
        matches: list[Locator] = []
        diagnostic = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                if not await row.is_visible():
                    continue
                tilstand = (
                    await row.get_attribute("data-tilstand")
                    or ""
                ).strip().casefold()
                cells = row.locator("td")
                if await cells.count() <= sagsid_index:
                    raise RuntimeError(
                        "En synlig sagsrække mangler SagsID-cellen."
                    )
                sagsid_text = _journalnotat_normaliser_tekst(
                    await cells.nth(sagsid_index).inner_text()
                )
                sag_match = wanted_sag == sagsid_text.casefold()
                tilstand_tilladt = tilstand in tilladte_tilstande
                diagnostic.append(
                    {
                        "raekke": index + 1,
                        "sagsid": sagsid_text,
                        "sagsid_match": sag_match,
                        "tilstand": tilstand,
                        "tilstand_tilladt": tilstand_tilladt,
                    }
                )
                if not sag_match or not tilstand_tilladt:
                    continue
                matches.append(row)
            except RuntimeError:
                raise
            except Exception as error:
                raise RuntimeError(
                    "En sagsrække kunne ikke læses sikkert. "
                    f"Række={index + 1}, "
                    f"fejltype={type(error).__name__}. "
                    "Ingen sag vælges."
                ) from error
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                "Flere sager matcher den samme sagsnøgle/SagsID "
                f"{sag!r}. Ingen sag vælges. "
                f"Diagnose={diagnostic!r}."
            )
        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError(
        "Ingen sag matchede sagsnøglen/SagsID "
        f"{sag!r}. "
        f"Diagnose={diagnostic!r}."
    )
def _journalnotat_normaliser_overskrift(
    value: object,
) -> str:
    """Normalisér tabeloverskrift og danske specialtegn."""
    return (
        _journalnotat_normaliser_tekst(value)
        .casefold()
        .replace("æ", "ae")
        .replace("ø", "oe")
        .replace("å", "aa")
    )


def _journalnotat_normaliser_navn(value: object) -> str:
    """Normaliser navn og fjern et afsluttende parentes-suffiks."""
    resultat = _journalnotat_normaliser_tekst(value)

    # Personoplysninger kan have et suffiks, som sagstabellen ikke viser.
    resultat = re.sub(r"\s+\([^()]*\)\s*$", "", resultat).strip()
    resultat = re.sub(r"\s*-\s*$", "", resultat).strip()

    return resultat.casefold()



async def _journalnotat_vent_paa_en_sag_valgt(
    page: Page,
    dropdown_selector: str,
    timeout: int,
) -> None:
    """Vent på, at readonly-inputfeltet viser '1 sag valgt'."""
    del dropdown_selector
    await _journalnotat_vent_paa_inputvaerdi(
        page=page,
        timeout=timeout,
    )


async def _journalnotat_minimer_sagsvaelger(
    page: Page,
    dropdown_selector: str,
    timeout: int,
) -> None:
    """Minimer sagsvælgeren robust efter det entydige valg."""
    if page.is_closed():
        raise RuntimeError(
            "KY-siden blev lukket under minimering af sagsvælgeren."
        )

    input_selector = (
        KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_INPUT
    )
    exact_input_selector = (
        "#command\\.tilfoejedeJournalnotater\\[0\\]\\.sager"
    )

    inputs = page.locator(f"{input_selector}:visible")
    if await inputs.count() == 0:
        inputs = page.locator(
            f"{exact_input_selector}:visible"
        )
    if await inputs.count() == 0:
        raise PlaywrightTimeoutError(
            "Sagsvælgerens readonly-input blev ikke fundet."
        )

    selected_input = inputs.last
    await selected_input.wait_for(
        state="visible",
        timeout=timeout,
    )
    await _journalnotat_vent_paa_inputvaerdi(
        page=page,
        timeout=timeout,
    )
    await selected_input.scroll_into_view_if_needed()

    try:
        await selected_input.focus()
    except Exception:
        await selected_input.click(
            timeout=min(ACTION_TIMEOUT_MS, timeout),
            force=True,
        )

    await selected_input.press("Escape")
    if await _journalnotat_er_sagsvaelger_minimeret(
        page=page,
        timeout=min(3_000, timeout),
    ):
        return

    await selected_input.evaluate(
        """
        element => {
            element.focus();
            for (const type of ['keydown', 'keyup']) {
                element.dispatchEvent(new KeyboardEvent(type, {
                    key: 'Escape',
                    code: 'Escape',
                    keyCode: 27,
                    which: 27,
                    bubbles: true,
                    cancelable: true
                }));
            }
        }
        """
    )
    if await _journalnotat_er_sagsvaelger_minimeret(
        page=page,
        timeout=min(3_000, timeout),
    ):
        return

    if await _journalnotat_klik_sagsvaelger_luk(page):
        if await _journalnotat_er_sagsvaelger_minimeret(
            page=page,
            timeout=min(3_000, timeout),
        ):
            return

    dropdown = page.locator(dropdown_selector).first
    if await dropdown.count() > 0 and await dropdown.is_visible():
        await dropdown.click(
            timeout=min(ACTION_TIMEOUT_MS, timeout),
            force=True,
        )

    if await _journalnotat_er_sagsvaelger_minimeret(
        page=page,
        timeout=min(3_000, timeout),
    ):
        return

    snapshot = await _journalnotat_sagsvaelger_snapshot(page)
    raise PlaywrightTimeoutError(
        "Sagsdropdownen kunne ikke minimeres. "
        f"Seneste status: {snapshot!r}."
    )


async def _journalnotat_vent_paa_inputvaerdi(
    page: Page,
    timeout: int,
) -> None:
    """Vent på, at readonly-inputfeltet viser præcis '1 sag valgt'."""
    input_selector = (
        KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_INPUT
    )
    exact_input_selector = (
        "#command\\.tilfoejedeJournalnotater\\[0\\]\\.sager"
    )
    elapsed_ms = 0
    seneste_vaerdi = ""

    while elapsed_ms < timeout:
        inputs = page.locator(f"{input_selector}:visible")
        if await inputs.count() == 0:
            inputs = page.locator(
                f"{exact_input_selector}:visible"
            )

        if await inputs.count() > 0:
            field = inputs.last
            try:
                seneste_vaerdi = _journalnotat_normaliser_tekst(
                    await field.input_value()
                )
                if seneste_vaerdi.casefold() == "1 sag valgt":
                    return
            except Exception:
                pass

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsvælgerens input viste ikke '1 sag valgt'. "
        f"Senest læste værdi: {seneste_vaerdi!r}."
    )


async def _journalnotat_klik_sagsvaelger_luk(
    page: Page,
) -> bool:
    """Find og aktivér et synligt lukkeelement i sagsdropdownen."""
    search_selector = (
        KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG
    )
    searches = page.locator(f"{search_selector}:visible")
    roots: list[Locator] = []

    if await searches.count() > 0:
        search = searches.last
        for xpath in (
            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' dropdown-menu ')][1]",
            "xpath=ancestor::*[contains(@id, 'dropdown')][1]",
            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' sagsvaelger ')][1]",
        ):
            try:
                root = search.locator(xpath)
                if (
                    await root.count() > 0
                    and await root.first.is_visible()
                ):
                    roots.append(root.first)
            except Exception:
                continue

    candidate_selector = (
        "button.close:visible, a.close:visible, "
        "[data-dismiss='dropdown']:visible, "
        "[aria-label*='luk' i]:visible, "
        "[title*='luk' i]:visible, "
        "[aria-label*='close' i]:visible, "
        "[title*='close' i]:visible, "
        ".glyphicon-remove:visible, .fa-times:visible"
    )

    for root in roots:
        try:
            candidates = root.locator(candidate_selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                if await candidate.is_visible():
                    await candidate.click(
                        timeout=ACTION_TIMEOUT_MS
                    )
                    return True
        except Exception:
            continue

    return False


async def _journalnotat_er_sagsvaelger_minimeret(
    page: Page,
    timeout: int,
) -> bool:
    """Returnér True, hvis sagsvælgeren er stabilt skjult."""
    elapsed_ms = 0
    stable_hidden_checks = 0

    while elapsed_ms < timeout:
        snapshot = await _journalnotat_sagsvaelger_snapshot(page)
        hidden = (
            snapshot["synlige_soegefelter"] == 0
            and snapshot["synlige_resultatraekker"] == 0
            and snapshot["aria_expanded"] != "true"
        )

        if hidden:
            stable_hidden_checks += 1
            if stable_hidden_checks >= 2:
                return True
        else:
            stable_hidden_checks = 0

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    return False


async def _journalnotat_sagsvaelger_snapshot(
    page: Page,
) -> dict[str, object]:
    """Læs synlighed og udvidelsestilstand for sagsvælgeren."""
    search_fields = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG}:visible"
    )
    result_rows = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_FOERSTE_RESULTAT}:visible"
    )
    container = page.locator(
        KYSelectors.Borgere.JOURNALNOTAT_DROPDOWN_CONTAINER
    ).first

    aria_expanded: str | None = None
    container_classes = ""
    try:
        if await container.count() > 0:
            aria_expanded = await container.get_attribute(
                "aria-expanded"
            )
            container_classes = (
                await container.get_attribute("class") or ""
            )
    except Exception:
        pass

    return {
        "synlige_soegefelter": await search_fields.count(),
        "synlige_resultatraekker": await result_rows.count(),
        "aria_expanded": aria_expanded,
        "container_classes": container_classes,
    }


async def _journalnotat_vent_paa_sagsvaelger_minimeret(
    page: Page,
    timeout: int,
) -> None:
    """Bagudkompatibel ventefunktion til sagsvælgerens lukkede tilstand."""
    if await _journalnotat_er_sagsvaelger_minimeret(
        page=page,
        timeout=timeout,
    ):
        return

    snapshot = await _journalnotat_sagsvaelger_snapshot(page)
    raise PlaywrightTimeoutError(
        "Sagsdropdownen blev ikke minimeret. "
        f"Seneste status: {snapshot!r}."
    )


async def _journalnotat_nulstil_skabelon(
    page: Page,
) -> None:
    """Ryd titel og nøgle fra et eventuelt tidligere afbrudt forsøg."""
    selectors = (
        KYSelectors.Borgere.JOURNALNOTAT_SKABELON_TITELFELT,
        KYSelectors.Borgere.JOURNALNOTAT_SKABELON_NOEGLEFELT,
    )

    for selector in selectors:
        fields = page.locator(selector)
        for index in range(await fields.count()):
            field = fields.nth(index)
            try:
                await field.evaluate(
                    """
                    element => {
                        element.value = '';
                        element.dispatchEvent(
                            new Event('input', { bubbles: true })
                        );
                        element.dispatchEvent(
                            new Event('change', { bubbles: true })
                        );
                    }
                    """
                )
            except Exception:
                continue


async def _journalnotat_vaelg_skabelon(
    page: Page,
    skabelon_titel: str,
    timeout: int,
) -> dict[str, str]:
    """Søg og vælg én journalnotatskabelon med eksakt titel."""
    controls = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SKABELON_KONTROL}:visible"
    )
    if await controls.count() == 0:
        raise RuntimeError(
            "Journalnotatets skabelonvælger blev ikke fundet."
        )

    control = controls.last
    await control.wait_for(
        state="visible",
        timeout=timeout,
    )
    await control.click(
        timeout=min(ACTION_TIMEOUT_MS, timeout)
    )

    searches = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SKABELONGRUPPE_SOEG}:visible"
    )
    if await searches.count() == 0:
        searches = page.locator(
            "input.form-control.skabelonvaelger-soeg:visible, "
            "input.skabelonvaelger-soeg[placeholder*='skabelon' i]:visible"
        )
    if await searches.count() == 0:
        raise RuntimeError(
            "Skabelonvælgerens søgefelt blev ikke fundet."
        )

    search = searches.last
    await search.fill(skabelon_titel)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")

    candidate = await _journalnotat_find_entydig_skabelon(
        page=page,
        skabelon_titel=skabelon_titel,
        timeout=timeout,
    )
    selected_title = _journalnotat_normaliser_tekst(
        await candidate.get_attribute("data-titel")
        or await candidate.inner_text()
    )
    selected_key = (
        await candidate.get_attribute("data-noegle")
        or await candidate.get_attribute("data-key")
        or await candidate.get_attribute("data-id")
        or ""
    ).strip()

    await candidate.click(
        timeout=min(ACTION_TIMEOUT_MS, timeout)
    )
    await _journalnotat_vent_paa_skabelon_valgt(
        page=page,
        control=control,
        selected_title=selected_title,
        selected_key=selected_key,
        timeout=timeout,
    )

    return {
        "titel": selected_title,
        "noegle": selected_key,
    }


async def _journalnotat_find_entydig_skabelon(
    page: Page,
    skabelon_titel: str,
    timeout: int,
) -> Locator:
    """Find én synlig skabelon med eksakt titel."""
    wanted = _journalnotat_normaliser_tekst(
        skabelon_titel
    ).casefold()
    elapsed_ms = 0
    selector = (
        "#journalnotat-group li[data-titel]:visible, "
        "#journalnotat-group li.hg-skabelon.cell:visible, "
        "#journalnotat-group [role='treeitem']:visible, "
        "#journalnotat-group [role='option']:visible, "
        "ul.skabelonlist:visible li[data-titel]:visible"
    )

    while elapsed_ms < timeout:
        candidates = page.locator(selector)
        matches: list[Locator] = []

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                title = _journalnotat_normaliser_tekst(
                    await candidate.get_attribute("data-titel")
                    or await candidate.inner_text()
                )
                if title.casefold() == wanted:
                    matches.append(candidate)
            except Exception:
                continue

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                "Flere skabeloner matcher titlen "
                f"{skabelon_titel!r}."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Journalnotatskabelonen "
        f"{skabelon_titel!r} blev ikke fundet."
    )


async def _journalnotat_vent_paa_skabelon_valgt(
    page: Page,
    control: Locator,
    selected_title: str,
    selected_key: str,
    timeout: int,
) -> None:
    """Vent på, at KY har registreret det valgte skabelonmatch."""
    elapsed_ms = 0
    wanted = selected_title.casefold()

    while elapsed_ms < timeout:
        try:
            values: list[str] = []
            for attribute in (
                "value",
                "data-titel",
                "title",
            ):
                value = await control.get_attribute(attribute)
                if value:
                    values.append(
                        _journalnotat_normaliser_tekst(value)
                    )

            try:
                values.append(
                    _journalnotat_normaliser_tekst(
                        await control.input_value()
                    )
                )
            except Exception:
                pass

            title_registered = any(
                value.casefold() == wanted
                or wanted in value.casefold()
                for value in values
                if value
            )

            key_registered = False
            if selected_key:
                key_registered = (
                    await page.locator(
                        f"input[type='hidden'][value='{selected_key}'], "
                        f"[data-noegle='{selected_key}'].selected, "
                        f"[data-noegle='{selected_key}'].active"
                    ).count()
                    > 0
                )

            visible_searches = page.locator(
                "#journalnotat-group "
                "input.skabelonvaelger-soeg:visible"
            )
            if (
                title_registered or key_registered
            ) and await visible_searches.count() == 0:
                return
        except Exception:
            replacement = page.locator(
                f"{KYSelectors.Borgere.JOURNALNOTAT_SKABELON_KONTROL}:visible"
            )
            if await replacement.count() > 0:
                control = replacement.last

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        f"Skabelonen {selected_title!r} blev ikke registreret som valgt."
    )


async def _journalnotat_afslut_opgave(
    page: Page,
    timeout: int,
) -> None:
    """Klik på Godkend og vent på et sikkert afslutningssignal."""
    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket, før journalnotatet kan godkendes."
        )

    selector = (
        "button[type='button'].btn.btn-primary.submit-opgave.margin-right"
        "[data-href='/opgave/handling/fortsaet']"
    )
    pattern = re.compile(
        r"^\s*Godkend\s*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket, før Godkend-knappen blev fundet."
            )

        candidates = page.locator(f"{selector}:visible")
        matches: list[Locator] = []

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_enabled():
                    continue
                text = _journalnotat_normaliser_tekst(
                    await candidate.get_attribute("value")
                    or await candidate.inner_text()
                )
                if pattern.fullmatch(text):
                    matches.append(candidate)
            except Exception:
                continue

        if len(matches) > 1:
            raise RuntimeError(
                "Flere synlige og aktive Godkend-knapper blev fundet. "
                "Journalnotatet er ikke godkendt."
            )

        if len(matches) == 1:
            button = matches[0]
            before_url = page.url
            before_opgave_id = (
                await _journalnotat_hent_aktivt_opgave_id(page)
            )

            await button.scroll_into_view_if_needed()
            await button.click(
                timeout=min(ACTION_TIMEOUT_MS, timeout)
            )
            await _journalnotat_vent_paa_afslutning(
                page=page,
                button=button,
                button_selector=selector,
                before_url=before_url,
                before_opgave_id=before_opgave_id,
                timeout=timeout,
            )
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "En synlig og aktiv Godkend-knap til journalnotatet "
        "blev ikke fundet inden for tidsgrænsen."
    )


async def _journalnotat_vent_paa_afslutning(
    page: Page,
    button: Locator,
    button_selector: str,
    before_url: str,
    before_opgave_id: str,
    timeout: int,
) -> None:
    """Vent på et sikkert signal om, at Godkend er behandlet af KY."""
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            return

        try:
            url_changed = page.url != before_url
            godkend_gone = (
                await page.locator(
                    f"{button_selector}:visible"
                ).count()
                == 0
            )
            try:
                clicked_button_gone = not await button.is_visible()
            except Exception:
                clicked_button_gone = True

            current_opgave_id = (
                await _journalnotat_hent_aktivt_opgave_id(page)
            )
            opgave_changed = bool(before_opgave_id) and (
                not current_opgave_id
                or current_opgave_id.casefold()
                != before_opgave_id.casefold()
            )

            if (
                url_changed
                or godkend_gone
                or clicked_button_gone
                or opgave_changed
            ):
                return
        except Exception:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Godkend-knappen blev klikket, men KY viste ikke et tydeligt "
        "afslutningssignal inden timeout. Kontrollér journalnotatets "
        "status i KY."
    )


async def _journalnotat_hent_aktivt_opgave_id(
    page: Page,
) -> str:
    """Læs det dynamiske opgave-id fra den aktuelle opgaveheader."""
    if page.is_closed():
        return ""

    buttons = page.locator(
        "div#opgave-header "
        "a.undock_panel_button[data-opgave-id]:visible"
    )
    if await buttons.count() == 0:
        return ""

    try:
        return (
            await buttons.first.get_attribute("data-opgave-id")
            or ""
        ).strip()
    except Exception:
        return ""


def _journalnotat_input_eller_env(
    value: str | None,
    env_name: str,
    feltnavn: str,
) -> str:
    """Brug funktionsinputtet eller hent værdien fra projektets .env-fil."""
    if value is not None and str(value).strip():
        return _journalnotat_paakraevet_tekst(
            str(value),
            feltnavn,
        )

    env_value = os.getenv(env_name, "").strip()
    if not env_value:
        raise ValueError(
            f"{feltnavn} mangler. Angiv funktionsinputtet eller tilføj "
            f"{env_name} til {ENV_FILE}."
        )

    return _journalnotat_paakraevet_tekst(
        env_value,
        feltnavn,
    )


def _journalnotat_paakraevet_tekst(
    value: str,
    feltnavn: str,
) -> str:
    """Trim et obligatorisk tekstinput."""
    normaliseret = _journalnotat_normaliser_tekst(value)
    if not normaliseret:
        raise ValueError(
            f"{feltnavn} må ikke være tomt."
        )
    return normaliseret


def _journalnotat_normaliser_tekst(
    value: object,
) -> str:
    """Saml whitespace og trim tekst."""
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


async def _journalnotat_find_editor(
    page: Page,
    timeout: int,
) -> tuple[object, Locator]:
    """Find præcis én synlig journalnotateditor i en iframe."""
    selector = (
        'body#tinymce[data-id="tilfoejedeJournalnotater0.notat"]'
        '[contenteditable="true"]'
    )
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under journalnotatudfyldningen."
            )

        matches: list[tuple[object, Locator]] = []
        for frame in page.frames:
            editor = frame.locator(selector)
            try:
                if await editor.count() == 1 and await editor.is_visible():
                    matches.append((frame, editor))
            except Exception:
                continue

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            raise RuntimeError(
                "Flere synlige journalnotateditorer blev fundet. "
                "Ingen tekst skrives."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Journalnotatets iframe-editor blev ikke fundet inden timeout."
    )


def _journalnotat_normaliser_sammenligning(value: object) -> str:
    """Normalisér linjeskift og ydre whitespace til sikker kontrol."""
    return (
        str(value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


async def _journalnotat_skriv_tekst(
    page: Page,
    journalnotat_tekst: str,
    timeout: int,
) -> None:
    """Erstat hele skabelonindholdet med den beregnede journalnotattekst."""
    forventet = str(journalnotat_tekst or "").strip()
    if not forventet:
        raise ValueError("journalnotat_tekst må ikke være tom.")

    frame, editor = await _journalnotat_find_editor(
        page=page,
        timeout=timeout,
    )

    # Skriv direkte i iframe-body'en. Der bruges ikke window.tinymce i
    # iframe-vinduet, fordi KY ikke eksponerer TinyMCE-API'et dér.
    await editor.fill(forventet)
    await editor.dispatch_event("input")
    await editor.dispatch_event("change")
    await editor.dispatch_event("blur")

    # Synkronisér med TinyMCE-instansen i parent-vinduet, hvis API'et findes.
    # Ellers opdateres det underliggende formularfelt direkte.
    await frame.evaluate(
        """
        ({ editorId, text }) => {
            const body = document.body;
            body.innerText = text;

            for (const eventType of ['input', 'change', 'blur']) {
                body.dispatchEvent(
                    new Event(eventType, { bubbles: true })
                );
            }

            const parentWindow = window.parent;
            const tinyMce = parentWindow && parentWindow.tinymce;
            const instance = (
                tinyMce && typeof tinyMce.get === 'function'
                    ? tinyMce.get(editorId)
                    : null
            );

            if (instance) {
                instance.setContent(body.innerHTML);
                instance.setDirty(true);
                instance.fire('input');
                instance.fire('change');
                if (typeof instance.save === 'function') {
                    instance.save();
                }
                return;
            }

            const parentDocument = parentWindow.document;
            const backingField = (
                parentDocument.getElementById(editorId)
                || parentDocument.querySelector(
                    '[name="tilfoejedeJournalnotater[0].notat"]'
                )
            );

            if (!backingField) {
                throw new Error(
                    'Journalnotatets underliggende formularfelt blev ikke fundet.'
                );
            }

            backingField.value = body.innerHTML;
            backingField.dispatchEvent(
                new Event('input', { bubbles: true })
            );
            backingField.dispatchEvent(
                new Event('change', { bubbles: true })
            );
        }
        """,
        {
            "editorId": "tilfoejedeJournalnotater0.notat",
            "text": forventet,
        },
    )

    await _journalnotat_kontroller_tekst(
        page=page,
        journalnotat_tekst=forventet,
        timeout=timeout,
    )


async def _journalnotat_kontroller_tekst(
    page: Page,
    journalnotat_tekst: str | None,
    timeout: int,
) -> None:
    """Nægt Godkend, hvis editoren ikke indeholder den beregnede tekst."""
    forventet = _journalnotat_normaliser_sammenligning(
        journalnotat_tekst
    )
    if not forventet:
        raise ValueError("journalnotat_tekst må ikke være tom.")

    frame, editor = await _journalnotat_find_editor(
        page=page,
        timeout=timeout,
    )
    faktisk = _journalnotat_normaliser_sammenligning(
        await editor.inner_text()
    )

    if faktisk != forventet:
        raise RuntimeError(
            "Journalnotatteksten matcher ikke beregningsresultatet. "
            "KY-opgaven godkendes ikke."
        )

    if "xxxx" in faktisk.casefold():
        raise RuntimeError(
            "Journalnotatet indeholder stadig xxxx. "
            "KY-opgaven godkendes ikke."
        )

    # Kontrollér også det formularfelt, som KY sender ved Godkend.
    backing_value = await frame.evaluate(
        """
        editorId => {
            const parentWindow = window.parent;
            const tinyMce = parentWindow && parentWindow.tinymce;
            const instance = (
                tinyMce && typeof tinyMce.get === 'function'
                    ? tinyMce.get(editorId)
                    : null
            );

            if (instance && typeof instance.getContent === 'function') {
                return instance.getContent({ format: 'text' });
            }

            const parentDocument = parentWindow.document;
            const backingField = (
                parentDocument.getElementById(editorId)
                || parentDocument.querySelector(
                    '[name="tilfoejedeJournalnotater[0].notat"]'
                )
            );

            if (!backingField) {
                return null;
            }

            const temporary = parentDocument.createElement('div');
            temporary.innerHTML = backingField.value || '';
            return temporary.innerText;
        }
        """,
        "tilfoejedeJournalnotater0.notat",
    )

    if backing_value is None:
        raise RuntimeError(
            "Journalnotatets formularfelt kunne ikke verificeres. "
            "KY-opgaven godkendes ikke."
        )

    backing_text = _journalnotat_normaliser_sammenligning(
        backing_value
    )
    if backing_text != forventet:
        raise RuntimeError(
            "Journalnotatets formularfelt matcher ikke beregningsresultatet. "
            "KY-opgaven godkendes ikke."
        )



class JournalnotatUploadfil(TypedDict):
    sti: Path
    navn: str
    indhold: bytes
    sha256: str


def _journalnotat_laes_uploadfil(sti: str | Path) -> JournalnotatUploadfil:
    """Laes filen en gang, saa upload og kontrol bruger samme bytes."""
    if not isinstance(sti, (str, Path)) or not str(sti).strip():
        raise ValueError("dokument_sti skal vaere en ikke-tom filsti.")
    path = Path(sti).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("dokument_sti er ikke en fil.")
    content = path.read_bytes()
    if not content:
        raise ValueError("Dokumentet er tomt.")
    if path.suffix.lower() == '.pdf' and not content.startswith(b'%PDF-'):
        raise ValueError("Filen har PDF-endelse, men mangler PDF-header.")
    return {"sti": path, "navn": path.name, "indhold": content,
            "sha256": hashlib.sha256(content).hexdigest()}


async def _journalnotat_upload_dokument(
    page: Page,
    checkpoint: OpstartOpgaveCheckpoint,
    fil: JournalnotatUploadfil,
    forventet_sag_id: str,
    timeout: int,
) -> dict[str, str]:
    """Vedhæft filen, vælg præcis den forventede sag og klik Gem én gang.

    Sagen findes alene via rækkens tekniske ``data-id``. KY's synlige
    readonly-felt skal derefter vise ``1 sag valgt``, og uploadformularens
    ``selectedSagIds`` skal indeholde præcis ét input med det forventede id.
    """
    S = KYSelectors.Borgere
    forventet_sag_id = str(forventet_sag_id or "").strip()
    if not forventet_sag_id:
        raise ValueError("Journalnotatets tekniske sag-id mangler.")

    aktivt_opgave_id = await _journalnotat_hent_aktivt_opgave_id(page)
    if aktivt_opgave_id.casefold() != str(checkpoint["opgave_id"]).casefold():
        raise RuntimeError(
            "Aktiv opgave matcher ikke journalnotatets checkpoint."
        )

    panel = page.locator(S.JOURNALNOTAT_UPLOAD_PANEL)
    await expect(panel).to_have_count(1, timeout=timeout)

    dokumenttabel = panel.locator(S.JOURNALNOTAT_UPLOAD_TABEL)
    if not await dokumenttabel.is_visible():
        toggle = panel.locator(S.JOURNALNOTAT_UPLOAD_PANEL_TOGGLE)
        await expect(toggle).to_have_count(1, timeout=timeout)
        await toggle.click(timeout=timeout)
    await expect(dokumenttabel).to_be_visible(timeout=timeout)

    dokumentraekker = dokumenttabel.locator("tbody tr.table-row[data-id]")
    eksisterende_ider = set(
        await dokumentraekker.evaluate_all(
            "rows => rows.map(row => row.getAttribute('data-id'))"
        )
    )
    samme_filnavn = await dokumentraekker.evaluate_all(
        """
        (rows, filnavn) => rows.some(row =>
            Array.from(row.cells).some(cell =>
                (cell.textContent || '').trim() === filnavn
            )
        )
        """,
        fil["navn"],
    )
    if samme_filnavn:
        raise RuntimeError(
            "Et dokument med samme filnavn findes allerede på opgaven. "
            "Gem klikkes ikke igen, fordi udfaldet af en tidligere upload "
            "kan være permanent."
        )

    aabn_upload = panel.locator(S.JOURNALNOTAT_UPLOAD_AABN)
    await expect(aabn_upload).to_have_count(1, timeout=timeout)
    await aabn_upload.click(timeout=timeout)

    # KY henter modalens HTML asynkront efter klikket. Vent derfor på
    # formularen i stedet for at undersøge DOM'en én enkelt gang med det samme.
    # Brug selector uden :visible under søgningen; synlighed kontrolleres bagefter.
    formular_selector = "form#uploadFilForm"
    deadline = monotonic() + timeout / 1000
    formular_matches: list[tuple[object, Locator]] = []

    while monotonic() < deadline:
        formular_matches = []
        for frame in page.frames:
            formularer = frame.locator(formular_selector)
            for index in range(await formularer.count()):
                formular = formularer.nth(index)
                if await formular.is_visible():
                    formular_matches.append((frame, formular))

        if len(formular_matches) == 1:
            break
        if len(formular_matches) > 1:
            raise RuntimeError(
                "Der blev fundet flere synlige uploadformularer på tværs af "
                "hovedside og iframes. Uploaden afbrydes."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
    else:
        raise PlaywrightTimeoutError(
            "Uploadvinduet blev åbnet, men form#uploadFilForm blev ikke "
            "synlig inden timeout."
        )

    upload_frame, form = formular_matches[0]
    await expect(form).to_be_visible(timeout=timeout)

    opgave_id_felt = form.locator('input[name="opgaveId"]')
    await expect(opgave_id_felt).to_have_count(1, timeout=timeout)
    formularens_opgave_id = (await opgave_id_felt.input_value()).strip()
    if formularens_opgave_id.casefold() != aktivt_opgave_id.casefold():
        raise RuntimeError("Uploadvinduet tilhører en anden opgave.")

    filinput = form.locator(S.JOURNALNOTAT_UPLOAD_FIL)
    await expect(filinput).to_have_count(1, timeout=timeout)
    await filinput.set_input_files(
        {
            "name": fil["navn"],
            "mimeType": (
                "application/pdf"
                if fil["sti"].suffix.lower() == ".pdf"
                else "application/octet-stream"
            ),
            "buffer": fil["indhold"],
        },
        timeout=timeout,
    )

    sagsfelt = form.locator(S.JOURNALNOTAT_UPLOAD_SAGSINPUT)
    await expect(sagsfelt).to_have_count(1, timeout=timeout)
    await sagsfelt.click(timeout=timeout)

    menu = form.locator(".sagsvaelger-list.dropdown-menu:visible")
    await expect(menu).to_have_count(1, timeout=timeout)
    await expect(menu).to_be_visible(timeout=timeout)

    for tilstand in ("aktiv", "passiv"):
        checkbox = menu.locator(
            f'input[type="checkbox"][data-tilstand="{tilstand}"]'
        )
        if await checkbox.count() == 1 and not await checkbox.is_checked():
            await checkbox.set_checked(True, timeout=timeout)

    sagstabel = menu.locator("table#sagsvaelgertable")
    await expect(sagstabel).to_have_count(1, timeout=timeout)
    await expect(sagstabel).to_be_visible(timeout=timeout)

    # Find rækken uden at interpolere sag-id'et i en CSS-selector.
    raekke_indekser = await sagstabel.locator(
        "tbody tr.table-row[data-id]"
    ).evaluate_all(
        """
        (rows, expectedId) => rows
            .map((row, index) => ({
                index,
                id: String(row.getAttribute('data-id') || '').trim()
            }))
            .filter(item => item.id === expectedId)
            .map(item => item.index)
        """,
        forventet_sag_id,
    )
    if len(raekke_indekser) != 1:
        raise RuntimeError(
            "Uploadvinduet indeholder ikke præcis én sag med det forventede "
            f"tekniske sag-id {forventet_sag_id!r}. "
            f"Antal fundet={len(raekke_indekser)}."
        )

    sagsraekker = sagstabel.locator("tbody tr.table-row[data-id]")
    sagsraekke = sagsraekker.nth(raekke_indekser[0])
    vaelg_celle = sagsraekke.locator("td.select-row")
    await expect(vaelg_celle).to_have_count(1, timeout=timeout)
    await vaelg_celle.scroll_into_view_if_needed()
    await vaelg_celle.click(timeout=timeout)

    await expect(sagsfelt).to_have_value(
        re.compile(r"^\s*1\s+sag\s+valgt\s*$", re.IGNORECASE),
        timeout=timeout,
    )

    # KY har ét selectedSagIds-input pr. sag i listen. Derfor må vi ikke
    # kræve, at der kun findes ét input i formularen. Vi kræver i stedet:
    # 1) Det synlige felt viser "1 sag valgt" (kontrolleret ovenfor).
    # 2) Præcis ét af inputtene har value lig det forventede tekniske sag-id.
    selected_sag_ids = form.locator('input[name="selectedSagIds"]')
    await expect(selected_sag_ids).not_to_have_count(0, timeout=timeout)

    matchende_sag_ider = await selected_sag_ids.evaluate_all(
        """
        (inputs, expectedId) => inputs
            .map((input, index) => ({
                index,
                value: String(input.value || '').trim(),
                disabled: input.disabled
            }))
            .filter(item => item.value === expectedId)
        """,
        forventet_sag_id,
    )

    if len(matchende_sag_ider) != 1:
        raise RuntimeError(
            "Uploadformularen indeholder ikke præcis ét selectedSagIds-input "
            f"med det forventede sag-id {forventet_sag_id!r}. "
            f"Antal fundet={len(matchende_sag_ider)}."
        )

    if matchende_sag_ider[0]["disabled"]:
        raise RuntimeError(
            "selectedSagIds-inputtet med det forventede sag-id er disabled."
        )

    if await menu.is_visible():
        luk_knap = menu.locator("button.close")
        if await luk_knap.count() == 1:
            await luk_knap.click(timeout=timeout)
            await expect(menu).to_be_hidden(timeout=timeout)

    # OuterHTML i KY er:
    # <button class="btn btn-primary" id="uploadFilBtn" type="button">Gem</button>
    gem = form.locator("button#uploadFilBtn")
    await expect(gem).to_have_count(1, timeout=timeout)
    await expect(gem).to_be_visible(timeout=timeout)
    await expect(gem).to_be_enabled(timeout=timeout)
    await gem.scroll_into_view_if_needed(timeout=timeout)

    logger.info(
        "Vedhæfter %s til journalnotatets sag %s og klikker Gem én gang.",
        fil["navn"],
        forventet_sag_id,
    )

    try:
        # Førstevalg: Playwrights normale klik på den konkrete Gem-knap.
        await gem.click(timeout=timeout)
    except PlaywrightError as klikfejl:
        # Backup udføres kun, hvis det normale klik kaster en Playwright-fejl.
        # Der klikkes én gang fysisk på knappens aktuelle midtpunkt.
        bounding_box = await gem.bounding_box()
        if bounding_box is None:
            raise RuntimeError(
                "Gem-knappen er synlig, men dens placering kunne ikke bestemmes."
            ) from klikfejl

        klik_x = bounding_box["x"] + bounding_box["width"] / 2
        klik_y = bounding_box["y"] + bounding_box["height"] / 2
        logger.warning(
            "Normalt klik på Gem fejlede (%s). Forsøger ét fysisk "
            "museklik på knappens midtpunkt x=%.1f, y=%.1f.",
            klikfejl,
            klik_x,
            klik_y,
        )
        await page.mouse.click(klik_x, klik_y)

    deadline = monotonic() + timeout / 1000
    while monotonic() < deadline:
        if page.is_closed():
            raise RuntimeError(
                "Siden lukkede efter upload-Gem. Udfaldet er ukendt; "
                "Gem må ikke klikkes igen automatisk."
            )
        fejl = form.locator(S.JOURNALNOTAT_UPLOAD_FEJL)
        if await fejl.count():
            raise RuntimeError(
                "KY afviste uploaden: "
                + " | ".join(await fejl.all_inner_texts())
            )
        if not await form.is_visible():
            break
        await page.wait_for_timeout(POLL_INTERVAL_MS)
    else:
        raise PlaywrightTimeoutError(
            "Uploadvinduet lukkede ikke efter Gem. Kontrollér KY før genkørsel."
        )

    deadline = monotonic() + timeout / 1000
    while monotonic() < deadline:
        nye_matches = await dokumentraekker.evaluate_all(
            """
            (rows, arg) => rows
                .filter(row =>
                    !arg.ids.includes(row.getAttribute('data-id'))
                    && Array.from(row.cells).some(cell =>
                        (cell.textContent || '').trim() === arg.name
                    )
                )
                .map(row => ({
                    id: row.getAttribute('data-id'),
                    href: row.querySelector(
                        'a[href*="/utilities/hentDokument/"]'
                    )?.getAttribute('href') || ''
                }))
            """,
            {"ids": list(eksisterende_ider), "name": fil["navn"]},
        )
        if len(nye_matches) > 1:
            raise RuntimeError(
                "Flere nye dokumentrækker matcher filnavnet. "
                "Journalnotatet godkendes ikke."
            )
        if len(nye_matches) == 1 and nye_matches[0]["href"]:
            match = nye_matches[0]
            dokument_url = urljoin(page.url, match["href"])
            parsed = urlparse(dokument_url)
            aktiv_url = urlparse(page.url)
            if (parsed.scheme, parsed.netloc) != (
                aktiv_url.scheme,
                aktiv_url.netloc,
            ):
                raise RuntimeError(
                    "Dokumentlinket peger uden for den aktive KY-server."
                )

            forventede_pids = parse_qs(
                urlparse(checkpoint["borger_url"]).query
            ).get("pId", [])
            if forventede_pids and parse_qs(parsed.query).get("pId") != forventede_pids:
                raise RuntimeError("Dokumentlinkets pId matcher ikke borgeren.")

            response = await page.context.request.get(
                dokument_url,
                timeout=timeout,
                max_redirects=0,
            )
            try:
                if response.status != 200:
                    raise RuntimeError(
                        "Dokumentkontrollen fik HTTP-fejl eller redirect."
                    )
                dokument_sha256 = hashlib.sha256(
                    await response.body()
                ).hexdigest()
            finally:
                await response.dispose()

            if dokument_sha256 != fil["sha256"]:
                raise RuntimeError(
                    "Det uploadede dokument matcher ikke kildefilen byte for byte."
                )

            return {
                "dokument_id": str(match["id"] or ""),
                "dokument_url": dokument_url,
                "dokument_sha256": dokument_sha256,
            }

        await page.wait_for_timeout(POLL_INTERVAL_MS)

    raise PlaywrightTimeoutError(
        "Gem blev udført, men den nye dokumentrække kunne ikke verificeres. "
        "Journalnotatet godkendes ikke automatisk."
    )

