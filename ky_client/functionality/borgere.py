from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ky_client.selectors import KYSelectors

logger = logging.getLogger(__name__)

ACTION_TIMEOUT_MS = 30_000
OPGAVE_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 250
STABLE_CHECKS = 4
WAIT_AFTER_TAB_CLOSE_MS = 1_000
MAX_CLOSE_ATTEMPTS = 3

CHECKPOINT_FELTER: dict[str, tuple[str, str, str]] = {
    "send_brev": (
        "Send brev Opgave-Id",
        "Send brev Opgave URL",
        "Send brev Opgavenavn",
    ),
    "journalnotat": (
        "Journalnotat Opgave-Id",
        "Journalnotat Opgave URL",
        "Journalnotat Opgavenavn",
    ),
    "opfoelgningsopgave": (
        "Opfølgningsopgave Opgave-Id",
        "Opfølgningsopgave Opgave URL",
        "Opfølgningsopgave Opgavenavn",
    ),
}


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
            raise TypeError(
                "Personoplysninger gav ikke en gyldig rækkeliste."
            )

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

        except PlaywrightError as error:
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
            except PlaywrightError:
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
        raise TypeError(
            "Personoplysninger gav ikke et læsbart DOM-snapshot."
        )

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
    buttons = page.locator(
        KYSelectors.Borgere.PERSON_CLOSE_BUTTON
    )
    ids: set[str] = set()
    for index in range(await buttons.count()):
        try:
            entity_id = await buttons.nth(index).get_attribute("data-entity-id")
        except PlaywrightError:
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
    active = page.locator(
        KYSelectors.Borgere.ACTIVE_PERSON_CLOSE_BUTTON
    ).first
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
        except PlaywrightError as error:
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
    selector = KYSelectors.Borgere.PERSON_CLOSE_BUTTON_BY_ID.format(
        entity_id=entity_id
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
        except PlaywrightError:
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
        *KYSelectors.Borgere.TOP_SEARCH_FALLBACKS,
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

                except PlaywrightError:
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
        *KYSelectors.Borgere.TOP_SEARCH_BUTTON_FALLBACKS,
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

            except PlaywrightError:
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

            except PlaywrightError as error:
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
        raise TypeError(
            "Personoplysninger blev ikke returneret som en liste."
        )


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

    print(flush=True)
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

    close_buttons = page.locator(KYSelectors.Borgere.PERSON_CLOSE_BUTTON)

    entity_ids: list[str] = []

    for index in range(await close_buttons.count()):
        try:
            entity_id = await close_buttons.nth(index).get_attribute("data-entity-id")
        except PlaywrightError:
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
            except PlaywrightError as error:
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

        except PlaywrightError:
            continue


async def vent_paa_person_fane_lukket(
    page: Page,
    entity_id: str,
    timeout_ms: int,
) -> None:
    """Vent på, at PERSON-fanens lukknap er fjernet eller skjult."""

    selector = KYSelectors.Borgere.PERSON_CLOSE_BUTTON_BY_ID.format(entity_id=entity_id)

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
            except PlaywrightError:
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

    header = page.locator(KYSelectors.Borgere.OPGAVE_HEADER).first
    undock_button = header.locator(KYSelectors.Borgere.OPGAVE_UNDOCK_BUTTON).first

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
        container = page.locator(KYSelectors.Borgere.HANDLINGER_CONTAINER).first

        try:
            candidates = container.locator(
                KYSelectors.Borgere.HANDLINGER_MENU_ITEMS,
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
        except PlaywrightError:
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
        header = page.locator(KYSelectors.Borgere.OPGAVE_HEADER_VISIBLE)
        undock = page.locator(KYSelectors.Borgere.OPGAVE_UNDOCK_READY)

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
        except PlaywrightError:
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
    checkpoint_type: str,
    item_data: dict[str, Any] | None = None,
    opgave_id: str | None = None,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpstartOpgaveCheckpoint:
    """
    Genoptag en tidligere startet KY-opgave eller opret en ny.

    checkpoint_type skal være en af:

    - send_brev
    - journalnotat
    - opfoelgningsopgave

    Hvis item_data["box"] allerede indeholder et checkpoint for den
    pågældende opgavetype, skal den eksisterende opgave genoptages.
    Funktionen må i den situation ikke oprette en ny opgave.

    Checkpointet skrives til item_data["box"], men den kaldende proces
    skal efterfølgende kalde update_item_data(), så ændringerne gemmes
    permanent på Automation Server.
    """

    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket før opstart af opgave."
        )

    checkpoint_type = _normaliser_checkpoint_type(
        checkpoint_type
    )

    normaliseret_menu_sti = tuple(
        _opstart_normaliser_tekst(menu_del)
        for menu_del in menu_sti
        if _opstart_normaliser_tekst(menu_del)
    )

    if not normaliseret_menu_sti:
        raise ValueError(
            "menu_sti skal indeholde mindst ét menupunkt."
        )

    forventet_opgavenavn = normaliseret_menu_sti[-1]

    borger_url = str(
        page.url or ""
    ).strip()

    if not borger_url:
        raise RuntimeError(
            "Borger-URL kunne ikke læses før opstart af opgaven."
        )

    gemt_checkpoint = hent_opgavecheckpoint_fra_item_data(
        item_data=item_data,
        checkpoint_type=checkpoint_type,
    )

    eksplicit_opgave_id = _opstart_normaliser_id(
        opgave_id
    )

    # Et eksplicit handlingsspecifikt ID har højeste prioritet.
    if eksplicit_opgave_id is not None:
        if gemt_checkpoint is None:
            gemt_checkpoint = {
                "opgave_id": eksplicit_opgave_id,
                "opgave_navn": forventet_opgavenavn,
                "opgave_url": "",
            }
        elif (
            gemt_checkpoint["opgave_id"].casefold()
            != eksplicit_opgave_id.casefold()
        ):
            raise RuntimeError(
                "Det eksplicitte opgave-id matcher ikke checkpointet "
                "i item_data. Intet åbnes eller oprettes. "
                f"Checkpoint-type={checkpoint_type!r}, "
                f"eksplicit id={eksplicit_opgave_id!r}, "
                f"gemt id={gemt_checkpoint['opgave_id']!r}."
            )

    # Hvis der er et gemt checkpoint, må der ikke oprettes en ny opgave.
    if gemt_checkpoint is not None:
        gemt_opgave_id = _opstart_normaliser_id(
            gemt_checkpoint.get("opgave_id")
        )

        gemt_opgave_url = _opstart_normaliser_tekst(
            gemt_checkpoint.get("opgave_url", "")
        )

        gemt_opgavenavn = _opstart_normaliser_tekst(
            gemt_checkpoint.get("opgave_navn", "")
        )

        if (
            gemt_opgavenavn
            and gemt_opgavenavn.casefold()
            != forventet_opgavenavn.casefold()
        ):
            raise RuntimeError(
                "Det gemte checkpoint tilhører en anden opgavetype. "
                "Intet åbnes eller oprettes. "
                f"Checkpoint-type={checkpoint_type!r}, "
                f"forventet navn={forventet_opgavenavn!r}, "
                f"gemt navn={gemt_opgavenavn!r}."
            )

        if not gemt_opgave_id and gemt_opgave_url:
            gemt_opgave_id = hent_opgave_id_fra_url(
                gemt_opgave_url
            )

        if not gemt_opgave_id:
            raise RuntimeError(
                "Der findes et gemt checkpoint, men opgave-id "
                "mangler og kan ikke udledes fra URL'en. "
                "Der oprettes ikke en ny opgave. "
                f"Checkpoint-type={checkpoint_type!r}."
            )

        if gemt_opgave_url:
            checkpoint = await genoptag_opgave_fra_checkpoint_url(
                page=page,
                opgave_id=gemt_opgave_id,
                opgave_navn=forventet_opgavenavn,
                opgave_url=gemt_opgave_url,
                borger_url=borger_url,
                menu_sti=normaliseret_menu_sti,
                timeout=timeout,
            )

            _gem_opgavecheckpoint_i_item_data(
                item_data=item_data,
                checkpoint=checkpoint,
                checkpoint_type=checkpoint_type,
            )

            return checkpoint

        # Bagudfald, hvis der findes et ID, men ingen URL.
        eksisterende_raekke = (
            await find_matchende_ubehandlet_opgave(
                page=page,
                forventet_opgave_id=gemt_opgave_id,
                forventet_opgavenavn=forventet_opgavenavn,
                timeout=timeout,
            )
        )

        if eksisterende_raekke is None:
            raise RuntimeError(
                "Itemet indeholder et gemt opgave-id, men opgaven "
                "kunne ikke findes blandt Ubehandlede opgaver. "
                "Der oprettes ikke en ny opgave, da det kan skabe "
                "en dublet. "
                f"Checkpoint-type={checkpoint_type!r}, "
                f"opgave-id={gemt_opgave_id!r}, "
                f"opgavenavn={forventet_opgavenavn!r}."
            )

        checkpoint = await aabn_matchende_ubehandlet_opgave(
            page=page,
            row=eksisterende_raekke,
            forventet_opgave_id=gemt_opgave_id,
            forventet_opgavenavn=forventet_opgavenavn,
            borger_url=borger_url,
            menu_sti=normaliseret_menu_sti,
            timeout=timeout,
        )

        _gem_opgavecheckpoint_i_item_data(
            item_data=item_data,
            checkpoint=checkpoint,
            checkpoint_type=checkpoint_type,
        )

        return checkpoint

    # Der findes intet gemt checkpoint.
    # Det er derfor sikkert at oprette en ny opgave.
    nyt_checkpoint = await aabn_opgave_og_hent_url(
        page=page,
        menu_sti=normaliseret_menu_sti,
        timeout=timeout,
    )

    checkpoint: OpstartOpgaveCheckpoint = {
        "opgave_id": str(
            nyt_checkpoint["opgave_id"]
        ).strip(),
        "opgave_navn": forventet_opgavenavn,
        "opgave_url": str(
            nyt_checkpoint["opgave_url"]
        ).strip(),
        "borger_url": str(
            nyt_checkpoint["borger_url"]
        ).strip(),
        "menu_sti": tuple(
            nyt_checkpoint["menu_sti"]
        ),
        "genoptaget": False,
        "kilde": "ny_opgave",
    }

    if not checkpoint["opgave_id"]:
        raise RuntimeError(
            "Den nyoprettede opgave mangler opgave-id."
        )

    if not checkpoint["opgave_url"]:
        raise RuntimeError(
            "Den nyoprettede opgave mangler opgave-URL."
        )

    if (
        checkpoint["opgave_navn"].casefold()
        != forventet_opgavenavn.casefold()
    ):
        raise RuntimeError(
            "Den nyoprettede opgaves navn matcher ikke menu_sti. "
            f"Forventet={forventet_opgavenavn!r}, "
            f"faktisk={checkpoint['opgave_navn']!r}."
        )

    _gem_opgavecheckpoint_i_item_data(
        item_data=item_data,
        checkpoint=checkpoint,
        checkpoint_type=checkpoint_type,
    )

    return checkpoint


def _normaliser_checkpoint_type(
    checkpoint_type: str,
) -> str:
    """Validér og normalisér navnet på checkpointtypen."""

    result = str(
        checkpoint_type or ""
    ).strip().casefold()

    if result not in CHECKPOINT_FELTER:
        raise ValueError(
            "checkpoint_type skal være en af: "
            + ", ".join(CHECKPOINT_FELTER)
            + f". Modtog {checkpoint_type!r}."
        )

    return result


def _checkpoint_feltnavne(
    checkpoint_type: str,
) -> tuple[str, str, str]:
    """Returnér ID-, URL- og navnefelt for checkpointtypen."""

    checkpoint_type = _normaliser_checkpoint_type(
        checkpoint_type
    )

    return CHECKPOINT_FELTER[checkpoint_type]


def hent_opgavecheckpoint_fra_item_data(
    item_data: dict[str, Any] | None,
    checkpoint_type: str,
) -> dict[str, str] | None:
    """
    Hent et handlingsspecifikt checkpoint fra item_data["box"].

    Hvis ingen af de tre felter er udfyldt, returneres None.
    Hvis checkpointet er delvist udfyldt, returneres de fundne data,
    så opstart_opgave kan validere eller udlede manglende ID fra URL.
    """

    if item_data is None:
        return None

    if not isinstance(item_data, dict):
        raise TypeError(
            "item_data skal være en dictionary."
        )

    box = item_data.get("box")

    if not isinstance(box, dict):
        raise TypeError(
            "item_data['box'] skal være en dictionary."
        )

    id_key, url_key, navn_key = _checkpoint_feltnavne(
        checkpoint_type
    )

    opgave_id = str(
        box.get(id_key) or ""
    ).strip()

    opgave_url = str(
        box.get(url_key) or ""
    ).strip()

    opgave_navn = _opstart_normaliser_tekst(
        box.get(navn_key) or ""
    )

    if not any(
        (
            opgave_id,
            opgave_url,
            opgave_navn,
        )
    ):
        return None

    return {
        "opgave_id": opgave_id,
        "opgave_url": opgave_url,
        "opgave_navn": opgave_navn,
    }


def _gem_opgavecheckpoint_i_item_data(
    item_data: dict[str, Any] | None,
    checkpoint: OpstartOpgaveCheckpoint,
    checkpoint_type: str,
) -> None:
    """
    Gem checkpointet under handlingsspecifikke felter i box.

    Funktionen ændrer dictionaryen i hukommelsen. Den kaldende proces
    skal bagefter kalde update_item_data().
    """

    if item_data is None:
        return

    if not isinstance(item_data, dict):
        raise TypeError(
            "item_data skal være en dictionary."
        )

    box = item_data.get("box")

    if not isinstance(box, dict):
        raise TypeError(
            "item_data['box'] skal være en dictionary."
        )

    id_key, url_key, navn_key = _checkpoint_feltnavne(
        checkpoint_type
    )

    opgave_id = _opstart_normaliser_id(
        checkpoint.get("opgave_id")
    )

    opgave_url = _opstart_normaliser_tekst(
        checkpoint.get("opgave_url", "")
    )

    opgave_navn = _opstart_normaliser_tekst(
        checkpoint.get("opgave_navn", "")
    )

    if not opgave_id:
        raise RuntimeError(
            "Checkpointet mangler opgave-id."
        )

    if not opgave_url:
        raise RuntimeError(
            "Checkpointet mangler opgave-URL."
        )

    if not opgave_navn:
        raise RuntimeError(
            "Checkpointet mangler opgavenavn."
        )

    box[id_key] = opgave_id
    box[url_key] = opgave_url
    box[navn_key] = opgave_navn

async def genoptag_opgave_fra_checkpoint_url(
    page: Page,
    opgave_id: str,
    opgave_navn: str,
    opgave_url: str,
    borger_url: str,
    menu_sti: tuple[str, ...],
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> OpstartOpgaveCheckpoint:
    """
    Genoptag en opgave direkte fra en tidligere gemt URL.

    Hvis checkpointet findes, må denne funktion ikke oprette en ny
    opgave. En fejl under navigation eller validering sendes derfor
    videre til processen.
    """

    if page.is_closed():
        raise RuntimeError(
            "KY-siden er lukket før genoptagelse af opgaven."
        )

    opgave_id = _opstart_normaliser_id(
        opgave_id
    )

    if not opgave_id:
        raise ValueError(
            "opgave_id må ikke være tomt."
        )

    opgave_navn = _opstart_kraev_tekst(
        opgave_navn,
        "opgave_navn",
    )

    opgave_url = _opstart_kraev_tekst(
        opgave_url,
        "opgave_url",
    )

    borger_url = _opstart_kraev_tekst(
        borger_url,
        "borger_url",
    )

    url_opgave_id = hent_opgave_id_fra_url(
        opgave_url
    )

    if not url_opgave_id:
        raise RuntimeError(
            "Det gemte checkpoint indeholder en URL, men "
            "opgave-id kunne ikke udledes fra URL'en. "
            f"URL={opgave_url!r}."
        )

    if (
        url_opgave_id.casefold()
        != opgave_id.casefold()
    ):
        raise RuntimeError(
            "Det gemte opgave-id matcher ikke opgave-id'et "
            "i den gemte URL. Intet åbnes. "
            f"Gemt id={opgave_id!r}, "
            f"URL-id={url_opgave_id!r}."
        )

    person_id = _hent_person_id_fra_borger_url(
        opgave_url
    )

    if not person_id:
        person_id = _hent_person_id_fra_borger_url(
            borger_url
        )

    if not person_id:
        raise RuntimeError(
            "Hverken opgave-URL eller borger-URL indeholder "
            "et gyldigt pId. Opgaven kan ikke genoptages sikkert."
        )

    destination_url = _byg_opgave_overblik_url(
        borger_url=borger_url,
        person_id=person_id,
        opgave_id=opgave_id,
    )

    print(
        "Genoptager opgave fra gemt checkpoint: "
        f"{destination_url}",
        flush=True,
    )

    try:
        response = await page.goto(
            destination_url,
            wait_until="domcontentloaded",
            timeout=timeout,
        )
    except PlaywrightTimeoutError as error:
        raise PlaywrightTimeoutError(
            "Navigation til den gemte KY-opgave fik timeout. "
            "Der oprettes ikke en ny opgave. "
            f"Opgave-id={opgave_id!r}, "
            f"URL={destination_url!r}."
        ) from error
    except PlaywrightError as error:
        raise RuntimeError(
            "Den gemte KY-opgave kunne ikke åbnes. "
            "Der oprettes ikke en ny opgave. "
            f"Opgave-id={opgave_id!r}, "
            f"URL={destination_url!r}, "
            f"fejl={error}."
        ) from error

    if response is not None and response.status >= 400:
        raise RuntimeError(
            "Den gemte KY-opgave returnerede en HTTP-fejl. "
            "Der oprettes ikke en ny opgave. "
            f"Status={response.status}, "
            f"URL={destination_url!r}."
        )

    await _vent_paa_opgave_overblik_url(
        page=page,
        forventet_person_id=person_id,
        forventet_opgave_id=opgave_id,
        timeout=timeout,
    )

    return {
        "opgave_id": opgave_id,
        "opgave_navn": opgave_navn,
        "opgave_url": page.url,
        "borger_url": borger_url,
        "menu_sti": menu_sti,
        "genoptaget": True,
        "kilde": "gemt_checkpoint_url",
    }

def _modtag_post_normaliser_tekst(value: Any) -> str:
    """Saml whitespace og trim tekst."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _opstart_normaliser_id(
    value: Any,
) -> str | None:
    """Trim et opgave-id uden at ændre id-værdien."""

    if value is None:
        return None

    result = str(value).strip()

    return result or None

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
                    await row.locator(KYSelectors.Borgere.TABLE_CELLS).nth(1).inner_text()
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
            except PlaywrightError:
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
    row_name = _opstart_normaliser_tekst(await row.locator(KYSelectors.Borgere.TABLE_CELLS).nth(1).inner_text())
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

    loaders = page.locator(
        KYSelectors.Borgere.OPGAVE_LOADERS
    )

    for index in range(await loaders.count()):
        try:
            if await loaders.nth(index).is_visible():
                return True
        except PlaywrightError:
            continue

    return False


async def _hent_opgavenavn_fra_header(header: Locator) -> str:
    """Læs fx OPG-OPZYF96Q dynamisk fra højre side af opgaveheaderen."""

    candidates = header.locator(KYSelectors.Borgere.OPGAVE_HEADER_NAME)

    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        try:
            if not await candidate.is_visible():
                continue
            value = _normaliser_tekst(await candidate.inner_text())
            if value:
                return value
        except PlaywrightError:
            continue

    raise RuntimeError("Opgavenavnet kunne ikke læses fra opgaveheaderen.")


def _normaliser_tekst(value: str) -> str:
    """Saml whitespace og fjern mellemrum før og efter teksten."""

    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()

# ---------------------------------------------------------------------------
# Modtag post: Dokument, sag og godkendelse
# ---------------------------------------------------------------------------
class ModtagPostResultat(TypedDict):
    """Samlet resultat fra ``modtag_post()``."""

    dokument: str
    dokument_behandlet: bool
    fundet: bool
    aaben_dokument: bool
    dokument_aabnet: bool
    dokumenttekst: str
    dokument_url: str
    aabnet_url: str
    sag: str
    sag_behandlet: bool
    sag_id: str
    sagstekst: str
    aktive_sager: bool
    passive_sager: bool
    sag_valgt: bool
    godkend: bool
    godkendt: bool


class ValgtSag(TypedDict):
    """Den sag, der blev valgt på Modtag post-opgaven."""

    sag_id: str
    sagstekst: str


async def modtag_post(
    page: Page,
    opgave_url: str,
    dokument: str | None = None,
    aaben_dokument: bool = False,
    sag: str | None = None,
    aktive_sager: bool = True,
    passive_sager: bool = False,
    godkend: bool = False,
    timeout: int = OPGAVE_TIMEOUT_MS,
) -> ModtagPostResultat:
    """Behandl valgte dele af en Modtag post-opgave i ét samlet kald.

    Funktionen navigerer altid til ``opgave_url`` og bekræfter, at den åbne
    opgave er Modtag post eller Modtaget post.

    Valgfri handlinger styres af inputtene:

    * ``dokument`` tomt eller ``None``:
      Dokumenter-panelet berøres ikke.
    * ``dokument`` udfyldt:
      Dokumentet findes, og åbnes kun når ``aaben_dokument=True``.
    * ``sag`` udfyldt:
      Sagen vælges, også selv om ``godkend=False``.
    * ``godkend=True``:
      Godkend klikkes først efter en sag er valgt eller sikkert valideret.

    Dermed bruges den samme offentlige funktion til læsning, sagsvalg og
    permanent godkendelse. Selectors ligger i ``KYSelectors.Borgere``.
    """
    _modtag_post_valider_input(
        page=page,
        opgave_url=opgave_url,
        dokument=dokument,
        aaben_dokument=aaben_dokument,
        sag=sag,
        aktive_sager=aktive_sager,
        passive_sager=passive_sager,
        godkend=godkend,
        timeout=timeout,
    )

    opgave_url = opgave_url.strip()
    dokument = _modtag_post_normaliser_tekst(dokument)
    sag = _modtag_post_normaliser_tekst(sag)

    logger.info(
        "Starter Modtag post. Dokument=%r, åbn dokument=%s, sag=%r, "
        "aktive=%s, passive=%s, godkend=%s.",
        dokument or None,
        aaben_dokument,
        sag or None,
        aktive_sager,
        passive_sager,
        godkend,
    )

    await _modtag_post_naviger_til_opgave(
        page=page,
        opgave_url=opgave_url,
        timeout=timeout,
    )
    await _modtag_post_bekraeft_opgave(
        page=page,
        timeout=timeout,
    )

    resultat: ModtagPostResultat = {
        "dokument": dokument,
        "dokument_behandlet": False,
        "fundet": False,
        "aaben_dokument": aaben_dokument,
        "dokument_aabnet": False,
        "dokumenttekst": "",
        "dokument_url": "",
        "aabnet_url": "",
        "sag": sag,
        "sag_behandlet": False,
        "sag_id": "",
        "sagstekst": "",
        "aktive_sager": aktive_sager,
        "passive_sager": passive_sager,
        "sag_valgt": False,
        "godkend": godkend,
        "godkendt": False,
    }

    if dokument:
        dokument_resultat = await _modtag_post_behandl_dokument(
            page=page,
            dokument=dokument,
            aaben_dokument=aaben_dokument,
            timeout=timeout,
        )
        resultat.update(dokument_resultat)
        resultat["dokument_behandlet"] = True
    else:
        logger.info(
            "Intet dokumentkriterium er angivet. "
            "Dokumenter-panelet springes over."
        )

    if sag:
        valgt_sag = await _modtag_post_vaelg_sag(
            page=page,
            sag=sag,
            aktive_sager=aktive_sager,
            passive_sager=passive_sager,
            timeout=timeout,
        )
        resultat["sag_behandlet"] = True
        resultat["sag_id"] = valgt_sag["sag_id"]
        resultat["sagstekst"] = valgt_sag["sagstekst"]
        resultat["sag_valgt"] = True
    else:
        logger.info(
            "Intet sagssøgekriterium er angivet. Sagsvælgeren springes over."
        )

    if godkend:
        if not resultat["sag_valgt"]:
            raise RuntimeError(
                "Modtag post-opgaven må ikke godkendes, før en sag er "
                "valgt og verificeret."
            )
        await _modtag_post_godkend_opgave(
            page=page,
            timeout=timeout,
        )
        resultat["godkendt"] = True
    else:
        logger.info("Modtag post-opgaven godkendes ikke.")

    logger.info(
        "Modtag post afsluttet. Dokument behandlet=%s, fundet=%s, "
        "dokument åbnet=%s, sag valgt=%s, godkendt=%s.",
        resultat["dokument_behandlet"],
        resultat["fundet"],
        resultat["dokument_aabnet"],
        resultat["sag_valgt"],
        resultat["godkendt"],
    )
    return resultat


async def _modtag_post_naviger_til_opgave(
    page: Page,
    opgave_url: str,
    timeout: int,
) -> None:
    """Navigér til den angivne Modtag post-opgave."""
    try:
        response = await page.goto(
            opgave_url,
            wait_until="domcontentloaded",
            timeout=timeout,
        )
    except PlaywrightTimeoutError as error:
        raise PlaywrightTimeoutError(
            "Navigationen til Modtag post-opgaven fik timeout. "
            f"URL={opgave_url!r}."
        ) from error
    except PlaywrightError as error:
        raise RuntimeError(
            "Navigationen til Modtag post-opgaven fejlede. "
            f"URL={opgave_url!r}, fejl={error}."
        ) from error

    if page.is_closed():
        raise RuntimeError("KY-siden blev lukket under navigationen.")
    if response is not None and response.status >= 400:
        raise RuntimeError(
            "Modtag post-opgaven returnerede en HTTP-fejl. "
            f"Status={response.status}, URL={opgave_url!r}."
        )


async def _modtag_post_bekraeft_opgave(
    page: Page,
    timeout: int,
) -> None:
    """Vent på et synligt Modtag post- eller Modtaget post-opgavenavn."""
    pattern = re.compile(
        r"^\s*Modtag(?:et)?\s+post(?:\s*-.*)?\s*$",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under kontrollen af Modtag post."
            )

        for frame in page.frames:
            try:
                candidates = frame.locator(
                    KYSelectors.Borgere.MODTAG_POST_OPGAVENAVN
                )
                for index in range(await candidates.count()):
                    candidate = candidates.nth(index)
                    if not await candidate.is_visible():
                        continue
                    text = _modtag_post_normaliser_tekst(
                        await candidate.inner_text()
                    )
                    if pattern.fullmatch(text):
                        logger.debug("Modtag post-opgave bekræftet: %r.", text)
                        return
            except PlaywrightError:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Hverken 'Modtag post' eller 'Modtaget post' blev synlig "
        f"inden for {timeout / 1_000:.0f} sekunder. URL={page.url!r}."
    )


async def _modtag_post_behandl_dokument(
    page: Page,
    dokument: str,
    aaben_dokument: bool,
    timeout: int,
) -> dict[str, Any]:
    """Find og åbn eventuelt dokumentet på den allerede åbne opgave."""
    dokument_panel = await _modtag_post_aabn_dokumentpanel(
        page=page,
        timeout=timeout,
    )
    matchende_raekke = await _modtag_post_find_dokumentraekke(
        page=page,
        dokument_panel=dokument_panel,
        dokument=dokument,
        timeout=timeout,
    )

    if matchende_raekke is None:
        logger.info("Dokument blev ikke fundet. Kriterium=%r.", dokument)
        return {
            "fundet": False,
            "dokument_aabnet": False,
            "dokumenttekst": "",
            "dokument_url": "",
            "aabnet_url": "",
        }

    try:
        dokumenttekst = _modtag_post_normaliser_tekst(
            await matchende_raekke.inner_text()
        )
    except PlaywrightError as error:
        raise RuntimeError(
            "Den matchende dokumentrække kunne ikke læses. "
            f"Dokument={dokument!r}, fejl={error}."
        ) from error

    if not dokumenttekst:
        raise RuntimeError(
            "Den matchende dokumentrække indeholder ingen læsbar tekst."
        )

    dokument_link = await _modtag_post_find_dokumentlink(
        matchende_raekke=matchende_raekke,
        dokument=dokument,
        dokumenttekst=dokumenttekst,
    )
    dokument_url = (
        await dokument_link.get_attribute("href") or ""
    ).strip()
    if not dokument_url:
        raise RuntimeError("Dokumentets link mangler href.")

    if not aaben_dokument:
        return {
            "fundet": True,
            "dokument_aabnet": False,
            "dokumenttekst": dokumenttekst,
            "dokument_url": dokument_url,
            "aabnet_url": "",
        }

    aabnet_url = _modtag_post_normaliser_tekst(
        await _modtag_post_aabn_dokumentlink(
            page=page,
            dokument_link=dokument_link,
            timeout=timeout,
        )
    )
    if not aabnet_url:
        raise RuntimeError(
            "Dokumentlinket blev aktiveret, men den åbnede URL er tom."
        )

    return {
        "fundet": True,
        "dokument_aabnet": True,
        "dokumenttekst": dokumenttekst,
        "dokument_url": dokument_url,
        "aabnet_url": aabnet_url,
    }


async def _modtag_post_aabn_dokumentpanel(
    page: Page,
    timeout: int,
) -> Locator:
    """Find den entydige Dokumenter-toggle og åbn dens lokale panel."""
    toggles = page.locator(
        KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_TOGGLE
    )
    await toggles.first.wait_for(state="attached", timeout=timeout)

    kandidatpar: list[tuple[Locator, Locator]] = []
    for index in range(await toggles.count()):
        toggle = toggles.nth(index)
        try:
            if not await toggle.is_visible():
                continue
            container = toggle.locator(
                KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_PANEL_CONTAINER
            )
            if await container.count() == 0:
                continue
            paneler = container.first.locator(
                KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_PANEL
            )
            if await paneler.count() > 0:
                kandidatpar.append((toggle, paneler.first))
        except PlaywrightError:
            continue

    if len(kandidatpar) != 1:
        raise RuntimeError(
            "Dokumenter-toggle og panel er ikke entydige. "
            f"Antal kandidater={len(kandidatpar)}."
        )

    toggle, panel = kandidatpar[0]
    if not await _modtag_post_dokumentpanel_er_aabent(
        toggle=toggle,
        panel=panel,
    ):
        await toggle.scroll_into_view_if_needed()
        await toggle.click(timeout=min(ACTION_TIMEOUT_MS, timeout))

    await _modtag_post_vent_paa_dokumentpanel_aabent(
        page=page,
        toggle=toggle,
        panel=panel,
        timeout=timeout,
    )
    return panel


async def _modtag_post_dokumentpanel_er_aabent(
    toggle: Locator,
    panel: Locator,
) -> bool:
    """Kontrollér paneltilstanden via attributter, klasser og synlighed."""
    aria_expanded = (
        await toggle.get_attribute("aria-expanded") or ""
    ).strip().casefold()
    classes = {
        value.casefold()
        for value in (await panel.get_attribute("class") or "").split()
    }
    return (
        aria_expanded == "true"
        or "in" in classes
        or "show" in classes
    ) and await panel.is_visible()


async def _modtag_post_vent_paa_dokumentpanel_aabent(
    page: Page,
    toggle: Locator,
    panel: Locator,
    timeout: int,
) -> None:
    """Vent på en stabil åben dokumentpaneltilstand."""
    elapsed_ms = 0
    stable_checks = 0

    while elapsed_ms < timeout:
        try:
            if await _modtag_post_dokumentpanel_er_aabent(toggle, panel):
                stable_checks += 1
                if stable_checks >= 2:
                    return
            else:
                stable_checks = 0
        except PlaywrightError:
            stable_checks = 0

        await page.wait_for_timeout(100)
        elapsed_ms += 100

    raise PlaywrightTimeoutError(
        "Dokumenter-panelet blev ikke åbnet inden for tidsgrænsen."
    )


async def _modtag_post_find_dokumentraekke(
    page: Page,
    dokument_panel: Locator,
    dokument: str,
    timeout: int,
) -> Locator | None:
    """Find én dokumentrække via eksakt cellematch eller delvist match."""
    wanted = _modtag_post_normaliser_tekst(dokument).casefold()
    p10_pattern = re.compile(
        r"(?<![A-Za-z0-9])P10[\s_-]*\d+(?!\d)",
        re.IGNORECASE,
    )
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under dokumentsøgningen."
            )

        rows = dokument_panel.locator(
            KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_RAEKKER
        )
        eksakte: list[tuple[Locator, str]] = []
        delvise: list[tuple[Locator, str]] = []
        laesbare_raekker = 0

        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                if not await row.is_visible():
                    continue
                row_text = _modtag_post_normaliser_tekst(
                    await row.inner_text()
                )
                if not row_text:
                    continue
                laesbare_raekker += 1

                if wanted == "p10":
                    if p10_pattern.search(row_text):
                        eksakte.append((row, row_text))
                    continue

                cells = row.locator(
                    KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_CELLER
                )
                cell_values: list[str] = []
                for cell_index in range(await cells.count()):
                    cell = cells.nth(cell_index)
                    if await cell.is_visible():
                        value = _modtag_post_normaliser_tekst(
                            await cell.inner_text()
                        )
                        if value:
                            cell_values.append(value)

                if (
                    any(value.casefold() == wanted for value in cell_values)
                    or row_text.casefold() == wanted
                ):
                    eksakte.append((row, row_text))
                elif wanted in row_text.casefold():
                    delvise.append((row, row_text))
            except PlaywrightError:
                continue

        matches = eksakte if eksakte else delvise
        if len(matches) == 1:
            return matches[0][0]
        if len(matches) > 1:
            raise RuntimeError(
                "Flere dokumentrækker matcher kriteriet. "
                f"Dokument={dokument!r}, "
                f"matches={[text for _, text in matches]!r}."
            )
        if laesbare_raekker > 0:
            return None

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    return None


async def _modtag_post_find_dokumentlink(
    matchende_raekke: Locator,
    dokument: str,
    dokumenttekst: str,
) -> Locator:
    """Returnér præcis ét synligt og aktivt dokumentlink i rækken."""
    links = matchende_raekke.locator(
        KYSelectors.Borgere.MODTAG_POST_DOKUMENTER_AABN_LINK
    )
    matches: list[Locator] = []

    for index in range(await links.count()):
        link = links.nth(index)
        try:
            if await link.is_visible() and await link.is_enabled():
                matches.append(link)
        except PlaywrightError:
            continue

    if len(matches) != 1:
        raise RuntimeError(
            "Dokumentrækken skal indeholde præcis ét synligt og aktivt link. "
            f"Dokument={dokument!r}, antal={len(matches)}, "
            f"rækketekst={dokumenttekst!r}."
        )
    return matches[0]


async def _modtag_post_aabn_dokumentlink(
    page: Page,
    dokument_link: Locator,
    timeout: int,
) -> str:
    """Klik dokumentlinket og returnér den åbnede URL."""
    await dokument_link.wait_for(state="visible", timeout=timeout)
    await dokument_link.scroll_into_view_if_needed()

    href = (await dokument_link.get_attribute("href") or "").strip()
    target = (
        await dokument_link.get_attribute("target") or ""
    ).strip().casefold()
    if not href:
        raise RuntimeError("Dokumentlinket mangler href.")

    if target == "_blank":
        try:
            async with page.expect_popup(timeout=timeout) as popup_info:
                await dokument_link.click(
                    timeout=min(ACTION_TIMEOUT_MS, timeout)
                )
            dokument_page = await popup_info.value
            try:
                await dokument_page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=timeout,
                )
            except PlaywrightTimeoutError:
                pass

            elapsed_ms = 0
            while (
                dokument_page.url == "about:blank"
                and elapsed_ms < timeout
                and not dokument_page.is_closed()
            ):
                await dokument_page.wait_for_timeout(POLL_INTERVAL_MS)
                elapsed_ms += POLL_INTERVAL_MS

            aabnet_url = dokument_page.url.strip()
            if not aabnet_url or aabnet_url == "about:blank":
                raise RuntimeError(
                    "Dokumentfanen blev åbnet uden en læsbar URL."
                )
            return aabnet_url
        except PlaywrightTimeoutError as error:
            raise PlaywrightTimeoutError(
                "Dokumentlinket blev klikket, men ingen ny fane blev åbnet. "
                f"href={href!r}."
            ) from error

    url_foer = page.url
    await dokument_link.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
    elapsed_ms = 0
    while elapsed_ms < timeout:
        if page.is_closed():
            return href
        if page.url != url_foer:
            return page.url
        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Dokumentlinket blev klikket, men siden skiftede ikke URL."
    )


async def _modtag_post_vaelg_sag(
    page: Page,
    sag: str,
    aktive_sager: bool,
    passive_sager: bool,
    timeout: int,
) -> ValgtSag:
    """Åbn sagsvælgeren, filtrér og vælg præcis én matchende sag."""
    hovedfelt = page.locator(
        KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_INPUT
    ).first
    await hovedfelt.wait_for(state="visible", timeout=timeout)
    await hovedfelt.scroll_into_view_if_needed()

    nuvaerende_vaerdi = _modtag_post_normaliser_tekst(
        await hovedfelt.input_value()
    )
    if nuvaerende_vaerdi.casefold() == "1 sag valgt":
        valgt = await _modtag_post_hent_valgt_sag(page)
        if valgt is None:
            raise RuntimeError(
                "Sagsvælgeren viser '1 sag valgt', men den valgte sag "
                "kunne ikke aflæses."
            )
        if not _modtag_post_sag_matcher(
            sag=sag,
            sag_id=valgt["sag_id"],
            sagstekst=valgt["sagstekst"],
        ):
            raise RuntimeError(
                "Modtag post har allerede én valgt sag, men sagen matcher "
                f"ikke søgekriteriet {sag!r}. Valgt sag={valgt!r}."
            )
        return valgt

    await hovedfelt.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
    await _modtag_post_saet_checkbox(
        page=page,
        selector=KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_AKTIVE,
        checked=aktive_sager,
        feltnavn="Aktive sager",
        timeout=timeout,
    )
    await _modtag_post_saet_checkbox(
        page=page,
        selector=KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_PASSIVE,
        checked=passive_sager,
        feltnavn="Passive sager",
        timeout=timeout,
    )

    soegefelt = await _modtag_post_find_eneste_synlige(
        page=page,
        selector=KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_SOEG,
        feltnavn="sagsvælgerens søgefelt",
        timeout=timeout,
    )
    await soegefelt.fill(sag)
    await soegefelt.dispatch_event("input")
    await soegefelt.dispatch_event("change")
    await soegefelt.dispatch_event("keyup")

    matchende_raekke = await _modtag_post_find_sagsraekke(
        page=page,
        sag=sag,
        aktive_sager=aktive_sager,
        passive_sager=passive_sager,
        timeout=timeout,
    )
    sag_id = _modtag_post_normaliser_tekst(
        await matchende_raekke.get_attribute("data-id") or ""
    )
    sagstekst = _modtag_post_normaliser_tekst(
        await matchende_raekke.inner_text()
    )
    if not sag_id:
        raise RuntimeError("Den valgte sagsrække mangler data-id.")

    vaelg_celle = matchende_raekke.locator(
        KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_VAELG_CELLE
    ).first
    if await vaelg_celle.count() == 0:
        raise RuntimeError(
            "Den matchende sagsrække mangler valgcellen."
        )

    await vaelg_celle.scroll_into_view_if_needed()
    await vaelg_celle.click(timeout=min(ACTION_TIMEOUT_MS, timeout))
    await _modtag_post_vent_paa_en_sag_valgt(
        hovedfelt=hovedfelt,
        timeout=timeout,
    )
    await _modtag_post_luk_sagsvaelger(
        page=page,
        timeout=timeout,
    )

    logger.info(
        "Sag valgt på Modtag post. Kriterium=%r, sag-id=%r.",
        sag,
        sag_id,
    )
    return {
        "sag_id": sag_id,
        "sagstekst": sagstekst,
    }


async def _modtag_post_hent_valgt_sag(
    page: Page,
) -> ValgtSag | None:
    """Aflæs den ene markerede sag, hvis den findes."""
    valgte = page.locator(
        KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_VALGTE_RAEKKER
    )
    synlige: list[Locator] = []

    for index in range(await valgte.count()):
        row = valgte.nth(index)
        try:
            if await row.is_visible():
                synlige.append(row)
        except PlaywrightError:
            continue

    if len(synlige) != 1:
        return None

    row = synlige[0]
    sag_id = _modtag_post_normaliser_tekst(
        await row.get_attribute("data-id") or ""
    )
    sagstekst = _modtag_post_normaliser_tekst(
        await row.inner_text()
    )
    if not sag_id or not sagstekst:
        return None

    return {
        "sag_id": sag_id,
        "sagstekst": sagstekst,
    }


async def _modtag_post_saet_checkbox(
    page: Page,
    selector: str,
    checked: bool,
    feltnavn: str,
    timeout: int,
) -> None:
    """Sæt præcis én synlig checkbox til den ønskede tilstand."""
    checkbox = await _modtag_post_find_eneste_synlige(
        page=page,
        selector=selector,
        feltnavn=feltnavn,
        timeout=timeout,
    )
    if not await checkbox.is_enabled():
        raise RuntimeError(
            f"Checkboxen {feltnavn!r} er synlig, men ikke aktiv."
        )
    if await checkbox.is_checked() != checked:
        await checkbox.set_checked(
            checked,
            timeout=min(ACTION_TIMEOUT_MS, timeout),
        )
    if await checkbox.is_checked() != checked:
        raise RuntimeError(
            f"Checkboxen {feltnavn!r} fik ikke tilstanden {checked}."
        )


async def _modtag_post_find_eneste_synlige(
    page: Page,
    selector: str,
    feltnavn: str,
    timeout: int,
) -> Locator:
    """Vent på og returnér præcis ét synligt element for en selector."""
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                f"KY-siden blev lukket under søgning efter {feltnavn}."
            )

        candidates = page.locator(selector)
        synlige: list[Locator] = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if await candidate.is_visible():
                    synlige.append(candidate)
            except PlaywrightError:
                continue

        if len(synlige) == 1:
            return synlige[0]
        if len(synlige) > 1:
            raise RuntimeError(
                f"Flere synlige elementer matcher {feltnavn}. "
                f"Antal={len(synlige)}."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        f"Et synligt element til {feltnavn} blev ikke fundet inden for "
        f"{timeout / 1_000:.0f} sekunder."
    )


async def _modtag_post_find_sagsraekke(
    page: Page,
    sag: str,
    aktive_sager: bool,
    passive_sager: bool,
    timeout: int,
) -> Locator:
    """Find ét entydigt eksakt eller delvist match i sagsvælgeren."""
    wanted = _modtag_post_normaliser_tekst(sag).casefold()
    elapsed_ms = 0
    seneste_laesbare: list[str] = []

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket under søgningen efter sag."
            )

        rows = page.locator(
            KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_RAEKKER
        )
        eksakte: list[tuple[Locator, str]] = []
        delvise: list[tuple[Locator, str]] = []
        seneste_laesbare = []

        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                if not await row.is_visible():
                    continue
                tilstand = _modtag_post_normaliser_tekst(
                    await row.get_attribute("data-tilstand") or ""
                ).casefold()
                if tilstand == "aktiv" and not aktive_sager:
                    continue
                if tilstand == "passiv" and not passive_sager:
                    continue

                cells = row.locator(
                    KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_DATA_CELLER
                )
                cell_values: list[str] = []
                for cell_index in range(await cells.count()):
                    cell = cells.nth(cell_index)
                    if await cell.is_visible():
                        value = _modtag_post_normaliser_tekst(
                            await cell.inner_text()
                        )
                        if value:
                            cell_values.append(value)

                row_text = _modtag_post_normaliser_tekst(
                    await row.inner_text()
                )
                if not row_text:
                    continue
                seneste_laesbare.append(row_text)

                if any(
                    value.casefold() == wanted
                    for value in cell_values
                ):
                    eksakte.append((row, row_text))
                elif wanted in row_text.casefold():
                    delvise.append((row, row_text))
            except PlaywrightError:
                continue

        matches = eksakte if eksakte else delvise
        if len(matches) == 1:
            return matches[0][0]
        if len(matches) > 1:
            raise RuntimeError(
                "Flere sager matcher søgekriteriet. Intet er valgt. "
                f"Sag={sag!r}, matches={[text for _, text in matches]!r}."
            )
        if seneste_laesbare:
            raise RuntimeError(
                "Ingen synlig sag matcher søgekriteriet. "
                f"Sag={sag!r}, synlige rækker={seneste_laesbare!r}."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Ingen sagsrække blev tilgængelig inden for "
        f"{timeout / 1_000:.0f} sekunder. Sag={sag!r}."
    )


async def _modtag_post_vent_paa_en_sag_valgt(
    hovedfelt: Locator,
    timeout: int,
) -> None:
    """Vent på, at hovedfeltet stabilt viser '1 sag valgt'."""
    elapsed_ms = 0
    stable_checks = 0
    seneste_vaerdi = ""

    while elapsed_ms < timeout:
        try:
            seneste_vaerdi = _modtag_post_normaliser_tekst(
                await hovedfelt.input_value()
            )
            if seneste_vaerdi.casefold() == "1 sag valgt":
                stable_checks += 1
                if stable_checks >= 2:
                    return
            else:
                stable_checks = 0
        except PlaywrightError:
            stable_checks = 0

        await hovedfelt.page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsvælgeren viste ikke '1 sag valgt' inden for "
        f"{timeout / 1_000:.0f} sekunder. "
        f"Seneste værdi={seneste_vaerdi!r}."
    )


async def _modtag_post_luk_sagsvaelger(
    page: Page,
    timeout: int,
) -> None:
    """Luk sagsvælgeren og verificér, at søgefeltet skjules."""
    soegefelter = page.locator(
        KYSelectors.Borgere.MODTAG_POST_SAGSVAELGER_SOEG
    )
    for index in range(await soegefelter.count()):
        candidate = soegefelter.nth(index)
        try:
            if await candidate.is_visible():
                await candidate.press("Escape")
                break
        except PlaywrightError:
            continue

    elapsed_ms = 0
    stable_checks = 0
    while elapsed_ms < timeout:
        synlige = 0
        for index in range(await soegefelter.count()):
            try:
                if await soegefelter.nth(index).is_visible():
                    synlige += 1
            except PlaywrightError:
                continue

        if synlige == 0:
            stable_checks += 1
            if stable_checks >= 2:
                return
        else:
            stable_checks = 0

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Sagsvælgeren blev ikke lukket inden for tidsgrænsen."
    )


async def _modtag_post_godkend_opgave(
    page: Page,
    timeout: int,
) -> None:
    """Klik Godkend og verificér, at KY har behandlet handlingen."""
    await _modtag_post_bekraeft_opgave(
        page=page,
        timeout=timeout,
    )
    godkend_knap = await _modtag_post_find_godkend_knap(
        page=page,
        timeout=timeout,
    )
    url_foer = page.url
    opgave_id_foer = _hent_query_parameter(
        url_foer,
        "opgaveId",
    )

    await godkend_knap.scroll_into_view_if_needed()
    await godkend_knap.click(
        timeout=min(ACTION_TIMEOUT_MS, timeout)
    )
    await _modtag_post_vent_paa_godkendelse_behandlet(
        page=page,
        klikket_knap=godkend_knap,
        url_foer=url_foer,
        opgave_id_foer=opgave_id_foer,
        timeout=timeout,
    )
    logger.info("Modtag post-opgaven er verificeret godkendt.")


async def _modtag_post_find_godkend_knap(
    page: Page,
    timeout: int,
) -> Locator:
    """Find præcis én synlig og aktiv Godkend-knap."""
    pattern = re.compile(r"^\s*Godkend\s*$", re.IGNORECASE)
    elapsed_ms = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            raise RuntimeError(
                "KY-siden blev lukket før Godkend-knappen blev fundet."
            )

        matches: list[Locator] = []
        for frame in page.frames:
            try:
                candidates = frame.locator(
                    KYSelectors.Borgere.MODTAG_POST_GODKEND
                )
                for index in range(await candidates.count()):
                    candidate = candidates.nth(index)
                    if not await candidate.is_visible():
                        continue
                    if not await candidate.is_enabled():
                        continue
                    text = _modtag_post_normaliser_tekst(
                        await candidate.get_attribute("value")
                        or await candidate.inner_text()
                    )
                    if pattern.fullmatch(text):
                        matches.append(candidate)
            except PlaywrightError:
                continue

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                "Flere synlige og aktive Godkend-knapper blev fundet. "
                "Intet er klikket."
            )

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Godkend-knappen blev ikke fundet på Modtag post-opgaven."
    )


async def _modtag_post_vent_paa_godkendelse_behandlet(
    page: Page,
    klikket_knap: Locator,
    url_foer: str,
    opgave_id_foer: str,
    timeout: int,
) -> None:
    """Vent på et stabilt signal om, at KY har behandlet Godkend-klikket."""
    elapsed_ms = 0
    stable_checks = 0

    while elapsed_ms < timeout:
        if page.is_closed():
            return

        try:
            url_nu = page.url
            opgave_id_nu = _hent_query_parameter(
                url_nu,
                "opgaveId",
            )
            url_aendret = url_nu != url_foer
            opgave_aendret = bool(opgave_id_foer) and (
                not opgave_id_nu
                or opgave_id_nu.casefold() != opgave_id_foer.casefold()
            )

            synlige_godkend = 0
            for frame in page.frames:
                try:
                    candidates = frame.locator(
                        KYSelectors.Borgere.MODTAG_POST_GODKEND
                    )
                    for index in range(await candidates.count()):
                        try:
                            if await candidates.nth(index).is_visible():
                                synlige_godkend += 1
                        except PlaywrightError:
                            continue
                except PlaywrightError:
                    continue

            try:
                klikket_knap_vaek = not await klikket_knap.is_visible()
            except PlaywrightError:
                klikket_knap_vaek = True

            behandlet = (
                url_aendret
                or opgave_aendret
                or synlige_godkend == 0
                or klikket_knap_vaek
            )
            stable_checks = stable_checks + 1 if behandlet else 0
            if stable_checks >= 3:
                return
        except PlaywrightError:
            stable_checks += 1
            if stable_checks >= 3:
                return

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise PlaywrightTimeoutError(
        "Godkend blev klikket, men KY viste ikke et stabilt "
        "afslutningssignal inden timeout."
    )


def _modtag_post_valider_input(
    page: Page,
    opgave_url: str,
    dokument: str | None,
    aaben_dokument: bool,
    sag: str | None,
    aktive_sager: bool,
    passive_sager: bool,
    godkend: bool,
    timeout: int,
) -> None:
    """Validér input og kombinationer før browserhandlinger udføres."""
    if not isinstance(page, Page):
        raise TypeError("page skal være en async Playwright Page.")
    if not isinstance(opgave_url, str):
        raise TypeError("opgave_url skal være en tekststreng.")
    if dokument is not None and not isinstance(dokument, str):
        raise TypeError("dokument skal være en tekststreng eller None.")
    if not isinstance(aaben_dokument, bool):
        raise TypeError("aaben_dokument skal være True eller False.")
    if sag is not None and not isinstance(sag, str):
        raise TypeError("sag skal være en tekststreng eller None.")
    if not isinstance(aktive_sager, bool):
        raise TypeError("aktive_sager skal være True eller False.")
    if not isinstance(passive_sager, bool):
        raise TypeError("passive_sager skal være True eller False.")
    if not isinstance(godkend, bool):
        raise TypeError("godkend skal være True eller False.")
    if not isinstance(timeout, int):
        raise TypeError("timeout skal være et heltal i millisekunder.")
    if timeout <= 0:
        raise ValueError("timeout skal være større end 0.")
    if not opgave_url.strip():
        raise ValueError("opgave_url må ikke være tom.")
    if re.match(
        r"^https?://",
        opgave_url.strip(),
        re.IGNORECASE,
    ) is None:
        raise ValueError("opgave_url skal være en absolut HTTP(S)-URL.")
    if aaben_dokument and not _modtag_post_normaliser_tekst(dokument):
        raise ValueError(
            "dokument skal være udfyldt, når aaben_dokument=True."
        )
    if godkend and not _modtag_post_normaliser_tekst(sag):
        raise ValueError("sag skal være udfyldt, når godkend=True.")
    if (
        _modtag_post_normaliser_tekst(sag)
        and not aktive_sager
        and not passive_sager
    ):
        raise ValueError(
            "Mindst én af aktive_sager eller passive_sager skal være True, "
            "når en sag skal vælges."
        )
    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før navigationen.")


def _modtag_post_sag_matcher(
    sag: str,
    sag_id: str,
    sagstekst: str,
) -> bool:
    """Kontrollér om en allerede valgt sag matcher søgekriteriet."""
    wanted = _modtag_post_normaliser_tekst(sag).casefold()
    actual_id = _modtag_post_normaliser_tekst(sag_id).casefold()
    actual_text = _modtag_post_normaliser_tekst(sagstekst).casefold()
    return wanted == actual_id or wanted in actual_text


