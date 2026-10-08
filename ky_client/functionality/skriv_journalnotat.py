"""Udfyld og afslut en allerede åbnet Skriv journalnotat-opgave i KY."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.functionality.borgere import (
    OPGAVE_TIMEOUT_MS,
    OpstartOpgaveCheckpoint,
)
from ky_client.selectors import KYSelectors


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
) -> JournalnotatResultat:
    """Overtag og udfyld journalnotatopgaven fra opstart_opgave.

    Funktionen åbner, genoptager eller opretter aldrig selv en opgave.
    ``checkpoint`` skal være resultatet fra den fælles ``opstart_opgave``.
    ``primaerpart_navn`` bruges til at vælge den entydige sagsrække.
    Ved ``test=True`` klikkes der ikke på Godkend.
    """
    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket før journalnotatet udfyldes."
        )

    _journalnotat_valider_checkpoint(checkpoint)

    udfyldt = await udfyld_aabnet_journalnotat(
        page=page,
        sag=sag,
        primaerpart_navn=primaerpart_navn,
        journalnotatskabelon=journalnotatskabelon,
        aktive=aktive,
        passive=passive,
        timeout=timeout,
    )

    afsluttet = False
    if test:
        print(
            "TESTTILSTAND: Journalnotatet afsluttes ikke.",
            flush=True,
        )
    else:
        await _journalnotat_afslut_opgave(
            page=page,
            timeout=timeout,
        )
        afsluttet = True

    return {
        "opgave_id": checkpoint["opgave_id"],
        "opgave_navn": checkpoint["opgave_navn"],
        "opgave_url": checkpoint["opgave_url"],
        "borger_url": checkpoint["borger_url"],
        "menu_sti": checkpoint["menu_sti"],
        "sag_id": udfyldt["sag_id"],
        "sagstekst": udfyldt["sagstekst"],
        "skabelon_titel": udfyldt["skabelon_titel"],
        "skabelon_noegle": udfyldt["skabelon_noegle"],
        "genoptaget": bool(checkpoint.get("genoptaget", False)),
        "kilde": str(checkpoint.get("kilde", "ny_opgave")),
        "test": test,
        "afsluttet": afsluttet,
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
) -> UdfyldtJournalnotat:
    """Udfyld en journalnotatopgave, som allerede er åbnet."""
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
    primaerpart_navn = _journalnotat_paakraevet_tekst(
        str(primaerpart_navn or ""),
        "primaerpart_navn",
    )

    await _journalnotat_vent_paa_formular(
        page=page,
        timeout=timeout,
    )
    await _journalnotat_nulstil_skabelon(page=page)

    valgt_sag = await _journalnotat_vaelg_sag(
        page=page,
        sag=sag,
        primaerpart_navn=primaerpart_navn,
        aktive=aktive,
        passive=passive,
        timeout=timeout,
    )
    valgt_skabelon = await _journalnotat_vaelg_skabelon(
        page=page,
        skabelon_titel=journalnotatskabelon,
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
    primaerpart_navn: str,
    aktive: bool,
    passive: bool,
    timeout: int,
) -> dict[str, str]:
    """Vælg én sag, hvor søgetekst og primærpart matcher."""
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
        selector=(
            KYSelectors.Borgere.JOURNALNOTAT_AKTIV_CHECKBOX
        ),
        checked=aktive,
        feltnavn="Aktive sager",
        timeout=timeout,
    )
    await _journalnotat_saet_checkbox(
        page=page,
        selector=(
            KYSelectors.Borgere.JOURNALNOTAT_PASSIV_CHECKBOX
        ),
        checked=passive,
        feltnavn="Passive sager",
        timeout=timeout,
    )

    search = page.locator(
        f"{KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG}:visible"
    ).last
    await search.wait_for(
        state="visible",
        timeout=timeout,
    )
    await search.fill(sag)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")

    row = await _journalnotat_find_entydig_sag(
        page=page,
        sag=sag,
        primaerpart_navn=primaerpart_navn,
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
    await row.click(
        timeout=min(ACTION_TIMEOUT_MS, timeout)
    )

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
    primaerpart_navn: str,
    timeout: int,
) -> Locator:
    """Find præcis én aktiv sag med korrekt SagsID og primærpart.

    Kun rækker med data-tilstand="aktiv" kan vælges.

    ``sag`` matches mod kolonnen SagsID.
    ``primaerpart_navn`` matches mod navnene i kolonnen
    Udb. modtager / Primærpart.

    Funktionen returnerer rækkens Locator, når præcis én aktiv række
    matcher. Funktionen stopper sikkert, hvis ingen eller flere rækker
    matcher.
    """
    wanted_sag = _journalnotat_normaliser_tekst(
        sag
    ).casefold()

    wanted_name = _journalnotat_normaliser_navn(
        primaerpart_navn
    )

    if not wanted_sag:
        raise ValueError(
            "sag må ikke være tom."
        )

    if not wanted_name:
        raise ValueError(
            "primaerpart_navn må ikke være tomt."
        )

    table = page.locator(
        "#sagsvaelgertable:visible"
    ).last

    await table.wait_for(
        state="visible",
        timeout=timeout,
    )

    header_cells = table.locator(
        "thead th"
    )

    headers = [
        _journalnotat_normaliser_overskrift(
            await header_cells.nth(index).inner_text()
        )
        for index in range(
            await header_cells.count()
        )
    ]

    sagsid_indexes = [
        index
        for index, header in enumerate(headers)
        if header == "sagsid"
    ]

    primaerpart_indexes = [
        index
        for index, header in enumerate(headers)
        if (
            "udb. modtager" in header
            and "primaerpart" in header
        )
    ]

    if len(sagsid_indexes) != 1:
        raise RuntimeError(
            "Journalnotatets sagstabel mangler en entydig "
            "kolonne med overskriften 'SagsID'. "
            f"Fundne kolonner={headers!r}."
        )

    if len(primaerpart_indexes) != 1:
        raise RuntimeError(
            "Journalnotatets sagstabel mangler en entydig kolonne "
            "'Udb. modtager / Primærpart'. "
            f"Fundne kolonner={headers!r}."
        )

    sagsid_index = sagsid_indexes[0]
    primaerpart_index = primaerpart_indexes[0]

    elapsed_ms = 0
    diagnostic: list[dict[str, str]] = []

    while elapsed_ms < timeout:
        # VIGTIGT:
        # Passive rækker ignoreres altid, også hvis checkboxen
        # "Passive" ved en fejl er markeret.
        rows = table.locator(
            "tbody tr.table-row"
            '[data-tilstand="aktiv"]:visible'
        )

        matches: list[Locator] = []
        diagnostic = []

        for index in range(
            await rows.count()
        ):
            row = rows.nth(index)

            try:
                if not await row.is_visible():
                    continue

                cells = row.locator("td")

                required_cell_count = max(
                    sagsid_index,
                    primaerpart_index,
                ) + 1

                if (
                    await cells.count()
                    < required_cell_count
                ):
                    raise RuntimeError(
                        "En aktiv sagsrække mangler enten "
                        "SagsID-cellen eller cellen for "
                        "'Udb. modtager / Primærpart'."
                    )

                sagsid_text = (
                    _journalnotat_normaliser_tekst(
                        await cells.nth(
                            sagsid_index
                        ).inner_text()
                    )
                )

                primaerpart_text = (
                    _journalnotat_normaliser_tekst(
                        await cells.nth(
                            primaerpart_index
                        ).inner_text()
                    )
                )

                diagnostic.append(
                    {
                        "sagsid": sagsid_text,
                        "tilstand": (
                            await row.get_attribute(
                                "data-tilstand"
                            )
                            or ""
                        ),
                        "primaerpart": primaerpart_text,
                    }
                )

                # SagsID-feltet kan eksempelvis indeholde
                # "HTF-XXXXXX", mens søgeværdien er "HTF".
                if (
                    wanted_sag
                    not in sagsid_text.casefold()
                ):
                    continue

                # Kolonnen indeholder typisk:
                #
                #   Udbetalingsmodtager / Primærpart
                #
                # Begge sider normaliseres og sammenlignes
                # separat med Navn fra Personoplysninger.
                primaerpart_names = [
                    _journalnotat_normaliser_navn(
                        value
                    )
                    for value in primaerpart_text.split("/")
                    if (
                        _journalnotat_normaliser_navn(
                            value
                        )
                        and (
                            _journalnotat_normaliser_navn(
                                value
                            )
                            != "-"
                        )
                    )
                ]

                if wanted_name not in primaerpart_names:
                    continue

                matches.append(row)

            except RuntimeError:
                raise
            except Exception:
                # En midlertidigt ustabil DOM-række må ikke
                # føre til et forkert valg. Rækken ignoreres,
                # og tabellen undersøges igen i næste loop.
                continue

        if len(matches) == 1:
            selected_row = matches[0]

            selected_state = (
                await selected_row.get_attribute(
                    "data-tilstand"
                )
                or ""
            ).strip().casefold()

            if selected_state != "aktiv":
                raise RuntimeError(
                    "Den fundne sagsrække er ikke aktiv. "
                    "Ingen sag vælges."
                )

            return selected_row

        if len(matches) > 1:
            raise RuntimeError(
                "Flere aktive sager matcher både "
                f"SagsID-søgningen {sag!r} og Navn fra "
                "Personoplysninger. Ingen sag vælges. "
                f"Matchende aktive rækker={diagnostic!r}."
            )

        await page.wait_for_timeout(
            POLL_INTERVAL_MS
        )
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Ingen aktiv sag matchede både "
        f"SagsID-søgningen {sag!r} og Navn fra "
        "Personoplysninger. "
        f"Synlige aktive kandidater={diagnostic!r}."
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


def _journalnotat_normaliser_navn(
    value: object,
) -> str:
    """Normalisér navn til en sikker sammenligning."""
    resultat = _journalnotat_normaliser_tekst(value)
    resultat = re.sub(
        r"\s*-\s*$",
        "",
        resultat,
    ).strip()
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
