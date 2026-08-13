"""Manuel test af oprettelse af opfølgningsopgave i KY.

Testen spørger kun efter CPR, inden browseren og KY startes. De øvrige
værdier er faste testværdier øverst i filen.

Kør:
    uv run python test_opfoelgningsopgave.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from playwright.async_api import Locator, Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import has_jsessionid, is_ky_url, launch_ky
from ky_client.selectors import KYSelectors

logger = logging.getLogger(__name__)

KY_CREDENTIAL_NAME = "DIRXFLX"
ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250

# ---------------------------------------------------------------------------
# FASTE TESTINPUTS TIL OPFØLGNINGSOPGAVEN
# ---------------------------------------------------------------------------

OPFOELGNINGSTYPE = "Brugerdefineret"
OPFOELGNINGSDATO = "01-09-2026"
SAGSBEHANDLER = "Sagsbehandler navn"
TITEL = "Manuel behandling af køretøj"
FREKVENS = "Aldrig"
HAENDELSESTYPE = "Skriv journalnotat"
BESKRIVELSE = "Sagen kræver manuel behandling."

# Journalnotat udfyldes ikke i denne første test.
# Journalnotat-panelet bliver derfor ikke udvidet.


def get_headless_flag() -> bool:
    """Skriv HEADLESS=false i .env for at se browseren."""

    return os.getenv("HEADLESS", "false").strip().casefold() == "true"


def main() -> None:
    """Spørg efter CPR før launch og start derefter den asynkrone test."""

    cpr = _normaliser_cpr(input("Indtast CPR-nummer: "))

    print()
    print("=" * 70)
    print("TEST AF OPFØLGNINGSOPGAVE")
    print(f"CPR: {_masker_cpr(cpr)}")
    print(f"Opfølgningstype: {OPFOELGNINGSTYPE}")
    print(f"Opfølgningsdato: {OPFOELGNINGSDATO}")
    print(f"Sagsbehandler: {SAGSBEHANDLER}")
    print(f"Titel: {TITEL}")
    print(f"Frekvens: {FREKVENS}")
    print(f"Hændelsestype: {HAENDELSESTYPE}")
    print(f"Beskrivelse: {BESKRIVELSE}")
    print("=" * 70)

    # CPR er læst og valideret, før BrowserSession og KY startes.
    asyncio.run(run_test(cpr))


async def run_test(cpr: str) -> None:
    """Launch KY, fremsøg borgeren og opret opfølgningsopgaven."""

    session = BrowserSession(
        headless=get_headless_flag(),
        debug=True,
        video=False,
    )

    await session.start()
    page = await session.new_page()

    try:
        await launch_ky(
            page=page,
            session=session,
            credential_name=KY_CREDENTIAL_NAME,
        )

        if not is_ky_url(page) or not await has_jsessionid(page):
            raise RuntimeError(
                "KY-sessionen er ikke gyldig efter launch. "
                f"Aktuel URL: {page.url}"
            )

        logger.info("KY er launchet: %s", page.url)

        await _soeg_borger(page=page, cpr=cpr)

        logger.info(
            "Borgeren er fremsøgt: %s",
            _masker_cpr(cpr),
        )

        await session.screenshot(
            page=page,
            name="TEST_01_borger_fremsoegt",
            always=True,
        )

        await _opret_opfoelgningsopgave(
            page=page,
            session=session,
            opfoelgningstype=OPFOELGNINGSTYPE,
            opfoelgningsdato=OPFOELGNINGSDATO,
            sagsbehandler=SAGSBEHANDLER,
            titel=TITEL,
            frekvens=FREKVENS,
            haendelsestype=HAENDELSESTYPE,
            beskrivelse=BESKRIVELSE,
        )

        print()
        print("=" * 70)
        print("OPFØLGNINGSOPGAVEN ER GEMT")
        print(f"CPR: {_masker_cpr(cpr)}")
        print(f"Titel: {TITEL}")
        print("=" * 70)

        await session.screenshot(
            page=page,
            name="TEST_06_opfoelgningsopgave_gemt",
            always=True,
        )

        if not get_headless_flag():
            print("Browseren holdes åben i 10 sekunder.")
            await page.wait_for_timeout(10_000)

    finally:
        await session.close()


async def _soeg_borger(page: Page, cpr: str) -> None:
    """Fremsøg borgeren via KY's globale søgefelt."""

    search_input = page.locator(KYSelectors.Main.TOP_SEARCH).first
    await search_input.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )
    await search_input.fill(cpr)
    await search_input.press("Enter")

    await page.locator(
        KYSelectors.Borgere.PERSON_OPLYSNINGER
    ).first.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

