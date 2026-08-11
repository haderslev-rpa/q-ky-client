def _nav(key: str) -> str:
    return f'[data-textkey="fagsystem.person.navigation.{key}"]'


class KYSelectors:
    class Login:
        MUNICIPALITY_SELECT = "select#SelectedAuthenticationUrl"
        OK_BUTTON = 'input[type="button"][value="OK"]'
        SUBMIT_BUTTON = 'input[type="submit"]'
        USERNAME = 'input[name="loginfmt"]'
        PASSWORD = 'input[name="passwd"]'

    class Main:
        LOGO = "img#fagsystem-logo"
        TOP_SEARCH = "input#topSearch"

    class Opgaveindbakke:
        VÆLG_OPGAVEPAKKE = "button[data-id='arbejdspakker']"
        UBEHANDLEDE_OPGAVER = "table#ubehandledeTable"

    class Borgere:
        # Overblik
        PERSON_OPLYSNINGER = "table#person-oplysninger"
        SAGSOVERSIGT = "table#sagsoversigt"
        UBEHANDLEDE_OPGAVER = "table#ubehandlede-opgaver"
        LIVSSITUATION = "table#person-overblik-livssituation"
        LÅST_BANNER = 'div[data-textkey="system.type.advarsel_item.reserveret_af"]'

        #Dokumenter på den åbnede opgave
        DOKUMENTER_TOGGLE = (
            "a[data-toggle='collapse']"
            ":has(span.panel-title:has-text('Dokumenter'))"
        )
        DOKUMENTER_TABELLER = "table:visible"
        DOKUMENTER_RAEKKER = "tbody tr.table-row"
        DOKUMENTER_AABN_LINK = (
            "a[target='_blank']"
            "[href*='aabnPdfDokument']"
        )

        # Navigation
        OVERBLIK = _nav("person_overblik")
        JOURNALNOTATER_DOKUMENTER = _nav("person_journalnotater_dokumenter")
        HAENDELSER = _nav("person_haendelser")
        UDBETALING = _nav("person_udbetalinger")
        KONTERINGER = _nav("person_konteringer")
        INDTAEGTER = _nav("person_indtaegter")
        SANKTIONER = _nav("person_sanktioner")
        FERIE = _nav("person_ferie")
        FORDRINGER = _nav("person_fordringer")
        MODREGNINGER = _nav("person_modregningsanmodninger")
        FUB = _nav("person_fub")
        SKAT = _nav("skatteindberetninger")
        JOBCENTER = _nav("person_jobcenter")
        MEDICINTILSKUD = _nav("person_medicintilskud")

        # Ferie
        FERIEPERIODER_TIL_BEREGNING = "table#ferieperioder-table"
        FRAVÆR_FRA_JOBCENTER = "table#fravaer-table"
        FERIEKONTO = "table#feriekonto"
        FERIEPERIODER_FRA_FERIEKONTO = "table#ferieperioder"

        # Skat
        SKATTEKORT_FRA_EINDKOMST = "table#skattekorttable"
        KOMMENDE_SKATTEINDBERETNINGER = "table#kommendeskatteindberetninger"
        HISTORISKE_SKATTEINDBERETNINGER = "table#historiskeskatteindberetninger"
        OVERSKYDENDE_SKAT = "table#overskydendeskat"
        OPLYSNINGER_SKAT = "table.select-year-skat-table"

        # Handlinger
        HANDLINGER_DROPDOWN = "li#handlinger-dropdown a.dropdown-toggle"
        HANDLINGER_SUBPROCESSER = 'a.handlinger-submenu-btn:has(span[data-textkey="fagsystem.handlinger.haendelsegruppe.sub"])'
        HANDLINGER_SUBPROCESSER_INDTÆGTER = (
            'a.handlinger-leaf[data-textkey="system.type.haendelse_type.hd_indtaegter"]'
        )
        OPGAVE_LOADER = "div#opgave-loader"

        # Handlinger - Indtægter
        INDTÆGTER_MANUEL_INDTASTNING = 'button[data-onclick*="/opgave/indtaegter/formFields"]:has(span[data-textkey="system.medtagkoncept.add"])'
        JOURNALNOTAT_EXPAND_KOLLAPSET = (
            'div.journalnotat_instans-header a[data-toggle="collapse"]'
        )
        JOURNALNOTAT_SAGSVAELGER_INPUT = "input.sagsvaelger-input"
        JOURNALNOTAT_AKTIV_VALGT_SAG = (
            '#sagsvaelgertable tr.selected[data-tilstand="aktiv"]'
        )
        JOURNALNOTAT_AKTIV_CHECKBOX_CHECKED = (
            'input[type="checkbox"][data-tilstand="aktiv"][checked="checked"]'
        )
        JOURNALNOTAT_PASSIV_CHECKBOX_CHECKED = (
            'input[type="checkbox"][data-tilstand="passiv"]:checked'
        )
        JOURNALNOTAT_PASSIV_CHECKBOX_UNCHECKED = (
            'input[type="checkbox"][data-tilstand="passiv"]:not(:checked)'
        )
        JOURNALNOTAT_SAGSVAELGER_SOEG = "input.sagsvaelger-soeg"
        JOURNALNOTAT_SAGSVAELGER_FOERSTE_RESULTAT = (
            '#sagsvaelgertable tbody tr:not([style*="display: none"])'
        )
        JOURNALNOTAT_VAELG_SKABELON = 'input[data-textkey="fagsystem.person.opgave.journalnotat_instans.vaelg_skabelon"]'
        JOURNALNOTAT_SKABELONGRUPPE_SOEG = (
            "#journalnotat-group input.form-control.skabelonvaelger-soeg"
        )
        JOURNALNOTAT_SKABELONGRUPPE_FOERSTE_RESULTAT = "ul.skabelonlist li.hg.cell li"
        INDTÆGTER_CVR_SE_NUMMER = "input#indtaegterTable\\.cvrNummer\\.valueString"
        INDTÆGTER_VIRKSOMHEDSNAVN = "input#indtaegterTable\\.cvrNavn\\.valueString"
        INDTÆGTER_TYPE = "select#indtaegterTable\\.indtaegtsType\\.valueString"
        INDTÆGTER_BELOEB = "input#indtaegterTable\\.beloeb\\.valueString"
        INDTÆGTER_DISPOSITIONSDATO = (
            "input#command\\.indtaegterTable\\.dispotitionsdato\\.valueString"
        )
        INDTÆGTER_PERIODE_FRA = (
            "input#command\\.indtaegterTable\\.optjeningsperiodeFra\\.valueString"
        )
        INDTÆGTER_PERIODE_TIL = (
            "input#command\\.indtaegterTable\\.optjeningsperiodeTil\\.valueString"
        )
        INDTÆGTER_PENSIONSBIDRAG_EGET = (
            "input#indtaegterTable\\.pensionsbidragEget\\.valueString"
        )
        INDTÆGTER_PENSIONSBIDRAG_ARBEJDSGIVER = (
            "input#indtaegterTable\\.pensionsbidragArbejdsgiver\\.valueString"
        )
        INDTÆGTER_ATP_BIDRAG_EGET = (
            "input#indtaegterTable\\.atpBidragEget\\.valueString"
        )
        INDTÆGTER_ATP_BIDRAG_ARBEJDSGIVER = (
            "input#indtaegterTable\\.atpBidragArbejdsgiver\\.valueString"
        )
        INDTÆGTER_AM_BIDRAG = "input#indtaegterTable\\.amBidrag\\.valueString"
        INDTÆGTER_TIMER_I_PERIODEN = "input#indtaegterTable\\.timer\\.valueString"
        INDTÆGTER_NETTOFERIEPENGE = (
            "input#indtaegterTable\\.feriepengeNetto\\.valueString"
        )
        INDTÆGTER_BRUTTOFICEREDE_NETTOFERIEPENGE = (
            "input#indtaegterTable\\.feriepengeNettoBruttoficeret\\.valueString"
        )
        INDTÆGTER_BRUTTOFERIEPENGE_TIMELOENNDE = (
            "input#indtaegterTable\\.bruttoferipengeTimeloennede\\.valueString"
        )
        INDTÆGTER_A_INDKOMST_SOM_FERIEPENGE = (
            "input#indtaegterTable\\.aIndkomstSomFeriepenge\\.valueString"
        )
        INDTÆGTER_SOEGNE_OG_HELLIGDAGSBETALING = (
            "input#indtaegterTable\\.opsparedeSoegneHelligdage\\.valueString"
        )
        INDTÆGTER_FRI_KOST_OG_LOGI = (
            "input#indtaegterTable\\.friKostOgLogi\\.valueString"
        )
        INDTÆGTER_FRI_BIL = "input#indtaegterTable\\.friBil\\.valueString"
        INDTÆGTER_FRI_TELEFON = "input#indtaegterTable\\.friTelefon\\.valueString"
        INDTÆGTER_SUNDHEDSFORSIKRING_OG_GRUPPELIV = (
            "input#indtaegterTable\\.sundhedsforsikringOgGruppeliv\\.valueString"
        )
        INDTÆGTER_SKATTEFRI_REJSE_OG_BEFORDRINGSGODTGOERELSE = (
            "input#indtaegterTable\\.rejseOgBefordringsgodtgoerelse\\.valueString"
        )
        INDTÆGTER_OPSPARET_FERIEFRIDAGE = (
            "input#indtaegterTable\\.opsparetFeriefridage\\.valueString"
        )
        INDTÆGTER_YDELSESARTER = "select#indtaegterTable\\.ydelsesarter\\.valueString"
        INDTÆGTER_GEM = 'button.submit-modul[data-href="/opgave/indtaegter/submitForm"]:has(span[data-textkey="system.medtagkoncept.gem"])'
        INDTÆGTER_GODKEND = 'button.submit-opgave[data-href="/opgave/handling/fortsaet"]:has(span[data-textkey="fagsystem.person.opgave.handling.godkend"])'
        INDTÆGTER_LUK = 'button#docked-close.submit-opgave[data-href="/opgave/handling/lukAfsluttetOpgave"]:has(span[data-textkey="fagsystem.person.opgave.handling.luk_afsluttet_opgave"])'

        # Rediger opgave
        REDIGER_OPGAVE_GEM = 'button.btn.btn-primary[data-textkey="fagsystem.edit_opgave_modal.edit.submit.btn"]'
        REDIGER_OPGAVE_LUK = (
            'button.btn.btn-primary[data-textkey="fagsystem.edit_opgave_modal.luk.btn"]'
        )

        # Godkend opgave
        GODKEND_OPGAVE_GODKEND = 'button[type="button"].btn.btn-primary.submit-opgave.margin-right[data-href="/opgave/handling/fortsaet"]:has(span[data-textkey="fagsystem.person.opgave.handling.godkend"])'
        GODKEND_OPGAVE_LUK = 'button#docked-close.submit-opgave[data-href="/opgave/handling/lukAfsluttetOpgave"]:has(span[data-textkey="fagsystem.person.opgave.handling.luk_afsluttet_opgave"])'

        # Afbryd opgave modal
        AFBRYD_OPGAVE_ANNULLER = 'button.btn.btn-primary[data-textkey="fagsystem.person.opgave.afbryd.modal.annuller.btn"]'
        AFBRYD_OPGAVE_AFBRYD_OG_SLET = 'button.btn.btn-primary[data-textkey="fagsystem.person.opgave.afbryd.modal.afbryd_og_slet.btn"]'
        AFBRYD_OPGAVE_AFBRYD_OG_GEM = 'button.btn.btn-primary[data-textkey="fagsystem.person.opgave.afbryd.modal.afbryd_og_gem.btn"]'
        LUK_ALLE_OPGAVER_AFBRYD_OG_GEM = 'button.btn.btn-primary[data-textkey="fagsystem.entitet.luk_alle_opgaver.afbryd_og_gem.btn"]'
        AFBRYD_OPGAVE_AABN_MODAL = 'a.btn.btn-primary.margin-right:has(span[data-textkey="fagsystem.person.opgave.handling.afbryd"])'

        # Luk alle opgaver
        LUK_ALLE_OPGAVER_FORM = "form#lukAlleOpgForm"

PACKAGE_CONTROL_SELECTORS = (
    "button[data-id='arbejdspakker']",
    "select#arbejdspakker",
    "select[name='arbejdspakker']",
    # øvrige selectors...
)