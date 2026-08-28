"""Asynkron borgerfunktionalitet til KY.

Modulet bruger udelukkende ``playwright.async_api``. Funktioner, der tidligere
havde suffikset ``_async``, har nu samme navn uden suffikset, men er fortsat
defineret med ``async def`` og skal kaldes med ``await``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.selectors import KYSelectors

ACTION_TIMEOUT_MS = 30_000
OPGAVE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250
STABLE_CHECKS = 4
WAIT_AFTER_TAB_CLOSE_MS = 1_000
MAX_CLOSE_ATTEMPTS = 3

AKTIV_OPGAVE_ID_KEY = "Aktiv Opgave-Id"
AKTIV_OPGAVE_URL_KEY = "Aktiv Opgave URL"
AKTIV_OPGAVE_NAVN_KEY = "Aktiv Opgavenavn"

PERSON_TAB_SELECTOR = "li.tab.topmenu-tab[data-tab-target-id='PERSON']"
PERSON_CLOSE_BUTTON_SELECTOR = (
    "li.tab.topmenu-tab[data-tab-target-id='PERSON'] "
    ".navigation-close-tab[data-entity-type='PERSON']"
)
ACTIVE_PERSON_TAB_SELECTOR = "li.tab.topmenu-tab.active[data-tab-target-id='PERSON']"


class Personoplysning(TypedDict):
    """En synlig nøgle/værdi-række fra Personoplysninger."""

    felt: str
    vaerdi: str


class BorgerResultat(TypedDict):
    """Valideret resultat fra et borgeropslag."""

    pId: str
    borger_url: str
    Personoplysninger: list[Personoplysning]


class BorgereClient:
    """Offentlig facade til asynkron borgerfunktionalitet i KY.

    ``ky_client_or_page`` kan enten være en async Playwright ``Page`` eller
    et klientobjekt med en async Playwright-side i attributten ``page``.
    De private modul-funktioner anvendes kun internt af denne facade.
    """

    def __init__(self, ky_client_or_page: object) -> None:
        page = getattr(ky_client_or_page, "page", ky_client_or_page)
        if not isinstance(page, Page):
            raise TypeError(
                "BorgereClient kræver en async Playwright Page eller "
                "et objekt med en async Page i attributten 'page'."
            )
        if page.is_closed():
            raise RuntimeError("KY-siden er lukket før BorgereClient oprettes.")

        self._page: Page = page
        self.p_id: str | None = None
        self.borger_url: str | None = None
        self._registrerede_borgerfane_ids: list[str] = []

    @property
    def page(self) -> Page:
        """Returnér klientens aktive Playwright-side."""

        if self._page.is_closed():
            raise RuntimeError("KY-siden er lukket.")
        return self._page

    async def naviger_til_borger(
        self,
        cpr: str,
        timeout: int = 120_000,
        max_forsog: int = 3,
    ) -> str:
        """Fremsøg en borger og registrér fanen, som opslaget åbner."""

        aabne_faner_foer = await _hent_aabne_borger_ids(self.page)

        borger_url = await naviger_til_borger(
            page=self.page,
            cpr=cpr,
            timeout=timeout,
            max_forsog=max_forsog,
        )

        person_id = _hent_person_id_fra_borger_url(borger_url)

        if not person_id:
            raise RuntimeError("Den validerede borger-URL mangler en gyldig pId.")

        aabne_faner_efter = await _hent_aabne_borger_ids(self.page)

        nye_faner = aabne_faner_efter - aabne_faner_foer

        for entity_id in nye_faner:
            if entity_id not in self._registrerede_borgerfane_ids:
                self._registrerede_borgerfane_ids.append(entity_id)

        # Normalt matcher pId og data-entity-id. Hvis den aktuelle pId
        # faktisk findes blandt fanernes entity-id'er, registreres den også.
        if (
            person_id in aabne_faner_efter
            and person_id not in self._registrerede_borgerfane_ids
        ):
            self._registrerede_borgerfane_ids.append(person_id)

        self.borger_url = borger_url
        self.p_id = person_id

        print(
            "Registrerede PERSON-faner åbnet via BorgereClient: "
            f"{self._registrerede_borgerfane_ids}",
            flush=True,
        )

        return borger_url

    async def hent_personoplysninger(
        self,
        cpr: str,
        timeout: int = 120_000,
    ) -> list[Personoplysning]:
        """Læs alle validerede Personoplysninger for den aktive borger."""

        normaliseret_cpr = _normaliser_cpr(cpr)
        if not normaliseret_cpr.isdigit() or len(normaliseret_cpr) != 10:
            raise ValueError("CPR skal bestå af præcis 10 cifre.")

        table = await _vent_paa_personoplysninger(
            page=self.page,
            forventet_cpr=normaliseret_cpr,
            timeout_ms=timeout,
        )
        snapshot = await _laes_personoplysninger_snapshot(table)
        snapshot_cpr = _normaliser_cpr(str(snapshot.get("cpr", "")))
        if snapshot_cpr != normaliseret_cpr:
            raise RuntimeError("Personoplysninger tilhører ikke det forventede CPR.")

        raw_rows = snapshot.get("rows", [])
        if not isinstance(raw_rows, list):
            raise RuntimeError("Personoplysninger gav ikke en gyldig rækkeliste.")

        resultat: list[Personoplysning] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, list) or len(raw_row) != 2:
                continue
            felt = _normaliser_tekst(str(raw_row[0]))
            vaerdi = _normaliser_tekst(str(raw_row[1]))
            if felt:
                resultat.append({"felt": felt, "vaerdi": vaerdi})

        if not resultat:
            raise RuntimeError(
                "Personoplysninger indeholdt ingen læsbare nøgle/værdi-rækker."
            )
        return resultat

    async def hent_borger(
        self,
        cpr: str,
        timeout: int = 120_000,
        max_forsog: int = 3,
    ) -> BorgerResultat:
        """Fremsøg borgeren og returnér pId, URL og Personoplysninger."""

        borger_url = await self.naviger_til_borger(
            cpr=cpr,
            timeout=timeout,
            max_forsog=max_forsog,
        )
        personoplysninger = await self.hent_personoplysninger(
            cpr=cpr,
            timeout=timeout,
        )
        if not self.p_id:
            raise RuntimeError("Borgeropslaget mangler pId efter validering.")

        return {
            "pId": self.p_id,
            "borger_url": borger_url,
            "Personoplysninger": personoplysninger,
        }

    @staticmethod
    def format_personoplysninger(
        personoplysninger: Sequence[Personoplysning],
    ) -> str:
        """Formatér Personoplysninger som læsbar tekst."""

        if not personoplysninger:
            return "(ingen personoplysninger)"
        feltbredde = max(len(oplysning["felt"]) for oplysning in personoplysninger)
        return "\n".join(
            f"{oplysning['felt']:<{feltbredde}} : {oplysning['vaerdi']}"
            for oplysning in personoplysninger
        )

    async def hent_aabne_borger_ids(self) -> set[str]:
        """Returnér entity-id'er for alle aktuelt åbne PERSON-faner."""

        return await _hent_aabne_borger_ids(self.page)

    async def luk_borgerfaner(
        self,
        entity_ids: Sequence[str],
        timeout_ms: int = ACTION_TIMEOUT_MS,
        maks_forsog: int = MAX_CLOSE_ATTEMPTS,
        vent_efter_gennemloeb_ms: int = WAIT_AFTER_TAB_CLOSE_MS,
    ) -> None:
        """Luk flere PERSON-faner robust og verificér den endelige tilstand."""

        # Den eksisterende modul-funktion udfører selv op til tre gennemløb
        # og genfinder faneknapperne efter KY's DOM-opdateringer.
        await luk_borgerfaner(
            page=self.page,
            entity_ids=entity_ids,
            timeout_ms=timeout_ms,
            maks_forsog=maks_forsog,
            vent_efter_gennemloeb_ms=vent_efter_gennemloeb_ms,
        )


