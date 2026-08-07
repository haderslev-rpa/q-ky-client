"""Fælles hjælpefunktioner til KY-klienten."""

from __future__ import annotations

import logging

from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    Frame,
    Locator,
    Page,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from ky_client.selectors import KYSelectors

logger = logging.getLogger(__name__)


def _is_placeholder_header_row(
    values: list[str],
) -> bool:
    """Return True when a row contains an empty-state placeholder."""

    if len(values) != 1:
        return False

    value = " ".join(values[0].lower().split())

    exact_placeholders = {
        "no items found",
        "ingen elementer fundet",
        "ingen resultater fundet",
        "ingen fundet",
    }

    if value in exact_placeholders:
        return True

    has_not_found_da = "ingen" in value and "fundet" in value

    has_result_or_items_da = "resultat" in value or "element" in value

    has_not_found_en = "found" in value and ("no" in value or "none" in value)

    return (has_not_found_da and has_result_or_items_da) or has_not_found_en


def extract_keyed_table(
    page: Page,
    table_selector: str,
) -> dict[str, str]:
    """Extract a two-column label/value table into a dict."""

    return page.evaluate(
        f"""
        () => {{
            const rows = document.querySelectorAll(
                '{table_selector} tbody tr'
            );

            const result = {{}};

            rows.forEach(row => {{
                const cells = row.querySelectorAll(
                    'td:not(.handlinger)'
                );

                if (cells.length >= 2) {{
                    result[cells[0].innerText.trim()] =
                        cells[1].innerText.trim();
                }}
            }});

            return result;
        }}
        """
    )


def extract_header_table(
    page: Page,
    table_selector: str,
) -> list[dict[str, str]]:
    """Extract a header-based table into a list of dicts."""

    rows: list[dict[str, str]] = page.evaluate(
        f"""
        () => {{
            const table = document.querySelector(
                '{table_selector}'
            );

            if (!table) {{
                return [];
            }}

            const isVisibleRow = row => {{
                if (
                    !row
                    || row.nodeType !== Node.ELEMENT_NODE
                ) {{
                    return false;
                }}

                const style = window.getComputedStyle(row);

                if (
                    style.display === 'none'
                    || style.visibility === 'hidden'
                ) {{
                    return false;
                }}

                return row.offsetParent !== null;
            }};

            const headers = Array.from(
                table.querySelectorAll('thead th')
            ).map(th => {{
                for (
                    const span
                    of th.querySelectorAll('span[data-textkey]')
                ) {{
                    if (!span.closest('ul')) {{
                        return span.innerText.trim();
                    }}
                }}

                return null;
            }});

            return Array.from(
                table.querySelectorAll('tbody tr')
            )
                .filter(isVisibleRow)
                .map(row => {{
                    const cells = row.querySelectorAll(
                        'td:not(.handlinger)'
                    );

                    const obj = {{}};

                    cells.forEach((cell, index) => {{
                        if (headers[index]) {{
                            obj[headers[index]] =
                                cell.innerText.trim();
                        }}
                    }});

                    return obj;
                }});
        }}
        """
    )

    filtered_rows: list[dict[str, str]] = []

    for row in rows:
        values = [str(value).strip() for value in row.values() if str(value).strip()]

        if not values:
            logger.debug(
                "Skipping empty header row in table '%s': %s",
                table_selector,
                row,
            )
            continue

        if _is_placeholder_header_row(values):
            logger.debug(
                "Skipping placeholder header row in table '%s': %s",
                table_selector,
                row,
            )
            continue

        filtered_rows.append(row)

    return filtered_rows