async def _udfyld_tekstfelt(
    page: Page,
    selector: str,
    value: str,
    field_name: str,
) -> None:
    """Udfyld den aktuelle synlige formularinstans robust."""

    fields = page.locator(
        f"{selector}:visible"
    )

    field_count = await fields.count()

    print(
        f"{field_name}: fandt {field_count} "
        "synlige kandidatfelter"
    )

    if field_count == 0:
        raise RuntimeError(
            f"Kunne ikke finde et synligt felt til "
            f"{field_name}. Selector={selector!r}"
        )

    # KY kan genopbygge formularen. Brug derfor den senest
    # oprettede synlige formularinstans.
    field = fields.last

    await field.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await field.scroll_into_view_if_needed()
    await field.click(timeout=ACTION_TIMEOUT_MS)

    # Forsøg 1: almindelig Playwright-fill.
    await field.fill(value)

    actual_value = await field.input_value()

    # Forsøg 2: tast teksten som egentlige tastetryk.
    if actual_value.strip() != value.strip():
        await field.press("Control+A")
        await field.press("Backspace")

        await field.press_sequentially(
            value,
            delay=20,
        )

        actual_value = await field.input_value()

    # Forsøg 3: brug browserens native value-setter.
    if actual_value.strip() != value.strip():
        await field.evaluate(
            """
            (element, newValue) => {
                const prototype =
                    element instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;

                const descriptor =
                    Object.getOwnPropertyDescriptor(
                        prototype,
                        "value"
                    );

                if (!descriptor || !descriptor.set) {
                    throw new Error(
                        "Native value-setter blev ikke fundet"
                    );
                }

                descriptor.set.call(
                    element,
                    newValue
                );

                element.dispatchEvent(
                    new InputEvent(
                        "input",
                        {
                            bubbles: true,
                            inputType: "insertText",
                            data: newValue
                        }
                    )
                );

                element.dispatchEvent(
                    new Event(
                        "change",
                        {
                            bubbles: true
                        }
                    )
                );

                element.dispatchEvent(
                    new FocusEvent(
                        "blur",
                        {
                            bubbles: true
                        }
                    )
                );
            }
            """,
            value,
        )

        actual_value = await field.input_value()

    print(
        f"{field_name}: "
        f"forventet={value!r}, "
        f"faktisk={actual_value!r}"
    )

    if actual_value.strip() != value.strip():
        outer_html = await field.evaluate(
            "(element) => element.outerHTML"
        )

        raise RuntimeError(
            f"{field_name} kunne ikke udfyldes. "
            f"Forventet={value!r}, "
            f"faktisk={actual_value!r}. "
            f"Element={outer_html}"
        )