async def naviger_til_borger(
    page: Page,
    cpr: str,
    timeout: int = 120_000,
    max_forsog: int = 3,
) -> str:
    """Fremsøg en borger og kontrollér CPR mod Personoplysninger.

    Hele CPR-værdien indsættes på én gang med ``fill()``. Efter opslaget
    læses CPR fra den synlige Personoplysninger-tabel. Hvis tabellens CPR
    ikke matcher det fremsøgte CPR, lukkes den aktuelle borgerfane, og
    opslaget gentages. Efter ``max_forsog`` mislykkede forsøg stoppes flowet.

    Args:
        page: Aktiv async Playwright-side i KY.
        cpr: CPR med 10 cifre eller bindestreg mellem de sidste fire cifre.
        timeout: Maksimal ventetid pr. opslag i millisekunder.
        max_forsog: Højst antal opslag. Standard er tre.

    Returns:
        Den validerede borger-URL med ``pId``.
    """
    cpr = _normaliser_cpr(cpr)
    if not cpr.isdigit() or len(cpr) != 10:
        raise ValueError("CPR skal bestå af præcis 10 cifre.")
    if max_forsog < 1:
        raise ValueError("max_forsog skal være mindst 1.")
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før borgeropslaget.")

    seneste_fundne_cpr = ""
    seneste_fejl = ""

    for forsog in range(1, max_forsog + 1):
        print()
        print("=" * 70)
        print(f"BORGEROPSLAG FORSØG {forsog}/{max_forsog}")
        print("=" * 70)

        aabne_ids_foer = await _hent_aabne_borger_ids(page)

        try:
            search_input = page.locator(KYSelectors.Main.TOP_SEARCH).first
            await search_input.wait_for(state="visible", timeout=timeout)
            await search_input.scroll_into_view_if_needed()

            # fill() erstatter hele feltets værdi på én gang og udsender input.
            await search_input.fill(cpr)
            await search_input.dispatch_event("change")

            actual_cpr = _normaliser_cpr(await search_input.input_value())
            if actual_cpr != cpr:
                raise RuntimeError("CPR blev ikke indsat korrekt i topsearch.")

            print("Hele CPR-værdien er indsat i topsearch.")
            await search_input.press("Enter")
            print("Første Enter er sendt.")

            try:
                table = await _vent_paa_stabil_personoplysninger_tabel(
                    page=page,
                    timeout_ms=min(15_000, timeout),
                )
            except PlaywrightTimeoutError:
                # Første Enter kan alene vælge autocomplete-resultatet.
                search_input = page.locator(KYSelectors.Main.TOP_SEARCH).first
                await search_input.wait_for(state="visible", timeout=timeout)
                if _normaliser_cpr(await search_input.input_value()) != cpr:
                    await search_input.fill(cpr)
                    await search_input.dispatch_event("change")
                await search_input.press("Enter")
                print("Andet Enter er sendt.")
                table = await _vent_paa_stabil_personoplysninger_tabel(
                    page=page,
                    timeout_ms=timeout,
                )

            fundet_cpr = await _hent_cpr_fra_personoplysninger(table)
            seneste_fundne_cpr = fundet_cpr

            print(f"Fremsøgt CPR: {_masker_cpr(cpr)}")
            print(f"CPR fra Personoplysninger: {_masker_cpr(fundet_cpr)}")

            if fundet_cpr != cpr:
                seneste_fejl = (
                    "CPR fra Personoplysninger matcher ikke det fremsøgte CPR."
                )
                print("Forkert borger blev vist.")
                await _luk_forkert_borgerfane(
                    page=page,
                    aabne_ids_foer=aabne_ids_foer,
                    timeout_ms=30_000,
                )
                if forsog < max_forsog:
                    await page.wait_for_timeout(1_000)
                continue

            borger_url = page.url
            person_id = _hent_person_id_fra_borger_url(borger_url)
            if not person_id:
                raise RuntimeError(
                    "Borgerens URL mangler en gyldig pId efter opslaget."
                )

            print(
                "Borgeropslaget er valideret: CPR i "
                "Personoplysninger matcher det fremsøgte CPR."
            )
            return borger_url

        except Exception as error:
            seneste_fejl = f"{type(error).__name__}: {error}"
            print(f"Borgeropslag fejlede på forsøg {forsog}: {seneste_fejl}")
            await _luk_faner_aabnet_under_forsog(
                page=page,
                aabne_ids_foer=aabne_ids_foer,
                timeout_ms=30_000,
            )
            if forsog < max_forsog:
                await page.wait_for_timeout(1_000)

    raise RuntimeError(
        "Borgeropslaget blev stoppet efter "
        f"{max_forsog} mislykkede forsøg. "
        f"Fremsøgt CPR: {_masker_cpr(cpr)}. "
        "Senest fundne CPR: "
        f"{_masker_cpr(seneste_fundne_cpr)}. "
        f"Seneste fejl: {seneste_fejl or 'ukendt fejl'}."
    )


async def _vent_paa_personoplysninger(
    page: Page,
    forventet_cpr: str,
    timeout_ms: int,
):
    """Vent på en stabil tabel og kræv, at CPR matcher forventningen."""
    table = await _vent_paa_stabil_personoplysninger_tabel(
        page=page,
        timeout_ms=timeout_ms,
    )
    fundet_cpr = await _hent_cpr_fra_personoplysninger(table)
    forventet_cpr = _normaliser_cpr(forventet_cpr)
    if fundet_cpr != forventet_cpr:
        raise PlaywrightTimeoutError(
            "Personoplysninger viser et andet CPR end det fremsøgte. "
            f"Forventet={_masker_cpr(forventet_cpr)}, "
            f"fundet={_masker_cpr(fundet_cpr)}."
        )
    return table


async def _vent_paa_stabil_personoplysninger_tabel(
    page: Page,
    timeout_ms: int,
):
    """Returnér den ene synlige Personoplysninger-tabel, når den er stabil."""
    elapsed_ms = 0
    poll_interval_ms = 250
    stable_required = 4
    stable_count = 0
    previous_signature: tuple[tuple[str, str], ...] | None = None
    last_diagnostic: dict[str, object] = {}

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under borgeropslaget.")

        candidates: list[tuple[object, dict[str, object]]] = []
        for frame in page.frames:
            try:
                tables = frame.locator(KYSelectors.Borgere.PERSON_OPLYSNINGER)
                for table_index in range(await tables.count()):
                    table = tables.nth(table_index)
                    if not await table.is_visible():
                        continue
                    snapshot = await _laes_personoplysninger_snapshot(table)
                    readable_rows = snapshot.get("rows", [])
                    if readable_rows:
                        candidates.append((table, snapshot))
            except Exception:
                # KY kan kortvarigt detach'e tabellen under faneskift.
                continue

        last_diagnostic = {
            "url": page.url,
            "synlige_laesbare_tabeller": len(candidates),
            "stabile_kontroller": stable_count,
        }

        if len(candidates) == 1:
            table, snapshot = candidates[0]
            rows = tuple(
                (str(row[0]), str(row[1]))
                for row in snapshot.get("rows", [])
                if isinstance(row, list) and len(row) == 2
            )
            signature = rows
            if signature == previous_signature:
                stable_count += 1
            else:
                previous_signature = signature
                stable_count = 1

            if stable_count >= stable_required and await table.is_visible():
                return table
        else:
            stable_count = 0
            previous_signature = None

        if elapsed_ms % 2_000 == 0:
            print(f"Venter på stabil Personoplysninger-tabel: {last_diagnostic}")

        await page.wait_for_timeout(poll_interval_ms)
        elapsed_ms += poll_interval_ms

    raise PlaywrightTimeoutError(
        "Personoplysninger blev ikke stabilt læst inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"Diagnose={last_diagnostic!r}."
    )


