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
        TOP_SEARCH_BUTTON = "div#topSearchBtn"

    class Opgaveindbakke:
        VÆLG_OPGAVEPAKKE = "button[data-id='arbejdspakker']"
        UBEHANDLEDE_OPGAVER = "table#ubehandledeTable"

        PACKAGE_CONTROL_SELECTORS = (
            "button[data-id='arbejdspakker']",
            "select#arbejdspakker",
            "select[name='arbejdspakker']",
        )

    class Borgere:
        # Overblik
        PERSON_OPLYSNINGER = "table#person-oplysninger"
        SAGSOVERSIGT = "table#sagsoversigt"
        UBEHANDLEDE_OPGAVER = "table#ubehandlede-opgaver"
        LIVSSITUATION = "table#person-overblik-livssituation"
        LÅST_BANNER = 'div[data-textkey="system.type.advarsel_item.reserveret_af"]'

        # Send brev - opgaveheader og loader
        SEND_BREV_HEADER = (
            "div#opgave-header.block-heading "
            "span[data-textkey='system.type.opgave.send_brev']"
        )
        SEND_BREV_HEADER_CONTAINER = "div#opgave-header.block-heading"
        SEND_BREV_UNDOCK_BUTTON = "a.undock_panel_button[data-opgave-id][data-url]"
        SEND_BREV_LOADER = (
            "div#empty_opgave_loader, "
            "div#opgave_loader, "
            "div#opgave-loader, "
            "i#opgave-spinner"
        )

        # Send brev - Vælg sag
        SEND_BREV_SAGSVAELGER = (
            "div.sagsvaelger.dropdown[data-id='command.alleTilfoejedeBreve[0].sagIds']"
        )
        SEND_BREV_SAGSVAELGER_TOGGLE = "div.dropdown-toggle[data-toggle='dropdown']"
        SEND_BREV_SAGSVAELGER_INPUT = "input.sagsvaelger-input[readonly]"
        SEND_BREV_SAGSVAELGER_MENU = "div.sagsvaelger-list.dropdown-menu"
        SEND_BREV_SAGSVAELGER_SOEG = "input.sagsvaelger-soeg"
        SEND_BREV_SAGSVAELGER_AKTIVE = "input[type='checkbox'][data-tilstand='aktiv']"
        SEND_BREV_SAGSVAELGER_PASSIVE = "input[type='checkbox'][data-tilstand='passiv']"
        SEND_BREV_SAGSVAELGER_RESULTS = "div.sagsvaelger-results"
        SEND_BREV_SAGSVAELGER_SELECT_CELL = "td.select-row"

        SEND_BREV_SAGSVAELGER_TABEL = "table[id^='brevSagsvaelgerTable']"

        SEND_BREV_SAGSVAELGER_RAEKKER = (
            "table[id^='brevSagsvaelgerTable'] > tbody > tr.table-row"
        )

        # Send brev - Brevskabelon
        SEND_BREV_BREVSKABELON_INPUT = (
            "input.skabelon_titel"
            "[name='alleTilfoejedeBreve[0].skabelonTitel']"
            "[readonly]"
        )
        SEND_BREV_BREVSKABELON_SOEG = "input[placeholder='Søg efter skabelon']"
        SEND_BREV_BREVSKABELON_TITLER = (
            "ul.skabelonlist li[data-titel], "
            "ul.skabelonlist li:not(:has(ul)), "
            "[role='treeitem']:not([aria-expanded]), "
            "[role='option'], "
            ".skabelonvaelger li:not(:has(ul))"
        )

        # Send brev - Standard bilag
        # VIGTIGT: Selectoren må kun matche readonly-inputs til en
        # skabelonvælger. Generiske button/data-toggle selectors kan ramme
        # Vælg sag-dropdownen og åbne den igen efter valg af brevskabelon.
        SEND_BREV_STANDARD_BILAG_CONTROL = (
            "input.skabelon_titel[readonly]:visible, "
            "input[readonly][data-titel]:visible, "
            "input[readonly][placeholder*='bilag' i]:visible"
        )
        SEND_BREV_STANDARD_BILAG_MENU = (
            "ul.skabelonlist:visible:has("
            "li.hg-skabelon.cell.VEDHAEFTNING[data-titel][data-noegle]"
            ")"
        )
        SEND_BREV_STANDARD_BILAG_TITLER = (
            "li.hg-skabelon.cell.VEDHAEFTNING[data-titel][data-noegle]"
        )

        # Send brev - fysisk post, bilagsdropdown og Brevtype
        SEND_BREV_FYSISK_POST = (
            "input[type='checkbox'].fysisk_post"
            "[name='alleTilfoejedeBreve[0].fysiskPost']"
        )
        SEND_BREV_FYSISK_POST_JS = "input[type='checkbox'].fysisk_post"
        SEND_BREV_FYSISK_POST_NAME = "alleTilfoejedeBreve[0].fysiskPost"
        SEND_BREV_POSTAGE_CONTAINER = "tr#postage-container0[name='postage-container']"
        SEND_BREV_POSTAGE_TYPE = "select[name='alleTilfoejedeBreve[0].postage']"
        SEND_BREV_STANDARD_BILAG_DROPDOWN = "div.skabelon-vaelger.dropdown-menu"
        SEND_BREV_STANDARD_BILAG_SOEG = (
            "input.skabelonvaelger-soeg[placeholder='Søg efter vedhæftning']"
        )

        UBEHANDLEDE_OPGAVER_TABLE = "table#ubehandlede-opgaver"

        UBEHANDLEDE_OPGAVER_ROWS = (
            "table#ubehandlede-opgaver tbody.datatable-tbody tr[data-id]"
        )

        UBEHANDLEDE_OPGAVER_NEXT = (
            "#ubehandlede-opgaver_next, "
            "[aria-controls='ubehandlede-opgaver']"
            ".paginate_button.next"
        )

        UBEHANDLET_OPGAVE_LINK = "a.undock_panel_button[data-opgave-id][data-url]"

        HANDLINGER_CONTAINER = "li#handlinger-dropdown"

        # Kopiér indholdet af class Borgere ind i KYSelectors.Borgere.
        # Fjern eksisterende dubletter med de samme navne først.

        # Opret opfølgningsopgave
        OPFOELGNING_LOADER = (
            "div#empty_opgave_loader, div#opgave_loader, div#opgave-loader"
        )

        OPFOELGNINGSTYPE = "select#opfoelgningsType"

        OPFOELGNINGSDATO = (
            "input#command\\.opfoelgningsdato, "
            "input[name='opfoelgningsdato'], "
            "input[name='command.opfoelgningsdato']"
        )

        OPFOELGNING_SAGSBEHANDLER = "input#typeahead, input[name='sagsbehandler']"

        OPFOELGNING_SAGSBEHANDLER_FORSLAG = (
            ".tt-menu:visible .tt-suggestion.tt-selectable, "
            ".typeahead.dropdown-menu:visible li:visible, "
            "[role='listbox']:visible [role='option']:visible"
        )

        OPFOELGNING_TITEL = "input#title, input[name='title']"

        OPFOELGNING_FREKVENS = "select#frekvens, select[name='frekvens']"

        OPFOELGNING_HAENDELSESTYPE = (
            "select#haendelseType, select[name='haendelseType']"
        )

        OPFOELGNING_BESKRIVELSE = "textarea#beskrivelse, textarea[name='beskrivelse']"

        OPFOELGNING_GEM = (
            "button[type='submit']:has-text('Gem'), "
            "input[type='submit'][value='Gem'], "
            "button.btn-submit-form:has-text('Gem'), "
            "a.btn-submit-form:has-text('Gem')"
        )

        OPFOELGNING_VALIDERING = (
            ".has-error:visible, "
            ".help-block:visible, "
            ".alert-danger:visible, "
            ".field-validation-error:visible"
        )

        # Alle åbne borgerfaner.
        # "active" anvendes ikke, fordi alle PERSON-faner skal findes.
        BORGER_FANER = "li.tab.topmenu-tab[data-tab-target-id='PERSON']"

        # Lukkeknapperne i alle åbne borgerfaner.
        LUK_BORGER_FANER = (
            "li.tab.topmenu-tab"
            "[data-tab-target-id='PERSON'] "
            "i.navigation-close-tab"
            "[data-entity-type='PERSON']"
        )

        PERSON_FANER = "li.tab.topmenu-tab[data-tab-target-id='PERSON']"

        PERSON_LUKKNAPPER = (
            "li.tab.topmenu-tab"
            "[data-tab-target-id='PERSON'] "
            ".navigation-close-tab"
            "[data-entity-type='PERSON']"
        )

        # Dokumenter på den åbnede opgave
        DOKUMENTER_TOGGLE = (
            "a[data-toggle='collapse']"
            "[href='#vedhaeftninger']"
            ":has(span.panel-title:has-text('Dokumenter'))"
        )
        DOKUMENTER_PANEL_CONTAINER = (
            "xpath=ancestor::div["
            "contains(concat(' ', normalize-space(@class), ' '), ' panel ')"
            "][1]"
        )
        DOKUMENTER_PANEL = "xpath=.//*[@id='vedhaeftninger']"
        DOKUMENTER_TABELLER = "table:visible"
        DOKUMENTER_RAEKKER = "tbody tr.table-row"
        DOKUMENTER_CELLER = "td:not(.handlinger), [role='gridcell']"
        DOKUMENTER_AABN_LINK = "a[target='_blank'][href*='aabnPdfDokument']"

        # Navigation async
        OVERBLIK = "li.tab[data-tab-target-id='PERSON_OVERBLIK']"
        JOURNALNOTATER_DOKUMENTER = (
            "li.tab[data-tab-target-id='PERSON_JOURNALNOTATER_DOKUMENTER']"
        )
        HAENDELSER = "li.tab[data-tab-target-id='PERSON_HAENDELSER']"
        UDBETALINGER = "li.tab[data-tab-target-id='PERSON_UDBETALINGER']"
        KONTERINGER = "li.tab[data-tab-target-id='PERSON_KONTERINGER']"
        INDTAEGTER = "li.tab[data-tab-target-id='PERSON_INDTÆGTER']"
        SANKTIONER = "li.tab[data-tab-target-id='PERSON_SANKTIONER']"
        FERIE = "li.tab[data-tab-target-id='PERSON_FERIE']"
        FORDRINGER = "li.tab[data-tab-target-id='PERSON_FORDRINGER']"
        MODREGNINGER = "li.tab[data-tab-target-id='PERSON_MODREGNINGSANMODNINGER']"
        FUB = "li.tab[data-tab-target-id='PERSON_FUB']"
        SKAT = "li.tab[data-tab-target-id='SKATTEINDBERETNINGER']"
        JOBCENTER = "li.tab[data-tab-target-id='PERSON_JOBCENTER']"
        MEDICINTILSKUD = "li.tab[data-tab-target-id='PERSON_MEDICINTILSKUD']"

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
        # Skriv journalnotat: formular og sag
        JOURNALNOTAT_EXPAND_KOLLAPSET = (
            'div.journalnotat_instans-header a[data-toggle="collapse"]'
        )
        JOURNALNOTAT_SAGSVAELGER_DROPDOWN = (
            "#command\\.tilfoejedeJournalnotater\\[0\\]\\.sager_dropdown"
        )
        JOURNALNOTAT_SAGSVAELGER_INPUT = "input.sagsvaelger-input"
        JOURNALNOTAT_SAGSVAELGER_EKSAKT_INPUT = (
            "#command\\.tilfoejedeJournalnotater\\[0\\]\\.sager"
        )
        JOURNALNOTAT_AKTIV_VALGT_SAG = (
            '#sagsvaelgertable tr.selected[data-tilstand="aktiv"]'
        )
        JOURNALNOTAT_VALGTE_SAGSRAEKKER = "#sagsvaelgertable tr.selected"
        JOURNALNOTAT_AKTIV_CHECKBOX = 'input[type="checkbox"][data-tilstand="aktiv"]'
        JOURNALNOTAT_PASSIV_CHECKBOX = 'input[type="checkbox"][data-tilstand="passiv"]'
        JOURNALNOTAT_AKTIV_CHECKBOX_CHECKED = (
            'input[type="checkbox"][data-tilstand="aktiv"]:checked'
        )
        JOURNALNOTAT_PASSIV_CHECKBOX_CHECKED = (
            'input[type="checkbox"][data-tilstand="passiv"]:checked'
        )
        JOURNALNOTAT_PASSIV_CHECKBOX_UNCHECKED = (
            'input[type="checkbox"][data-tilstand="passiv"]:not(:checked)'
        )
        JOURNALNOTAT_SAGSVAELGER_SOEG = "input.sagsvaelger-soeg"
        JOURNALNOTAT_SAGSVAELGER_RESULTATER = (
            "#sagsvaelgertable tbody tr:not([style*='display: none'])"
        )
        # Bagudkompatibelt navn til eksisterende kode.
        JOURNALNOTAT_SAGSVAELGER_FOERSTE_RESULTAT = JOURNALNOTAT_SAGSVAELGER_RESULTATER
        JOURNALNOTAT_DROPDOWN_CONTAINER = (
            "div#journalnotat_instans_0_dropdown.dropdown-toggle"
        )

        # Skriv journalnotat: skabelon
        JOURNALNOTAT_VAELG_SKABELON = (
            'input[data-textkey="fagsystem.person.opgave.'
            'journalnotat_instans.vaelg_skabelon"]'
        )
        JOURNALNOTAT_SKABELON_KONTROL = (
            "input.form-control.skabelon_titel.cursor-pointer"
            "#tilfoejedeJournalnotater0\\.skabelonTitel"
            "[name='tilfoejedeJournalnotater[0].skabelonTitel']"
        )
        JOURNALNOTAT_SKABELON_TITELFELT = (
            "input.skabelon_titel.cursor-pointer"
            "[name='tilfoejedeJournalnotater[0].skabelonTitel']"
        )
        JOURNALNOTAT_SKABELON_NOEGLEFELT = (
            "input[type='hidden'][name='tilfoejedeJournalnotater[0].skabelonNoegle']"
        )
        JOURNALNOTAT_SKABELONGRUPPE_SOEG = (
            "#journalnotat-group input.form-control.skabelonvaelger-soeg"
        )
        JOURNALNOTAT_SKABELON_RESULTATER = (
            "#journalnotat-group li[data-titel], "
            "#journalnotat-group li.hg-skabelon.cell, "
            "#journalnotat-group [role='treeitem'], "
            "#journalnotat-group [role='option'], "
            "ul.skabelonlist li[data-titel]"
        )
        JOURNALNOTAT_SKABELONGRUPPE_FOERSTE_RESULTAT = "ul.skabelonlist li.hg.cell li"

        # Midlertidige aliases, så eksisterende skriv_journalnotat.py virker.
        control_selector = JOURNALNOTAT_SKABELON_KONTROL
        field_selector = JOURNALNOTAT_SKABELON_TITELFELT
        key_selector = JOURNALNOTAT_SKABELON_NOEGLEFELT

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
