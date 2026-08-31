"""Asynkron behandling af en allerede åbnet Send brev-opgave i KY.

Opgaven åbnes og checkpointes først via
``ky_client.functionality.borgere.opstart_opgave``.
Dette modul forventer, at Send brev-opgaven allerede er åbnet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypedDict

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.functionality.borgere import OpstartOpgaveCheckpoint

from ..selectors import KYSelectors

ACTION_TIMEOUT_MS = 30_000
OPGAVE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250
SEND_STABILIZATION_WAIT_MS = 1_500
SEND_BREV_MENU_STI = ("Administration", "Send brev")


class ValgtSagInfo(TypedDict):
    sag_id: str
    sagstekst: str
    aktive: bool
    passive: bool


class ValgtStandardBilagInfo(TypedDict):
    titel: str
    noegle: str


class SendBrevResultat(TypedDict):
    """Resultat fra udfyldning af en allerede åbnet Send brev-opgave."""
    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]
    sag_id: str
    sagstekst: str
    brevskabelon: str
    bilag_titel: str
    bilag_noegle: str
    fysisk_post: bool
    genoptaget: bool
    kilde: str
    test: bool
    sendt: bool


async def send_brev(
    page: Page,
    checkpoint: OpstartOpgaveCheckpoint,
    sag: str,
    skabelon_sti: Sequence[str],
    bilag_titel: str | None = None,
    fysisk_post: bool = False,
    aktive: bool = True,
    passive: bool = True,
    test: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> SendBrevResultat:
    """Overtag og udfyld Send brev-opgaven fra opstart_opgave.

    Funktionen åbner, genoptager eller opretter aldrig selv en opgave.
    ``checkpoint`` skal være resultatet fra den fælles ``opstart_opgave``.
    Ved ``test=True`` klikkes der ikke på Godkend, og brevet sendes ikke.
    """
    _valider_send_brev_checkpoint(checkpoint)
    valgt_sag = await vaelg_sag_til_brev(
        page=page,
        soegevaerdi=sag,
        aktive=aktive,
        passive=passive,
        timeout=timeout,
    )
    brevskabelon = await vaelg_brevskabelon_fra_sti(
        page=page,
        skabelon_sti=skabelon_sti,
        timeout=timeout,
    )
    bilag = {"titel": "", "noegle": ""}
    if bilag_titel:
        bilag = await vaelg_standard_bilag(
            page=page,
            bilag_titel=bilag_titel,
            timeout=timeout,
        )
        await vent_efter_bilag_dropdown_er_minimeret(page=page, timeout=timeout)
    await saet_fysisk_post(page=page, fysisk_post=fysisk_post, timeout=timeout)
    sendt = await godkend_og_send_brev(page=page, test=test, timeout=timeout)
    return {
        "opgave_id": checkpoint["opgave_id"],
        "opgave_navn": checkpoint["opgave_navn"],
        "opgave_url": checkpoint["opgave_url"],
        "borger_url": checkpoint["borger_url"],
        "menu_sti": checkpoint["menu_sti"],
        "sag_id": valgt_sag["sag_id"],
        "sagstekst": valgt_sag["sagstekst"],
        "brevskabelon": brevskabelon,
        "bilag_titel": bilag["titel"],
        "bilag_noegle": bilag["noegle"],
        "fysisk_post": fysisk_post,
        "genoptaget": bool(checkpoint.get("genoptaget", False)),
        "kilde": str(checkpoint.get("kilde", "ny_opgave")),
        "test": test,
        "sendt": sendt,
    }


def _valider_send_brev_checkpoint(checkpoint: OpstartOpgaveCheckpoint) -> None:
    """Kræv checkpoint fra opstart_opgave til Send brev."""
    if not isinstance(checkpoint, dict):
        raise TypeError("checkpoint skal komme fra opstart_opgave().")
    for key in ("opgave_id", "opgave_navn", "opgave_url", "borger_url", "menu_sti"):
        if not checkpoint.get(key):
            raise RuntimeError(f"Checkpointet mangler {key}.")
    if str(checkpoint["opgave_navn"]).casefold() != "Send brev".casefold():
        raise RuntimeError(
            "Checkpointet tilhører ikke Send brev. "
            f"Faktisk opgavenavn={checkpoint['opgave_navn']!r}."
        )


async def vaelg_sag_til_brev(
    page: Page,
    soegevaerdi: str,
    aktive: bool = True,
    passive: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> ValgtSagInfo:
    """Søg efter eksakt SagsID og vælg rækken med fysisk dobbeltklik."""

    await _bekraeft_send_brev_opgave(page, timeout)
    sags_id_input = _normaliser(soegevaerdi)
    if not sags_id_input:
        raise ValueError("soegevaerdi/SagsID må ikke være tomt.")

    root = page.locator(f"{KYSelectors.Borgere.SEND_BREV_SAGSVAELGER}:visible").first
    await root.wait_for(state="visible", timeout=timeout)

    toggle = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_TOGGLE).first
    await toggle.click(timeout=ACTION_TIMEOUT_MS)

    menu = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_MENU).first
    await menu.wait_for(state="visible", timeout=timeout)

    await _set_checkbox(
        root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_AKTIVE).first,
        aktive,
    )
    await _set_checkbox(
        root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_PASSIVE).first,
        passive,
    )

    search = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_SOEG).first
    await search.wait_for(state="visible", timeout=timeout)
    await search.fill(sags_id_input)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")

    await root.locator(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_RESULTS
    ).first.wait_for(state="visible", timeout=timeout)

    row = await _find_sag_med_eksakt_sagsid(
        root=root,
        sags_id=sags_id_input,
        timeout=timeout,
    )
    dynamic_sag_id = (await row.get_attribute("data-id") or "").strip()
    sagstekst = _normaliser(await row.inner_text())
    if not dynamic_sag_id:
        raise RuntimeError("Den fundne sagsrække mangler data-id.")

    await _dobbeltklik_paa_sagsraekke(page, row, timeout)
    await _vent_paa_sagsvalg_registreret(root, dynamic_sag_id, timeout)

    return {
        "sag_id": dynamic_sag_id,
        "sagstekst": sagstekst,
        "aktive": aktive,
        "passive": passive,
    }


async def vaelg_brevskabelon(
    page: Page,
    skabelon_titel: str,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> str:
    """Åbn Brevskabelon, søg og vælg ét eksakt titelmatch."""

    await _bekraeft_send_brev_opgave(page, timeout)
    skabelon_titel = _normaliser(skabelon_titel)
    if not skabelon_titel:
        raise ValueError("skabelon_titel må ikke være tom.")

    field = page.locator(
        f"{KYSelectors.Borgere.SEND_BREV_BREVSKABELON_INPUT}:visible"
    ).last
    await field.wait_for(state="visible", timeout=timeout)
    await field.click(timeout=ACTION_TIMEOUT_MS)

    search = page.locator(
        f"{KYSelectors.Borgere.SEND_BREV_BREVSKABELON_SOEG}:visible"
    ).last
    await search.wait_for(state="visible", timeout=timeout)
    await search.fill(skabelon_titel)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")

    candidate = await _find_unique_template(page, skabelon_titel, timeout)
    selected_title = _normaliser(
        await candidate.get_attribute("data-titel") or await candidate.inner_text()
    )
    await candidate.click(timeout=ACTION_TIMEOUT_MS)
    await _wait_for_input_value(field, selected_title, timeout)
    return selected_title


async def vaelg_brevskabelon_fra_sti(
    page: Page,
    skabelon_sti: Sequence[str],
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> str:
    """Vælg slutskabelonen fra en valideret mappesti.

    KY's søgefelt finder den eksakte slutskabelon hurtigere og mere stabilt
    end manuel gennemgang af et allerede udvidet mappetræ. Mappestien bruges
    som et tydeligt offentligt API; sidste led er den skabelon, der vælges.
    """

    sti = tuple(
        _normaliser(delnavn) for delnavn in skabelon_sti if _normaliser(delnavn)
    )
    if len(sti) < 2:
        raise ValueError(
            "skabelon_sti skal indeholde mindst én mappe og ét skabelonnavn."
        )

    print(f"Vælger brevskabelon fra sti: {' > '.join(sti)}")
    return await vaelg_brevskabelon(
        page=page,
        skabelon_titel=sti[-1],
        timeout=timeout,
    )


async def vaelg_standard_bilag(
    page: Page,
    bilag_titel: str,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> ValgtStandardBilagInfo:
    """Vælg Standard bilag og klik straks på Tilføj bilag.

    Efter klik på resultatet i dropdownmenuen findes ``Tilføj bilag`` i den
    samme tabelrække som Standard bilag-feltet. Der ventes ikke på yderligere
    menu- eller inputkontrol mellem de to klik.
    """

    await _bekraeft_send_brev_opgave(page, timeout)
    bilag_titel = _normaliser(bilag_titel)
    if not bilag_titel:
        raise ValueError("bilag_titel må ikke være tom.")

    field = page.locator(
        "input[type='text'].skabelon_titel.cursor-pointer"
        "[name='alleTilfoejedeBreve[0].tilfoejBilag']"
        "[readonly][placeholder='Vælg skabelon']:visible"
    ).last
    await field.wait_for(state="visible", timeout=timeout)
    await field.click(timeout=ACTION_TIMEOUT_MS)

    menu = await _vent_paa_standard_bilag_dropdown(page, timeout)
    search = menu.locator(
        "input.skabelonvaelger-soeg[placeholder='Søg efter vedhæftning']"
    ).first
    await search.wait_for(state="visible", timeout=timeout)
    await search.fill(bilag_titel)
    await search.dispatch_event("input")
    await search.dispatch_event("keyup")

    bilag = await _vent_paa_standard_bilag_resultat(
        menu,
        bilag_titel,
        timeout,
    )
    selected_title = _normaliser(
        await bilag.get_attribute("data-titel") or await bilag.inner_text()
    )
    selected_key = (await bilag.get_attribute("data-noegle") or "").strip()
    if not selected_key:
        raise RuntimeError(f"Standardbilaget '{selected_title}' mangler data-noegle.")

    # Genfind lige før klik, fordi KY kan udskifte li-elementet ved filtrering.
    bilag = menu.locator(
        f"li.hg-skabelon.cell.VEDHAEFTNING[data-noegle='{selected_key}']:visible"
    ).last
    await bilag.wait_for(state="visible", timeout=timeout)
    await bilag.click(timeout=ACTION_TIMEOUT_MS)

    # Ingen ekstra vent på skjult menu eller feltværdi: klik direkte på
    # Tilføj bilag i den samme Standard bilag-række.
    add_button = await _find_tilfoej_bilag_i_samme_raekke(field, timeout)
    await add_button.click(timeout=ACTION_TIMEOUT_MS)

    await _vent_paa_standard_bilag_tilfoejet(
        page,
        field,
        selected_key,
        timeout,
    )
    return {"titel": selected_title, "noegle": selected_key}


async def vent_efter_bilag_dropdown_er_minimeret(
    page: Page,
    timeout: int = OPGAVE_TIMEOUT_MS,
    wait_after_closed_ms: int = 1_500,
) -> None:
    """Vent på lukket bilagsdropdown og derefter en stabiliseringspause."""

    elapsed = 0
    stable_closed_count = 0
    last_snapshot: dict[str, int] | None = None

    while elapsed < timeout:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under vent på bilagsdropdownen.")

        snapshot = await page.evaluate(
            r"""
            () => {
                const menus = Array.from(document.querySelectorAll(
                    'div.skabelon-vaelger.dropdown-menu'
                )).filter(menu => menu.querySelector(
                    "input.skabelonvaelger-soeg"
                    + "[placeholder='Søg efter vedhæftning']"
                ));
                const isVisible = element => {
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && element.getClientRects().length > 0;
                };
                return {
                    menuCount: menus.length,
                    visibleMenuCount: menus.filter(isVisible).length
                };
            }
            """
        )
        last_snapshot = snapshot

        if int(snapshot["visibleMenuCount"]) == 0:
            stable_closed_count += 1
        else:
            stable_closed_count = 0

        if stable_closed_count >= 3:
            print(
                "Bilagsdropdownen er minimeret. "
                f"Venter yderligere {wait_after_closed_ms} ms."
            )
            await page.wait_for_timeout(wait_after_closed_ms)
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Bilagsdropdownen blev ikke stabilt minimeret. "
        f"Seneste snapshot={last_snapshot!r}."
    )


async def saet_fysisk_post(
    page: Page,
    fysisk_post: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> bool:
    """Sæt fysisk post med den JavaScript-metode, der virker i KY.

    Funktionen finder den korrekte checkbox direkte i DOM'en, bruger den
    native ``checked``-setter, sender ``input`` og ``change`` og kalder
    ``postage_method(0)``. Tilstanden valideres bagefter direkte i DOM'en.
    """

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før fysisk post indstilles.")

    await _bekraeft_send_brev_opgave(page, timeout)

    await page.wait_for_function(
        r"""
        () => Boolean(document.querySelector(
            "input[type='checkbox'].fysisk_post"
        ))
        """,
        timeout=timeout,
    )

    result = await page.evaluate(
        r"""
        desired => {
            const selector = "input[type='checkbox'].fysisk_post";
            const expectedName = 'alleTilfoejedeBreve[0].fysiskPost';
            const candidates = Array.from(document.querySelectorAll(selector));
            const isVisible = element => {
                const style = window.getComputedStyle(element);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && element.getClientRects().length > 0;
            };
            const checkbox = candidates.find(element =>
                element.name === expectedName && isVisible(element)
            ) || candidates.find(element =>
                element.name === expectedName
            ) || candidates.find(isVisible) || candidates[0];

            if (!checkbox) {
                throw new Error('Ingen fysisk post-checkbox blev fundet.');
            }
            if (checkbox.disabled) {
                throw new Error('Fysisk post-checkboxen er disabled.');
            }

            const before = Boolean(checkbox.checked);
            const descriptor = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype,
                'checked'
            );

            if (before !== desired) {
                if (descriptor && descriptor.set) {
                    descriptor.set.call(checkbox, desired);
                } else {
                    checkbox.checked = desired;
                }

                checkbox.dispatchEvent(new Event('input', {
                    bubbles: true,
                    composed: true
                }));
                checkbox.dispatchEvent(new Event('change', {
                    bubbles: true,
                    composed: true
                }));
            }

            let postageMethodCalled = false;
            if (typeof window.postage_method === 'function') {
                window.postage_method(0);
                postageMethodCalled = true;
            }

            return {
                before,
                desired,
                immediatelyAfter: Boolean(checkbox.checked),
                postageMethodCalled,
                candidateCount: candidates.length,
                id: checkbox.id || '',
                name: checkbox.name || '',
                onchange: checkbox.getAttribute('onchange') || '',
                dataOnchange: checkbox.getAttribute('data-onchange') || ''
            };
        }
        """,
        fysisk_post,
    )
    print(f"JavaScript-resultat for fysisk post: {result}")

    await _vent_paa_fysisk_post_javascript_tilstand(
        page=page,
        fysisk_post=fysisk_post,
        timeout=timeout,
    )
    return fysisk_post


async def _vent_paa_fysisk_post_javascript_tilstand(
    page: Page,
    fysisk_post: bool,
    timeout: int,
) -> None:
    """Vent på stabil checkbox- og Brevtype-tilstand direkte i DOM'en."""

    elapsed = 0
    stable_count = 0
    previous_state: tuple[bool, bool, str, str] | None = None
    last_snapshot: dict[str, object] | None = None

    while elapsed < timeout:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under validering af fysisk post.")

        snapshot = await page.evaluate(
            r"""
            desired => {
                const selector = "input[type='checkbox'].fysisk_post";
                const expectedName = 'alleTilfoejedeBreve[0].fysiskPost';
                const candidates = Array.from(
                    document.querySelectorAll(selector)
                );
                const checkbox = candidates.find(element =>
                    element.name === expectedName
                ) || candidates[0] || null;
                const row = document.querySelector(
                    "tr#postage-container0[name='postage-container']"
                );
                const select = document.querySelector(
                    "select[name='alleTilfoejedeBreve[0].postage']"
                );
                const rowStyle = row ? window.getComputedStyle(row) : null;
                const option = select && select.selectedIndex >= 0
                    ? select.options[select.selectedIndex]
                    : null;

                return {
                    desired,
                    checkboxFound: Boolean(checkbox),
                    candidateCount: candidates.length,
                    checked: checkbox ? Boolean(checkbox.checked) : false,
                    brevtypeSynlig: Boolean(
                        row && rowStyle
                        && rowStyle.display !== 'none'
                        && rowStyle.visibility !== 'hidden'
                    ),
                    brevtypeDisplay: rowStyle ? rowStyle.display : '',
                    brevtypeValue: select ? select.value : '',
                    brevtypeText: option
                        ? (option.textContent || '').trim()
                        : ''
                };
            }
            """,
            fysisk_post,
        )
        last_snapshot = snapshot
        state = (
            bool(snapshot["checked"]),
            bool(snapshot["brevtypeSynlig"]),
            str(snapshot["brevtypeValue"]),
            str(snapshot["brevtypeText"]),
        )

        if fysisk_post:
            valid = (
                snapshot["checkboxFound"] is True
                and snapshot["checked"] is True
                and snapshot["brevtypeSynlig"] is True
                and snapshot["brevtypeValue"] == "1"
                and str(snapshot["brevtypeText"]).casefold() == "b-post"
            )
        else:
            valid = (
                snapshot["checkboxFound"] is True
                and snapshot["checked"] is False
                and snapshot["brevtypeSynlig"] is False
            )

        if valid and state == previous_state:
            stable_count += 1
        elif valid:
            stable_count = 1
        else:
            stable_count = 0
        previous_state = state

        if stable_count >= 3:
            print(f"Fysisk post er stabilt indstillet: {snapshot}")
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "JavaScript gav ikke den ønskede fysiske post-tilstand. "
        f"Forventet={fysisk_post}, seneste snapshot={last_snapshot!r}."
    )


