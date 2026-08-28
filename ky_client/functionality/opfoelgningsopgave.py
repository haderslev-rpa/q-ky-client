"""Udfyld og afslut en allerede åbnet KY-opfølgningsopgave.

Dette modul åbner eller genoptager ikke opgaver. Kald først den fælles
``opstart_opgave`` fra ``ky_client.functionality.borgere``. Derefter overtager
``opret_opfoelgningsopgave`` den formular, som allerede er åben.

Eksempel::

    checkpoint = await opstart_opgave(
        page=page,
        menu_sti=("Administration", "Opret opfølgningsopgave"),
        item_data=item_data,
        opgave_id=tidligere_opgave_id,
        timeout=120_000,
    )

    resultat = await opret_opfoelgningsopgave(
        page=page,
        checkpoint=checkpoint,
        opfoelgningstype="Brugerdefineret",
        opfoelgningsdato="01-09-2026",
        sagsbehandler="Navn",
        titel="Kontrol",
        frekvens="En gang",
        beskrivelse="Følg op på sagen.",
        test=True,
    )

Sikker standard:
    ``test=True`` udfylder og validerer formularen, men klikker ikke på Gem.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.functionality.borgere import (
    ACTION_TIMEOUT_MS,
    OPGAVE_TIMEOUT_MS,
    POLL_INTERVAL_MS,
)


FORVENTET_OPGAVENAVN = "Opret opfølgningsopgave"
FORVENTET_MENU_STI = ("Administration", FORVENTET_OPGAVENAVN)


class OpfoelgningsopgaveCheckpoint(TypedDict, total=False):
    """Checkpoint fra den fælles ``opstart_opgave``."""

    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]
    genoptaget: bool
    kilde: str


class OpfoelgningsopgaveResultat(TypedDict):
    """Resultat efter udfyldning eller gemning af opfølgningsopgaven."""

    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]
    genoptaget: bool
    kilde: str
    opfoelgningstype: str
    opfoelgningsdato: str
    sagsbehandler: str
    titel: str | None
    frekvens: str | None
    haendelsestype: str | None
    beskrivelse: str | None
    test: bool
    gemt: bool


async def opret_opfoelgningsopgave(
    page: Page,
    checkpoint: OpfoelgningsopgaveCheckpoint | dict[str, Any],
    opfoelgningstype: str,
    opfoelgningsdato: str,
    sagsbehandler: str,
    titel: str | None = None,
    frekvens: str | None = None,
    haendelsestype: str | None = None,
    beskrivelse: str | None = None,
    vaelg_sagsbehandler_fra_typeahead: bool = False,
    test: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpfoelgningsopgaveResultat:
    """Overtag og udfyld en allerede åbnet opfølgningsopgave.

    ``checkpoint`` skal være resultatet fra den fælles ``opstart_opgave``.
    Funktionen er uafhængig af, om checkpointet repræsenterer en genoptaget
    ubehandlet opgave eller en nyoprettet opgave.

    Når ``test=True``, udfyldes og valideres formularen uden klik på Gem.
    """

    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket. Opfølgningsopgaven kan ikke udfyldes."
        )

    valideret_checkpoint = _valider_checkpoint(checkpoint)

    opfoelgningstype = _paakraevet_tekst(
        opfoelgningstype,
        "opfoelgningstype",
    )
    opfoelgningsdato = _paakraevet_tekst(
        opfoelgningsdato,
        "opfoelgningsdato",
    )
    sagsbehandler = _paakraevet_tekst(
        sagsbehandler,
        "sagsbehandler",
    )
    titel = _valgfri_tekst(titel)
    frekvens = _valgfri_tekst(frekvens)
    haendelsestype = _valgfri_tekst(haendelsestype)
    beskrivelse = _valgfri_tekst(beskrivelse)

    _print_start(
        checkpoint=valideret_checkpoint,
        opfoelgningstype=opfoelgningstype,
        opfoelgningsdato=opfoelgningsdato,
        sagsbehandler=sagsbehandler,
        titel=titel,
        frekvens=frekvens,
        haendelsestype=haendelsestype,
        beskrivelse=beskrivelse,
        test=test,
    )

    await _vent_paa_opgaveloader(page=page, timeout=timeout)
    await _vent_paa_formular(page=page, timeout=timeout)

    await _vaelg_option_via_value_eller_label(
        page=page,
        selector="select#opfoelgningsType",
        option=opfoelgningstype,
        feltnavn="Opfølgningstype",
        timeout=timeout,
    )

    # KY kan genopbygge formularen efter skift af opfølgningstype.
    await page.wait_for_timeout(1_000)
    await _vent_paa_opgaveloader(page=page, timeout=timeout)

    await _udfyld_felt(
        page=page,
        selector="input#command\\.opfoelgningsdato",
        value=opfoelgningsdato,
        feltnavn="Opfølgningsdato",
        timeout=timeout,
    )

    await _udfyld_sagsbehandler(
        page=page,
        sagsbehandler=sagsbehandler,
        vaelg_forslag=vaelg_sagsbehandler_fra_typeahead,
        timeout=timeout,
    )

    brugerdefineret = await _er_brugerdefineret(
        page=page,
        timeout=timeout,
    )

    if brugerdefineret:
        _valider_brugerdefinerede_input(
            titel=titel,
            frekvens=frekvens,
            beskrivelse=beskrivelse,
        )

        assert titel is not None
        assert frekvens is not None
        assert beskrivelse is not None

        await _vaelg_option_via_value_eller_label(
            page=page,
            selector="select#frekvens",
            option=frekvens,
            feltnavn="Frekvens",
            timeout=timeout,
        )

        if haendelsestype:
            await _vaelg_option_via_value_eller_label(
                page=page,
                selector="select#haendelseType",
                option=haendelsestype,
                feltnavn="Hændelsestype",
                timeout=timeout,
            )

        await page.wait_for_timeout(1_000)
        await _vent_paa_opgaveloader(page=page, timeout=timeout)

        await _udfyld_felt(
            page=page,
            selector="input[name='title']",
            value=titel,
            feltnavn="Titel",
            timeout=timeout,
        )
        await _udfyld_felt(
            page=page,
            selector="textarea[name='beskrivelse']",
            value=beskrivelse,
            feltnavn="Beskrivelse",
            timeout=timeout,
        )

        await page.wait_for_timeout(750)
        await _kontroller_felt(
            page=page,
            selector="input[name='title']",
            forventet=titel,
            feltnavn="Titel",
            timeout=timeout,
        )
        await _kontroller_felt(
            page=page,
            selector="textarea[name='beskrivelse']",
            forventet=beskrivelse,
            feltnavn="Beskrivelse",
            timeout=timeout,
        )
    elif any(
        value is not None
        for value in (titel, frekvens, haendelsestype, beskrivelse)
    ):
        raise ValueError(
            "titel, frekvens, haendelsestype og beskrivelse må kun angives, "
            "når opfølgningstypen er Brugerdefineret."
        )

    gemt = False
    if test:
        print()
        print("=" * 70)
        print("TESTTILSTAND: OPFØLGNINGSOPGAVEN ER UDFYLDT")
        print("test=True, så der klikkes ikke på Gem.")
        print("=" * 70)
    else:
        gem = await _find_entydig_knap(
            page=page,
            text="Gem",
            timeout=timeout,
        )
        await gem.scroll_into_view_if_needed()
        await gem.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
        await _vent_paa_gemt(
            page=page,
            gem=gem,
            timeout=timeout,
        )
        gemt = True

    resultat: OpfoelgningsopgaveResultat = {
        "opgave_id": valideret_checkpoint["opgave_id"],
        "opgave_navn": valideret_checkpoint["opgave_navn"],
        "opgave_url": valideret_checkpoint["opgave_url"],
        "borger_url": valideret_checkpoint["borger_url"],
        "menu_sti": valideret_checkpoint["menu_sti"],
        "genoptaget": valideret_checkpoint["genoptaget"],
        "kilde": valideret_checkpoint["kilde"],
        "opfoelgningstype": opfoelgningstype,
        "opfoelgningsdato": opfoelgningsdato,
        "sagsbehandler": sagsbehandler,
        "titel": titel,
        "frekvens": frekvens,
        "haendelsestype": haendelsestype,
        "beskrivelse": beskrivelse,
        "test": test,
        "gemt": gemt,
    }

    print()
    print("=" * 70)
    print("OPFØLGNINGSOPGAVEN ER BEHANDLET")
    print(f"Opgave-id: {resultat['opgave_id']}")
    print(f"Genoptaget: {resultat['genoptaget']}")
    print(f"Kilde: {resultat['kilde']}")
    print(f"Test: {resultat['test']}")
    print(f"Gemt: {resultat['gemt']}")
    print("=" * 70)

    return resultat


def _valider_checkpoint(
    checkpoint: OpfoelgningsopgaveCheckpoint | dict[str, Any],
) -> OpfoelgningsopgaveCheckpoint:
    """Validér checkpointet fra ``opstart_opgave``."""

    if not isinstance(checkpoint, dict):
        raise TypeError("checkpoint skal være en dictionary.")

    opgave_id = _normaliser_tekst(str(checkpoint.get("opgave_id") or ""))
    opgave_navn = _normaliser_tekst(
        str(checkpoint.get("opgave_navn") or "")
    )
    opgave_url = _normaliser_tekst(str(checkpoint.get("opgave_url") or ""))
    borger_url = _normaliser_tekst(str(checkpoint.get("borger_url") or ""))
    menu_sti = tuple(
        _normaliser_tekst(str(delnavn))
        for delnavn in checkpoint.get("menu_sti", ())
        if _normaliser_tekst(str(delnavn))
    )
    genoptaget = bool(checkpoint.get("genoptaget", False))
    kilde = _normaliser_tekst(str(checkpoint.get("kilde") or ""))

    if not opgave_id:
        raise ValueError("Checkpointet mangler opgave_id.")
    if not opgave_url:
        raise ValueError("Checkpointet mangler opgave_url.")
    if not borger_url:
        raise ValueError("Checkpointet mangler borger_url.")
    if opgave_navn.casefold() != FORVENTET_OPGAVENAVN.casefold():
        raise ValueError(
            "Checkpointet tilhører ikke Opret opfølgningsopgave. "
            f"Opgavenavn={opgave_navn!r}."
        )
    if tuple(delnavn.casefold() for delnavn in menu_sti) != tuple(
        delnavn.casefold() for delnavn in FORVENTET_MENU_STI
    ):
        raise ValueError(
            "Checkpointets menu_sti matcher ikke Opret opfølgningsopgave. "
            f"menu_sti={menu_sti!r}."
        )

    if not kilde:
        kilde = "ubehandlede_opgaver" if genoptaget else "ny_opgave"

    expected_source = "ubehandlede_opgaver" if genoptaget else "ny_opgave"
    if kilde != expected_source:
        raise ValueError(
            "Checkpointets genoptaget-flag og kilde er inkonsistente. "
            f"genoptaget={genoptaget}, kilde={kilde!r}."
        )

    return {
        "opgave_id": opgave_id,
        "opgave_navn": opgave_navn,
        "opgave_url": opgave_url,
        "borger_url": borger_url,
        "menu_sti": menu_sti,
        "genoptaget": genoptaget,
        "kilde": kilde,
    }


async def _vent_paa_formular(page: Page, timeout: int) -> None:
    """Vent på opfølgningsopgavens centrale formularfelter."""

    opfoelgningstype = page.locator("select#opfoelgningsType:visible").last
    dato = page.locator("input#command\\.opfoelgningsdato:visible").last
    sagsbehandler = page.locator("input#typeahead:visible").last

    await opfoelgningstype.wait_for(state="visible", timeout=timeout)
    await dato.wait_for(state="visible", timeout=timeout)
    await sagsbehandler.wait_for(state="visible", timeout=timeout)


async def _vent_paa_opgaveloader(page: Page, timeout: int) -> None:
    """Vent på, at kendte opgaveloadere er skjult eller fjernet."""

    await page.wait_for_function(
        """
        () => {
            const selectors = [
                '#empty_opgave_loader',
                '#opgave_loader',
                '#opgave-loader'
            ];
            return selectors.every(selector => {
                const loader = document.querySelector(selector);
                if (!loader) return true;
                const style = window.getComputedStyle(loader);
                return style.display === 'none'
                    || style.visibility === 'hidden'
                    || style.opacity === '0'
                    || loader.offsetParent === null;
            });
        }
        """,
        timeout=timeout,
    )


async def _vaelg_option_via_value_eller_label(
    page: Page,
    selector: str,
    option: str,
    feltnavn: str,
    timeout: int,
) -> str:
    """Vælg en option via eksakt value eller synlig label."""

    selects = page.locator(f"{selector}:visible")
    if await selects.count() == 0:
        raise RuntimeError(
            f"Et synligt selectfelt til {feltnavn} blev ikke fundet. "
            f"Selector={selector!r}."
        )

    select = selects.last
    await select.wait_for(state="visible", timeout=timeout)
    available: list[str] = []
    selected_value: str | None = None
    options = select.locator("option")

    for index in range(await options.count()):
        current = options.nth(index)
        value = _normaliser_tekst(
            await current.get_attribute("value") or ""
        )
        label = _normaliser_tekst(await current.inner_text())
        available.append(f"{label} ({value})")

        if (
            value.casefold() == option.casefold()
            or label.casefold() == option.casefold()
        ):
            selected_value = value
            break

    if selected_value is None:
        raise ValueError(
            f"Kunne ikke finde {option!r} i {feltnavn}. "
            f"Muligheder: {available}"
        )

    await select.select_option(value=selected_value)
    await select.dispatch_event("input")
    await select.dispatch_event("change")

    actual = await select.input_value()
    if actual != selected_value:
        raise RuntimeError(
            f"{feltnavn} blev ikke valgt korrekt. "
            f"Forventet={selected_value!r}, faktisk={actual!r}."
        )

    return selected_value


async def _udfyld_felt(
    page: Page,
    selector: str,
    value: str,
    feltnavn: str,
    timeout: int,
) -> None:
    """Udfyld og kontrollér den seneste synlige feltinstans."""

    fields = page.locator(f"{selector}:visible")
    if await fields.count() == 0:
        raise RuntimeError(
            f"Kunne ikke finde et synligt felt til {feltnavn}. "
            f"Selector={selector!r}."
        )

    field = fields.last
    await field.wait_for(state="visible", timeout=timeout)
    await field.scroll_into_view_if_needed()
    await field.fill(value)
    await field.dispatch_event("input")
    await field.dispatch_event("change")

    actual = await field.input_value()
    if actual.strip() != value.strip():
        raise RuntimeError(
            f"{feltnavn} kunne ikke udfyldes. "
            f"Forventet={value!r}, faktisk={actual!r}."
        )


async def _kontroller_felt(
    page: Page,
    selector: str,
    forventet: str,
    feltnavn: str,
    timeout: int,
) -> None:
    """Kontrollér, at et felt ikke er blevet nulstillet."""

    fields = page.locator(f"{selector}:visible")
    if await fields.count() == 0:
        raise RuntimeError(
            f"{feltnavn} forsvandt fra formularen. Selector={selector!r}."
        )

    field = fields.last
    await field.wait_for(state="visible", timeout=timeout)
    faktisk = await field.input_value()

    if faktisk.strip() != forventet.strip():
        raise RuntimeError(
            f"{feltnavn} blev nulstillet. "
            f"Forventet={forventet!r}, faktisk={faktisk!r}."
        )


async def _udfyld_sagsbehandler(
    page: Page,
    sagsbehandler: str,
    vaelg_forslag: bool,
    timeout: int,
) -> None:
    """Indsæt sagsbehandler og vælg valgfrit et entydigt forslag."""

    fields = page.locator("input#typeahead:visible")
    if await fields.count() == 0:
        raise RuntimeError("Et synligt sagsbehandlerfelt blev ikke fundet.")

    field = fields.last
    await field.wait_for(state="visible", timeout=timeout)
    await field.fill(sagsbehandler)
    await field.dispatch_event("input")
    await field.dispatch_event("change")

    if not vaelg_forslag:
        faktisk = await field.input_value()
        if faktisk.strip() != sagsbehandler.strip():
            raise RuntimeError(
                "Sagsbehandlerteksten blev ikke indsat korrekt. "
                f"Forventet={sagsbehandler!r}, faktisk={faktisk!r}."
            )
        return

    wanted = sagsbehandler.casefold()
    elapsed_ms = 0

    while elapsed_ms < timeout:
        suggestions = page.locator(
            ".tt-menu:visible .tt-suggestion.tt-selectable"
        )
        exact: list[Locator] = []
        contains: list[Locator] = []

        for index in range(await suggestions.count()):
            suggestion = suggestions.nth(index)
            try:
                if not await suggestion.is_visible():
                    continue
                text = _normaliser_tekst(await suggestion.inner_text())
                if text.casefold() == wanted:
                    exact.append(suggestion)
                elif wanted in text.casefold():
                    contains.append(suggestion)
            except Exception:
                continue

        matches = exact or contains
        if len(matches) == 1:
            await matches[0].click(
                timeout=min(ACTION_TIMEOUT_MS, timeout)
            )
            return
        if len(matches) > 1:
            raise RuntimeError(
                f"Flere sagsbehandlere matcher {sagsbehandler!r}."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsbehandleren blev ikke fundet i typeahead-listen: "
        f"{sagsbehandler}."
    )


async def _er_brugerdefineret(page: Page, timeout: int) -> bool:
    """Kontrollér, om den valgte opfølgningstype er Brugerdefineret."""

    selected = page.locator(
        "select#opfoelgningsType:visible option:checked"
    ).last
    await selected.wait_for(state="attached", timeout=timeout)

    value = _normaliser_tekst(
        await selected.get_attribute("value") or ""
    ).casefold()
    label = _normaliser_tekst(await selected.inner_text()).casefold()

    return value == "manuel" or label == "brugerdefineret"


def _valider_brugerdefinerede_input(
    titel: str | None,
    frekvens: str | None,
    beskrivelse: str | None,
) -> None:
    """Kræv de felter, som Brugerdefineret behøver."""

    missing: list[str] = []
    if not titel:
        missing.append("titel")
    if not frekvens:
        missing.append("frekvens")
    if not beskrivelse:
        missing.append("beskrivelse")

    if missing:
        raise ValueError(
            "Brugerdefineret kræver følgende felter: "
            f"{', '.join(missing)}."
        )


async def _find_entydig_knap(
    page: Page,
    text: str,
    timeout: int,
) -> Locator:
    """Find én synlig og aktiv submitknap med eksakt tekst."""

    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    elapsed_ms = 0

    while elapsed_ms < timeout:
        candidates = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button.btn-submit-form, a.btn-submit-form"
        )
        matches: list[Locator] = []

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                if not await candidate.is_enabled():
                    continue
                value = await candidate.get_attribute("value")
                visible_text = _normaliser_tekst(
                    value or await candidate.inner_text()
                )
                if pattern.fullmatch(visible_text):
                    matches.append(candidate)
            except Exception:
                continue

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"Flere aktive knapper matcher teksten {text!r}."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        f"Knappen {text!r} blev ikke fundet inden for "
        f"{timeout / 1000:.0f} sekunder."
    )


async def _vent_paa_gemt(
    page: Page,
    gem: Locator,
    timeout: int,
) -> None:
    """Vent på lukket formular eller vis en valideringsfejl."""

    await _vent_paa_opgaveloader(page=page, timeout=timeout)

    try:
        await gem.wait_for(
            state="hidden",
            timeout=min(30_000, timeout),
        )
    except PlaywrightTimeoutError as error:
        validation = await _hent_synlig_validering(page)
        raise RuntimeError(
            "Opfølgningsopgaven blev ikke gemt. "
            f"Synlig validering: {validation or 'ukendt fejl'}"
        ) from error


async def _hent_synlig_validering(page: Page) -> str:
    """Returnér synlige valideringsbeskeder fra formularen."""

    messages = page.locator(
        ".has-error:visible, .help-block:visible, "
        ".alert-danger:visible, .field-validation-error:visible"
    )
    values: list[str] = []

    for index in range(await messages.count()):
        try:
            text = _normaliser_tekst(await messages.nth(index).inner_text())
        except Exception:
            continue
        if text and text not in values:
            values.append(text)

    return " | ".join(values)


def _paakraevet_tekst(value: str, feltnavn: str) -> str:
    """Trim et obligatorisk input og afvis tomme værdier."""

    normaliseret = _normaliser_tekst(str(value or ""))
    if not normaliseret:
        raise ValueError(f"{feltnavn} må ikke være tom.")
    return normaliseret


def _valgfri_tekst(value: str | None) -> str | None:
    """Trim et valgfrit input og returnér None for tom tekst."""

    if value is None:
        return None
    normaliseret = _normaliser_tekst(str(value))
    return normaliseret or None


def _normaliser_tekst(value: str) -> str:
    """Saml whitespace og trim tekst."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def _print_start(
    checkpoint: OpfoelgningsopgaveCheckpoint,
    opfoelgningstype: str,
    opfoelgningsdato: str,
    sagsbehandler: str,
    titel: str | None,
    frekvens: str | None,
    haendelsestype: str | None,
    beskrivelse: str | None,
    test: bool,
) -> None:
    """Print checkpoint og funktionsinput."""

    print()
    print("=" * 70)
    print("OVERTAGER ÅBNET OPFØLGNINGSOPGAVE")
    print(f"Opgave-id: {checkpoint['opgave_id']}")
    print(f"Genoptaget: {checkpoint['genoptaget']}")
    print(f"Kilde: {checkpoint['kilde']}")
    print(f"Opfølgningstype: {opfoelgningstype!r}")
    print(f"Opfølgningsdato: {opfoelgningsdato!r}")
    print(f"Sagsbehandler: {sagsbehandler!r}")
    print(f"Titel: {titel!r}")
    print(f"Frekvens: {frekvens!r}")
    print(f"Hændelsestype: {haendelsestype!r}")
    print(f"Beskrivelse: {beskrivelse!r}")
    print(f"test: {test}")
    print("=" * 70)