def extract_datatable_all_pages(
    page: Page,
    table_id: str,
) -> list[dict[str, str]]:
    """Extract all DataTable rows across all available pages."""

    table_selector = f"table#{table_id}"
    all_rows: list[dict[str, str]] = []

    def _wait_ready(
        timeout_ms: int = 5_000,
    ) -> None:
        try:
            page.wait_for_selector(
                f"{table_selector} tbody",
                timeout=timeout_ms,
            )

        except PlaywrightError as error:
            if page.is_closed():
                raise RuntimeError(
                    f"Page was closed while waiting for table '{table_id}'."
                ) from error

            raise

        try:
            page.wait_for_function(
                """
                id => {
                    const processing = document.querySelector(
                        `#${id}_processing`
                    );

                    if (!processing) {
                        return true;
                    }

                    const style = window.getComputedStyle(
                        processing
                    );

                    return (
                        style.display === 'none'
                        || style.visibility === 'hidden'
                    );
                }
                """,
                arg=table_id,
                timeout=timeout_ms,
            )

        except PlaywrightError as error:
            if page.is_closed():
                raise RuntimeError(
                    f"Page was closed while waiting for table '{table_id}' to be ready."
                ) from error

            logger.warning(
                "Table '%s' processing check timed out. Continuing anyway.",
                table_id,
            )

    _wait_ready()

    def _read_page_state() -> dict:
        return page.evaluate(
            """
            id => {
                const table = document.querySelector(
                    `table#${id}`
                );

                if (!table) {
                    return {
                        rows: [],
                        page: 0,
                        nextDisabled: true,
                        totalCount: 0
                    };
                }

                const headers = Array.from(
                    table.querySelectorAll('thead th')
                ).map(th => {
                    const span = th.querySelector(
                        'span[data-textkey]'
                    );

                    const text = span
                        ? span.innerText.trim()
                        : th.innerText.trim();

                    return text || null;
                });

                const rowElements = Array.from(
                    table.querySelectorAll(
                        'tbody tr.table-row'
                    )
                ).filter(
                    row => !row.querySelector(
                        'td.dataTables_empty'
                    )
                );

                const rows = rowElements.map(row => {
                    const cells = Array.from(
                        row.querySelectorAll('td')
                    ).map(
                        td => td.innerText.trim()
                    );

                    const obj = {};

                    cells.forEach((cell, index) => {
                        if (headers[index]) {
                            obj[headers[index]] = cell;
                        }
                    });

                    obj['data-opgaveid'] =
                        row.getAttribute('data-opgaveid')
                        || 'N/A';

                    return obj;
                });

                const page = Number(
                    table.getAttribute('data-page')
                    || '0'
                );

                const totalCount = Number(
                    table.getAttribute('data-total-count')
                    || '0'
                );

                const next =
                    document.querySelector(`#${id}_next`)
                    || document.querySelector(
                        `#${id}_paginate `
                        + `a.paginate_button.next`
                    );

                const nextDisabled =
                    !next
                    || next.classList.contains('disabled')
                    || next.getAttribute(
                        'aria-disabled'
                    ) === 'true';

                return {
                    rows,
                    page,
                    nextDisabled,
                    totalCount
                };
            }
            """,
            arg=table_id,
        )

    table_meta = page.evaluate(
        """
        id => {
            const table = document.querySelector(
                `table#${id}`
            );

            if (!table) {
                return {
                    totalCount: 0,
                    pageSize: 10
                };
            }

            return {
                totalCount: Number(
                    table.getAttribute(
                        'data-total-count'
                    ) || '0'
                ),
                pageSize: Math.max(
                    1,
                    Number(
                        table.getAttribute(
                            'data-page-size'
                        ) || '10'
                    )
                )
            };
        }
        """,
        arg=table_id,
    )

    total_count = int(table_meta["totalCount"])

    page_size = int(table_meta["pageSize"])

    expected_pages = max(
        1,
        (total_count + page_size - 1) // page_size,
    )

    for page_index in range(expected_pages):
        state = _read_page_state()

        if page_index == 0 and not state["rows"] and int(state["totalCount"]) > 0:
            _wait_ready(
                timeout_ms=3_000,
            )

            state = _read_page_state()

        page_rows = [
            row
            for row in state["rows"]
            if not _is_placeholder_header_row(
                [str(value).strip() for value in row.values() if str(value).strip()]
            )
        ]

        all_rows.extend(page_rows)

        if page_index >= expected_pages - 1 or state["nextDisabled"]:
            break

        next_button = page.locator(f"#{table_id}_next")

        if next_button.count() == 0:
            next_button = page.locator(f"#{table_id}_paginate a.paginate_button.next")

        if next_button.count() == 0:
            logger.debug(
                "No next button found for table '%s'.",
                table_id,
            )
            break

        next_class = next_button.get_attribute("class") or ""

        next_aria_disabled = (next_button.get_attribute("aria-disabled") or "").lower()

        if "disabled" in next_class or next_aria_disabled == "true":
            break

        try:
            next_button.click(
                timeout=5_000,
            )

        except PlaywrightTimeoutError:
            logger.debug(
                "Next button click timed out for table '%s'. Stopping pagination.",
                table_id,
            )
            break

        try:
            page.wait_for_function(
                """
                ({ id, previous }) => {
                    const table = document.querySelector(
                        `table#${id}`
                    );

                    if (!table) {
                        return false;
                    }

                    return Number(
                        table.getAttribute(
                            'data-page'
                        ) || '0'
                    ) !== previous;
                }
                """,
                arg={
                    "id": table_id,
                    "previous": int(state["page"]),
                },
                timeout=4_000,
            )

        except PlaywrightTimeoutError:
            logger.debug(
                "Timed out waiting for data-page change in table '%s'.",
                table_id,
            )

        _wait_ready(
            timeout_ms=3_000,
        )

    logger.debug(
        "Extracted %d total rows from table '%s'.",
        len(all_rows),
        table_id,
    )

    return all_rows