async def godkend_og_send_brev(
    page: Page,
    test: bool = True,
    timeout: int = OPGAVE_TIMEOUT_MS,
    wait_after_click_ms: int = SEND_STABILIZATION_WAIT_MS,
) -> bool:
    """Godkend brevet og verificér, at KY er gået videre.

    Ved ``test=True`` klikkes der ikke på Godkend. I produktion klikkes
    der på den eneste synlige og aktive Godkend-knap. Efter klikket får
    KY en kort stabiliseringspause. Brevet betragtes kun som sendt, når
    ingen synlig og aktiv Godkend-knap længere findes.
    """
    await _bekraeft_send_brev_opgave(page, timeout)

    if test:
        print()
        print("=" * 70)
        print("TESTTILSTAND: BREVET BLIVER IKKE AFSENDT")
        print("Godkend-knappen bliver ikke klikket.")
        print("=" * 70)
        return False

    if wait_after_click_ms < 0:
        raise ValueError("wait_after_click_ms må ikke være negativ.")

    selector = (
        "button[type='button'].btn.btn-primary.submit-opgave.margin-right"
        "[data-href='/opgave/handling/fortsaet']"
    )
    button = await _find_eneste_aktive_godkend_knap(
        page=page,
        selector=selector,
    )

    print()
    print("=" * 70)
    print("PRODUKTIONSTILSTAND: KLIKKER PÅ GODKEND")
    print("Brevet afsendes nu.")
    print("=" * 70)

    await button.click(timeout=ACTION_TIMEOUT_MS)

    # Giv KY tid til at behandle handlingen og opdatere DOM'en.
    if wait_after_click_ms:
        await page.wait_for_timeout(wait_after_click_ms)

    await _vent_paa_godkend_knap_ikke_synlig(
        page=page,
        selector=selector,
        timeout=timeout,
    )

    print("Godkend-knappen er ikke længere synlig. Brevet er sendt.")
    return True