async def _laes_personoplysninger_snapshot(table) -> dict[str, object]:
    """Læs alle synlige nøgle/værdi-rækker og CPR i ét DOM-snapshot."""
    snapshot = await table.evaluate(
        r"""
        table => {
            const normalize = value =>
                (value || '').replace(/\s+/g, ' ').trim();
            const normalizeLabel = value => normalize(value)
                .replace(/:$/, '')
                .trim()
                .toLocaleLowerCase('da-DK');
            const cprLabels = new Set([
                'cpr', 'cpr-nummer', 'cpr nummer', 'personnummer'
            ]);
            const isVisible = element => {
                const style = window.getComputedStyle(element);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && element.getClientRects().length > 0;
            };

            const rows = [];
            let cpr = '';
            for (const row of table.querySelectorAll(
                'tbody.datatable-tbody > tr.table-row'
            )) {
                if (!isVisible(row)) continue;
                const cells = Array.from(row.querySelectorAll(
                    ':scope > td:not(.handlinger)'
                )).filter(isVisible).map(cell => normalize(cell.innerText));
                if (cells.length < 2) continue;

                const label = cells[0];
                const value = cells[1];
                if (!label) continue;
                rows.push([label, value]);

                if (cprLabels.has(normalizeLabel(label))) {
                    const match = value.match(
                        /(?:^|\D)(\d{6})[\s-]?(\d{4})(?!\d)/
                    );
                    cpr = match ? `${match[1]}${match[2]}` : value;
                }
            }
            return { cpr, rows };
        }
        """
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("Personoplysninger gav ikke et læsbart DOM-snapshot.")
    return snapshot


async def _hent_cpr_fra_personoplysninger(table) -> str:
    """Læs CPR fra tabellen og ignorér fx alder i parentes."""
    snapshot = await _laes_personoplysninger_snapshot(table)
    cpr = _normaliser_cpr(str(snapshot.get("cpr", "")))
    if not cpr.isdigit() or len(cpr) != 10:
        raise RuntimeError("CPR-rækken kunne ikke læses korrekt fra Personoplysninger.")
    return cpr


async def _hent_aabne_borger_ids(page: Page) -> set[str]:
    """Returnér entity-id'er for alle åbne PERSON-faner."""
    selector = (
        "li.tab.topmenu-tab[data-tab-target-id='PERSON'] "
        ".navigation-close-tab[data-entity-type='PERSON']"
    )
    buttons = page.locator(selector)
    ids: set[str] = set()
    for index in range(await buttons.count()):
        try:
            entity_id = await buttons.nth(index).get_attribute("data-entity-id")
        except Exception:
            continue
        if entity_id:
            ids.add(entity_id.strip())
    return ids


async def _luk_forkert_borgerfane(
    page: Page,
    aabne_ids_foer: set[str],
    timeout_ms: int,
) -> None:
    """Luk fanen fra det fejlagtige opslag."""
    aabne_ids_efter = await _hent_aabne_borger_ids(page)
    nye_ids = aabne_ids_efter - aabne_ids_foer

    if nye_ids:
        for entity_id in nye_ids:
            await luk_borgerfane(page, entity_id, timeout_ms)
        return

    # Hvis KY genbrugte en eksisterende fane, lukkes den aktive PERSON-fane.
    active_selector = (
        "li.tab.topmenu-tab.active[data-tab-target-id='PERSON'] "
        ".navigation-close-tab[data-entity-type='PERSON']"
    )
    active = page.locator(active_selector).first
    if await active.count() == 0:
        raise RuntimeError(
            "Forkert borger blev vist, men den aktive borgerfane kunne ikke findes."
        )
    entity_id = await active.get_attribute("data-entity-id")
    if not entity_id:
        raise RuntimeError("Den aktive borgerfane mangler data-entity-id.")
    await luk_borgerfane(page, entity_id, timeout_ms)


async def _luk_faner_aabnet_under_forsog(
    page: Page,
    aabne_ids_foer: set[str],
    timeout_ms: int,
) -> None:
    """Ryd kun PERSON-faner op, som blev åbnet under det aktuelle forsøg."""
    if page.is_closed():
        return
    aabne_ids_efter = await _hent_aabne_borger_ids(page)
    for entity_id in aabne_ids_efter - aabne_ids_foer:
        try:
            await luk_borgerfane(page, entity_id, timeout_ms)
        except Exception as error:
            print(
                f"Kunne ikke lukke PERSON-fane {entity_id}: "
                f"{type(error).__name__}: {error}"
            )


async def luk_borgerfane(
    page: Page,
    entity_id: str,
    timeout_ms: int,
) -> None:
    """Luk én PERSON-fane og vent på, at fanen forsvinder."""
    selector = (
        "li.tab.topmenu-tab[data-tab-target-id='PERSON'] "
        ".navigation-close-tab[data-entity-type='PERSON']"
        f"[data-entity-id='{entity_id}']"
    )
    button = page.locator(selector).first
    if await button.count() == 0:
        return

    await button.scroll_into_view_if_needed()
    await button.click(timeout=min(30_000, timeout_ms))
    await page.wait_for_timeout(300)

    # Håndter kendte dialoger med åbne opgaver.
    dialog_selectors = (
        KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_GEM,
        KYSelectors.Borgere.LUK_ALLE_OPGAVER_AFBRYD_OG_GEM,
    )
    for dialog_selector in dialog_selectors:
        try:
            candidates = page.locator(f"{dialog_selector}:visible")
            if await candidates.count() > 0:
                candidate = candidates.first
                if await candidate.is_enabled():
                    await candidate.click(timeout=min(30_000, timeout_ms))
                    break
        except Exception:
            continue

    elapsed_ms = 0
    while elapsed_ms < timeout_ms:
        if await page.locator(selector).count() == 0:
            print(f"PERSON-fanen blev lukket: {entity_id}")
            return
        await page.wait_for_timeout(250)
        elapsed_ms += 250

    raise PlaywrightTimeoutError(
        f"PERSON-fanen blev ikke lukket inden for tidsgrænsen. Entity-id={entity_id}."
    )


def _hent_person_id_fra_borger_url(url: str) -> str:
    """Returnér en gyldig UUID fra URL'ens pId, ellers en tom streng."""
    match = re.search(r"(?:[?&])pId=([0-9a-fA-F-]+)(?:&|$)", url or "")
    if not match:
        return ""
    person_id = match.group(1)
    uuid_pattern = (
        r"[0-9a-fA-F]{8}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{12}"
    )
    return person_id if re.fullmatch(uuid_pattern, person_id) else ""


def _normaliser_cpr(value: str) -> str:
    """Udtræk præcis CPR-delen og ignorér fx alder i parentes."""
    value = str(value or "").strip()
    match = re.search(r"(?<!\d)(\d{6})[\s-]?(\d{4})(?!\d)", value)
    if match:
        return match.group(1) + match.group(2)
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 10 else ""


def _masker_cpr(value: str) -> str:
    """Maskér CPR til brug i logoutput."""
    cpr = _normaliser_cpr(value)
    return f"******{cpr[-4:]}" if len(cpr) == 10 else "[ukendt CPR]"


# Hjælpefunktioner flyttet fra den fungerende test


async def find_aktivt_topsearch(
    page: Page,
    timeout_ms: int,
) -> tuple[Frame, Locator]:
    """Find et synligt, aktivt og redigerbart topSearch-felt."""

    selectors = (
        KYSelectors.Main.TOP_SEARCH,
        "input#topsearch",
        "input[name='topSearch']",
        "input[name='topsearch']",
        "input[id*='topsearch' i]",
        "input[name*='topsearch' i]",
        "input[class*='topsearch' i]",
        "input[placeholder*='CPR' i]",
        "input[aria-label*='CPR' i]",
        "input[placeholder*='Søg' i]",
        "input[aria-label*='Søg' i]",
        "input[type='search']",
        "[role='searchbox']",
    )

    elapsed_ms = 0
    last_diagnostic: list[dict[str, object]] = []

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under søgningen efter topSearch.")

        diagnostic: list[dict[str, object]] = []

        for frame in page.frames:
            for selector in selectors:
                try:
                    candidates = frame.locator(selector)
                    candidate_count = await candidates.count()

                    if candidate_count:
                        diagnostic.append(
                            {
                                "frame": frame.url,
                                "selector": selector,
                                "antal": candidate_count,
                            }
                        )

                    for index in range(candidate_count):
                        candidate = candidates.nth(index)

                        if not await candidate.is_visible():
                            continue

                        if not await candidate.is_enabled():
                            continue

                        if not await candidate.is_editable():
                            continue

                        return frame, candidate

                except Exception:
                    continue

        last_diagnostic = diagnostic

        if elapsed_ms % 2_000 == 0:
            print(
                f"Borgeropslag: venter på aktivt topSearch. Diagnose: {diagnostic}",
                flush=True,
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Et synligt, aktivt og redigerbart topSearch-felt blev "
        f"ikke fundet inden for {timeout_ms / 1000:.0f} sekunder. "
        f"URL: {page.url}. Diagnose: {last_diagnostic!r}"
    )


async def klik_topsearch_knap(
    page: Page,
) -> bool:
    """Find og klik en synlig KY-søgeknap."""

    selectors = (
        KYSelectors.Main.TOP_SEARCH_BUTTON,
        "div#topSearchBtn",
        "button#topSearchBtn",
        "#topSearchBtn button",
        "#topSearchBtn a",
        "button[aria-label*='Søg' i]",
        "button[title*='Søg' i]",
    )

    for frame in page.frames:
        for selector in selectors:
            try:
                buttons = frame.locator(selector)

                for index in range(await buttons.count()):
                    button = buttons.nth(index)

                    if not await button.is_visible():
                        continue

                    if not await button.is_enabled():
                        continue

                    await button.scroll_into_view_if_needed()

                    await button.click(
                        timeout=ACTION_TIMEOUT_MS,
                    )

                    print(
                        "Borgeropslag: KY's søgeknap blev klikket "
                        f"via selector {selector!r}.",
                        flush=True,
                    )

                    return True

            except Exception:
                continue

    return False


async def vent_paa_synlig_personoplysninger(
    page: Page,
    timeout_ms: int,
) -> Locator:
    """Returnér den første synlige tabel med læsbare rækker.

    Der foretages ingen CPR-kontrol.
    """

    elapsed_ms = 0
    last_diagnostic: list[dict[str, object]] = []

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under ventetiden på Personoplysninger."
            )

        diagnostic: list[dict[str, object]] = []

        for frame in page.frames:
            try:
                tables = frame.locator(KYSelectors.Borgere.PERSON_OPLYSNINGER)

                table_count = await tables.count()

                frame_diagnostic: dict[str, object] = {
                    "frame": frame.url,
                    "antal_tabeller": table_count,
                }

                diagnostic.append(frame_diagnostic)

                for index in range(table_count):
                    table = tables.nth(index)

                    if not await table.is_visible():
                        continue

                    readable_rows = await antal_laesbare_person_rows(table)

                    frame_diagnostic["synlig_tabel"] = True
                    frame_diagnostic["laesbare_raekker"] = readable_rows

                    if readable_rows > 0:
                        return table

            except Exception as error:
                diagnostic.append(
                    {
                        "frame": frame.url,
                        "fejl": (f"{type(error).__name__}: {error}"),
                    }
                )

        last_diagnostic = diagnostic

        if elapsed_ms % 2_000 == 0:
            print(
                f"Venter på synlig Personoplysninger-tabel: {diagnostic}",
                flush=True,
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "En synlig Personoplysninger-tabel med mindst én læsbar "
        f"række blev ikke fundet inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"URL: {page.url}. Diagnose: {last_diagnostic!r}"
    )


