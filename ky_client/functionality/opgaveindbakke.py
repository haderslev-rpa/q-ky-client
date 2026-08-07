"""Asynkron funktionalitet til KY's opgaveindbakke."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


logger = logging.getLogger(__name__)

DEFAULT_OPGAVEPAKKE = "NT - Modtaget Post ( HTF, RESS, REVA )"

ACTION_TIMEOUT_MS = 15_000
TABLE_TIMEOUT_MS = 120_000
WAIT_AFTER_MENU_OPEN_MS = 750
POLL_INTERVAL_MS = 250
TABLE_STABLE_PERIOD_MS = 1_000

DROPDOWN_BUTTON = "button[data-id='arbejdspakker']"
DROPDOWN_OPTIONS = (
    ".bootstrap-select.open .dropdown-menu li:visible, "
    ".bootstrap-select.show .dropdown-menu li:visible, "
    ".dropdown-menu.show li:visible"
)
RESULTS_TABLE = "table#ubehandledeTable"
PROCESSING_INDICATOR = "#ubehandledeTable_processing"

EMPTY_ROW_MARKERS = (
    "ingen data",
    "ingen opgaver",
    "ingen resultater",
    "der blev ikke fundet",
    "no data available",
    "no matching records",
)


class OpgaveindbakkeError(RuntimeError):
    """Fejl ved navigation eller opslag i KY's opgaveindbakke."""