async def _find_eneste_aktive_godkend_knap(
    page: Page,
    selector: str,
) -> Locator:
    """Find præcis én synlig og aktiv Godkend-knap."""
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før brevafsendelse.")

    candidates = page.locator(f"{selector}:visible")
    matches: list[Locator] = []

    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        try:
            text = _normaliser(await candidate.inner_text())
            if text.casefold() == "godkend" and await candidate.is_enabled():
                matches.append(candidate)
        except Exception:
            continue

    if not matches:
        raise PlaywrightTimeoutError(
            "En synlig og aktiv Godkend-knap til Send brev blev ikke fundet."
        )

    if len(matches) > 1:
        raise RuntimeError(
            "Flere synlige og aktive Godkend-knapper blev fundet. "
            "Brevet er ikke afsendt."
        )

    return matches[0]


async def _vent_paa_godkend_knap_ikke_synlig(
    page: Page,
    selector: str,
    timeout: int,
) -> None:
    """Vent på, at ingen synlig og aktiv Godkend-knap længere findes."""
    elapsed = 0

    while elapsed < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket, før brevafsendelsen kunne verificeres."
            )

        visible_and_enabled = 0
        candidates = page.locator(f"{selector}:visible")

        try:
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                try:
                    text = _normaliser(await candidate.inner_text())
                    if (
                        text.casefold() == "godkend"
                        and await candidate.is_enabled()
                    ):
                        visible_and_enabled += 1
                except Exception:
                    # Et udskiftet eller detached element tæller ikke som synligt.
                    continue
        except Exception:
            # Navigation kan kortvarigt udskifte DOM'en. Kontrollér igen i næste poll.
            await page.wait_for_timeout(POLL_INTERVAL_MS)
            elapsed += POLL_INTERVAL_MS
            continue

        if visible_and_enabled == 0:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Godkend-knappen er stadig synlig og aktiv efter klik. "
        "Brevet kunne derfor ikke verificeres som sendt."
    )