async def antal_laesbare_person_rows(
    table: Locator,
) -> int:
    """Returnér antallet af læsbare label/værdi-rækker."""

    return int(
        await table.evaluate(
            r"""
            table => {
                const normalize = value =>
                    (value || '')
                        .replace(/\s+/g, ' ')
                        .trim();

                const isVisible = element => {
                    const style = window.getComputedStyle(element);

                    return (
                        style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && element.getClientRects().length > 0
                    );
                };

                return Array.from(
                    table.querySelectorAll(
                        'tbody.datatable-tbody > tr.table-row'
                    )
                ).filter(row => {
                    if (!isVisible(row)) {
                        return false;
                    }

                    const cells = Array.from(
                        row.querySelectorAll(
                            ':scope > td:not(.handlinger)'
                        )
                    ).filter(isVisible);

                    return (
                        cells.length >= 2
                        && Boolean(normalize(cells[0].innerText))
                    );
                }).length;
            }
            """
        )
    )


async def laes_personoplysninger(
    table: Locator,
) -> list[dict[str, str]]:
    """Læs alle synlige label/værdi-rækker.

    Tredje kolonne, ``td.handlinger``, ignoreres.
    """

    rows = await table.evaluate(
        r"""
        table => {
            const normalize = value =>
                (value || '')
                    .replace(/\s+/g, ' ')
                    .trim();

            const isVisible = element => {
                const style = window.getComputedStyle(element);

                return (
                    style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && element.getClientRects().length > 0
                );
            };

            return Array.from(
                table.querySelectorAll(
                    'tbody.datatable-tbody > tr.table-row'
                )
            )
                .filter(isVisible)
                .map(row => {
                    const cells = Array.from(
                        row.querySelectorAll(
                            ':scope > td:not(.handlinger)'
                        )
                    ).filter(isVisible);

                    return {
                        label: cells.length >= 1
                            ? normalize(cells[0].innerText)
                            : '',
                        value: cells.length >= 2
                            ? normalize(cells[1].innerText)
                            : '',
                        data_id:
                            row.getAttribute('data-id') || ''
                    };
                })
                .filter(row => row.label);
        }
        """
    )

    if not isinstance(rows, list):
        raise RuntimeError("Personoplysninger blev ikke returneret som en liste.")

    result: list[dict[str, str]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        label = str(row.get("label", "")).strip()

        if not label:
            continue

        result.append(
            {
                "label": label,
                "value": str(row.get("value", "")).strip(),
                "data_id": str(row.get("data_id", "")).strip(),
            }
        )

    return result


def print_personoplysninger(
    rows: list[dict[str, str]],
    heading: str,
) -> None:
    """Print alle udtrukne personoplysninger."""

    assert rows, f"Personoplysninger for {heading} indeholdt ingen læsbare rækker."

    print(
        "",
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )
    print(
        heading,
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )

    for row_number, row in enumerate(
        rows,
        start=1,
    ):
        print(
            f"{row_number:02d}. {row['label']}: {row['value']}",
            flush=True,
        )

    print(
        "-" * 70,
        flush=True,
    )
    print(
        f"Antal printede felter: {len(rows)}",
        flush=True,
    )
    print(
        "-" * 70,
        flush=True,
    )


async def hent_person_tab_ids(
    page: Page,
) -> list:

    if page.is_closed():
        return []

    close_buttons = page.locator(PERSON_CLOSE_BUTTON_SELECTOR)

    entity_ids: list[str] = []

    for index in range(await close_buttons.count()):
        try:
            entity_id = await close_buttons.nth(index).get_attribute("data-entity-id")
        except Exception:
            continue

        if not entity_id:
            continue

        entity_id = entity_id.strip()

        if entity_id and entity_id not in entity_ids:
            entity_ids.append(entity_id)

    return entity_ids


async def vent_paa_minimum_person_tabs(
    page: Page,
    minimum_count: int,
    timeout_ms: int,
) -> None:
    """Vent på mindst det angivne antal PERSON-faner."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        current_count = len(await hent_person_tab_ids(page))

        if current_count >= minimum_count:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    current_ids = await hent_person_tab_ids(page)

    raise PlaywrightTimeoutError(
        "Det forventede antal PERSON-faner blev ikke åbnet. "
        f"Forventede mindst {minimum_count}, "
        f"men fandt {len(current_ids)}. "
        f"Fundne entity-id'er: {current_ids}"
    )


async def luk_borgerfaner(
    page: Page,
    entity_ids: Sequence[str],
    timeout_ms: int = ACTION_TIMEOUT_MS,
    maks_forsog: int = MAX_CLOSE_ATTEMPTS,
    vent_efter_gennemloeb_ms: int = WAIT_AFTER_TAB_CLOSE_MS,
) -> None:
    """Luk de angivne PERSON-faner robust og verificér resultatet."""

    if maks_forsog < 1:
        raise ValueError("maks_forsog skal være mindst 1.")

    wanted_ids = list(
        dict.fromkeys(
            str(entity_id).strip() for entity_id in entity_ids if str(entity_id).strip()
        )
    )

    if not wanted_ids:
        return

    for attempt in range(
        1,
        maks_forsog + 1,
    ):
        open_ids = set(await hent_person_tab_ids(page))

        remaining_ids = [entity_id for entity_id in wanted_ids if entity_id in open_ids]

        if not remaining_ids:
            print(
                "Alle borgerfaner er lukket.",
                flush=True,
            )
            return

        print(
            f"Lukkeforsøg {attempt}/{maks_forsog}. "
            f"Resterende faner: {len(remaining_ids)}",
            flush=True,
        )

        # Luk fanerne én ad gangen. Genfind knappen før hvert klik,
        # da KY genopbygger fanemenuen efter lukningen.
        for entity_id in remaining_ids:
            try:
                await luk_borgerfane(
                    page=page,
                    entity_id=entity_id,
                    timeout_ms=timeout_ms,
                )
            except Exception as error:
                print(
                    "Kunne ikke lukke PERSON-fane "
                    f"{entity_id}: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        await page.wait_for_timeout(vent_efter_gennemloeb_ms)

    open_ids = set(await hent_person_tab_ids(page))

    remaining_ids = [entity_id for entity_id in wanted_ids if entity_id in open_ids]

    if remaining_ids:
        raise AssertionError(
            "Det lykkedes ikke at lukke alle borgerfaner. "
            f"Resterende entity-id'er: {remaining_ids}"
        )


async def haandter_eventuel_lukke_dialog(
    page: Page,
) -> None:
    """Klik 'Afbryd og gem', hvis KY viser en dialog ved fanelukning."""

    selectors = (
        KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_GEM,
        KYSelectors.Borgere.LUK_ALLE_OPGAVER_AFBRYD_OG_GEM,
    )

    # Giv dialogen kort tid til at blive synlig. Hvis der ikke kommer
    # en dialog, fortsætter funktionen uden fejl.
    await page.wait_for_timeout(300)

    for selector in selectors:
        try:
            buttons = page.locator(f"{selector}:visible")

            if await buttons.count() == 0:
                continue

            button = buttons.first

            if not await button.is_enabled():
                continue

            print(
                "KY viste en dialog ved fanelukning. Klikker på 'Afbryd og gem'.",
                flush=True,
            )

            await button.click(
                timeout=ACTION_TIMEOUT_MS,
            )

            return

        except Exception:
            continue


async def vent_paa_person_fane_lukket(
    page: Page,
    entity_id: str,
    timeout_ms: int,
) -> None:
    """Vent på, at PERSON-fanens lukknap er fjernet eller skjult."""

    selector = f"{PERSON_CLOSE_BUTTON_SELECTOR}[data-entity-id='{entity_id}']"

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        buttons = page.locator(selector)

        if await buttons.count() == 0:
            return

        visible = False

        for index in range(await buttons.count()):
            try:
                if await buttons.nth(index).is_visible():
                    visible = True
                    break
            except Exception:
                continue

        if not visible:
            return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "PERSON-fanen blev ikke lukket inden for "
        f"{timeout_ms / 1000:.0f} sekunder. "
        f"Entity-id: {entity_id}"
    )


# Generisk asynkron opgavefunktionalitet


class OpgaveCheckpoint(TypedDict):
    """Data, som kan gemmes og bruges til at genoptage en KY-opgave."""

    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]


async def aabn_opgave_og_hent_url(
    page: Page,
    menu_sti: Sequence[str],
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpgaveCheckpoint:
    """Åbn en opgave fra Handlinger og returnér et genoptageligt checkpoint.

    Args:
        page: Aktiv async Playwright-side på den fremsøgte borger.
        menu_sti: Menupunkter efter Handlinger, fx
            ``("Administration", "Send brev")``.
        timeout: Maksimal ventetid i millisekunder.

    Returns:
        Dynamisk opgave-id, opgavenavn, absolut opgave-URL, borger-URL og
        den anvendte menusti.

    Funktionen udfylder ikke opgaven. Checkpointet bør gemmes straks efter
    retur, før en separat opgavefunktion fortsætter behandlingen.
    """

    normalized_path = tuple(
        _normaliser_tekst(item) for item in menu_sti if _normaliser_tekst(item)
    )

    if not normalized_path:
        raise ValueError("menu_sti skal indeholde mindst ét menupunkt.")
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket, før opgaven kan åbnes.")

    borger_url = page.url
    if not borger_url:
        raise RuntimeError("Borger-URL kunne ikke læses før opgaveåbning.")

    handlinger = page.locator(KYSelectors.Borgere.HANDLINGER_DROPDOWN).first
    await handlinger.wait_for(state="visible", timeout=timeout)
    await handlinger.scroll_into_view_if_needed()

    if not await handlinger.is_enabled():
        raise RuntimeError("Handlinger er synlig, men ikke klikbar.")

    await handlinger.click(timeout=ACTION_TIMEOUT_MS)

    for menu_text in normalized_path:
        menu_item = await _find_synligt_handlinger_menupunkt(
            page=page,
            text=menu_text,
            timeout=timeout,
        )
        await menu_item.scroll_into_view_if_needed()
        await menu_item.click(timeout=ACTION_TIMEOUT_MS)

    await _vent_paa_opgave_checkpoint(
        page=page,
        timeout=timeout,
    )

    header = page.locator("div#opgave-header.block-heading").first
    undock_button = header.locator(
        "a.undock_panel_button[data-opgave-id][data-url]"
    ).first

    await header.wait_for(state="visible", timeout=timeout)
    await undock_button.wait_for(state="attached", timeout=timeout)

    opgave_id = _normaliser_tekst(
        await undock_button.get_attribute("data-opgave-id") or ""
    )
    relative_url = _normaliser_tekst(
        await undock_button.get_attribute("data-url") or ""
    )
    opgave_navn = await _hent_opgavenavn_fra_header(header)

    if not opgave_id:
        raise RuntimeError("Den åbnede opgave mangler data-opgave-id.")
    if not relative_url:
        raise RuntimeError("Den åbnede opgave mangler data-url.")
    if opgave_id not in relative_url:
        raise RuntimeError(
            "Opgave-ID og opgave-URL matcher ikke. "
            f"ID={opgave_id!r}, URL={relative_url!r}."
        )

    opgave_url = urljoin(page.url, relative_url)

    checkpoint: OpgaveCheckpoint = {
        "opgave_id": opgave_id,
        "opgave_navn": opgave_navn,
        "opgave_url": opgave_url,
        "borger_url": borger_url,
        "menu_sti": normalized_path,
    }

    print()
    print("=" * 70)
    print("OPGAVE-CHECKPOINT ER KLAR")
    print(f"Menusti: Handlinger > {' > '.join(normalized_path)}")
    print(f"Opgavenavn: {opgave_navn}")
    print(f"Opgave-ID: {opgave_id}")
    print(f"Opgave-URL: {opgave_url}")
    print(f"Borger-URL: {borger_url}")
    print("=" * 70)

    return checkpoint


async def _find_synligt_handlinger_menupunkt(
    page: Page,
    text: str,
    timeout: int,
) -> Locator:
    """Find et eksakt, synligt menupunkt i Handlinger-menuen."""

    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    elapsed_ms = 0

    while elapsed_ms < timeout:
        container = page.locator("li#handlinger-dropdown").first

        try:
            candidates = container.locator(
                "a, button, [role='menuitem']",
                has_text=pattern,
            )

            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                if not await candidate.is_visible():
                    continue
                if not await candidate.is_enabled():
                    continue

                visible_text = _normaliser_tekst(await candidate.inner_text())
                if pattern.fullmatch(visible_text):
                    return candidate
        except Exception:
            # KY genopbygger dropdown-DOM'en, når undermenuer åbnes.
            pass

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        f"Menupunktet '{text}' blev ikke fundet i Handlinger inden for "
        f"{timeout / 1000:.0f} sekunder."
    )


async def _vent_paa_opgave_checkpoint(
    page: Page,
    timeout: int,
) -> None:
    """Vent på stabil header, skjult loader og læsbart dynamisk opgave-ID."""

    elapsed_ms = 0
    stable_count = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under opgaveåbningen.")

        loader_visible = await _er_opgave_loader_synlig(page)
        header = page.locator("div#opgave-header.block-heading:visible")
        undock = page.locator(
            "div#opgave-header a.undock_panel_button[data-opgave-id][data-url]"
        )

        ready = False

        try:
            if (
                not loader_visible
                and await header.count() > 0
                and await undock.count() > 0
            ):
                button = undock.first
                opgave_id = _normaliser_tekst(
                    await button.get_attribute("data-opgave-id") or ""
                )
                data_url = _normaliser_tekst(
                    await button.get_attribute("data-url") or ""
                )
                classes = (await header.first.get_attribute("class") or "").split()

                ready = (
                    bool(opgave_id)
                    and bool(data_url)
                    and opgave_id in data_url
                    and "expanded" in classes
                )
        except Exception:
            ready = False

        if ready:
            stable_count += 1
            if stable_count >= STABLE_CHECKS:
                return
        else:
            stable_count = 0

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Opgaven blev åbnet, men et stabilt checkpoint med opgave-ID og "
        f"opgave-URL blev ikke tilgængeligt. Aktuel URL: {page.url}"
    )


"""Generel opstart af KY-opgaver.

Indsæt indholdet i ``ky_client/functionality/borgere.py``. Funktionen er en
fælles indgang til eksisterende ``aabn_opgave_og_hent_url`` og indeholder
ingen Send brev-specifik logik.
"""


class OpstartOpgaveCheckpoint(TypedDict):
    """Checkpoint for en ny eller sikkert genoptaget KY-opgave."""

    opgave_id: str
    opgave_navn: str
    opgave_url: str
    borger_url: str
    menu_sti: tuple[str, ...]
    genoptaget: bool
    kilde: str


async def opstart_opgave(
    page: Page,
    menu_sti: tuple[str, ...],
    item_data: dict[str, Any] | None = None,
    opgave_id: str | None = None,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpstartOpgaveCheckpoint:
    """Genoptag itemets ubehandlede opgave eller opret en ny.

    Funktionen leder altid i ``Ubehandlede opgaver`` foer oprettelse.

    En eksisterende raekke maa kun aabnes, naar alle disse krav er opfyldt:

    1. Raekkens ``data-id`` matcher itemets gemte aktive opgave-id.
    2. Linkets ``data-opgave-id`` matcher samme id.
    3. Opgavenavnet matcher sidste led i ``menu_sti`` eksakt.

    Dermed kan robotten ikke overtage en anden brugers ubehandlede opgave
    alene fordi opgaven hedder fx ``Skriv journalnotat`` eller ``Send brev``.

    ``opgave_id`` har hoejeste prioritet. Ellers udledes id fra item_data.
    Ved nyoprettelse gemmes checkpointet i item_data["box"] under de tre
    ``Aktiv ...``-noegler, saa et senere browserforloeb kan genoptage det.
    """

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket foer opstart af opgave.")

    normaliseret_menu_sti = tuple(
        _opstart_normaliser_tekst(menu_del)
        for menu_del in menu_sti
        if _opstart_normaliser_tekst(menu_del)
    )
    if not normaliseret_menu_sti:
        raise ValueError("menu_sti skal indeholde mindst et menupunkt.")

    forventet_opgavenavn = normaliseret_menu_sti[-1]
    borger_url = page.url

    aktivt_opgave_id = _opstart_normaliser_id(opgave_id)
    if aktivt_opgave_id is None and item_data is not None:
        aktivt_opgave_id = hent_aktivt_opgave_id_fra_item_data(item_data)

    # Der kigges altid i tabellen. Uden et id maa ingen eksisterende raekke
    # aabnes, fordi opgavenavnet ikke er tilstraekkeligt til ejerskabskontrol.
    eksisterende_raekke = await find_matchende_ubehandlet_opgave(
        page=page,
        forventet_opgave_id=aktivt_opgave_id,
        forventet_opgavenavn=forventet_opgavenavn,
        timeout=timeout,
    )

    if eksisterende_raekke is not None:
        checkpoint = await aabn_matchende_ubehandlet_opgave(
            page=page,
            row=eksisterende_raekke,
            forventet_opgave_id=aktivt_opgave_id,
            forventet_opgavenavn=forventet_opgavenavn,
            borger_url=borger_url,
            menu_sti=normaliseret_menu_sti,
            timeout=timeout,
        )
        _gem_aktivt_checkpoint_i_item_data(item_data, checkpoint)
        return checkpoint

    # Hvis itemets aktive id findes i tabellen med et andet opgavenavn,
    # rejser find_matchende_ubehandlet_opgave allerede en fejl. Kun et sikkert
    # "ikke fundet" maa falde videre til oprettelse.
    nyt_checkpoint = await aabn_opgave_og_hent_url(
        page=page,
        menu_sti=normaliseret_menu_sti,
        timeout=timeout,
    )

    checkpoint: OpstartOpgaveCheckpoint = {
        "opgave_id": str(nyt_checkpoint["opgave_id"]).strip(),
        "opgave_navn": forventet_opgavenavn,
        "opgave_url": str(nyt_checkpoint["opgave_url"]).strip(),
        "borger_url": str(nyt_checkpoint["borger_url"]).strip(),
        "menu_sti": tuple(nyt_checkpoint["menu_sti"]),
        "genoptaget": False,
        "kilde": "ny_opgave",
    }

    if not checkpoint["opgave_id"]:
        raise RuntimeError("Den nyoprettede opgave mangler opgave-id.")
    if checkpoint["opgave_navn"].casefold() != forventet_opgavenavn.casefold():
        raise RuntimeError(
            "Den nyoprettede opgaves navn matcher ikke menu_sti. "
            f"Forventet={forventet_opgavenavn!r}, "
            f"faktisk={checkpoint['opgave_navn']!r}."
        )

    _gem_aktivt_checkpoint_i_item_data(item_data, checkpoint)
    return checkpoint


def hent_aktivt_opgave_id_fra_item_data(
    item_data: dict[str, Any],
) -> str | None:
    """Udled kun det id, der sikkert kan knyttes til itemet.

    Prioritet:
    1. box["Aktiv Opgave-Id"]
    2. box["Aktiv Opgave URL"]
    3. box["Opgave-Id"]
    4. box["URL"]
    5. box["Original URL"]

    De to foerste noegler skrives af ``opstart_opgave`` efter nyoprettelse.
    """

    if not isinstance(item_data, dict):
        raise TypeError("item_data skal vaere en dictionary.")

    box = item_data.get("box")
    if not isinstance(box, dict):
        raise TypeError("item_data['box'] skal vaere en dictionary.")

    for key in (
        AKTIV_OPGAVE_ID_KEY,
        "Opgave-Id",
        "Opgave-ID",
        "opgave_id",
        "opgave-id",
    ):
        id_value = _opstart_normaliser_id(box.get(key))
        if id_value:
            return id_value

    for key in (
        AKTIV_OPGAVE_URL_KEY,
        "URL",
        "Original URL",
    ):
        url_value = str(box.get(key) or "").strip()
        if not url_value:
            continue
        id_value = hent_opgave_id_fra_url(url_value)
        if id_value:
            return id_value

    return None


def hent_opgave_id_fra_url(url: str) -> str | None:
    """Udled opgave-id fra kendte query-parametre eller KY-stier."""

    value = str(url or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    query = parse_qs(parsed.query)

    for query_key in (
        "opgaveId",
        "opgaveid",
        "opgave_id",
        "taskId",
        "taskid",
    ):
        query_values = query.get(query_key)
        if query_values:
            id_value = _opstart_normaliser_id(query_values[0])
            if id_value:
                return id_value

    path_match = re.search(
        r"/opgave/(?:undock/)?([A-Za-z0-9_-]{6,})/?$",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if path_match:
        return _opstart_normaliser_id(path_match.group(1))

    uuid_match = re.search(
        r"(?<![A-Fa-f0-9])"
        r"([A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-"
        r"[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12})"
        r"(?![A-Fa-f0-9])",
        value,
    )
    if uuid_match:
        return _opstart_normaliser_id(uuid_match.group(1))

    return None


async def find_matchende_ubehandlet_opgave(
    page: Page,
    forventet_opgave_id: str | None,
    forventet_opgavenavn: str,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> Locator | None:
    """Gennemgaa Ubehandlede opgaver og returner et sikkert id+navn-match.

    Uden ``forventet_opgave_id`` gennemgaas tabellen stadig, men ingen raekke
    maa returneres. Det er den bevidste beskyttelse mod at tage andres opgaver.
    """

    forventet_opgavenavn = _opstart_kraev_tekst(
        forventet_opgavenavn,
        "forventet_opgavenavn",
    )

    table = page.locator(KYSelectors.Borgere.UBEHANDLEDE_OPGAVER_TABLE).first
    try:
        await table.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        print(
            "Tabellen Ubehandlede opgaver er ikke synlig; "
            "ingen eksisterende opgave kan genoptages.",
            flush=True,
        )
        return None

    seen_signatures: set[str] = set()

    while True:
        await _vent_paa_ubehandlede_opgaver_stabil(page, timeout)
        rows = page.locator(KYSelectors.Borgere.UBEHANDLEDE_OPGAVER_ROWS)

        first_row_id = ""
        if await rows.count() > 0:
            first_row_id = (await rows.first.get_attribute("data-id") or "").strip()
        signature = f"{first_row_id}:{await rows.count()}"
        if signature in seen_signatures:
            break
        seen_signatures.add(signature)

        sikre_matches: list[Locator] = []

        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                row_id = (await row.get_attribute("data-id") or "").strip()
                opgave_link = row.locator(
                    KYSelectors.Borgere.UBEHANDLET_OPGAVE_LINK
                ).first
                link_id = (
                    await opgave_link.get_attribute("data-opgave-id") or ""
                ).strip()
                row_name = _opstart_normaliser_tekst(
                    await row.locator("td").nth(1).inner_text()
                )

                # Sammenlign kun ejerskab, hvis itemet har et id.
                if forventet_opgave_id is None:
                    continue

                row_id_match = row_id.casefold() == forventet_opgave_id.casefold()
                link_id_match = link_id.casefold() == forventet_opgave_id.casefold()

                if not row_id_match and not link_id_match:
                    continue

                # Et delvist id-match er mistænkeligt og maa ikke ignoreres.
                if not (row_id_match and link_id_match):
                    raise RuntimeError(
                        "Ubehandlede opgaver indeholder et inkonsistent id. "
                        f"Item-id={forventet_opgave_id!r}, "
                        f"row-id={row_id!r}, link-id={link_id!r}. "
                        "Intet er aabnet."
                    )

                if row_name.casefold() != forventet_opgavenavn.casefold():
                    raise RuntimeError(
                        "Itemets opgave-id findes, men opgavenavnet matcher "
                        "ikke inputtet. Intet er aabnet eller oprettet. "
                        f"Id={forventet_opgave_id!r}, "
                        f"forventet navn={forventet_opgavenavn!r}, "
                        f"fundet navn={row_name!r}."
                    )

                sikre_matches.append(row)

            except RuntimeError:
                raise
            except Exception:
                continue

        if len(sikre_matches) == 1:
            return sikre_matches[0]
        if len(sikre_matches) > 1:
            raise RuntimeError(
                "Flere ubehandlede opgaver har samme id og navn. Intet er aabnet."
            )

        if not await _gaa_til_naeste_ubehandlede_side(page, timeout):
            break

    print(
        "Ingen sikker ubehandlet opgave matchede baade id og navn. "
        f"Id={forventet_opgave_id!r}, navn={forventet_opgavenavn!r}.",
        flush=True,
    )
    return None


async def aabn_matchende_ubehandlet_opgave(
    page: Page,
    row: Locator,
    forventet_opgave_id: str,
    forventet_opgavenavn: str,
    borger_url: str,
    menu_sti: tuple[str, ...],
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpstartOpgaveCheckpoint:
    """Åbn den matchende opgave via entitet/overblik med pId og opgaveId.

    Tabellen kan indeholde en ``data-url`` med ``/opgave/undock/<id>``.
    Den URL bruges ikke til navigation, fordi KY kræver borgerens ``pId``.
    Rækkens id, link-id og navn valideres stadig, før den korrekte URL bygges.
    """

    row_id = (await row.get_attribute("data-id") or "").strip()
    row_name = _opstart_normaliser_tekst(await row.locator("td").nth(1).inner_text())
    link = row.locator(KYSelectors.Borgere.UBEHANDLET_OPGAVE_LINK).first
    link_id = (await link.get_attribute("data-opgave-id") or "").strip()

    if row_id.casefold() != forventet_opgave_id.casefold():
        raise RuntimeError("Rækkens data-id ændrede sig før åbning.")
    if link_id.casefold() != forventet_opgave_id.casefold():
        raise RuntimeError("Linkets data-opgave-id matcher ikke itemet.")
    if row_name.casefold() != forventet_opgavenavn.casefold():
        raise RuntimeError("Opgavenavnet ændrede sig før åbning.")

    person_id = _hent_person_id_fra_borger_url(borger_url)
    if not person_id:
        raise RuntimeError(
            "Borger-URL'en mangler et gyldigt pId. Den eksisterende opgave "
            "kan derfor ikke åbnes sikkert."
        )

    destination_url = _byg_opgave_overblik_url(
        borger_url=borger_url,
        person_id=person_id,
        opgave_id=forventet_opgave_id,
    )

    print(
        "Åbner eksisterende opgave via korrekt entitet/overblik-URL: "
        f"{destination_url}",
        flush=True,
    )

    response = await page.goto(
        destination_url,
        wait_until="domcontentloaded",
        timeout=timeout,
    )
    if response is not None and response.status >= 400:
        raise RuntimeError(
            "Den eksisterende opgave returnerede HTTP-fejl "
            f"{response.status}: {destination_url}"
        )

    await _vent_paa_opgave_overblik_url(
        page=page,
        forventet_person_id=person_id,
        forventet_opgave_id=forventet_opgave_id,
        timeout=timeout,
    )

    return {
        "opgave_id": forventet_opgave_id,
        "opgave_navn": forventet_opgavenavn,
        "opgave_url": page.url,
        "borger_url": borger_url,
        "menu_sti": menu_sti,
        "genoptaget": True,
        "kilde": "ubehandlede_opgaver",
    }


def _byg_opgave_overblik_url(
    borger_url: str,
    person_id: str,
    opgave_id: str,
) -> str:
    """Byg KY's korrekte opgave-URL med pId og opgaveId."""

    parsed = urlparse(str(borger_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"Borger-URL er ikke absolut: {borger_url!r}.")

    person_id = str(person_id or "").strip()
    opgave_id = str(opgave_id or "").strip()
    if not person_id:
        raise ValueError("person_id må ikke være tomt.")
    if not opgave_id:
        raise ValueError("opgave_id må ikke være tomt.")

    query = urlencode({"pId": person_id, "opgaveId": opgave_id})
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/ky-fagsystem/entitet/overblik",
            "",
            query,
            "",
        )
    )


