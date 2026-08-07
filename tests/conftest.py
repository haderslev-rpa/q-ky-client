"""Faelles pytest-fixtures til KY-klientens integrationstests.

Credentials hentes ikke fra .env eller miljoevariabler.

Testene sender kun navnet paa credential-posten til ``launch_ky()``.
``login_via_faelles_kommunal_idp()`` og ``BrowserSession`` foretager derefter
selve credential-opslaget i Automation Server.

Standard credential-post:
    DIRXFLX

Credential-navnet kan overskrives ved koersel:
    pytest -s -vv --ky-credential-name DIRXFLX

Et test-CPR kan angives uden miljoevariabler:
    pytest -s -vv --test-cpr 0101011234
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from q_haderslev_vbo.playwright.browser_session import BrowserSession


DEFAULT_KY_CREDENTIAL_NAME = "DIRXFLX"


# ---------------------------------------------------------------------------
# Pytest-kommandolinjeparametre
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Tilfoej KY-parametre til pytest uden anvendelse af .env."""

    group = parser.getgroup(
        "ky",
        "Indstillinger til KY-integrationstests",
    )

    group.addoption(
        "--ky-credential-name",
        action="store",
        default=DEFAULT_KY_CREDENTIAL_NAME,
        help=(
            "Navnet paa credential-posten i Automation Server. "
            f"Standard: {DEFAULT_KY_CREDENTIAL_NAME}"
        ),
    )

    group.addoption(
        "--test-cpr",
        action="store",
        default=None,
        help=(
            "Valgfrit CPR-nummer til tests, som kraever et borgeropslag. "
            "CPR skal bestaa af praecis 10 cifre."
        ),
    )

    group.addoption(
        "--ky-headless",
        action="store_true",
        default=False,
        help="Start browseren headless.",
    )

    group.addoption(
        "--ky-debug",
        dest="ky_debug",
        action="store_true",
        default=True,
        help="Aktiver BrowserSession-debug (standard).",
    )

    group.addoption(
        "--no-ky-debug",
        dest="ky_debug",
        action="store_false",
        help="Deaktiver BrowserSession-debug.",
    )

    group.addoption(
        "--ky-video",
        action="store_true",
        default=False,
        help="Aktiver videooptagelse i BrowserSession.",
    )


# ---------------------------------------------------------------------------
# Async backend
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Koer asynkrone tests med asyncio."""

    return "asyncio"


# ---------------------------------------------------------------------------
# Credential-navn til Automation Server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ky_credential_name(
    pytestconfig: pytest.Config,
) -> str:
    """Returner navnet paa credential-posten i Automation Server.

    Fixturen returnerer kun postens navn. Brugernavn, adgangskode, API-noegle,
    customer-id og IDP-guid hentes ikke her og ligger ikke i .env.
    """

    value = str(
        pytestconfig.getoption("--ky-credential-name")
        or ""
    ).strip()

    if not value:
        pytest.fail(
            "--ky-credential-name maa ikke vaere tom.",
            pytrace=False,
        )

    return value


@pytest.fixture(scope="session")
def ky_user(
    ky_credential_name: str,
) -> str:
    """Bagudkompatibelt alias til eksisterende tests.

    ``ky_user`` er nu navnet paa credential-posten i Automation Server.
    Fixturen indeholder ikke et faktisk brugernavn.
    """

    return ky_credential_name


# ---------------------------------------------------------------------------
# Valgfrit test-CPR
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_cpr(
    pytestconfig: pytest.Config,
) -> str:
    """Returner og valider ``--test-cpr`` for tests med borgeropslag."""

    raw_value = pytestconfig.getoption("--test-cpr")

    if raw_value is None:
        pytest.fail(
            "Testen kraever et CPR-nummer. "
            "Angiv det med --test-cpr 0101011234.",
            pytrace=False,
        )

    cpr = str(raw_value).replace("-", "").replace(" ", "").strip()

    if not cpr.isdigit() or len(cpr) != 10:
        pytest.fail(
            "--test-cpr skal bestaa af praecis 10 cifre.",
            pytrace=False,
        )

    return cpr


# ---------------------------------------------------------------------------
# BrowserSession-konfiguration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ky_headless(pytestconfig: pytest.Config) -> bool:
    """Returner den valgte headless-indstilling."""

    return bool(pytestconfig.getoption("--ky-headless"))


@pytest.fixture(scope="session")
def ky_debug(pytestconfig: pytest.Config) -> bool:
    """Returner den valgte debug-indstilling."""

    return bool(pytestconfig.getoption("ky_debug"))


@pytest.fixture(scope="session")
def ky_video(pytestconfig: pytest.Config) -> bool:
    """Returner den valgte videoindstilling."""

    return bool(pytestconfig.getoption("--ky-video"))


@pytest.fixture
async def automation_session(
    ky_headless: bool,
    ky_debug: bool,
    ky_video: bool,
) -> AsyncIterator[BrowserSession]:
    """Opret og ryd op i en rigtig BrowserSession.

    BrowserSession bruges direkte af ``launch_ky()``. Credentials til IDP-login
    hentes af Automation Server-flowet ud fra ``credential_name``; fixturen
    opretter derfor ingen lokal credential-dictionary.

    Fixturen starter sessionen, men opretter ikke automatisk en Page. Testen kan
    oprette en side med::

        page = await automation_session.new_page()

    Sessionen lukkes automatisk efter testen.
    """

    session = BrowserSession(
        headless=ky_headless,
        debug=ky_debug,
        video=ky_video,
    )

    await session.start()

    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
async def ky_page(
    automation_session: BrowserSession,
) -> AsyncIterator[object]:
    """Opret en Page i den aktive BrowserSession.

    Fixturen logger ikke ind i KY. Brug ``launch_ky()`` i testen med
    ``ky_credential_name`` eller ``ky_user``.
    """

    page = await automation_session.new_page()

    recorder = getattr(
        automation_session,
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

    yield page