def navigate_to(
    page: Page,
    nav_selector: str,
    wait_for_selector: str,
    timeout: int = 30_000,
) -> None:
    """Click a navigation tab and wait for its content."""

    page.click(
        nav_selector,
    )

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=timeout,
        )

    except PlaywrightError:
        logger.debug("Page did not reach networkidle state. Continuing anyway.")

    try:
        page.wait_for_selector(
            wait_for_selector,
            timeout=timeout,
        )

    except PlaywrightError as error:
        if page.is_closed():
            raise RuntimeError(
                f"Page was closed while waiting for '{wait_for_selector}'."
            ) from error

        raise


def _find_visible_editable_input(
    frame: Frame,
    selector: str,
) -> Locator | None:
    """Find a visible, enabled and editable input in a frame."""

    try:
        candidates = frame.locator(
            selector,
        )

        count = candidates.count()

    except PlaywrightError:
        return None

    for index in range(count):
        candidate = candidates.nth(index)

        try:
            if not candidate.is_visible():
                continue

            if not candidate.is_enabled():
                continue

            if not candidate.is_editable():
                continue

            return candidate

        except PlaywrightError:
            continue

    return None


def _find_topsearch(
    page: Page,
) -> Locator:
    """Find KY's active topsearch field, including inside iframes."""

    selectors = [
        KYSelectors.Main.TOP_SEARCH,
        "input#topsearch",
        "input#topSearch",
        "input[name='topsearch']",
        "input[name='topSearch']",
        "input[id*='topsearch' i]",
        "input[name*='topsearch' i]",
        "input[class*='topsearch' i]",
        "input[placeholder*='CPR' i]",
        "input[placeholder*='cpr' i]",
        "input[placeholder*='Søg' i]",
        "input[placeholder*='søg' i]",
        "input[type='search']",
    ]

    logger.debug(
        "Searching for topsearch in %d frame(s).",
        len(page.frames),
    )

    for frame in page.frames:
        for selector in selectors:
            candidate = _find_visible_editable_input(
                frame,
                selector,
            )

            if candidate is None:
                continue

            logger.info(
                "Topsearch found with selector '%s' inside frame '%s'.",
                selector,
                frame.url,
            )

            return candidate

    diagnostic_inputs: list[dict] = []

    for frame in page.frames:
        try:
            inputs = frame.locator("input")

            for index in range(inputs.count()):
                input_element = inputs.nth(index)

                try:
                    information = input_element.evaluate(
                        """
                        element => ({
                            id: element.id || null,
                            name: element.name || null,
                            type: element.type || null,
                            placeholder:
                                element.placeholder || null,
                            className:
                                element.className || null,
                            visible:
                                element.offsetWidth > 0
                                && element.offsetHeight > 0
                        })
                        """
                    )

                    information["frame_url"] = frame.url
                    diagnostic_inputs.append(information)

                except PlaywrightError:
                    continue

        except PlaywrightError:
            continue

    logger.error(
        "Topsearch was not found. Available inputs: %s",
        diagnostic_inputs,
    )

    raise RuntimeError(
        "Kunne ikke finde et synligt og redigerbart "
        "topsearch-felt i KY. "
        "Se loggen for en liste over sidens inputfelter."
    )