async def _opret_opfoelgningsopgave(
    page: Page,
    session: BrowserSession,
    opfoelgningstype: str,
    opfoelgningsdato: str,
    sagsbehandler: str,
    titel: str,
    frekvens: str,
    beskrivelse: str,
    haendelsestype: str | None = None,
) -> None:
    """Opret opfølgningsopgave på den allerede fremsøgte borger."""

    opfoelgningstype = opfoelgningstype.strip()
    opfoelgningsdato = opfoelgningsdato.strip()
    sagsbehandler = sagsbehandler.strip()
    titel = titel.strip()
    frekvens = frekvens.strip()
    beskrivelse = beskrivelse.strip()

    if haendelsestype:
        haendelsestype = haendelsestype.strip()

    if not opfoelgningstype:
        raise ValueError(
            "Opfølgningstype må ikke være tom."
        )

    if not opfoelgningsdato:
        raise ValueError(
            "Opfølgningsdato må ikke være tom."
        )

    if not sagsbehandler:
        raise ValueError(
            "Sagsbehandler må ikke være tom."
        )

    print()
    print("=" * 70)
    print("INPUT TIL OPFØLGNINGSOPGAVE")
    print(f"Opfølgningstype: {opfoelgningstype!r}")
    print(f"Opfølgningsdato: {opfoelgningsdato!r}")
    print(f"Sagsbehandler: {sagsbehandler!r}")
    print(f"Titel: {titel!r}")
    print(f"Frekvens: {frekvens!r}")
    print(f"Hændelsestype: {haendelsestype!r}")
    print(f"Beskrivelse: {beskrivelse!r}")
    print("=" * 70)

    # ==========================================================
    # 1. HANDLINGER
    # ==========================================================

    handlinger = page.locator(
        KYSelectors.Borgere.HANDLINGER_DROPDOWN
    ).first

    await handlinger.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await handlinger.scroll_into_view_if_needed()

    if not await handlinger.is_enabled():
        raise RuntimeError(
            "Handlinger-dropdown er synlig, men ikke aktiv."
        )

    await handlinger.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    await page.wait_for_timeout(500)

    await session.screenshot(
        page=page,
        name="TEST_02_handlinger_aabnet",
        always=True,
    )

    # ==========================================================
    # 2. ADMINISTRATION
    # ==========================================================

    administration = await _find_handlinger_menu_item(
        page=page,
        text="Administration",
    )

    await administration.scroll_into_view_if_needed()

    await administration.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    await page.wait_for_timeout(500)

    await session.screenshot(
        page=page,
        name="TEST_03_administration_aabnet",
        always=True,
    )

    # ==========================================================
    # 3. OPRET OPFØLGNINGSOPGAVE
    # ==========================================================

    opret_opgave = await _find_handlinger_menu_item(
        page=page,
        text="Opret opfølgningsopgave",
    )

    await opret_opgave.scroll_into_view_if_needed()

    await opret_opgave.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    await _wait_for_empty_opgave_loader_to_clear(
        page
    )

    opfoelgningstype_select = page.locator(
        "select#opfoelgningsType"
    ).first

    await opfoelgningstype_select.wait_for(
        state="attached",
        timeout=PAGE_TIMEOUT_MS,
    )

    await session.screenshot(
        page=page,
        name="TEST_04_opfoelgningsformular_aabnet",
        always=True,
    )

    # ==========================================================
    # 4. OPFØLGNINGSTYPE
    # ==========================================================

    await _select_option_by_value_or_label(
        page=page,
        selector="select#opfoelgningsType",
        option=opfoelgningstype,
    )

    # KY kan genopbygge formularen ved change-event.
    await page.wait_for_timeout(1_000)

    await _wait_for_empty_opgave_loader_to_clear(
        page
    )

    # ==========================================================
    # 5. OPFØLGNINGSDATO
    # ==========================================================

    dato_input = page.locator(
        "input#command\\.opfoelgningsdato:visible"
    ).last

    await dato_input.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await dato_input.scroll_into_view_if_needed()
    await dato_input.fill(opfoelgningsdato)

    await dato_input.dispatch_event("input")
    await dato_input.dispatch_event("change")

    # ==========================================================
    # 6. SAGSBEHANDLER UDEN TYPEAHEAD-VALIDERING
    # ==========================================================

    sagsbehandler_input = page.locator(
        "input#typeahead:visible"
    ).last

    await sagsbehandler_input.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await sagsbehandler_input.scroll_into_view_if_needed()

    await sagsbehandler_input.fill(
        sagsbehandler
    )

    await sagsbehandler_input.dispatch_event(
        "input"
    )

    await sagsbehandler_input.dispatch_event(
        "change"
    )

    actual_sagsbehandler = (
        await sagsbehandler_input.input_value()
    )

    logger.info(
        "[OPFØLGNINGSOPGAVE] Sagsbehandlertekst: %s",
        actual_sagsbehandler,
    )

    # Der klikkes med vilje ikke på et typeahead-resultat.

    # ==========================================================
    # 7. KONTROLLÉR OM TYPEN ER BRUGERDEFINERET
    # ==========================================================

    selected_type = page.locator(
        "select#opfoelgningsType option:checked"
    ).first

    selected_value = (
        await selected_type.get_attribute("value")
        or ""
    ).strip().casefold()

    selected_label = (
        await selected_type.inner_text()
    ).strip().casefold()

    is_custom = (
        selected_value == "manuel"
        or selected_label == "brugerdefineret"
    )

    print()
    print("=" * 70)
    print("VALGT OPFØLGNINGSTYPE")
    print(f"Value: {selected_value!r}")
    print(f"Label: {selected_label!r}")
    print(f"Brugerdefineret: {is_custom}")
    print("=" * 70)

    # ==========================================================
    # 8. BRUGERDEFINEREDE FELTER
    # ==========================================================

    if is_custom:
        if not titel:
            raise ValueError(
                "Titel er påkrævet ved Brugerdefineret."
            )

        if not frekvens:
            raise ValueError(
                "Frekvens er påkrævet ved Brugerdefineret."
            )

        if not beskrivelse:
            raise ValueError(
                "Beskrivelse er påkrævet ved Brugerdefineret."
            )

        # ------------------------------------------------------
        # Dropdownfelter udfyldes først.
        # ------------------------------------------------------

        await _select_option_by_value_or_label(
            page=page,
            selector="select#frekvens",
            option=frekvens,
        )

        if haendelsestype:
            await _select_option_by_value_or_label(
                page=page,
                selector="select#haendelseType",
                option=haendelsestype,
            )

        # KY kan genopbygge formularen efter dropdownændringer.
        await page.wait_for_timeout(1_500)

        await _wait_for_empty_opgave_loader_to_clear(
            page
        )

        # ------------------------------------------------------
        # Titel og Beskrivelse udfyldes som de sidste felter.
        # ------------------------------------------------------

        await _udfyld_tekstfelt(
            page=page,
            selector="input[name='title']",
            value=titel,
            field_name="Titel",
        )

        await _udfyld_tekstfelt(
            page=page,
            selector="textarea[name='beskrivelse']",
            value=beskrivelse,
            field_name="Beskrivelse",
        )

        # Vent kort for at opdage eventuel nulstilling.
        await page.wait_for_timeout(750)

        # Hent felterne på ny, fordi KY kan have genopbygget DOM'en.
        title_input = page.locator(
            "input[name='title']:visible"
        ).last

        beskrivelse_input = page.locator(
            "textarea[name='beskrivelse']:visible"
        ).last

        actual_title = await title_input.input_value()

        actual_beskrivelse = (
            await beskrivelse_input.input_value()
        )

        print()
        print("=" * 70)
        print("TEKSTFELTER LIGE FØR GEM")
        print(f"Titel forventet: {titel!r}")
        print(f"Titel faktisk: {actual_title!r}")
        print(
            f"Beskrivelse forventet: "
            f"{beskrivelse!r}"
        )
        print(
            f"Beskrivelse faktisk: "
            f"{actual_beskrivelse!r}"
        )
        print("=" * 70)

        if actual_title.strip() != titel:
            raise RuntimeError(
                "Titel blev ikke indsat eller blev "
                "nulstillet før Gem. "
                f"Forventet={titel!r}, "
                f"faktisk={actual_title!r}"
            )

        if actual_beskrivelse.strip() != beskrivelse:
            raise RuntimeError(
                "Beskrivelse blev ikke indsat eller blev "
                "nulstillet før Gem. "
                f"Forventet={beskrivelse!r}, "
                f"faktisk={actual_beskrivelse!r}"
            )

    # ==========================================================
    # 9. SCREENSHOT FØR GEM
    # ==========================================================

    await session.screenshot(
        page=page,
        name="TEST_05_opfoelgningsformular_udfyldt",
        always=True,
    )

    # ==========================================================
    # 10. GEM
    # ==========================================================

    gem = await _find_visible_button(
        page=page,
        text="Gem",
    )

    await gem.scroll_into_view_if_needed()

    await gem.click(
        timeout=ACTION_TIMEOUT_MS,
    )

    await _wait_for_empty_opgave_loader_to_clear(
        page
    )

    try:
        await gem.wait_for(
            state="hidden",
            timeout=30_000,
        )

    except Exception:
        validation = await _visible_validation_text(
            page
        )

        logger.warning(
            "Gem blev ikke bekræftet. "
            "Synlig validering: %s",
            validation or "ukendt fejl",
        )

        print()
        print("=" * 70)
        print("GEM BLEV IKKE BEKRÆFTET")
        print(
            f"Synlig validering: "
            f"{validation or 'ukendt fejl'}"
        )
        print("=" * 70)