async def _vent_paa_opgave_overblik_url(
    page: Page,
    forventet_person_id: str,
    forventet_opgave_id: str,
    timeout: int,
) -> None:
    """Vent på korrekt overblik-sti og korrekte queryparametre."""

    await page.wait_for_function(
        """
        ({ expectedPid, expectedTaskId }) => {
            const url = new URL(window.location.href);

            return (
                url.pathname.toLowerCase().endsWith(
                    '/ky-fagsystem/entitet/overblik'
                )
                && (url.searchParams.get('pId') || '').toLowerCase()
                    === expectedPid.toLowerCase()
                && (url.searchParams.get('opgaveId') || '').toLowerCase()
                    === expectedTaskId.toLowerCase()
            );
        }
        """,
        arg={
            "expectedPid": forventet_person_id,
            "expectedTaskId": forventet_opgave_id,
        },
        timeout=timeout,
    )

    faktisk_person_id = _hent_query_parameter(page.url, "pId")
    faktisk_opgave_id = _hent_query_parameter(page.url, "opgaveId")
    if faktisk_person_id.casefold() != forventet_person_id.casefold():
        raise RuntimeError(
            "Den åbnede opgave-URL indeholder et forkert pId. "
            f"Forventet={forventet_person_id!r}, faktisk={faktisk_person_id!r}."
        )
    if faktisk_opgave_id.casefold() != forventet_opgave_id.casefold():
        raise RuntimeError(
            "Den åbnede opgave-URL indeholder et forkert opgaveId. "
            f"Forventet={forventet_opgave_id!r}, "
            f"faktisk={faktisk_opgave_id!r}."
        )