async def _bekraeft_send_brev_opgave(page: Page, timeout: int) -> None:
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket.")
    await page.wait_for_selector(
        KYSelectors.Borgere.SEND_BREV_HEADER,
        state="visible",
        timeout=timeout,
    )
    await page.wait_for_selector(
        KYSelectors.Borgere.SEND_BREV_SAGSVAELGER,
        state="visible",
        timeout=timeout,
    )


async def _find_sag_med_eksakt_sagsid(
    root: Locator,
    sags_id: str,
    timeout: int,
) -> Locator:
    wanted = sags_id.casefold()
    elapsed = 0
    while elapsed < timeout:
        rows = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_RAEKKER)
        matches: list[Locator] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                if not await row.is_visible():
                    continue
                value = _normaliser(
                    await row.locator("td:not(.handlinger)").first.inner_text()
                ).casefold()
                if value == wanted:
                    matches.append(row)
            except Exception:
                continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Flere rækker har SagsID '{sags_id}'.")
        await root.page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError(f"Ingen synlig række har SagsID '{sags_id}'.")


async def _dobbeltklik_paa_sagsraekke(
    page: Page,
    row: Locator,
    timeout: int,
) -> None:
    await row.wait_for(state="visible", timeout=timeout)
    await row.scroll_into_view_if_needed()
    cells = row.locator("td:not(.CUSTOM_HTML):not(.select-row):not(.handlinger)")
    if await cells.count() == 0:
        raise RuntimeError("Sagsrækken har ingen klikbare dataceller.")
    first_box = await cells.first.bounding_box()
    last_box = await cells.last.bounding_box()
    if first_box is None or last_box is None:
        raise RuntimeError("Sagsrækkens koordinater kunne ikke bestemmes.")
    left = first_box["x"]
    right = last_box["x"] + last_box["width"]
    top = min(first_box["y"], last_box["y"])
    bottom = max(
        first_box["y"] + first_box["height"],
        last_box["y"] + last_box["height"],
    )
    await page.mouse.dblclick(
        left + (right - left) / 2,
        top + (bottom - top) / 2,
        button="left",
        delay=100,
    )