async def _udfyld_sagsbehandler_uden_validering(
    page: Page,
    sagsbehandler: str,
) -> None:
    """Skriv sagsbehandlerteksten uden at vælge et typeahead-resultat."""

    typeahead = page.locator(
        "input#typeahead:visible"
    ).last

    await typeahead.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )

    await typeahead.scroll_into_view_if_needed()
    await typeahead.click(timeout=ACTION_TIMEOUT_MS)

    await typeahead.fill("")
    await typeahead.fill(sagsbehandler)

    await typeahead.dispatch_event("input")
    await typeahead.dispatch_event("change")
    await typeahead.dispatch_event("blur")

    actual_value = await typeahead.input_value()

    print()
    print("=" * 70)
    print("SAGSBEHANDLER UDFYLDT UDEN VALIDERING")
    print(f"Indtastet tekst: {actual_value!r}")
    print("Typeahead-resultatet bliver ikke kontrolleret eller valgt.")
    print("=" * 70)

    logger.info(
        "[OPFØLGNINGSOPGAVE] Sagsbehandlertekst indsat "
        "uden typeahead-validering: %s",
        actual_value,
    )

    selected_type = page.locator(
        "select#opfoelgningsType option:checked"
    ).first
    selected_value = (
        await selected_type.get_attribute("value") or ""
    ).casefold()
    selected_label = (
        await selected_type.inner_text()
    ).strip().casefold()

    is_custom = (
        selected_value == "manuel"
        or selected_label == "brugerdefineret"
    )

    if is_custom:
        if not titel or not frekvens:
            raise ValueError(
                "Titel og frekvens er påkrævet ved Brugerdefineret."
            )

        # Vælg dropdownfelterne først, fordi KY kan genopbygge
        # den brugerdefinerede formular ved change-events.
        await _select_option_by_value_or_label(
            page=page,
            selector="select#frekvens",
            option=frekvens,
        )

        if haendelsestype:
            await _select_option_by_value_or_label(
                page=page,
                selector="select#haendelseType",
                option=haendelsestype,
            )

        # Vent på at KY er færdig med at opdatere formularen.
        await page.wait_for_timeout(1_000)
        await _wait_for_empty_opgave_loader_to_clear(page)

        # Udfyld kun de synlige tekstfelter og gør det efter dropdownfelterne.
        title_input = page.locator(
            "input#title:visible"
        ).first

        await title_input.wait_for(
            state="visible",
            timeout=PAGE_TIMEOUT_MS,
        )

        await title_input.fill(titel)

        beskrivelse_input = page.locator(
            "textarea#beskrivelse:visible"
        ).first

        await beskrivelse_input.wait_for(
            state="visible",
            timeout=PAGE_TIMEOUT_MS,
        )

        await beskrivelse_input.fill(beskrivelse)

        # Send både input- og change-events, så KY registrerer værdierne.
        await title_input.dispatch_event("input")
        await title_input.dispatch_event("change")

        await beskrivelse_input.dispatch_event("input")
        await beskrivelse_input.dispatch_event("change")

        # Kontrollér at værdierne faktisk står i de synlige felter.
        actual_title = await title_input.input_value()
        actual_beskrivelse = await beskrivelse_input.input_value()

        print()
        print("=" * 70)
        print("KONTROL AF BRUGERDEFINEREDE TEKSTFELTER")
        print(f"Titel forventet: {titel!r}")
        print(f"Titel i feltet: {actual_title!r}")
        print(f"Beskrivelse forventet: {beskrivelse!r}")
        print(f"Beskrivelse i feltet: {actual_beskrivelse!r}")
        print("=" * 70)

        if actual_title.strip() != titel.strip():
            raise RuntimeError(
                "Titel blev ikke indsat korrekt. "
                f"Forventet: {titel!r}. "
                f"Faktisk: {actual_title!r}"
            )

        if actual_beskrivelse.strip() != beskrivelse.strip():
            raise RuntimeError(
                "Beskrivelse blev ikke indsat korrekt. "
                f"Forventet: {beskrivelse!r}. "
                f"Faktisk: {actual_beskrivelse!r}"
            )

    await session.screenshot(
        page=page,
        name="TEST_05_opfoelgningsformular_udfyldt",
        always=True,
    )

    # Gem
    gem = await _find_visible_button(
        page=page,
        text="Gem",
    )
    await gem.click(timeout=ACTION_TIMEOUT_MS)
    await _wait_for_empty_opgave_loader_to_clear(page)

    try:
        await gem.wait_for(
            state="hidden",
            timeout=30_000,
        )
    except Exception:
        validation = await _visible_validation_text(page)
        raise RuntimeError(
            "Opfølgningsopgaven blev ikke gemt. "
            f"Validering: {validation or 'ukendt fejl'}"
        )