def _hent_query_parameter(url: str, name: str) -> str:
    """Returnér første værdi for en queryparameter, ellers tom tekst."""

    values = parse_qs(urlparse(str(url or "").strip()).query).get(name, [])
    return str(values[0]).strip() if values else ""


async def _vent_paa_ubehandlede_opgaver_stabil(
    page: Page,
    timeout: int,
) -> None:
    """Vent paa at DataTables processing-indikatoren er skjult."""

    await page.wait_for_function(
        """
        () => {
            const table = document.querySelector('table#ubehandlede-opgaver');
            if (!table) return false;
            const wrapper = table.closest('.dataTables_wrapper');
            const processing = wrapper
                ? wrapper.querySelector('.dataTables_processing')
                : null;
            if (!processing) return true;
            const style = getComputedStyle(processing);
            return style.display === 'none'
                || style.visibility === 'hidden'
                || processing.getClientRects().length === 0;
        }
        """,
        timeout=timeout,
    )
    await page.wait_for_timeout(POLL_INTERVAL_MS)


async def _gaa_til_naeste_ubehandlede_side(
    page: Page,
    timeout: int,
) -> bool:
    """Gaa til naeste tabelside, hvis Next findes og er aktiv."""

    candidates = page.locator(KYSelectors.Borgere.UBEHANDLEDE_OPGAVER_NEXT)
    if await candidates.count() == 0:
        return False

    next_button = candidates.last
    classes = (await next_button.get_attribute("class") or "").casefold()
    aria_disabled = (
        await next_button.get_attribute("aria-disabled") or "false"
    ).casefold()

    if "disabled" in classes or aria_disabled == "true":
        return False
    if not await next_button.is_visible():
        return False

    rows = page.locator(KYSelectors.Borgere.UBEHANDLEDE_OPGAVER_ROWS)
    previous_first_id = ""
    if await rows.count() > 0:
        previous_first_id = (await rows.first.get_attribute("data-id") or "").strip()

    await next_button.click(timeout=min(ACTION_TIMEOUT_MS, timeout))

    try:
        await page.wait_for_function(
            """
            previousId => {
                const first = document.querySelector(
                    'table#ubehandlede-opgaver tbody tr[data-id]'
                );
                return (
                    !first
                    || first.getAttribute('data-id') !== previousId
                );
            }
            """,
            arg=previous_first_id,
            timeout=timeout,
        )

    except PlaywrightTimeoutError:
        return False

    return True


