"""Hent KY-opgavetabellen fra en allerede igangvaerende KY-session.

Filen er selvstaendig og importerer ikke hjaelpefunktioner fra andre testfiler.

Funktionen:
- starter ikke BrowserSession,
- kalder ikke launch_ky(),
- opretter ikke en ny Page,
- lukker ikke Page, BrowserContext, browser eller session,
- modtager en eksisterende session og Page,
- vaelger opgavepakken,
- venter paa at den nye tabel er indlaest og stabil,
- laeser alle tabelraekker som list[dict],
- henter Opgave-Id og URL,
- renser den fejludlaeste URL internt,
- bygger en samlet URL med cb&opgaveId=<id>,
- navigerer ikke til URL'erne.
"""

from __future__ import annotations

import os
import re
from pprint import pprint
from typing import Any
from urllib.parse import quote

from playwright.async_api import Frame, Locator, Page

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_url,
)


DEFAULT_OPGAVEPAKKE = "NT - Modtaget Post ( HTF, RESS, REVA )"

DROPDOWN_TIMEOUT_MS = int(
    os.getenv("KY_DROPDOWN_TIMEOUT_MS", "60000")
)
TABLE_TIMEOUT_MS = int(
    os.getenv("KY_DROPDOWN_TABLE_TIMEOUT_MS", "120000")
)
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


async def hent_opgavetabel_fra_igangvaerende_ky(
    session: Any,
    page: Page,
    opgavepakke: str = DEFAULT_OPGAVEPAKKE,
) -> list[dict[str, Any]]:
    """Hent opgavetabellen fra den eksisterende KY-side."""

    assert not page.is_closed(), (
        "Den eksisterende Playwright-side er lukket."
    )
    assert is_ky_url(page), (
        "Den eksisterende side er ikke en gyldig KY-side. "
        f"Aktuel URL: {page.url}"
    )
    assert await has_jsessionid(page), (
        "Den eksisterende KY-side har ingen aktiv JSESSIONID."
    )

    opgavepakke = opgavepakke.strip()
    if not opgavepakke:
        raise ValueError("Navnet paa opgavepakken maa ikke vaere tomt.")

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)
    if callable(set_page):
        set_page(page)

    print()
    print("=" * 70)
    print("BRUGER IGANGVAERENDE KY-SESSION")
    print("=" * 70)
    print(f"Aktuel URL: {page.url}")
    print(f"Opgavepakke: {opgavepakke}")

    old_table_state = await _get_current_table_state(page)

    dropdown_frame, dropdown = await _wait_for_dropdown_ready(
        page=page,
        timeout_ms=DROPDOWN_TIMEOUT_MS,
    )

    await dropdown.scroll_into_view_if_needed()
    await dropdown.click(timeout=DROPDOWN_TIMEOUT_MS)

    await _wait_for_dropdown_open(
        page=page,
        dropdown=dropdown,
        timeout_ms=DROPDOWN_TIMEOUT_MS,
    )

    selected_text = await _select_package(
        page=page,
        preferred_frame=dropdown_frame,
        package_name=opgavepakke,
    )

    print(f"Valgt opgavepakke: {selected_text}")
    print("Venter paa at den nye tabel bliver indlaest...")

    table_frame, _ = await _wait_for_new_table(
        page=page,
        old_table_state=old_table_state,
        selected_package_text=selected_text,
        timeout_ms=TABLE_TIMEOUT_MS,
    )

    table = await _wait_for_table_stable(
        page=page,
        frame=table_frame,
        timeout_ms=TABLE_TIMEOUT_MS,
    )

    opgaver = await _read_table_rows(table)
    opgaver = ret_opgave_urls(opgaver)

    print()
    print("=" * 70)
    print("OPGAVETABEL SOM LIST[DICT]")
    print("=" * 70)
    pprint(opgaver, sort_dicts=False, width=240)

    print()
    print(f"Antal opgaver: {len(opgaver)}")

    print()
    print("=" * 70)
    print("OPGAVE-ID OG RETTEDE URL'ER")
    print("=" * 70)

    pprint(
        [
            {
                "Opgave-Id": opgave.get("Opgave-Id"),
                "Original URL": opgave.get("Original URL"),
                "URL": opgave.get("URL"),
            }
            for opgave in opgaver
        ],
        sort_dicts=False,
        width=240,
    )

    print()
    print("Udtraekket er faerdigt.")
    print("KY-siden og BrowserSession er stadig aabne.")

    return opgaver


