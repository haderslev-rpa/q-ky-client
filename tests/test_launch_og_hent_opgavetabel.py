"""Integrationstest: launch KY og hent opgavetabellen.

Testen:
1. Bruger BrowserSession fra tests/conftest.py.
2. Bruger credential-navnet fra pytest-fixturen ``ky_credential_name``.
3. Henter de faktiske credentials gennem Automation Server-loginflowet.
4. Launcher KY på den eksisterende Playwright-side.
5. Opretter OpgaveindbakkeClient med samme side.
6. Henter opgavetabellen med rettede URL'er.
7. Printer resultatet som list[dict].
8. Navigerer ikke til opgave-URL'erne.
9. Holder browseren åben, indtil testen afbrydes med Ctrl+C.

Filen bruger ikke ``load_dotenv()`` og henter ikke credentials fra
miljøvariabler.

Kør med standard credential-post og standard opgavepakke:

    pytest tests/test_launch_og_hent_opgavetabel.py -s -vv

Vælg en anden credential-post i Automation Server:

    pytest tests/test_launch_og_hent_opgavetabel.py -s -vv \
        --ky-credential-name DIRXFLX
"""

from __future__ import annotations

import asyncio
from pprint import pprint

import pytest
from playwright.async_api import Page

from q_haderslev_vbo.playwright.browser_session import BrowserSession

from ky_client.functionality.launch import (
    has_jsessionid,
    is_ky_error_url,
    is_ky_url,
    launch_ky,
)
from ky_client.functionality.opgaveindbakke import (
    DEFAULT_OPGAVEPAKKE,
    OpgaveindbakkeClient,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
]

PAGE_TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT_MS = 120_000


async def test_launch_og_hent_opgavetabel(
    automation_session: BrowserSession,
    ky_page: Page,
    ky_credential_name: str,
) -> None:
    """Launch KY og hent opgavetabellen på samme session og side."""

    session = automation_session
    page = ky_page
    opgavepakke = DEFAULT_OPGAVEPAKKE

    page.set_default_timeout(PAGE_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

    recorder = getattr(session, "recorder", None)
    set_page = getattr(recorder, "set_page", None)

    if callable(set_page):
        set_page(page)

    # ----------------------------------------------------------
    # TRIN 1: Launch KY
    # ----------------------------------------------------------

    print()
    print("=" * 70)
    print("TRIN 1: LAUNCHER KY")
    print("=" * 70)
    print(
        "Credential-post i Automation Server: "
        f"{ky_credential_name}"
    )

    await launch_ky(
        page=page,
        session=session,
        credential_name=ky_credential_name,
    )

    assert not page.is_closed(), (
        "Playwright-siden blev lukket under KY-launch."
    )

    assert not is_ky_error_url(page), (
        "KY viste fejlsiden efter launch. "
        f"Aktuel URL: {page.url}"
    )

    assert is_ky_url(page), (
        "Siden er ikke en gyldig KY-side efter launch. "
        f"Aktuel URL: {page.url}"
    )

    assert await has_jsessionid(page), (
        "KY blev åbnet, men JSESSIONID blev ikke fundet."
    )

    print("KY-launch blev gennemført.")
    print("JSESSIONID blev fundet.")
    print(f"Aktuel URL: {page.url}")

    # ----------------------------------------------------------
    # TRIN 2: Hent opgavetabellen via produktionsklienten
    # ----------------------------------------------------------

    print()
    print("=" * 70)
    print("TRIN 2: HENTER OPGAVETABEL")
    print("=" * 70)
    print(f"Opgavepakke: {opgavepakke}")

    opgaveindbakke = OpgaveindbakkeClient(
        page=page,
    )

    opgaver = await opgaveindbakke.hent_opgaver(
        opgavepakke=opgavepakke,
    )

    # ----------------------------------------------------------
    # TRIN 3: Kontrollér outputtet
    # ----------------------------------------------------------

    assert isinstance(opgaver, list), (
        "Tabeludtrækket returnerede ikke en liste."
    )

    assert not page.is_closed(), (
        "Playwright-siden blev lukket under tabeludtrækket."
    )

    for index, opgave in enumerate(opgaver, start=1):
        assert isinstance(opgave, dict), (
            f"Opgave nummer {index} er ikke en dictionary."
        )

        assert "Renset URL" not in opgave, (
            "Det interne felt 'Renset URL' må ikke være i outputtet."
        )

        assert "Alle URL'er" not in opgave, (
            "Det interne felt 'Alle URL\'er' må ikke være i outputtet."
        )

        assert "_Alle URLer" not in opgave, (
            "Det interne URL-hjælpefelt må ikke være i outputtet."
        )

    # ----------------------------------------------------------
    # TRIN 4: Print hele outputtet
    # ----------------------------------------------------------

    print()
    print("=" * 70)
    print("SAMLET OUTPUT SOM LIST[DICT]")
    print("=" * 70)

    pprint(
        opgaver,
        sort_dicts=False,
        width=240,
    )

    print()
    print(f"Antal opgaver: {len(opgaver)}")

    # ----------------------------------------------------------
    # TRIN 5: Print kun Opgave-Id og URL-felter
    # ----------------------------------------------------------

    print()
    print("=" * 70)
    print("OPGAVE-ID OG URL'ER")
    print("=" * 70)

    opgave_links = [
        {
            "Opgave-Id": opgave.get("Opgave-Id"),
            "Original URL": opgave.get("Original URL"),
            "URL": opgave.get("URL"),
        }
        for opgave in opgaver
    ]

    pprint(
        opgave_links,
        sort_dicts=False,
        width=240,
    )

    print()
    print("=" * 70)
    print("SAMLET TEST GENNEMFØRT")
    print("=" * 70)
    print(f"Aktuel KY-URL: {page.url}")
    print("Der navigeres ikke til opgave-URL'erne.")
    print("Browseren holdes åben.")
    print("Tryk Ctrl+C i terminalen for at afslutte.")

    # BrowserSession ejes af conftest-fixturen og lukkes derfor ikke her.
    # Når testen afbrydes, rydder automation_session-fixturen sessionen op.
    await asyncio.Event().wait()
