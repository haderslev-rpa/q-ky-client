"""Pytest-integrationstest for oprettelse af opfølgningsopgave i KY.

Kør:
    uv run pytest tests/test_opfoelgningsopgave.py -s -vv

Miljøvariabler:
    TEST_CPR_1=DDMMYYXXXX
    HEADLESS=false

Testen genbruger den nye asynkrone borgere.py til borgeropslag,
opgaveåbning og lukning af PERSON-faner.
"""

from __future__ import annotations

import os
import re

import pytest
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.borgere import (
    aabn_opgave_og_hent_url,
    hent_person_tab_ids,
    luk_borgerfaner,
    naviger_til_borger,
)
from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

ACTION_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250
MAX_SEARCH_ATTEMPTS = 3
WAIT_BEFORE_BROWSER_CLOSE_MS = 10_000

OPFOELGNINGSTYPE = "Brugerdefineret"
OPFOELGNINGSDATO = "01-09-2026"
SAGSBEHANDLER = "Sagsbehandler navn"
TITEL = "Manuel behandling af køretøj"
FREKVENS = "Aldrig"
HAENDELSESTYPE = "Skriv journalnotat"
BESKRIVELSE = "Sagen kræver manuel behandling."


def get_headless_flag() -> bool:
    """Returnér browserens headless-indstilling fra miljøet."""

    return os.getenv("HEADLESS", "false").strip().casefold() == "true"


