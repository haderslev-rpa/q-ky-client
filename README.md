# ky-client

Python-bibliotek der giver en browser-automatiseringsklient til Kommunernes Ydelsessystem (KY), Danmarks kommunale ydelsessystem.

> Denne klient er ikke officielt støttet eller godkendt af KY/Kombit. Brug på eget ansvar.

## Om KY

Kommunernes Ydelsessystem (KY) er det fælles fagsystem for kommunale ydelser (kontanthjælp, sygedagpenge m.m.). Klienten driver KY's web-UI via Playwright og eksponerer strukturerede metoder til sagsbehandling.

## Installation

```bash
uv add git+https://github.com/odense-rpa/ky-client
```

## Forudsætninger

- Python ≥ 3.13
- Playwright-browsere installeret (`playwright install`)
- Adgang til KY med brugernavn, adgangskode og kommune-IDP

## Konfiguration

| Miljøvariabel | Beskrivelse |
|---|---|
| `KY_USER` | Brugernavn til KY |
| `KY_PASSWORD` | Adgangskode til KY |
| `KY_IDP` | Identity provider-id (kommunevælger på KY-login) |
| `TEST_CPR` | CPR-nummer brugt i tests |

## Nuværende funktionalitet

- **Login** — logger ind i KY med brugernavn, adgangskode og kommune-IDP via Playwright
- **Borgeropslagm** — henter fuld sagsoversigt for en borger (personoplysninger, relationer, boforhold, uløste opgaver, sagsliste, seneste hændelser)
- **Låsekontrol** — tjekker om en borgerpost er låst
- **Ferieoplysninger** — henter ferieperioder, jobcenterafwesenhed og feriekonto
- **Skatteoplysninger** — henter skattekort, kommende og historiske indberetninger samt restskat
- **Indkomstindberetning** — manuel indberetning af indkomst med bred feltdækning (løn, dagpenge, sygedagpenge, fleksjob, feriegodtgørelse, pensionsbidrag, ATP, AM-bidrag, naturalydelser m.m.) samt valgfri journalnotatoprettelse
- **Dokumentupload** — uploader et dokument til en specifik borgersag
- **Opgavehåndtering** — åbn, godkend, afbryd og rediger individuelle borgeropgaver
- **Opgaveindbakke** — gennemse opgaveindbakken pr. opgavepakke med paginering
-ggb **Luk sagsfane** — luk en åben borgersagsfane og håndter dialog om ugemte ændringer

## Brug

```python
from ky_client import KYClientManager

async with KYClientManager() as ky:
    # Borgerslagsopslag
    borger = await ky.borger.get_borger("1234567890")

    # Indkomstindberetning
    await ky.borger.indberet_indkomst("1234567890", indkomsttype="Løn", beloeb=25000)

    # Opgaveindbakke
    opgaver = await ky.opgaveindbakke.get_opgaver(pakke="Min pakke")
```

`KYClientManager` sammensætter:
- `KYClient` — lav-niveau Playwright + httpx-session
- `BorgereClient` — borger-niveau operationer
- `OpgaveindbakkeClient` — opgaveindbakke

Browseren startes non-headless med dansk locale (`da-DK`) og 1920×1080 viewport.

## Afhængigheder

| Pakke | Formål |
|---|---|
| `playwright` | Browserautomatisering — driver KY-webUI ved login og alle interaktioner |
| `httpx` | HTTP-klient brugt internt til API-kald inden for KY-sessionen |
| `authlib` | OAuth/autentificeringssupport |
| `requests` | Alternativ HTTP-klient |
| `ruff` | Python-linter og kodeformatter |
| `pytest` | Testframework |

## GDPR og sikkerhed

Biblioteket behandler CPR-numre og tilknyttede borgerdata, herunder personoplysninger, familierelationer, boforhold, indkomsttal, skatteoplysninger, ferieregistreringer og sagshistorik. Data transmitteres udelukkende til og fra KY-systemet og gemmes ikke lokalt af biblioteket. Adgang bør begrænses til autoriserede systemer og brugere.

## Licens

MIT
