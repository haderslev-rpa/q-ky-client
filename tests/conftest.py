"""Fælles pytest-fixtures til KY-klientens integrationstests.

Credentials hentes ikke fra ``.env``. Testene sender kun navnet på
credential-posten til ``launch_ky()``. ``BrowserSession`` og login-flowet
foretager derefter credential-opslaget i Automation Server.

Test-CPR hentes som standard fra projektets ``.env``::

    TEST_CPR=0101011234

Værdien kan overskrives ved kørsel::

    pytest -s -vv --test-cpr 0101011234

Standard credential-post er ``DIRXFLX`` og kan overskrives med::

    pytest -s -vv --ky-credential-name DIRXFLX
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from dotenv import load_dotenv
from playwright.async_api import Page
from q_haderslev_vbo.playwright.browser_session import BrowserSession

DEFAULT_KY_CREDENTIAL_NAME = "DIRXFLX"
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"

# Indlæs .env én gang, når conftest.py importeres. Eksisterende miljøvariabler
# overskrives ikke; CLI-parameteren får stadig førsteprioritet i test_cpr.
load_dotenv(ENV_FILE, override=False)


# ---------------------------------------------------------------------------
# Pytest-kommandolinjeparametre
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Tilføj fælles KY-parametre til pytest."""

    group = parser.getgroup(
        "ky",
        "Indstillinger til KY-integrationstests",
    )
    group.addoption(
        "--ky-credential-name",
        action="store",
        default=DEFAULT_KY_CREDENTIAL_NAME,
        help=(
            "Navnet på credential-posten i Automation Server. "
            f"Standard: {DEFAULT_KY_CREDENTIAL_NAME}"
        ),
    )
    group.addoption(
        "--test-cpr",
        action="store",
        default=None,
        help=(
            "CPR til integrationstests. Overskriver TEST_CPR fra .env. "
            "CPR skal bestå af præcis 10 cifre."
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
        help="Aktivér BrowserSession-debug (standard).",
    )
    group.addoption(
        "--no-ky-debug",
        dest="ky_debug",
        action="store_false",
        help="Deaktivér BrowserSession-debug.",
    )
    group.addoption(
        "--ky-video",
        action="store_true",
        default=False,
        help="Aktivér videooptagelse i BrowserSession.",
    )


# ---------------------------------------------------------------------------
# Async backend
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Kør asynkrone tests med asyncio."""

    return "asyncio"


# ---------------------------------------------------------------------------
# Credential-navn til Automation Server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ky_credential_name(
    pytestconfig: pytest.Config,
) -> str:
    """Returnér navnet på credential-posten i Automation Server."""

    value = str(pytestconfig.getoption("--ky-credential-name") or "").strip()
    if not value:
        pytest.fail(
            "--ky-credential-name må ikke være tom.",
            pytrace=False,
        )
    return value


@pytest.fixture(scope="session")
def ky_user(
    ky_credential_name: str,
) -> str:
    """Bagudkompatibelt alias til eksisterende tests."""

    return ky_credential_name


# ---------------------------------------------------------------------------
# Test-CPR fra CLI eller .env
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_cpr(
    pytestconfig: pytest.Config,
) -> str:
    """Returnér og validér test-CPR fra CLI eller ``TEST_CPR`` i .env.

    Prioritet:
    1. ``--test-cpr``
    2. miljøvariablen ``TEST_CPR`` indlæst fra projektets .env
    """

    cli_value = pytestconfig.getoption("--test-cpr")
    env_value = os.getenv("TEST_CPR")
    raw_value = cli_value if cli_value is not None else env_value

    if raw_value is None or not str(raw_value).strip():
        pytest.fail(
            "Testen kræver et CPR-nummer. "
            f"Angiv TEST_CPR i {ENV_FILE} eller brug "
            "--test-cpr 0101011234.",
            pytrace=False,
        )

    cpr = re.sub(r"\D", "", str(raw_value))
    if len(cpr) != 10:
        source = "--test-cpr" if cli_value is not None else "TEST_CPR i .env"
        pytest.fail(
            f"{source} skal bestå af præcis 10 cifre.",
            pytrace=False,
        )

    source = "CLI" if cli_value is not None else ".env"
    print(f"Test-CPR hentet fra {source}: ******{cpr[-4:]}")
    return cpr


# ---------------------------------------------------------------------------
# BrowserSession-konfiguration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ky_headless(pytestconfig: pytest.Config) -> bool:
    """Returnér den valgte headless-indstilling."""

    return bool(pytestconfig.getoption("--ky-headless"))


@pytest.fixture(scope="session")
def ky_debug(pytestconfig: pytest.Config) -> bool:
    """Returnér den valgte debug-indstilling."""

    return bool(pytestconfig.getoption("ky_debug"))


@pytest.fixture(scope="session")
def ky_video(pytestconfig: pytest.Config) -> bool:
    """Returnér den valgte videoindstilling."""

    return bool(pytestconfig.getoption("--ky-video"))


@pytest.fixture
async def automation_session(
    ky_headless: bool,
    ky_debug: bool,
    ky_video: bool,
) -> AsyncIterator[BrowserSession]:
    """Opret og ryd op i en rigtig BrowserSession."""

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
) -> AsyncIterator[Page]:
    """Opret en Playwright Page i den aktive BrowserSession."""

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
