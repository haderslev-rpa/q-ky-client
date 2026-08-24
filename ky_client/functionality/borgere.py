"""Asynkron borgerfunktionalitet til KY.

Modulet bruger udelukkende ``playwright.async_api``. Funktioner, der tidligere
havde suffikset ``_async``, har nu samme navn uden suffikset, men er fortsat
defineret med ``async def`` og skal kaldes med ``await``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypedDict
from urllib.parse import urljoin

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.selectors import KYSelectors

ACTION_TIMEOUT_MS = 30_000
OPGAVE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250
STABLE_CHECKS = 4
WAIT_AFTER_TAB_CLOSE_MS = 1_000
MAX_CLOSE_ATTEMPTS = 3

PERSON_TAB_SELECTOR = (
    "li.tab.topmenu-tab[data-tab-target-id='PERSON']"
)
PERSON_CLOSE_BUTTON_SELECTOR = (
    "li.tab.topmenu-tab[data-tab-target-id='PERSON'] "
    ".navigation-close-tab[data-entity-type='PERSON']"
)
ACTIVE_PERSON_TAB_SELECTOR = (
    "li.tab.topmenu-tab.active[data-tab-target-id='PERSON']"
)



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

    async def naviger_til_borger_async(
        self,
        cpr: str,
        timeout: int = 120_000,
        max_forsog: int = 3,
    ) -> str:
        """Fremsøg en borger og registrér fanen, som opslaget åbner."""

        aabne_faner_foer = await _hent_aabne_borger_ids(
            self.page
        )

        borger_url = await naviger_til_borger(
            page=self.page,
            cpr=cpr,
            timeout=timeout,
            max_forsog=max_forsog,
        )

        person_id = _hent_person_id_fra_borger_url(
            borger_url
        )

        if not person_id:
            raise RuntimeError(
                "Den validerede borger-URL mangler en gyldig pId."
            )

        aabne_faner_efter = await _hent_aabne_borger_ids(
            self.page
        )

        nye_faner = aabne_faner_efter - aabne_faner_foer

        for entity_id in nye_faner:
            if entity_id not in self._registrerede_borgerfane_ids:
                self._registrerede_borgerfane_ids.append(
                    entity_id
                )

        # Normalt matcher pId og data-entity-id. Hvis den aktuelle pId
        # faktisk findes blandt fanernes entity-id'er, registreres den også.
        if (
            person_id in aabne_faner_efter
            and person_id not in self._registrerede_borgerfane_ids
        ):
            self._registrerede_borgerfane_ids.append(
                person_id
            )

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
            raise RuntimeError(
                "Personoplysninger tilhører ikke det forventede CPR."
            )

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

    async def hent_borger_async(
        self,
        cpr: str,
        timeout: int = 120_000,
        max_forsog: int = 3,
    ) -> BorgerResultat:
        """Fremsøg borgeren og returnér pId, URL og Personoplysninger."""

        borger_url = await self.naviger_til_borger_async(
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

    async def hent_aabne_borger_ids_async(self) -> set[str]:
        """Returnér entity-id'er for alle aktuelt åbne PERSON-faner."""

        return await _hent_aabne_borger_ids(self.page)

    async def luk_borgerfaner_async(
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
            entity_ids=list(entity_ids),
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

            actual_cpr = _normaliser_cpr(
                await search_input.input_value()
            )
            if actual_cpr != cpr:
                raise RuntimeError(
                    "CPR blev ikke indsat korrekt i topsearch."
                )

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
                search_input = page.locator(
                    KYSelectors.Main.TOP_SEARCH
                ).first
                await search_input.wait_for(state="visible", timeout=timeout)
                if _normaliser_cpr(
                    await search_input.input_value()
                ) != cpr:
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
            print(
                "CPR fra Personoplysninger: "
                f"{_masker_cpr(fundet_cpr)}"
            )

            if fundet_cpr != cpr:
                seneste_fejl = (
                    "CPR fra Personoplysninger matcher ikke det "
                    "fremsøgte CPR."
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
            print(
                f"Borgeropslag fejlede på forsøg {forsog}: "
                f"{seneste_fejl}"
            )
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
                    snapshot = await _laes_personoplysninger_snapshot(
                        table
                    )
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
            print(
                "Venter på stabil Personoplysninger-tabel: "
                f"{last_diagnostic}"
            )

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
        raise RuntimeError(
            "Personoplysninger gav ikke et læsbart DOM-snapshot."
        )
    return snapshot


async def _hent_cpr_fra_personoplysninger(table) -> str:
    """Læs CPR fra tabellen og ignorér fx alder i parentes."""
    snapshot = await _laes_personoplysninger_snapshot(table)
    cpr = _normaliser_cpr(str(snapshot.get("cpr", "")))
    if not cpr.isdigit() or len(cpr) != 10:
        raise RuntimeError(
            "CPR-rækken kunne ikke læses korrekt fra Personoplysninger."
        )
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
            entity_id = await buttons.nth(index).get_attribute(
                "data-entity-id"
            )
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
            "Forkert borger blev vist, men den aktive borgerfane "
            "kunne ikke findes."
        )
    entity_id = await active.get_attribute("data-entity-id")
    if not entity_id:
        raise RuntimeError(
            "Den aktive borgerfane mangler data-entity-id."
        )
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
        "PERSON-fanen blev ikke lukket inden for tidsgrænsen. "
        f"Entity-id={entity_id}."
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
            raise RuntimeError(
                "KY-siden blev lukket under søgningen efter topSearch."
            )

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
                "Borgeropslag: venter på aktivt topSearch. "
                f"Diagnose: {diagnostic}",
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
                "KY-siden blev lukket under ventetiden på "
                "Personoplysninger."
            )

        diagnostic: list[dict[str, object]] = []

        for frame in page.frames:
            try:
                tables = frame.locator(
                    KYSelectors.Borgere.PERSON_OPLYSNINGER
                )

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

                    readable_rows = await antal_laesbare_person_rows(
                        table
                    )

                    frame_diagnostic["synlig_tabel"] = True
                    frame_diagnostic["laesbare_raekker"] = readable_rows

                    if readable_rows > 0:
                        return table

            except Exception as error:
                diagnostic.append(
                    {
                        "frame": frame.url,
                        "fejl": (
                            f"{type(error).__name__}: {error}"
                        ),
                    }
                )

        last_diagnostic = diagnostic

        if elapsed_ms % 2_000 == 0:
            print(
                "Venter på synlig Personoplysninger-tabel: "
                f"{diagnostic}",
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
        raise RuntimeError(
            "Personoplysninger blev ikke returneret som en liste."
        )

    result: list[dict[str, str]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        label = str(
            row.get("label", "")
        ).strip()

        if not label:
            continue

        result.append(
            {
                "label": label,
                "value": str(
                    row.get("value", "")
                ).strip(),
                "data_id": str(
                    row.get("data_id", "")
                ).strip(),
            }
        )

    return result

def print_personoplysninger(
    rows: list[dict[str, str]],
    heading: str,
) -> None:
    """Print alle udtrukne personoplysninger."""

    assert rows, (
        f"Personoplysninger for {heading} indeholdt ingen læsbare rækker."
    )

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
            f"{row_number:02d}. "
            f"{row['label']}: {row['value']}",
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

    close_buttons = page.locator(
        PERSON_CLOSE_BUTTON_SELECTOR
    )

    entity_ids: list[str] = []

    for index in range(await close_buttons.count()):
        try:
            entity_id = await close_buttons.nth(
                index
            ).get_attribute(
                "data-entity-id"
            )
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
        current_count = len(
            await hent_person_tab_ids(page)
        )

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
    entity_ids: list[str],
) -> None:
    """Luk de angivne PERSON-faner med højst tre gennemløb."""

    wanted_ids = list(
        dict.fromkeys(entity_ids)
    )

    for attempt in range(
        1,
        MAX_CLOSE_ATTEMPTS + 1,
    ):
        open_ids = set(
            await hent_person_tab_ids(page)
        )

        remaining_ids = [
            entity_id
            for entity_id in wanted_ids
            if entity_id in open_ids
        ]

        if not remaining_ids:
            print(
                "Alle borgerfaner er lukket.",
                flush=True,
            )
            return

        print(
            f"Lukkeforsøg {attempt}/{MAX_CLOSE_ATTEMPTS}. "
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
                )
            except Exception as error:
                print(
                    "Kunne ikke lukke PERSON-fane "
                    f"{entity_id}: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        await page.wait_for_timeout(
            WAIT_AFTER_TAB_CLOSE_MS
        )

    open_ids = set(
        await hent_person_tab_ids(page)
    )

    remaining_ids = [
        entity_id
        for entity_id in wanted_ids
        if entity_id in open_ids
    ]

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
            buttons = page.locator(
                f"{selector}:visible"
            )

            if await buttons.count() == 0:
                continue

            button = buttons.first

            if not await button.is_enabled():
                continue

            print(
                "KY viste en dialog ved fanelukning. "
                "Klikker på 'Afbryd og gem'.",
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

    selector = (
        f"{PERSON_CLOSE_BUTTON_SELECTOR}"
        f"[data-entity-id='{entity_id}']"
    )

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
        _normaliser_tekst(item)
        for item in menu_sti
        if _normaliser_tekst(item)
    )

    if not normalized_path:
        raise ValueError("menu_sti skal indeholde mindst ét menupunkt.")
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket, før opgaven kan åbnes.")

    borger_url = page.url
    if not borger_url:
        raise RuntimeError("Borger-URL kunne ikke læses før opgaveåbning.")

    handlinger = page.locator(
        KYSelectors.Borgere.HANDLINGER_DROPDOWN
    ).first
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

    header = page.locator(
        "div#opgave-header.block-heading"
    ).first
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

                visible_text = _normaliser_tekst(
                    await candidate.inner_text()
                )
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
        header = page.locator(
            "div#opgave-header.block-heading:visible"
        )
        undock = page.locator(
            "div#opgave-header "
            "a.undock_panel_button[data-opgave-id][data-url]"
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
                classes = (
                    await header.first.get_attribute("class") or ""
                ).split()

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

OPGAVE_TIMEOUT_MS = 120_000


class OpgaveCheckpoint(TypedDict):
    """Stabile oplysninger om den opgave, som KY har åbnet."""

    borger_url: str
    opgave_url: str
    opgave_id: str
    opgave_navn: str


async def opstart_opgave(
    page: Page,
    menu_sti: Sequence[str],
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpgaveCheckpoint:
    """Åbn en vilkårlig KY-opgave og returnér et valideret checkpoint.

    Funktionen er generel og kan bruges til alle opgavetyper, eksempelvis::

        checkpoint = await opstart_opgave(
            page=page,
            menu_sti=("Administration", "Send brev"),
        )

    Selve navigationen udføres af den eksisterende
    ``aabn_opgave_og_hent_url`` i ``borgere.py``.
    """

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før opgaven startes.")

    normaliseret_sti = tuple(
        " ".join(str(delnavn).split())
        for delnavn in menu_sti
        if " ".join(str(delnavn).split())
    )
    if not normaliseret_sti:
        raise ValueError("menu_sti skal indeholde mindst ét menupunkt.")

    checkpoint = await aabn_opgave_og_hent_url(
        page=page,
        menu_sti=normaliseret_sti,
        timeout=timeout,
    )

    if page.is_closed():
        raise RuntimeError("KY-siden blev lukket under opstart af opgaven.")

    opgave_id = str(checkpoint.get("opgave_id", "")).strip()
    opgave_url = str(checkpoint.get("opgave_url", "")).strip()
    opgave_navn = str(checkpoint.get("opgave_navn", "")).strip()
    borger_url = str(checkpoint.get("borger_url", "")).strip()

    if not opgave_id:
        raise RuntimeError("Opgavecheckpointet mangler opgave_id.")
    if not opgave_url:
        raise RuntimeError("Opgavecheckpointet mangler opgave_url.")
    if not opgave_navn:
        raise RuntimeError("Opgavecheckpointet mangler opgave_navn.")
    if not borger_url:
        raise RuntimeError("Opgavecheckpointet mangler borger_url.")
    if opgave_id not in opgave_url:
        raise RuntimeError(
            "Opgavecheckpointets opgave_id indgår ikke i opgave_url. "
            f"opgave_id={opgave_id!r}, opgave_url={opgave_url!r}."
        )

    resultat: OpgaveCheckpoint = {
        "borger_url": borger_url,
        "opgave_url": opgave_url,
        "opgave_id": opgave_id,
        "opgave_navn": opgave_navn,
    }

    print()
    print("=" * 70)
    print("OPGAVE STARTET")
    print(f"Menusti: {' > '.join(normaliseret_sti)}")
    print(f"Opgavenavn: {opgave_navn}")
    print(f"Opgave-id: {opgave_id}")
    print(f"Opgave-URL: {opgave_url}")
    print(f"Borger-URL: {borger_url}")
    print("=" * 70)

    return resultat


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
    """Saml whitespace og trim tekst."""

    return re.sub(r"\s+", " ", value).strip()

"""Indsæt dette afsnit nederst i ky_client/functionality/borgere.py.

Forudsætter, at borgere.py allerede importerer:

    import re
    from playwright.async_api import Locator, Page
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

og allerede indeholder:

    aabn_opgave_og_hent_url(...)
    ACTION_TIMEOUT_MS
    OPGAVE_TIMEOUT_MS
    POLL_INTERVAL_MS
"""


async def opret_opfoelgningsopgave(
    page: Page,
    opfoelgningstype: str,
    opfoelgningsdato: str,
    sagsbehandler: str,
    titel: str | None = None,
    frekvens: str | None = None,
    haendelsestype: str | None = None,
    beskrivelse: str | None = None,
    vaelg_sagsbehandler_fra_typeahead: bool = False,
    test: bool = False,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpgaveCheckpoint:
    """Åbn, udfyld og gem en opfølgningsopgave på den aktive borger.

    Borgeren skal være fremsøgt og aktiv, før funktionen kaldes. Brug fx
    ``await naviger_til_borger(...)`` først.

    Args:
        page:
            Aktiv asynkron Playwright-side i KY.
        opfoelgningstype:
            Synlig label eller option-værdi i ``select#opfoelgningsType``.
            Eksempel: ``Brugerdefineret`` eller ``manuel``.
        opfoelgningsdato:
            Dato til ``input#command.opfoelgningsdato``.
            Eksempel: ``01-09-2026``.
        sagsbehandler:
            Tekst til ``input#typeahead``.
        titel:
            Titel til den brugerdefinerede opfølgningsopgave. Påkrævet,
            hvis den valgte opfølgningstype er Brugerdefineret.
        frekvens:
            Synlig label eller option-værdi i ``select#frekvens``.
            Påkrævet ved Brugerdefineret.
        haendelsestype:
            Synlig label eller option-værdi i ``select#haendelseType``.
            Valgfri.
        beskrivelse:
            Tekst til beskrivelsesfeltet. Påkrævet ved Brugerdefineret.
        vaelg_sagsbehandler_fra_typeahead:
            Hvis True, vælges et matchende typeahead-resultat. Hvis False,
            indsættes teksten uden valg af et forslag.
        test:
            Hvis True, udfyldes og valideres alle felter, men der klikkes
            ikke på Gem. Funktionen returnerer stadig opgavecheckpointet,
            så den udfyldte formular kan inspiceres i browseren.
        timeout:
            Maksimal ventetid i millisekunder.

    Returns:
        Opgavecheckpointet fra ``aabn_opgave_og_hent_url``.

    Raises:
        ValueError:
            Hvis obligatoriske input mangler eller en dropdownværdi ikke
            findes.
        RuntimeError:
            Hvis formularen ikke kan udfyldes eller gemmes.
        PlaywrightTimeoutError:
            Hvis et nødvendigt element ikke bliver tilgængeligt.
    """

    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket. Opfølgningsopgaven kan ikke oprettes."
        )

    opfoelgningstype = _normaliser_paakraevet_input(
        opfoelgningstype,
        "opfoelgningstype",
    )
    opfoelgningsdato = _normaliser_paakraevet_input(
        opfoelgningsdato,
        "opfoelgningsdato",
    )
    sagsbehandler = _normaliser_paakraevet_input(
        sagsbehandler,
        "sagsbehandler",
    )
    titel = _normaliser_valgfrit_input(titel)
    frekvens = _normaliser_valgfrit_input(frekvens)
    haendelsestype = _normaliser_valgfrit_input(haendelsestype)
    beskrivelse = _normaliser_valgfrit_input(beskrivelse)

    print()
    print("=" * 70)
    print("OPRETTER OPFØLGNINGSOPGAVE")
    print(f"Opfølgningstype: {opfoelgningstype!r}")
    print(f"Opfølgningsdato: {opfoelgningsdato!r}")
    print(f"Sagsbehandler: {sagsbehandler!r}")
    print(f"Titel: {titel!r}")
    print(f"Frekvens: {frekvens!r}")
    print(f"Hændelsestype: {haendelsestype!r}")
    print(f"Beskrivelse: {beskrivelse!r}")
    print(f"test: {test}")
    print("=" * 70)

    checkpoint = await aabn_opgave_og_hent_url(
        page=page,
        menu_sti=("Administration", "Opret opfølgningsopgave"),
        timeout=timeout,
    )

    await _vent_paa_tom_opgaveloader(
        page=page,
        timeout=timeout,
    )

    await _vaelg_option_via_value_eller_label(
        page=page,
        selector="select#opfoelgningsType",
        option=opfoelgningstype,
        timeout=timeout,
    )

    # KY kan genopbygge formularen efter skift af opfølgningstype.
    await page.wait_for_timeout(1_000)
    await _vent_paa_tom_opgaveloader(page=page, timeout=timeout)

    await _udfyld_synligt_felt(
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

    is_brugerdefineret = await _er_valgt_opfoelgningstype_brugerdefineret(
        page=page,
        timeout=timeout,
    )

    if is_brugerdefineret:
        if not titel:
            raise ValueError(
                "titel er påkrævet ved opfølgningstypen Brugerdefineret."
            )
        if not frekvens:
            raise ValueError(
                "frekvens er påkrævet ved opfølgningstypen Brugerdefineret."
            )
        if not beskrivelse:
            raise ValueError(
                "beskrivelse er påkrævet ved opfølgningstypen "
                "Brugerdefineret."
            )

        # Dropdowns udfyldes før tekstfelter, da KY kan genopbygge DOM'en.
        await _vaelg_option_via_value_eller_label(
            page=page,
            selector="select#frekvens",
            option=frekvens,
            timeout=timeout,
        )

        if haendelsestype:
            await _vaelg_option_via_value_eller_label(
                page=page,
                selector="select#haendelseType",
                option=haendelsestype,
                timeout=timeout,
            )

        await page.wait_for_timeout(1_500)
        await _vent_paa_tom_opgaveloader(page=page, timeout=timeout)

        await _udfyld_synligt_felt(
            page=page,
            selector="input[name='title']",
            value=titel,
            feltnavn="Titel",
            timeout=timeout,
        )
        await _udfyld_synligt_felt(
            page=page,
            selector="textarea[name='beskrivelse']",
            value=beskrivelse,
            feltnavn="Beskrivelse",
            timeout=timeout,
        )

        # Kontrollér, at en efterfølgende DOM-opdatering ikke nulstillede dem.
        await page.wait_for_timeout(750)
        await _kontroller_synligt_felt(
            page=page,
            selector="input[name='title']",
            forventet=titel,
            feltnavn="Titel",
            timeout=timeout,
        )
        await _kontroller_synligt_felt(
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
            "titel, frekvens, haendelsestype og beskrivelse må kun "
            "angives, når opfølgningstypen er Brugerdefineret."
        )

    if test:
        print()
        print("=" * 70)
        print("test-TILSTAND: FORMULAREN ER UDFYLDT")
        print("Der klikkes ikke på Gem.")
        print(f"Opgavenavn: {checkpoint['opgave_navn']}")
        print(f"Opgave-id: {checkpoint['opgave_id']}")
        print("=" * 70)
        return checkpoint

    gem = await _find_synlig_aktiv_knap(
        page=page,
        text="Gem",
        timeout=timeout,
    )
    await gem.scroll_into_view_if_needed()
    await gem.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
    await _vent_paa_tom_opgaveloader(page=page, timeout=timeout)

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

    print()
    print("=" * 70)
    print("OPFØLGNINGSOPGAVEN ER GEMT")
    print(f"Opgavenavn: {checkpoint['opgave_navn']}")
    print(f"Opgave-id: {checkpoint['opgave_id']}")
    print("=" * 70)

    return checkpoint


async def _vent_paa_tom_opgaveloader(
    page: Page,
    timeout: int,
) -> None:
    """Vent på, at #empty_opgave_loader er skjult eller fjernet."""

    await page.wait_for_function(
        """
        () => {
            const loader = document.querySelector('#empty_opgave_loader');
            if (!loader) return true;

            const style = window.getComputedStyle(loader);
            return (
                style.display === 'none'
                || style.visibility === 'hidden'
                || style.opacity === '0'
                || loader.offsetParent === null
            );
        }
        """,
        timeout=timeout,
    )


async def _vaelg_option_via_value_eller_label(
    page: Page,
    selector: str,
    option: str,
    timeout: int,
) -> str:
    """Vælg en option via eksakt value eller synlig label."""

    select = page.locator(f"{selector}:visible").last
    await select.wait_for(state="attached", timeout=timeout)

    options = select.locator("option")
    selected_value: str | None = None
    available: list[str] = []

    for index in range(await options.count()):
        current = options.nth(index)
        value = (await current.get_attribute("value") or "").strip()
        label = re.sub(
            r"\s+",
            " ",
            await current.inner_text(),
        ).strip()
        available.append(f"{label} ({value})")

        if (
            value.casefold() == option.casefold()
            or label.casefold() == option.casefold()
        ):
            selected_value = value
            break

    if selected_value is None:
        raise ValueError(
            f"Kunne ikke finde {option!r} i {selector}. "
            f"Muligheder: {available}"
        )

    await select.select_option(value=selected_value)
    await select.dispatch_event("input")
    await select.dispatch_event("change")

    # Understøt bootstrap-select, hvis KY bruger plugin-visningen.
    await page.evaluate(
        """
        ({ selector, value }) => {
            const elements = Array.from(document.querySelectorAll(selector));
            const element = elements.find(item => {
                const style = window.getComputedStyle(item);
                return style.display !== 'none'
                    && style.visibility !== 'hidden';
            }) || elements[elements.length - 1];

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

    return selected_value


async def _udfyld_synligt_felt(
    page: Page,
    selector: str,
    value: str,
    feltnavn: str,
    timeout: int,
) -> None:
    """Udfyld den seneste synlige input- eller textarea-instans robust."""

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

    actual_value = await field.input_value()

    if actual_value.strip() != value.strip():
        # Fallback til browserens native value-setter.
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

    if actual_value.strip() != value.strip():
        raise RuntimeError(
            f"{feltnavn} kunne ikke udfyldes. "
            f"Forventet={value!r}, faktisk={actual_value!r}."
        )


async def _kontroller_synligt_felt(
    page: Page,
    selector: str,
    forventet: str,
    feltnavn: str,
    timeout: int,
) -> None:
    """Kontrollér værdien i den seneste synlige feltinstans."""

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
            f"{feltnavn} blev nulstillet før Gem. "
            f"Forventet={forventet!r}, faktisk={faktisk!r}."
        )


async def _udfyld_sagsbehandler(
    page: Page,
    sagsbehandler: str,
    vaelg_forslag: bool,
    timeout: int,
) -> None:
    """Indsæt sagsbehandlertekst og vælg valgfrit et typeahead-resultat."""

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

    pattern = re.compile(
        rf"^\s*{re.escape(sagsbehandler)}(?:\s|\().*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < timeout:
        suggestions = page.locator(
            ".tt-menu:visible .tt-suggestion.tt-selectable"
        )
        for index in range(await suggestions.count()):
            suggestion = suggestions.nth(index)
            try:
                if not await suggestion.is_visible():
                    continue
                text = re.sub(
                    r"\s+",
                    " ",
                    await suggestion.inner_text(),
                ).strip()
                if pattern.match(text):
                    await suggestion.click(
                        timeout=min(ACTION_TIMEOUT_MS, timeout)
                    )
                    return
            except Exception:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsbehandleren blev ikke fundet i typeahead-listen: "
        f"{sagsbehandler}."
    )


async def _er_valgt_opfoelgningstype_brugerdefineret(
    page: Page,
    timeout: int,
) -> bool:
    """Kontrollér den valgte opfølgningstype."""

    selected = page.locator(
        "select#opfoelgningsType:visible option:checked"
    ).last
    await selected.wait_for(state="attached", timeout=timeout)

    value = (await selected.get_attribute("value") or "").strip().casefold()
    label = (await selected.inner_text()).strip().casefold()

    return value == "manuel" or label == "brugerdefineret"


async def _find_synlig_aktiv_knap(
    page: Page,
    text: str,
    timeout: int,
) -> Locator:
    """Find en synlig og aktiv submitknap med eksakt tekst."""

    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    elapsed_ms = 0

    while elapsed_ms < timeout:
        candidates = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button.btn-submit-form, a.btn-submit-form"
        )

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                if not await candidate.is_enabled():
                    continue

                value = await candidate.get_attribute("value")
                visible_text = value or await candidate.inner_text()
                visible_text = re.sub(
                    r"\s+",
                    " ",
                    visible_text or "",
                ).strip()

                if pattern.fullmatch(visible_text):
                    return candidate
            except Exception:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        f"Knappen {text!r} blev ikke fundet inden for "
        f"{timeout / 1000:.0f} sekunder."
    )


async def _hent_synlig_validering(page: Page) -> str:
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


def _normaliser_paakraevet_input(value: str, feltnavn: str) -> str:
    """Trim et obligatorisk input og afvis tomme værdier."""

    normaliseret = str(value or "").strip()
    if not normaliseret:
        raise ValueError(f"{feltnavn} må ikke være tom.")
    return normaliseret


def _normaliser_valgfrit_input(value: str | None) -> str | None:
    """Trim et valgfrit input og returnér None for tom tekst."""

    if value is None:
        return None
    normaliseret = str(value).strip()
    return normaliseret or None