def _gem_aktivt_checkpoint_i_item_data(
    item_data: dict[str, Any] | None,
    checkpoint: OpstartOpgaveCheckpoint,
) -> None:
    """Gem genoptagelsesdata i box uden at overskrive oprindelig URL."""

    if item_data is None:
        return
    if not isinstance(item_data, dict):
        raise TypeError("item_data skal vaere en dictionary.")

    box = item_data.get("box")
    if not isinstance(box, dict):
        raise TypeError("item_data['box'] skal vaere en dictionary.")

    box[AKTIV_OPGAVE_ID_KEY] = checkpoint["opgave_id"]
    box[AKTIV_OPGAVE_URL_KEY] = checkpoint["opgave_url"]
    box[AKTIV_OPGAVE_NAVN_KEY] = checkpoint["opgave_navn"]


def _opstart_normaliser_id(value: Any) -> str | None:
    """Trim et opgave-id uden at aendre id-vaerdien."""

    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _opstart_kraev_tekst(value: str, field_name: str) -> str:
    """Trim et obligatorisk tekstinput."""

    result = _opstart_normaliser_tekst(value)
    if not result:
        raise ValueError(f"{field_name} maa ikke vaere tomt.")
    return result


def _opstart_normaliser_tekst(value: str) -> str:
    """Saml whitespace og trim tekst."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


async def _er_opgave_loader_synlig(page: Page) -> bool:
    """Returnér True, hvis en kendt generel KY-opgaveloader er synlig."""

    loader_selector = (
        "div#empty_opgave_loader, "
        "div#opgave_loader, "
        "div#opgave-loader, "
        "i#opgave-spinner, "
        ".table-init-loader:visible"
    )
    loaders = page.locator(loader_selector)

    for index in range(await loaders.count()):
        try:
            if await loaders.nth(index).is_visible():
                return True
        except Exception:
            continue

    return False


async def _hent_opgavenavn_fra_header(header: Locator) -> str:
    """Læs fx OPG-OPZYF96Q dynamisk fra højre side af opgaveheaderen."""

    candidates = header.locator(".pull-right > span.margin-right")

    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        try:
            if not await candidate.is_visible():
                continue
            value = _normaliser_tekst(await candidate.inner_text())
            if value:
                return value
        except Exception:
            continue

    raise RuntimeError("Opgavenavnet kunne ikke læses fra opgaveheaderen.")

def _normaliser_tekst(value: str) -> str:
    """Saml whitespace og fjern mellemrum før og efter teksten."""

    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()