async def _vent_paa_sagsvalg_registreret(
    root: Locator,
    sag_id: str,
    timeout: int,
) -> None:
    display = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_INPUT).first
    menu = root.locator(KYSelectors.Borgere.SEND_BREV_SAGSVAELGER_MENU).first
    elapsed = 0
    while elapsed < timeout:
        try:
            value = _normaliser(await display.input_value())
            chosen = root.locator(
                f"input[type='hidden'][value='{sag_id}'][data-chosen='true']"
            )
            if (
                await chosen.count() > 0
                and value.casefold() != "ingen sager valgt"
                and not await menu.is_visible()
            ):
                return
        except Exception:
            pass
        await root.page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError("Sagen blev ikke registreret som valgt.")


async def _find_unique_template(
    page: Page,
    title: str,
    timeout: int,
) -> Locator:
    wanted = title.casefold()
    elapsed = 0
    while elapsed < timeout:
        candidates = page.locator(KYSelectors.Borgere.SEND_BREV_BREVSKABELON_TITLER)
        matches: list[Locator] = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                text = _normaliser(
                    await candidate.get_attribute("data-titel")
                    or await candidate.inner_text()
                )
                if text.casefold() == wanted:
                    matches.append(candidate)
            except Exception:
                continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError("Flere brevskabeloner matcher titlen.")
        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError(f"Brevskabelonen '{title}' blev ikke fundet.")