async def _find_handlinger_menu_item(
    page: Page,
    text: str,
) -> Locator:
    """Find et menupunkt kun inde i Handlinger-dropdownen."""

    pattern = re.compile(
        rf"^\s*{re.escape(text)}\s*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < PAGE_TIMEOUT_MS:
        container = page.locator(
            "li#handlinger-dropdown"
        ).first
        candidates = container.locator(
            "a, button, [role='menuitem']",
            has_text=pattern,
        )

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                visible_text = re.sub(
                    r"\s+",
                    " ",
                    await candidate.inner_text(),
                ).strip()

                if (
                    await candidate.is_visible()
                    and await candidate.is_enabled()
                    and pattern.fullmatch(visible_text)
                ):
                    return candidate
            except Exception:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise TimeoutError(
        f"Menupunktet '{text}' blev ikke fundet."
    )


async def _wait_for_empty_opgave_loader_to_clear(
    page: Page,
) -> None:
    """Vent på at #empty_opgave_loader er skjult eller fjernet."""

    await page.wait_for_function(
        """
        () => {
            const loader = document.querySelector('#empty_opgave_loader');

            if (!loader) {
                return true;
            }

            const style = window.getComputedStyle(loader);

            return (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                style.opacity === '0' ||
                loader.offsetParent === null
            );
        }
        """,
        timeout=PAGE_TIMEOUT_MS,
    )


async def _select_option_by_value_or_label(
    page: Page,
    selector: str,
    option: str,
) -> None:
    """Vælg option via value eller synlig tekst."""

    select = page.locator(selector).first
    await select.wait_for(
        state="attached",
        timeout=PAGE_TIMEOUT_MS,
    )

    options = select.locator("option")
    selected_value = None

    for index in range(await options.count()):
        current = options.nth(index)
        value = await current.get_attribute("value") or ""
        label = re.sub(
            r"\s+",
            " ",
            await current.inner_text(),
        ).strip()

        if option.casefold() in {
            value.casefold(),
            label.casefold(),
        }:
            selected_value = value
            break

    if selected_value is None:
        raise ValueError(
            f"Kunne ikke finde '{option}' i {selector}."
        )

    await select.select_option(value=selected_value)
    await select.dispatch_event("input")
    await select.dispatch_event("change")

    # Understøt bootstrap-select, hvis KY anvender plugin-visningen.
    await page.evaluate(
        """
        ({ selector, value }) => {
            const element = document.querySelector(selector);
            const jq = window.jQuery || window.$;

            if (
                element &&
                jq &&
                typeof jq(element).selectpicker === 'function'
            ) {
                jq(element).selectpicker('val', value);
                jq(element).trigger('changed.bs.select');
                jq(element).trigger('change');
            }
        }
        """,
        {
            "selector": selector,
            "value": selected_value,
        },
    )


async def _vaelg_sagsbehandler(
    page: Page,
    sagsbehandler: str,
) -> None:
    """Skriv fuldt navn og klik det matchende typeahead-resultat."""

    typeahead = page.locator("input#typeahead").first
    await typeahead.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )
    await typeahead.fill(sagsbehandler)

    pattern = re.compile(
        rf"^\s*{re.escape(sagsbehandler)}(?:\s|\().*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < PAGE_TIMEOUT_MS:
        suggestions = page.locator(
            ".tt-menu:visible .tt-suggestion.tt-selectable"
        )

        for index in range(await suggestions.count()):
            suggestion = suggestions.nth(index)
            visible_text = re.sub(
                r"\s+",
                " ",
                await suggestion.inner_text(),
            ).strip()

            if (
                await suggestion.is_visible()
                and pattern.match(visible_text)
            ):
                await suggestion.click(
                    timeout=ACTION_TIMEOUT_MS
                )
                return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise TimeoutError(
        f"Sagsbehandleren blev ikke fundet: {sagsbehandler}"
    )


async def _find_visible_button(
    page: Page,
    text: str,
) -> Locator:
    pattern = re.compile(
        rf"^\s*{re.escape(text)}\s*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < PAGE_TIMEOUT_MS:
        candidates = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button.btn-submit-form, a.btn-submit-form"
        )

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            value = await candidate.get_attribute("value")
            visible_text = value or await candidate.inner_text()
            visible_text = re.sub(
                r"\s+",
                " ",
                visible_text or "",
            ).strip()

            if (
                await candidate.is_visible()
                and await candidate.is_enabled()
                and pattern.fullmatch(visible_text)
            ):
                return candidate

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise TimeoutError(
        f"Knappen '{text}' blev ikke fundet."
    )


async def _visible_validation_text(page: Page) -> str:
    messages = page.locator(
        ".has-error:visible, .help-block:visible, "
        ".alert-danger:visible, .field-validation-error:visible"
    )
    values: list[str] = []

    for index in range(await messages.count()):
        text = re.sub(
            r"\s+",
            " ",
            await messages.nth(index).inner_text(),
        ).strip()

        if text and text not in values:
            values.append(text)

    return " | ".join(values)


def _normaliser_cpr(value: str) -> str:
    cpr = re.sub(r"\D", "", value)

    if len(cpr) != 10:
        raise ValueError(
            "CPR skal indeholde præcis ti cifre."
        )

    return cpr


def _masker_cpr(cpr: str) -> str:
    if len(cpr) != 10:
        return "**********"

    return f"{cpr[:6]}-****"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s [%(levelname)s] "
            "%(name)s: %(message)s"
        ),
    )

    main()