async def _wait_for_dropdown_ready(
    page: Page,
    timeout_ms: int,
) -> tuple[Frame, Locator]:
    """Vent til den synlige Bootstrap-dropdown er klar."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        for frame in page.frames:
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

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "Dropdown-knappen blev ikke klar inden for "
        f"{timeout_ms / 1000:.0f} sekunder. URL: {page.url}"
    )


async def _wait_for_dropdown_open(
    page: Page,
    dropdown: Locator,
    timeout_ms: int,
) -> None:
    """Vent til dropdown-menuen er aaben."""

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        try:
            expanded = await dropdown.get_attribute("aria-expanded")
            options = page.locator(DROPDOWN_OPTIONS)

            if expanded == "true":
                for index in range(await options.count()):
                    if await options.nth(index).is_visible():
                        return
        except Exception:
            pass

        await page.wait_for_timeout(100)
        elapsed_ms += 100

    raise AssertionError("Dropdown-menuen blev ikke synlig.")


async def _select_package(
    page: Page,
    preferred_frame: Frame,
    package_name: str,
) -> str:
    """Vaelg pakken, selv om KY tilfoejer et dynamisk antal."""

    escaped = re.escape(_normalise(package_name)).replace(r"\ ", r"\s+")
    pattern = re.compile(
        rf"^\s*{escaped}\s*\(\s*\d+\s*\)\s*$",
        re.IGNORECASE,
    )

    frames = [
        preferred_frame,
        *[frame for frame in page.frames if frame != preferred_frame],
    ]

    elapsed_ms = 0

    while elapsed_ms < DROPDOWN_TIMEOUT_MS:
        for frame in frames:
            try:
                options = frame.locator(DROPDOWN_OPTIONS)

                for index in range(await options.count()):
                    option = options.nth(index)

                    if not await option.is_visible():
                        continue

                    text = _normalise(await option.inner_text())
                    if pattern.fullmatch(text) is None:
                        continue

                    await option.scroll_into_view_if_needed()
                    await option.click(timeout=DROPDOWN_TIMEOUT_MS)
                    return text
            except Exception:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(f"Opgavepakken blev ikke fundet: {package_name}")


async def _get_current_table_state(page: Page) -> str | None:
    """Hent en signatur for tabellen foer dropdown-valget."""

    for frame in page.frames:
        try:
            table = frame.locator(RESULTS_TABLE).first

            if await table.count() == 0:
                continue

            return await _get_table_state(table)
        except Exception:
            continue

    return None


async def _get_table_state(table: Locator) -> str:
    """Hent en stabil tekstsignatur for tabellens raekker."""

    return await table.evaluate(
        """
        table => {
            const normalize = value =>
                (value || '').replace(/\\s+/g, ' ').trim();

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
    page: Page,
    old_table_state: str | None,
    selected_package_text: str,
    timeout_ms: int,
) -> tuple[Frame, Locator]:
    """Vent til dropdown-valget har indlaest den nye tabel."""

    elapsed_ms = 0
    saw_processing = False
    unchanged_grace_ms = 3_000

    package_name = re.sub(
        r"\s*\(\s*\d+\s*\)\s*$",
        "",
        selected_package_text,
    ).strip()

    while elapsed_ms < timeout_ms:
        for frame in page.frames:
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

                if package_name.casefold() not in _normalise(
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

                new_state = await _get_table_state(table)
                changed = (
                    old_table_state is None
                    or new_state != old_table_state
                )

                if changed or saw_processing or elapsed_ms >= unchanged_grace_ms:
                    return frame, table
            except Exception:
                continue

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError(
        "Den nye opgavetabel blev ikke indlaest inden for "
        f"{timeout_ms / 1000:.0f} sekunder. URL: {page.url}"
    )


async def _wait_for_table_stable(
    page: Page,
    frame: Frame,
    timeout_ms: int,
) -> Locator:
    """Vent til tabellen har vaeret uaendret i mindst et sekund."""

    elapsed_ms = 0
    stable_ms = 0
    previous_state: str | None = None

    while elapsed_ms < timeout_ms:
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

        await page.wait_for_timeout(POLL_INTERVAL_MS)
        elapsed_ms += POLL_INTERVAL_MS

    raise AssertionError("Den nye opgavetabel blev ikke stabil.")


async def _read_table_rows(
    table: Locator,
) -> list[dict[str, Any]]:
    """Laes tabellen samlet i browseren, inklusive ID og URL'er."""

    return await table.evaluate(
        """
        table => {
            const normalize = value =>
                (value || '').replace(/\\s+/g, ' ').trim();

            const cleanRawUrl = value => {
                if (!value) return null;

                const cleaned = value
                    .trim()
                    .replace(/^['"]+|['"]+$/g, '')
                    .replace(/\\/'\\//g, '/')
                    .replace(/\\/"\\//g, '/');

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
                table.querySelectorAll('thead th')
            ).map((header, index) =>
                normalize(header.innerText) || `kolonne_${index + 1}`
            );

            return Array.from(table.querySelectorAll('tbody tr'))
                .filter(row => {
                    const style = window.getComputedStyle(row);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && !row.querySelector('td.dataTables_empty')
                        && row.querySelectorAll('td').length > 0;
                })
                .map(row => {
                    const result = {};

                    Array.from(row.querySelectorAll('td')).forEach(
                        (cell, index) => {
                            const key = headers[index]
                                || `kolonne_${index + 1}`;
                            result[key] = normalize(cell.innerText);
                        }
                    );

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

                    const possibleElements = [
                        row,
                        ...row.querySelectorAll(
                            '[data-url], [data-href], [onclick], [data-onclick]'
                        )
                    ];

                    possibleElements.forEach(element => {
                        ['data-url', 'data-href'].forEach(name => {
                            const url = absoluteUrl(element.getAttribute(name));
                            if (url) urls.push(url);
                        });

                        ['onclick', 'data-onclick'].forEach(name => {
                            const script = element.getAttribute(name) || '';
                            const match = script.match(/['"](\\/[^'"]+)['"]/);

                            if (match) {
                                const url = absoluteUrl(match[1]);
                                if (url) urls.push(url);
                            }
                        });
                    });

                    const uniqueUrls = [...new Set(urls)];
                    result['URL'] = uniqueUrls[0] || null;
                    result["Alle URL'er"] = uniqueUrls;

                    return result;
                });
        }
        """
    )


def ret_opgave_urls(
    opgaver: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tilfoej original og samlet URL uden ekstra URL-felter i outputtet."""

    result: list[dict[str, Any]] = []

    for opgave in opgaver:
        rettet_opgave = dict(opgave)

        opgave_id = str(
            rettet_opgave.get("Opgave-Id") or ""
        ).strip()
        original_url = str(
            rettet_opgave.get("URL") or ""
        ).strip()

        # Gem den URL, som blev hentet direkte fra tabelraekken.
        rettet_opgave["Original URL"] = original_url or None

        if original_url:
            # Den rensede URL bruges kun internt og kommer ikke med som felt.
            renset_url = clean_task_url(original_url)
            rettet_opgave["URL"] = (
                build_task_url(renset_url, opgave_id)
                if opgave_id
                else renset_url
            )
        else:
            rettet_opgave["URL"] = None

        # Fjern de felter, som ikke skal med i outputtet.
        rettet_opgave.pop("Renset URL", None)
        rettet_opgave.pop("Alle URL'er", None)

        result.append(rettet_opgave)

    return result

def clean_task_url(url: str) -> str:
    """Fjern fejlagtige apostroffer og URL-kodede anfoerselstegn."""

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


def build_task_url(original_url: str, task_id: str) -> str:
    """Byg URL'en med cb&opgaveId=<id> uden at navigere til den."""

    url = original_url.strip()
    task_id = task_id.strip()

    if not url:
        raise ValueError("original_url maa ikke vaere tom.")
    if not task_id:
        raise ValueError("task_id maa ikke vaere tom.")

    if re.search(r"(?:[?&])opgaveId=", url, flags=re.IGNORECASE):
        return url

    encoded_task_id = quote(task_id, safe="")

    if url.casefold().endswith("%27"):
        return url[:-3] + "cb&opgaveId=" + encoded_task_id

    return url + "cb&opgaveId=" + encoded_task_id


def _normalise(value: str) -> str:
    """Saml mellemrum og linjeskift."""

    return re.sub(r"\s+", " ", value).strip()