class OpgaveindbakkeClient:
    """Klient til KY's opgaveindbakke på en aktiv async Playwright-side.

    Klienten kan oprettes på to måder:

    1. Direkte med en Playwright Page::

        client = OpgaveindbakkeClient(page=page)

    2. Bagudkompatibelt med et objekt, som har en ``page``-attribut::

        client = OpgaveindbakkeClient(ky_client=ky_client)
    """

    def __init__(
        self,
        ky_client: Any | None = None,
        *,
        page: Page | None = None,
    ) -> None:
        if page is None and ky_client is not None:
            page = getattr(ky_client, "page", None)

        if page is None:
            raise ValueError(
                "OpgaveindbakkeClient kræver enten page=Page eller "
                "ky_client med en page-attribut."
            )

        self._client = ky_client
        self._page = page
        self.valgt_opgavepakke = ""
        self.forventet_antal: int | None = None
        self._last_table: Locator | None = None
        self._last_rows: list[Locator] = []

    async def hent_opgaver(
        self,
        opgavepakke: str = DEFAULT_OPGAVEPAKKE,
    ) -> list[dict[str, Any]]:
        """Vælg en opgavepakke og returnér dens opgaver som ``list[dict]``.

        Opgavepakken angives uden det dynamiske antal. KY-tekster som
        ``Min opgavepakke (12)`` matches automatisk.

        Hver dictionary indeholder tabelkolonnerne og, når tilgængeligt:

        - ``Opgave-Id``
        - ``Original URL``
        - ``URL``: renset og samlet med ``cb&opgaveId=<id>``

        Hjælpefeltet ``Alle URL'er`` bruges kun internt og fjernes fra output.
        Funktionen navigerer ikke til de fundne URL'er.
        """

        self._validate_active_page()

        package_name = self._normalise(opgavepakke)
        if not package_name:
            raise ValueError("Navnet på opgavepakken må ikke være tomt.")

        old_table_state = await self._get_current_table_state()

        frame, dropdown = await self._wait_for_dropdown_ready()
        await dropdown.scroll_into_view_if_needed()
        await dropdown.click(timeout=ACTION_TIMEOUT_MS)
        await self._wait_for_dropdown_open(dropdown)

        selected_text = await self._select_package(
            preferred_frame=frame,
            package_name=package_name,
        )

        self.valgt_opgavepakke = selected_text
        self.forventet_antal = self._extract_count(selected_text)

        logger.info("Valgt opgavepakke: %s", selected_text)

        if self.forventet_antal == 0:
            self._last_rows = []
            return []

        table_frame, _ = await self._wait_for_new_table(
            old_table_state=old_table_state,
            selected_package_text=selected_text,
        )

        table = await self._wait_for_table_stable(table_frame)
        self._last_table = table

        rows = await self._read_table(table)
        self._last_rows = await self._get_visible_data_rows(table)

        logger.info(
            "hent_opgaver() hentede %d række(r) fra '%s'.",
            len(rows),
            selected_text,
        )

        return rows

    async def aabn_foerste_opgave(self) -> None:
        """Åbn den øverste datarække fra seneste ``hent_opgaver()``-kald."""

        if not self._last_rows:
            raise OpgaveindbakkeError(
                "Der er ingen tabelrækker at åbne. Kald hent_opgaver() først."
            )

        row = self._last_rows[0]
        await row.scroll_into_view_if_needed()

        links = row.locator("a:visible")
        if await links.count() > 0:
            await links.first.click(timeout=ACTION_TIMEOUT_MS)
        else:
            buttons = row.locator(
                "button:visible, input[type='button']:visible"
            )
            if await buttons.count() > 0:
                await buttons.first.click(timeout=ACTION_TIMEOUT_MS)
            else:
                await row.click(timeout=ACTION_TIMEOUT_MS)

        await self._page.wait_for_timeout(2_000)
        logger.info("Den øverste opgave blev åbnet.")

    async def _wait_for_dropdown_ready(self) -> tuple[Frame, Locator]:
        """Vent på KY's synlige Bootstrap-knap til opgavepakker."""

        elapsed_ms = 0

        while elapsed_ms < TABLE_TIMEOUT_MS:
            for frame in self._page.frames:
                try:
                    buttons = frame.locator(DROPDOWN_BUTTON)

                    for index in range(await buttons.count()):
                        button = buttons.nth(index)

                        if not await button.is_visible():
                            continue
                        if not await button.is_enabled():
                            continue
                        if await button.get_attribute("disabled") is not None:
                            continue
                        if await button.get_attribute("aria-disabled") == "true":
                            continue

                        return frame, button
                except Exception:
                    continue

            await self._page.wait_for_timeout(POLL_INTERVAL_MS)
            elapsed_ms += POLL_INTERVAL_MS

        raise OpgaveindbakkeError(
            "Dropdown-knappen til opgavepakker blev ikke klar. "
            f"Aktuel URL: {self._page.url}"
        )

    async def _wait_for_dropdown_open(self, dropdown: Locator) -> None:
        """Vent til Bootstrap-menuen er åbnet og har en synlig mulighed."""

        elapsed_ms = 0

        while elapsed_ms < TABLE_TIMEOUT_MS:
            try:
                expanded = await dropdown.get_attribute("aria-expanded")
                options = self._page.locator(DROPDOWN_OPTIONS)

                if expanded == "true":
                    for index in range(await options.count()):
                        if await options.nth(index).is_visible():
                            return
            except Exception:
                pass

            await self._page.wait_for_timeout(100)
            elapsed_ms += 100

        raise OpgaveindbakkeError(
            "Dropdown-menuen blev ikke synlig efter klik."
        )

    async def _select_package(
        self,
        preferred_frame: Frame,
        package_name: str,
    ) -> str:
        """Vælg opgavepakken med det dynamiske antal i parentes."""

        pattern = self._package_pattern(package_name)
        frames = [
            preferred_frame,
            *[
                frame
                for frame in self._page.frames
                if frame != preferred_frame
            ],
        ]

        elapsed_ms = 0

        while elapsed_ms < TABLE_TIMEOUT_MS:
            for frame in frames:
                try:
                    options = frame.locator(DROPDOWN_OPTIONS)

                    for index in range(await options.count()):
                        option = options.nth(index)

                        if not await option.is_visible():
                            continue

                        text = self._normalise(await option.inner_text())
                        if pattern.fullmatch(text) is None:
                            continue

                        await option.scroll_into_view_if_needed()
                        await option.click(timeout=ACTION_TIMEOUT_MS)
                        return text
                except Exception:
                    continue

            await self._page.wait_for_timeout(POLL_INTERVAL_MS)
            elapsed_ms += POLL_INTERVAL_MS

        raise OpgaveindbakkeError(
            f"Opgavepakken blev ikke fundet: {package_name}"
        )

    async def _get_current_table_state(self) -> str | None:
        """Hent en signatur for tabellen før dropdown-valget."""

        for frame in self._page.frames:
            try:
                table = frame.locator(RESULTS_TABLE).first
                if await table.count() == 0:
                    continue
                return await self._get_table_state(table)
            except Exception:
                continue

        return None

    async def _get_table_state(self, table: Locator) -> str:
        """Hent en stabil tekstsignatur for tabellens rækker."""

        return await table.evaluate(
            r"""
            table => {
                const normalize = value =>
                    (value || '').replace(/\s+/g, ' ').trim();

                return Array.from(table.querySelectorAll('tbody tr'))
                    .map(row => {
                        const id = row.getAttribute('data-opgaveid')
                            || row.getAttribute('data-id')
                            || '';
                        return `${id}|${normalize(row.innerText)}`;
                    })
                    .join('||');
            }
            """
        )

    async def _wait_for_new_table(
        self,
        old_table_state: str | None,
        selected_package_text: str,
    ) -> tuple[Frame, Locator]:
        """Vent til dropdown-valget har indlæst den valgte tabel."""

        elapsed_ms = 0
        saw_processing = False
        unchanged_grace_ms = 3_000

        package_name = re.sub(
            r"\s*\(\s*\d+\s*\)\s*$",
            "",
            selected_package_text,
        ).strip()

        while elapsed_ms < TABLE_TIMEOUT_MS:
            for frame in self._page.frames:
                try:
                    processing = frame.locator(PROCESSING_INDICATOR).first

                    if (
                        await processing.count() > 0
                        and await processing.is_visible()
                    ):
                        saw_processing = True
                        continue

                    dropdown = frame.locator(DROPDOWN_BUTTON).first
                    if await dropdown.count() == 0:
                        continue

                    dropdown_text = (
                        await dropdown.get_attribute("title")
                        or await dropdown.inner_text()
                    )

                    if package_name.casefold() not in self._normalise(
                        dropdown_text
                    ).casefold():
                        continue

                    table = frame.locator(RESULTS_TABLE).first
                    if await table.count() == 0:
                        continue
                    if not await table.is_visible():
                        continue
                    if await table.locator("tbody tr").count() == 0:
                        continue

                    new_state = await self._get_table_state(table)
                    changed = (
                        old_table_state is None
                        or new_state != old_table_state
                    )

                    if (
                        changed
                        or saw_processing
                        or elapsed_ms >= unchanged_grace_ms
                    ):
                        return frame, table
                except Exception:
                    continue

            await self._page.wait_for_timeout(POLL_INTERVAL_MS)
            elapsed_ms += POLL_INTERVAL_MS

        raise OpgaveindbakkeError(
            "Den nye opgavetabel blev ikke indlæst. "
            f"Aktuel URL: {self._page.url}"
        )

    async def _wait_for_table_stable(self, frame: Frame) -> Locator:
        """Vent til tabellen har været uændret i mindst ét sekund."""

        elapsed_ms = 0
        stable_ms = 0
        previous_state: str | None = None

        while elapsed_ms < TABLE_TIMEOUT_MS:
            try:
                processing = frame.locator(PROCESSING_INDICATOR).first

                if (
                    await processing.count() > 0
                    and await processing.is_visible()
                ):
                    stable_ms = 0
                    previous_state = None
                else:
                    table = frame.locator(RESULTS_TABLE).first

                    if await table.count() > 0 and await table.is_visible():
                        state = await table.evaluate(
                            """
                            table => Array.from(
                                table.querySelectorAll('tbody tr')
                            ).map(row => row.outerHTML).join('')
                            """
                        )

                        if state == previous_state:
                            stable_ms += POLL_INTERVAL_MS
                        else:
                            previous_state = state
                            stable_ms = 0

                        if stable_ms >= TABLE_STABLE_PERIOD_MS:
                            return table
            except Exception:
                stable_ms = 0
                previous_state = None

            await self._page.wait_for_timeout(POLL_INTERVAL_MS)
            elapsed_ms += POLL_INTERVAL_MS

        raise OpgaveindbakkeError(
            "Den nye opgavetabel blev ikke stabil."
        )

    async def _read_table(
        self,
        table: Locator,
    ) -> list[dict[str, Any]]:
        """Læs tabellen samlet i browseren, inklusive ID og URL'er."""

        rows: list[dict[str, Any]] = await table.evaluate(
            r"""
            table => {
                const normalize = value =>
                    (value || '').replace(/\s+/g, ' ').trim();

                const cleanRawUrl = value => {
                    if (!value) return null;

                    const cleaned = value
                        .trim()
                        .replace(/^['"]+|['"]+$/g, '')
                        .replace(/\/'\//g, '/')
                        .replace(/\/"\//g, '/');

                    if (
                        !cleaned
                        || cleaned === '#'
                        || cleaned.toLowerCase().startsWith('javascript:')
                    ) {
                        return null;
                    }

                    return cleaned;
                };

                const absoluteUrl = value => {
                    const cleaned = cleanRawUrl(value);
                    if (!cleaned) return null;

                    try {
                        return new URL(cleaned, document.baseURI).href;
                    } catch {
                        return cleaned;
                    }
                };

                const headers = Array.from(
                    table.querySelectorAll('thead th, [role="columnheader"]')
                ).map((header, index) =>
                    normalize(header.innerText) || `kolonne_${index + 1}`
                );

                return Array.from(table.querySelectorAll('tbody tr'))
                    .filter(row => {
                        const style = window.getComputedStyle(row);
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && !row.querySelector('td.dataTables_empty')
                            && row.querySelectorAll('td, [role="gridcell"]').length > 0;
                    })
                    .map(row => {
                        const result = {};
                        const cells = Array.from(
                            row.querySelectorAll('td, [role="gridcell"]')
                        );

                        cells.forEach((cell, index) => {
                            const key = headers[index]
                                || `kolonne_${index + 1}`;
                            result[key] = normalize(cell.innerText);
                        });

                        const taskId = row.getAttribute('data-opgaveid')
                            || row.getAttribute('data-id');

                        if (taskId) {
                            result['Opgave-Id'] = taskId;
                        }

                        const urls = [];

                        row.querySelectorAll('a[href]').forEach(anchor => {
                            const url = absoluteUrl(anchor.getAttribute('href'));
                            if (url) urls.push(url);
                        });

                        const elements = [
                            row,
                            ...row.querySelectorAll(
                                '[data-url], [data-href], [onclick], [data-onclick]'
                            )
                        ];

                        elements.forEach(element => {
                            ['data-url', 'data-href'].forEach(name => {
                                const url = absoluteUrl(element.getAttribute(name));
                                if (url) urls.push(url);
                            });

                            ['onclick', 'data-onclick'].forEach(name => {
                                const script = element.getAttribute(name) || '';
                                const match = script.match(/['"](\/[^'"]+)['"]/);

                                if (match) {
                                    const url = absoluteUrl(match[1]);
                                    if (url) urls.push(url);
                                }
                            });
                        });

                        const uniqueUrls = [...new Set(urls)];
                        result['URL'] = uniqueUrls[0] || null;
                        result['_Alle URLer'] = uniqueUrls;

                        return result;
                    });
            }
            """
        )

        return self._ret_opgave_urls(rows)

    async def _get_visible_data_rows(self, table: Locator) -> list[Locator]:
        """Gem klikbare datarækker til ``aabn_foerste_opgave()``."""

        candidates = table.locator(
            "tbody tr:not(:has(td.dataTables_empty))"
        )
        rows: list[Locator] = []

        for index in range(await candidates.count()):
            row = candidates.nth(index)
            try:
                if await row.is_visible():
                    rows.append(row)
            except PlaywrightTimeoutError:
                continue

        return rows

    def _ret_opgave_urls(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Returnér kun Original URL og færdig URL i det offentlige output."""

        result: list[dict[str, Any]] = []

        for row in rows:
            item = dict(row)
            task_id = str(item.get("Opgave-Id") or "").strip()
            original_url = str(item.get("URL") or "").strip()

            item["Original URL"] = original_url or None

            if original_url:
                cleaned_url = self._clean_task_url(original_url)
                item["URL"] = (
                    self._build_task_url(cleaned_url, task_id)
                    if task_id
                    else cleaned_url
                )
            else:
                item["URL"] = None

            # Interne hjælpefelter må ikke ende i API-outputtet.
            item.pop("Renset URL", None)
            item.pop("Alle URL'er", None)
            item.pop("_Alle URLer", None)

            result.append(item)

        return result

    def _validate_active_page(self) -> None:
        """Kontrollér de synkrone forudsætninger for den aktive side."""

        if self._page.is_closed():
            raise OpgaveindbakkeError("KY-siden er lukket.")

    @staticmethod
    def _clean_task_url(url: str) -> str:
        """Fjern fejlagtige apostroffer og URL-kodede anførselstegn."""

        cleaned = url.strip()
        cleaned = cleaned.replace("/'/", "/")
        cleaned = cleaned.replace('/"/', "/")
        cleaned = re.sub(r"/%27/", "/", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"/%22/", "/", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:%27|%22|['\"])$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    @staticmethod
    def _build_task_url(original_url: str, task_id: str) -> str:
        """Byg URL'en med ``cb&opgaveId=<id>`` uden at navigere."""

        url = original_url.strip()
        task_id = task_id.strip()

        if not url:
            raise ValueError("original_url må ikke være tom.")
        if not task_id:
            raise ValueError("task_id må ikke være tom.")

        if re.search(r"(?:[?&])opgaveId=", url, flags=re.IGNORECASE):
            return url

        encoded_task_id = quote(task_id, safe="")

        if url.casefold().endswith("%27"):
            return url[:-3] + "cb&opgaveId=" + encoded_task_id

        return url + "cb&opgaveId=" + encoded_task_id

    @staticmethod
    def _package_pattern(package_name: str) -> re.Pattern[str]:
        """Lav regex for pakken efterfulgt af et dynamisk antal."""

        escaped = re.escape(OpgaveindbakkeClient._normalise(package_name))
        escaped = escaped.replace(r"\ ", r"\s+")
        return re.compile(
            rf"^\s*{escaped}\s*\(\s*\d+\s*\)\s*$",
            re.IGNORECASE,
        )

    @staticmethod
    def _extract_count(value: str) -> int | None:
        """Hent det afsluttende antal, fx ``(12)`` til ``12``."""

        match = re.search(r"\(\s*(\d+)\s*\)\s*$", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _normalise(value: str) -> str:
        """Saml mellemrum og linjeskift."""

        return re.sub(r"\s+", " ", value).strip()