def _focus_topsearch(
    page: Page,
    search_input: Locator,
    timeout: int,
) -> None:
    """Click and focus the topsearch input."""

    search_input.wait_for(
        state="visible",
        timeout=timeout,
    )

    search_input.scroll_into_view_if_needed()

    try:
        search_input.click(
            timeout=timeout,
        )

    except PlaywrightError:
        logger.debug("Normal click on topsearch failed. Trying a forced click.")

        search_input.click(
            force=True,
            timeout=timeout,
        )

    page.wait_for_timeout(
        250,
    )

    try:
        has_focus = search_input.evaluate(
            """
            element => document.activeElement === element
            """
        )

    except PlaywrightError:
        has_focus = False

    if not has_focus:
        search_input.focus()

        page.wait_for_timeout(
            100,
        )

        has_focus = search_input.evaluate(
            """
            element => document.activeElement === element
            """
        )

    if not has_focus:
        raise RuntimeError("Topsearch blev fundet, men feltet kunne ikke få fokus.")


def _insert_cpr_with_keyboard(
    page: Page,
    search_input: Locator,
    cpr: str,
) -> bool:
    """Insert CPR using the browser's text-input mechanism."""

    page.keyboard.press(
        "Control+A",
    )

    page.keyboard.press(
        "Backspace",
    )

    page.keyboard.insert_text(
        cpr,
    )

    page.wait_for_timeout(
        300,
    )

    actual_value = search_input.input_value()

    logger.debug(
        "Topsearch value after keyboard insertion: %r",
        actual_value,
    )

    return actual_value == cpr


def _insert_cpr_with_native_setter(
    search_input: Locator,
    cpr: str,
) -> bool:
    """
    Insert CPR using the input element's native value setter.

    Input, change and key events are dispatched so KY's
    JavaScript receives the change.
    """

    search_input.evaluate(
        """
        (element, value) => {
            element.focus();

            const prototype =
                window.HTMLInputElement.prototype;

            const descriptor =
                Object.getOwnPropertyDescriptor(
                    prototype,
                    'value'
                );

            if (descriptor && descriptor.set) {
                descriptor.set.call(
                    element,
                    value
                );
            } else {
                element.value = value;
            }

            element.dispatchEvent(
                new InputEvent(
                    'input',
                    {
                        bubbles: true,
                        composed: true,
                        inputType: 'insertText',
                        data: value
                    }
                )
            );

            element.dispatchEvent(
                new Event(
                    'change',
                    {
                        bubbles: true,
                        composed: true
                    }
                )
            );
        }
        """,
        cpr,
    )

    actual_value = search_input.input_value()

    logger.debug(
        "Topsearch value after native insertion: %r",
        actual_value,
    )

    return actual_value == cpr


def _insert_cpr(
    page: Page,
    search_input: Locator,
    cpr: str,
) -> None:
    """Insert CPR using keyboard input with a native fallback."""

    if _insert_cpr_with_keyboard(
        page,
        search_input,
        cpr,
    ):
        return

    logger.warning(
        "keyboard.insert_text() did not update topsearch. "
        "Trying the native input setter."
    )

    if _insert_cpr_with_native_setter(
        search_input,
        cpr,
    ):
        return

    actual_value = search_input.input_value()

    raise RuntimeError(
        "CPR-nummeret kunne ikke indsættes i topsearch. "
        f"Forventede {cpr!r}, men feltet indeholder "
        f"{actual_value!r}."
    )


