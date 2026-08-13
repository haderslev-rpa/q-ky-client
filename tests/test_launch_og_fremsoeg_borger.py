from __future__ import annotations

import pytest

from playwright.async_api import Page

from q_haderslev_vbo.playwright.browser_session import (
    BrowserSession,
)

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)

from ky_client.functionality.borgere import (
    naviger_til_borger_async,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

ACTION_TIMEOUT_MS = 30_000
BORGER_TIMEOUT_MS = 120_000

PERSON_FANER = (
    "li.tab.topmenu-tab"
    "[data-tab-target-id='PERSON']"
)

PERSON_LUKKNAPPER = (
    "li.tab.topmenu-tab"
    "[data-tab-target-id='PERSON'] "
    "i.navigation-close-tab"
    "[data-entity-type='PERSON']"
)


async def _hent_person_entity_ids(
    page: Page,
) -> list[str]:
    """
    Returnér alle åbne PERSON-faners
    data-entity-id.
    """

    ids: list[str] = []

    lukkeknapper = page.locator(
        PERSON
    )

    antal = await lukkeknapper.count()

    for indeks in range(antal):

        entity_id = await (
            lukkeknapper
            .nth(indeks)
            .get_attribute(
                "data-entity-id"
            )
        )

        if entity_id:
            ids.append(entity_id)

    return ids


async def _luk_person_fane(
    page: Page,
    entity_id: str,
) -> None:

    selector = (
        f"{PERSON_LUKKNAPPER}"
        f'[data-entity-id="{entity_id}"]'
    )

    lukknap = (
        page.locator(selector)
        .first
    )

    if await lukknap.count() == 0:

        print(
            f"PERSON-fane findes "
            f"ikke længere: {entity_id}"
        )

        return

    print(
        f"Lukker PERSON-fane: "
        f"{entity_id}"
    )

    await lukknap.click()

    await page.wait_for_timeout(
        1000
    )


async def _luk_alle_person_faner(
    page: Page,
    maks_forsoeg: int = 3,
) -> None:

    for forsoeg in range(
        1,
        maks_forsoeg + 1,
    ):

        entity_ids = (
            await _hent_person_entity_ids(
                page
            )
        )

        print()
        print("=" * 70)
        print(
            f"FORSØG {forsoeg}"
        )
        print(
            f"PERSON-faner fundet: "
            f"{len(entity_ids)}"
        )
        print("=" * 70)

        if not entity_ids:

            print(
                "Ingen PERSON-faner fundet."
            )

            return

        for entity_id in entity_ids:

            try:

                await _luk_person_fane(
                    page,
                    entity_id,
                )

            except Exception as exc:

                print(
                    f"Kunne ikke lukke "
                    f"{entity_id}: {exc}"
                )

        await page.wait_for_timeout(
            2000
        )

    resterende = (
        await _hent_person_entity_ids(
            page
        )
    )

    assert not resterende, (
        "Der findes stadig åbne "
        "PERSON-faner: "
        f"{resterende}"
    )


async def test_launch_fremsoeg_to_borgere_og_luk_faner(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
    test_cpr: str,
) -> None:

    page = ky_page
    session = automation_session

    page.set_default_timeout(
        ACTION_TIMEOUT_MS
    )

    page.set_default_navigation_timeout(
        BORGER_TIMEOUT_MS
    )

    recorder = getattr(
        session,
        "recorder",
        None,
    )

    set_page = getattr(
        recorder,
        "set_page",
        None,
    )

    if callable(set_page):
        set_page(page)

    print()
    print("=" * 70)
    print("TRIN 1: LAUNCHER KY")
    print("=" * 70)

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    assert not page.is_closed()

    assert not is_ky_error_url(
        page
    )

    assert is_ky_url(
        page
    )

    assert await has_jsessionid(
        page
    )

    print("KY ER KLAR")
    print(page.url)



    print()
    print("=" * 70)
    print(f"FREMSØGER BORGER {TEST_CPR_1}")
    print("=" * 70)

    await naviger_til_borger_async(
        page=page,
        cpr=TEST_CPR_1,
    )

    print()
    print("=" * 70)
    print(f"FREMSØGER BORGER {TEST_CPR_2}")
    print("=" * 70)

    await naviger_til_borger_async(
        page=page,
        cpr=TEST_CPR_2,
    )
    print(
        "Borger fremsøgt."
    )
    print(page.url)

    #
    # BREAKPOINT HER
    #

    print()
    print("=" * 70)
    print("TRIN 3: LÆSER PERSON-FANER")
    print("=" * 70)

    person_ids = (
        await _hent_person_entity_ids(
            page
        )
    )

    print(
        f"Fundet {len(person_ids)} "
        "PERSON-faner"
    )

    for entity_id in person_ids:

        print(
            f"  {entity_id}"
        )

    print()
    print("=" * 70)
    print("TRIN 4: LUKKER PERSON-FANER")
    print("=" * 70)

    await _luk_alle_person_faner(
        page
    )

    print()
    print("=" * 70)
    print("TRIN 5: VERIFICERER")
    print("=" * 70)

    resterende = (
        await _hent_person_entity_ids(
            page
        )
    )

    assert len(resterende) == 0

    print(
        "Alle PERSON-faner er lukket."
    )

    await session.screenshot(
        page=page,
        name="person_faner_lukket",
        always=True,
    )