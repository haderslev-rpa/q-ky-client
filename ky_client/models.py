from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal


class IndtægterType(Enum):
    ANDEN_BRUTTOINDTAEGT = "Anden bruttoindtægt"
    ANDEN_INDTAEGT_NETTO = "Anden indtægt Netto"
    ARBEJDSINDTAEGT_USTOETTET = "Arbejdsindtægt ved ustøttet beskæftigelse"
    ARBEJDSINDTAEGT_USTOETTET_SAMME_JOB = (
        "Arbejdsindtægt ved ustøttet beskæftigelse (samme job)"
    )
    ARBEJDLOESHEDSDAGPENGE = "Arbejdsløshedsdagpenge"
    BARSELSDAGPENGE = "Barselsdagpenge"
    FERIEPENGE_FORHINDRET = "Feriepenge ved ikke afholdt ferie (forhindret)"
    FERIEPENGE_SELVVALGT = "Feriepenge ved ikke afholdt ferie (selvvalgt)"
    FORVENTET_FLEKSJOB_LY = "Forventet indtægt fra fleksjob/LY (aktuel måned)"
    INDTAEGT_EU = "Indtægt indenfor EU"
    INDTAEGT_FLEKSJOB_NAESTE_MAANED = "Indtægt ved fleksjob til træk i måneden efter"
    INDTAEGT_FLEKSJOB_SAMME_MAANED = "Indtægt ved fleksjob/LY til træk i samme måned"
    INDTAEGT_TILBUD = "Indtægt ved tilbud"
    LOENMODTAGERNES_GARANTIFOND = "Lønmodtagernes garantifond"
    PRAKTIKLOEN = "Praktikløn"
    RESSOURCEFORLOEBSYDELSE_SYGDOM = "Ressourceforløbsydelse ved sygdom under fleksjob"
    SELVSTAENDIG_VIRKSOMHED = "Selvstændig virksomhed"
    SELVSTAENDIG_VIRKSOMHED_UDGAAR = "Selvstændig virksomhed (udgår 1/7-25 grundet 225)"
    SYGEDAGPENGE = "Sygedagpenge"
    VAELG_FRA_LISTE = "Vælg fra liste"


class Ydelsesarter(Enum):
    HJAELP_TIL_FORSOERGGELSE = "Hjælp til forsørgelse"
    LEDIGHEDSYDELSE = "Ledighedsydelse"
    REVALIDERINGSYDELSE = "Revalideringsydelse"
    FLEKSLOENTILSKUD = "Fleksløntilskud"
    RESSOURCEFORLOEBSYDELSE = "Ressourceforløbsydelse"
    RESSOURCEFORLOEBSYDELSE_JOBAFKLARING = "Ressourceforløbsydelse under jobafklaring"


class AfbrydType(Enum):
    AFBRYD = "Afbryd"
    AFBRYD_OG_SLET = "Afbryd og slet"
    AFBRYD_OG_GEM = "Afbryd og gem"


@dataclass
class Indtægter:
    cvr_se_nummer: str | None = None
    virksomhedsnavn: str | None = None
    indtaegtstype: IndtægterType = field(default=IndtægterType.VAELG_FRA_LISTE)
    beloeb: Decimal = Decimal("0.0")
    dispositionsdato: str = ""
    periode_fra: str = ""
    periode_til: str = ""
    pensionsbidrag_eget: float | None = None
    pensionsbidrag_arbejdsgiver: float | None = None
    atp_bidrag_eget: float | None = None
    atp_bidrag_arbejdsgiver: float | None = None
    am_bidrag: float | None = None
    timer_i_perioden: int | None = None
    nettoferiepenge: float | None = None
    bruttoficerede_nettoferiepenge: float | None = None
    bruttoferiepenge_timeloennede: float | None = None
    a_indkomst_som_feriepenge: float | None = None
    soegne_og_helligdagsbetaling: float | None = None
    fri_kost_og_logi: float | None = None
    fri_bil: float | None = None
    fri_telefon: float | None = None
    sundhedsforsikring_og_gruppeliv: float | None = None
    skattefri_rejse_og_befordringsgodtgoerelse: float | None = None
    opsparet_feriefridage: float | None = None
    ydelsesarter: Ydelsesarter | None = None


@dataclass
class Journalnotat:
    indhold: str
    sagstype: str
    skabelongruppe: str
    skabelon: str


@dataclass
class RedigerOpgave:
    prioritet: Literal["Lav", "Mellem", "Høj"] = "Lav"
    forfalds_dato: str = ""
    opfølgningsopgavetype: str = ""
    sagsbehandler: str = ""
    frekvens: Literal[
        "Aldrig",
        "Dagligt",
        "Ugenligt",
        "Hver anden uge",
        "Månedligt",
        "Hver tredje måned",
        "Halvårligt",
        "Årligt",
    ] = "Aldrig"