def _wait_for_person_oplysninger(
    page: Page,
    timeout: int,
) -> bool:
    """Wait for the citizen overview in the page or an iframe."""

    interval_ms = 250
    attempts = max(
        1,
        timeout // interval_ms,
    )

    for _ in range(attempts):
        if page.is_closed():
            raise RuntimeError(
                "Browser page was closed while waiting for the citizen overview."
            )

        for frame in page.frames:
            try:
                person_oplysninger = frame.locator(
                    KYSelectors.Borgere.PERSON_OPLYSNINGER
                )

                if (
                    person_oplysninger.count() > 0
                    and person_oplysninger.first.is_visible()
                ):
                    return True

            except PlaywrightError:
                continue

        page.wait_for_timeout(
            interval_ms,
        )

    return False


def _press_enter(
    page: Page,
    search_input: Locator,
) -> None:
    """Press Enter while topsearch has focus."""

    search_input.focus()

    try:
        search_input.press(
            "Enter",
        )

    except PlaywrightError:
        logger.debug(
            "locator.press('Enter') failed. Using page.keyboard.press('Enter')."
        )

        page.keyboard.press(
            "Enter",
        )


def naviger_til_borger(
    page: Page,
    cpr: str,
    timeout: int = 30_000,
) -> None:
    """
    Search for a citizen by CPR and wait for the overview.

    The function uses the Page instance already owned by
    KYClientManager. It does not create another browser session.
    """

    cpr = cpr.strip()

    if not cpr:
        raise ValueError("CPR-nummeret må ikke være tomt.")

    if not cpr.isdigit():
        raise ValueError("CPR-nummeret må kun indeholde cifre.")

    if len(cpr) != 10:
        raise ValueError("CPR-nummeret skal bestå af præcis 10 cifre.")

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før borgeropslaget.")

    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=timeout,
        )

    except PlaywrightError:
        logger.debug(
            "KY did not reach domcontentloaded before "
            "the CPR search. Continuing anyway."
        )

    # Close an open dropdown, modal or overlay.
    page.keyboard.press(
        "Escape",
    )

    page.wait_for_timeout(
        500,
    )

    search_input = _find_topsearch(
        page,
    )

    _focus_topsearch(
        page,
        search_input,
        timeout,
    )

    _insert_cpr(
        page,
        search_input,
        cpr,
    )

    logger.info("CPR value was inserted into topsearch.")

    old_url = page.url

    _press_enter(
        page,
        search_input,
    )

    logger.info(
        "Enter was pressed in topsearch. URL before search: %s",
        old_url,
    )

    if _wait_for_person_oplysninger(
        page,
        timeout,
    ):
        logger.info("Citizen overview loaded successfully.")
        return

    logger.warning(
        "The citizen overview did not load after the first Enter. Retrying the search."
    )

    # The first Enter may only select an autocomplete result.
    _focus_topsearch(
        page,
        search_input,
        timeout,
    )

    current_value = search_input.input_value()

    if current_value != cpr:
        _insert_cpr(
            page,
            search_input,
            cpr,
        )

    # Try keyboard-level Enter on the second attempt.
    page.keyboard.press(
        "Enter",
    )

    if _wait_for_person_oplysninger(
        page,
        timeout,
    ):
        logger.info("Citizen overview loaded after the retry.")
        return

    raise PlaywrightTimeoutError(
        "CPR-opslaget blev udført, men KY viste ikke "
        "borgerens personoplysninger inden for "
        f"{timeout / 1000:.0f} sekunder. "
        f"CPR: {cpr}. "
        f"URL før opslag: {old_url}. "
        f"Nuværende URL: {page.url}."
    )