async def test_opret_opfoelgningsopgave(
    ky_credential_name: str,
) -> None:
    """Fremsøg TEST_CPR_1, opret opfølgningsopgave og ryd op."""

    cpr = _hent_test_cpr()
    session = BrowserSession(
        headless=get_headless_flag(),
        debug=True,
        video=False,
    )
    page: Page | None = None
    faner_foer_test: set[str] = set()

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
        assert not is_ky_error_url(page), (
            f"KY viste fejlsiden efter launch: {page.url}"
        )
        assert is_ky_url(page), f"Siden er ikke en gyldig KY-side: {page.url}"
        assert await has_jsessionid(page), "KY-sessionen mangler JSESSIONID."

        faner_foer_test = set(await hent_person_tab_ids(page))

        _print_step("FREMSØGER OG VALIDERER BORGER")
        print(f"TEST_CPR_1: {_masker_cpr(cpr)}", flush=True)

        borger_url = await naviger_til_borger(
            page=page,
            cpr=cpr,
            timeout=PAGE_TIMEOUT_MS,
            max_forsog=MAX_SEARCH_ATTEMPTS,
        )

        assert borger_url, "Borgeropslaget returnerede ingen URL."
        print(f"Valideret borger-URL: {borger_url}", flush=True)

        await session.screenshot(
            page=page,
            name="TEST_01_borger_fremsoegt",
            always=True,
        )

        _print_step("ÅBNER OPFØLGNINGSOPGAVE")

        checkpoint = await aabn_opgave_og_hent_url(
            page=page,
            menu_sti=("Administration", "Opret opfølgningsopgave"),
            timeout=PAGE_TIMEOUT_MS,
        )

        assert checkpoint["opgave_id"], "Checkpoint mangler opgave_id."
        assert checkpoint["opgave_url"], "Checkpoint mangler opgave_url."

        print(f"Opgavenavn: {checkpoint['opgave_navn']}", flush=True)
        print(f"Opgave-id: {checkpoint['opgave_id']}", flush=True)
        print(f"Opgave-URL: {checkpoint['opgave_url']}", flush=True)

        await session.screenshot(
            page=page,
            name="TEST_02_opfoelgningsformular_aabnet",
            always=True,
        )

        _print_step("UDFYLDER OPFØLGNINGSOPGAVE")

        await _udfyld_og_gem_opfoelgningsopgave(
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

        _print_step("OPFØLGNINGSOPGAVEN ER GEMT")
        print(f"CPR: {_masker_cpr(cpr)}", flush=True)
        print(f"Titel: {TITEL}", flush=True)

        await session.screenshot(
            page=page,
            name="TEST_06_opfoelgningsopgave_gemt",
            always=True,
        )

        if not get_headless_flag():
            print("Browseren holdes åben i 10 sekunder.", flush=True)
            await page.wait_for_timeout(WAIT_BEFORE_BROWSER_CLOSE_MS)

    finally:
        if page is not None and not page.is_closed():
            try:
                faner_efter_test = set(await hent_person_tab_ids(page))
                testens_faner = sorted(faner_efter_test - faner_foer_test)

                if testens_faner:
                    _print_step("LUKKER TESTENS PERSON-FANER")
                    await luk_borgerfaner(
                        page=page,
                        entity_ids=testens_faner,
                    )
            except Exception as error:
                print(
                    "Oprydning af PERSON-faner fejlede: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        await session.close()


async def _udfyld_og_gem_opfoelgningsopgave(
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
    """Udfyld og gem formularen, som borgere.py allerede har åbnet."""

    await _wait_for_empty_opgave_loader_to_clear(page)

    await _select_option_by_value_or_label(
        page=page,
        selector="select#opfoelgningsType",
        option=opfoelgningstype,
    )

    await page.wait_for_timeout(1_000)
    await _wait_for_empty_opgave_loader_to_clear(page)

    dato_input = page.locator(
        "input#command\\.opfoelgningsdato:visible"
    ).last
    await dato_input.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await dato_input.fill(opfoelgningsdato)
    await dato_input.dispatch_event("input")
    await dato_input.dispatch_event("change")

    sagsbehandler_input = page.locator("input#typeahead:visible").last
    await sagsbehandler_input.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT_MS,
    )
    await sagsbehandler_input.fill(sagsbehandler)
    await sagsbehandler_input.dispatch_event("input")
    await sagsbehandler_input.dispatch_event("change")

    actual_sagsbehandler = await sagsbehandler_input.input_value()
    assert actual_sagsbehandler.strip() == sagsbehandler.strip(), (
        "Sagsbehandlerteksten blev ikke indsat korrekt. "
        f"Forventet={sagsbehandler!r}, faktisk={actual_sagsbehandler!r}."
    )

    selected_type = page.locator(
        "select#opfoelgningsType option:checked"
    ).first
    selected_value = (
        await selected_type.get_attribute("value") or ""
    ).strip().casefold()
    selected_label = (
        await selected_type.inner_text()
    ).strip().casefold()

    is_custom = (
        selected_value == "manuel"
        or selected_label == "brugerdefineret"
    )

    assert is_custom, (
        "Den valgte opfølgningstype blev ikke registreret som "
        "Brugerdefineret. "
        f"Value={selected_value!r}, label={selected_label!r}."
    )

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

    await page.wait_for_timeout(1_500)
    await _wait_for_empty_opgave_loader_to_clear(page)

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

    await page.wait_for_timeout(750)

    actual_title = await page.locator(
        "input[name='title']:visible"
    ).last.input_value()
    actual_beskrivelse = await page.locator(
        "textarea[name='beskrivelse']:visible"
    ).last.input_value()

    assert actual_title.strip() == titel.strip(), (
        "Titel blev nulstillet før Gem. "
        f"Forventet={titel!r}, faktisk={actual_title!r}."
    )
    assert actual_beskrivelse.strip() == beskrivelse.strip(), (
        "Beskrivelse blev nulstillet før Gem. "
        f"Forventet={beskrivelse!r}, faktisk={actual_beskrivelse!r}."
    )

    await session.screenshot(
        page=page,
        name="TEST_05_opfoelgningsformular_udfyldt",
        always=True,
    )

    gem = await _find_visible_button(page=page, text="Gem")
    await gem.scroll_into_view_if_needed()
    await gem.click(timeout=ACTION_TIMEOUT_MS)
    await _wait_for_empty_opgave_loader_to_clear(page)

    try:
        await gem.wait_for(state="hidden", timeout=30_000)
    except PlaywrightTimeoutError as error:
        validation = await _visible_validation_text(page)
        raise AssertionError(
            "Opfølgningsopgaven blev ikke gemt. "
            f"Synlig validering: {validation or 'ukendt fejl'}"
        ) from error


async def _udfyld_tekstfelt(
    page: Page,
    selector: str,
    value: str,
    field_name: str,
) -> None:
    """Udfyld den seneste synlige formularinstans robust."""

    fields = page.locator(f"{selector}:visible")
    field_count = await fields.count()

    if field_count == 0:
        raise AssertionError(
            f"Kunne ikke finde et synligt felt til {field_name}. "
            f"Selector={selector!r}"
        )

    field = fields.last
    await field.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await field.scroll_into_view_if_needed()
    await field.fill(value)

    actual_value = await field.input_value()

    if actual_value.strip() != value.strip():
        await field.evaluate(
            """
            (element, newValue) => {
                const prototype = element instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(
                    prototype,
                    'value'
                );
                if (!descriptor || !descriptor.set) {
                    throw new Error('Native value-setter blev ikke fundet');
                }
                descriptor.set.call(element, newValue);
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            value,
        )
        actual_value = await field.input_value()

    assert actual_value.strip() == value.strip(), (
        f"{field_name} kunne ikke udfyldes. "
        f"Forventet={value!r}, faktisk={actual_value!r}."
    )


async def _wait_for_empty_opgave_loader_to_clear(page: Page) -> None:
    """Vent på at #empty_opgave_loader er skjult eller fjernet."""

    await page.wait_for_function(
        """
        () => {
            const loader = document.querySelector('#empty_opgave_loader');
            if (!loader) return true;
            const style = window.getComputedStyle(loader);
            return style.display === 'none'
                || style.visibility === 'hidden'
                || style.opacity === '0'
                || loader.offsetParent === null;
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
    await select.wait_for(state="attached", timeout=PAGE_TIMEOUT_MS)

    options = select.locator("option")
    selected_value: str | None = None

    for index in range(await options.count()):
        current = options.nth(index)
        value = await current.get_attribute("value") or ""
        label = re.sub(r"\s+", " ", await current.inner_text()).strip()

        if option.casefold() in {value.casefold(), label.casefold()}:
            selected_value = value
            break

    if selected_value is None:
        available = [
            re.sub(r"\s+", " ", await options.nth(i).inner_text()).strip()
            for i in range(await options.count())
        ]
        raise AssertionError(
            f"Kunne ikke finde {option!r} i {selector}. "
            f"Muligheder: {available}"
        )

    await select.select_option(value=selected_value)
    await select.dispatch_event("input")
    await select.dispatch_event("change")

    await page.evaluate(
        """
        ({ selector, value }) => {
            const element = document.querySelector(selector);
            const jq = window.jQuery || window.$;
            if (
                element
                && jq
                && typeof jq(element).selectpicker === 'function'
            ) {
                jq(element).selectpicker('val', value);
                jq(element).trigger('changed.bs.select');
                jq(element).trigger('change');
            }
        }
        """,
        {"selector": selector, "value": selected_value},
    )


async def _find_visible_button(page: Page, text: str) -> Locator:
    """Find en synlig og aktiv submitknap med eksakt tekst."""

    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
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
            visible_text = re.sub(r"\s+", " ", visible_text or "").strip()

            if (
                await candidate.is_visible()
                and await candidate.is_enabled()
                and pattern.fullmatch(visible_text)
            ):
                return candidate

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(f"Knappen {text!r} blev ikke fundet.")


async def _visible_validation_text(page: Page) -> str:
    """Returnér synlige valideringsbeskeder fra formularen."""

    messages = page.locator(
        ".has-error:visible, .help-block:visible, "
        ".alert-danger:visible, .field-validation-error:visible"
    )
    values: list[str] = []

    for index in range(await messages.count()):
        try:
            text = re.sub(
                r"\s+",
                " ",
                await messages.nth(index).inner_text(),
            ).strip()
        except Exception:
            continue

        if text and text not in values:
            values.append(text)

    return " | ".join(values)


def _hent_test_cpr() -> str:
    """Hent TEST_CPR_1 fra miljøet."""

    raw_value = os.getenv("TEST_CPR_1", "").strip()
    match = re.fullmatch(r"\s*(\d{6})[\s-]?(\d{4})\s*", raw_value)

    if not match:
        pytest.fail(
            "TEST_CPR_1 mangler eller er ugyldigt. "
            "Angiv 10 cifre eller DDMMÅÅ-NNNN i .env.",
            pytrace=False,
        )

    return match.group(1) + match.group(2)


def _masker_cpr(cpr: str) -> str:
    """Maskér CPR i logoutput."""

    digits = re.sub(r"\D", "", cpr)
    return f"******{digits[-4:]}" if len(digits) == 10 else "[ugyldigt CPR]"


def _set_recorder_page(
    session: BrowserSession,
    page: Page,
) -> None:
    """Knyt BrowserSessions recorder til Playwright-siden."""

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