async def _vent_paa_standard_bilag_dropdown(
    page: Page,
    timeout: int,
) -> Locator:
    elapsed = 0
    selector = (
        "div.skabelon-vaelger.dropdown-menu:visible:has("
        "input.skabelonvaelger-soeg[placeholder='Søg efter vedhæftning']"
        ")"
    )
    while elapsed < timeout:
        menus = page.locator(selector)
        for index in range(await menus.count()):
            menu = menus.nth(index)
            try:
                if await menu.is_visible():
                    return menu
            except Exception:
                continue
        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError("Standard bilag-dropdownen blev ikke synlig.")


async def _vent_paa_standard_bilag_resultat(
    menu: Locator,
    bilag_titel: str,
    timeout: int,
) -> Locator:
    wanted = bilag_titel.casefold()
    elapsed = 0
    while elapsed < timeout:
        candidates = menu.locator(
            "li.hg-skabelon.cell.VEDHAEFTNING[data-titel][data-noegle]"
        )
        matches: list[Locator] = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                title = _normaliser(
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
            raise RuntimeError(f"Flere standardbilag har titlen '{bilag_titel}'.")
        await menu.page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError(f"Standardbilaget '{bilag_titel}' blev ikke fundet.")


async def _find_tilfoej_bilag_i_samme_raekke(
    field: Locator,
    timeout: int,
) -> Locator:
    """Find kun Tilføj bilag-linket i Standard bilags egen tabelrække."""

    row = field.locator("xpath=ancestor::tr[1]")
    await row.wait_for(state="visible", timeout=timeout)
    pattern = re.compile(r"^\s*Tilføj bilag\s*$", re.IGNORECASE)
    controls = row.locator("a:visible, button:visible, input[type='button']:visible")
    matches: list[Locator] = []
    for index in range(await controls.count()):
        control = controls.nth(index)
        if not await control.is_enabled():
            continue
        text = _normaliser(
            await control.get_attribute("value") or await control.inner_text()
        )
        if pattern.fullmatch(text):
            matches.append(control)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PlaywrightTimeoutError(
            "Tilføj bilag blev ikke fundet i Standard bilag-rækken."
        )
    raise RuntimeError(
        "Flere Tilføj bilag-kontroller blev fundet i Standard bilag-rækken."
    )


async def _vent_paa_standard_bilag_tilfoejet(
    page: Page,
    field: Locator,
    bilag_noegle: str,
    timeout: int,
) -> None:
    elapsed = 0
    while elapsed < timeout:
        try:
            field_value = _normaliser(await field.input_value())
            registered = page.locator(
                f"input[type='hidden'][value='{bilag_noegle}'], "
                f"[data-noegle='{bilag_noegle}'].selected, "
                f"[data-noegle='{bilag_noegle}'].active"
            )
            if not field_value or await registered.count() > 0:
                return
        except Exception:
            # Tilføj bilag kan genopbygge rækken og detach'e feltet.
            return
        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError("Standardbilaget blev ikke registreret.")


async def _wait_for_input_value(
    input_field: Locator,
    expected: str,
    timeout: int,
) -> None:
    elapsed = 0
    while elapsed < timeout:
        try:
            value = _normaliser(await input_field.input_value())
            if value.casefold() == expected.casefold():
                return
        except Exception:
            pass
        await input_field.page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed += POLL_INTERVAL_MS
    raise PlaywrightTimeoutError(f"Feltet viste ikke værdien '{expected}'.")


async def _set_checkbox(locator: Locator, checked: bool) -> None:
    await locator.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
    if await locator.is_checked() != checked:
        await locator.set_checked(checked)


def _normaliser(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

