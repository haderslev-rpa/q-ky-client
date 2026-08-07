import re
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ky_client.client import KYClient
from ky_client.models import AfbrydType, Indtægter, Journalnotat, RedigerOpgave
from ky_client.selectors import KYSelectors
from ky_client.utils import (
    extract_header_table,
    extract_keyed_table,
    navigate_to,
    naviger_til_borger,
)


class BorgereClient:
    def __init__(self, ky_client: KYClient) -> None:
        self._page: Page = ky_client.page
        self.p_id: str | None = None

    def _wait_for_opgave_loader_to_clear(self, timeout: int = 30000) -> None:
        self._page.wait_for_function(
            """(selector) => {
                const loader = document.querySelector(selector);
                if (!loader) {
                    return true;
                }

                const style = window.getComputedStyle(loader);
                const hidden =
                    style.display === 'none' ||
                    style.visibility === 'hidden' ||
                    style.opacity === '0';
                return hidden || style.pointerEvents === 'none';
            }""",
            arg=KYSelectors.Borgere.OPGAVE_LOADER,
            timeout=timeout,
        )

    def _opret_journalnotat(self, journalnotat: Journalnotat) -> None:
        # Håndter collapse
        expand_toggle = self._page.locator(
            KYSelectors.Borgere.JOURNALNOTAT_EXPAND_KOLLAPSET
        )
        if expand_toggle.count() > 0:
            expand_toggle.first.click(timeout=30000)

        # Håndter i forvejen valgte sagstyper, fremsøg sagstype og vælg på ny
        sagsvaelger_input = self._page.locator(
            KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_INPUT
        ).first

        sagsvaelger_input.click(timeout=30000)

        if sagsvaelger_input.count() > 0:
            valgt_sag_tekst = sagsvaelger_input.input_value().strip()
            if valgt_sag_tekst == "1 sag valgt":
                valgt_aktiv_sag = self._page.locator(
                    KYSelectors.Borgere.JOURNALNOTAT_AKTIV_VALGT_SAG
                ).first
                if valgt_aktiv_sag.count() > 0:
                    valgt_aktiv_sag.click(timeout=30000)

        self._page.fill(
            KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_SOEG,
            journalnotat.sagstype,
        )
        self._page.evaluate(
            """() => {
                const el = document.querySelector('input.form-control.sagsvaelger-soeg');
                if (!el) {
                    return;
                }

                if (window.$) {
                    window.$(el).trigger('keyup');
                    return;
                }

                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            }"""
        )
        self._page.wait_for_selector(
            KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_FOERSTE_RESULTAT,
            timeout=30000,
        )
        self._page.locator(
            KYSelectors.Borgere.JOURNALNOTAT_SAGSVAELGER_FOERSTE_RESULTAT
        ).first.click(timeout=30000)

        sagsvaelger_input.click(timeout=30000)

        # Vælg skabelongruppe og skabelon
        self._page.click(KYSelectors.Borgere.JOURNALNOTAT_VAELG_SKABELON, timeout=30000)
        self._page.fill(
            KYSelectors.Borgere.JOURNALNOTAT_SKABELONGRUPPE_SOEG,
            journalnotat.skabelongruppe,
        )
        self._page.evaluate(
            """() => {
                const el = document.querySelector('#journalnotat-group input.form-control.skabelonvaelger-soeg');
                if (!el) {
                    return;
                }

                if (window.$) {
                    window.$(el).trigger('keyup');
                    return;
                }

                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            }"""
        )

        skabelon_titel = journalnotat.skabelon.replace('"', '\\"')
        skabelon_selector = (
            "ul.skabelonlist li.hg.cell[style=''][data-noegle*='journalnotatskabelon_gruppe'] "
            f'li[data-titel="{skabelon_titel}"]'
        )
        self._page.wait_for_selector(skabelon_selector, timeout=5000)
        self._page.click(skabelon_selector, timeout=30000)

        # Indsæt journalnotat
        self._page.wait_for_function(
            """() => {
                return typeof tinymce !== 'undefined' && !!tinymce.get('tilfoejedeJournalnotater0.notat');
            }""",
            timeout=5000,
        )
        self._page.evaluate(
            """(indhold) => {
                tinymce.get('tilfoejedeJournalnotater0.notat').setContent(indhold);
            }""",
            journalnotat.indhold,
        )

    def hent_borgersag(self, cpr: str) -> dict:
        naviger_til_borger(self._page, cpr, timeout=30000)

        match = re.search(r"pId=([a-f0-9\-]*)", self._page.url)

        data = {
            "pId": match.group(1) if match else None,
        }

        keyed_tables = {
            "Personoplysninger": "table#person-oplysninger",
            "Relationer": "table#person-overblik-relationer",
            "Livssituation": "table#person-overblik-livssituation",
        }
        for key, selector in keyed_tables.items():
            if self._page.locator(selector).is_visible():
                data[key] = extract_keyed_table(self._page, selector)

        header_tables = {
            "Ferier": "table#ferier",
            "Ubehandlede opgaver": "table#ubehandlede-opgaver",
            "Sagsoversigt": "table#sagsoversigt",
            "Seneste hændelser": "table#seneste-haendelser",
        }
        for key, selector in header_tables.items():
            if self._page.locator(selector).is_visible():
                data[key] = extract_header_table(self._page, selector)

        return data

    def luk_borgersag(self, p_id: str) -> bool:
        self._page.click(f'li.tab.topmenu-tab i[data-entity-id="{p_id}"]')

        try:
            self._page.wait_for_selector(
                KYSelectors.Opgaveindbakke.VÆLG_OPGAVEPAKKE, timeout=5000
            )
            return True
        except PlaywrightTimeoutError:
            pass

        self._page.wait_for_selector(
            KYSelectors.Borgere.LUK_ALLE_OPGAVER_FORM, timeout=5000
        )

        for selector in (
            KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_GEM,
            KYSelectors.Borgere.LUK_ALLE_OPGAVER_AFBRYD_OG_GEM,
        ):
            if self._page.locator(selector).is_visible():
                self._page.click(selector, timeout=30000)
                return False

        raise PlaywrightTimeoutError(
            "Close-all-tasks popup was shown, but no known 'Afbryd og gem' button was visible."
        )

    def er_borger_låst(self, cpr: str) -> bool:
        naviger_til_borger(self._page, cpr, timeout=30000)
        return self._page.locator(KYSelectors.Borgere.LÅST_BANNER).is_visible()

    def hent_ferieoplysninger(self, cpr: str) -> dict:
        naviger_til_borger(self._page, cpr, timeout=30000)
        navigate_to(
            self._page,
            KYSelectors.Borgere.FERIE,
            KYSelectors.Borgere.FERIEPERIODER_TIL_BEREGNING,
        )

        data = {
            "Ferieperioder til beregning": extract_header_table(
                self._page, KYSelectors.Borgere.FERIEPERIODER_TIL_BEREGNING
            ),
            "Fravær fra Jobcenter": extract_header_table(
                self._page, KYSelectors.Borgere.FRAVÆR_FRA_JOBCENTER
            ),
            "Feriekonto": extract_header_table(
                self._page, KYSelectors.Borgere.FERIEKONTO
            ),
            "Ferieperioder fra Feriekonto": extract_header_table(
                self._page, KYSelectors.Borgere.FERIEPERIODER_FRA_FERIEKONTO
            ),
        }

        return data

    def hent_skatteoplysninger(self, cpr: str) -> dict:
        naviger_til_borger(self._page, cpr, timeout=30000)
        navigate_to(
            self._page,
            KYSelectors.Borgere.SKAT,
            KYSelectors.Borgere.SKATTEKORT_FRA_EINDKOMST,
        )

        data = {
            "Skattekort fra eIndkomst": extract_header_table(
                self._page, KYSelectors.Borgere.SKATTEKORT_FRA_EINDKOMST
            ),
            "Kommende skatteindberetninger": extract_header_table(
                self._page, KYSelectors.Borgere.KOMMENDE_SKATTEINDBERETNINGER
            ),
            "Historiske skatteindberetninger": extract_header_table(
                self._page, KYSelectors.Borgere.HISTORISKE_SKATTEINDBERETNINGER
            ),
            "Overskydende skat": extract_keyed_table(
                self._page, KYSelectors.Borgere.OVERSKYDENDE_SKAT
            ),
            "Skat": extract_keyed_table(
                self._page, KYSelectors.Borgere.OPLYSNINGER_SKAT
            ),
        }

        return data

    def upload_dokument(self, cpr: str, sagsnøgle: str, file_path: Path) -> None:
        naviger_til_borger(self._page, cpr, timeout=30000)
        self._page.wait_for_selector(KYSelectors.Borgere.SAGSOVERSIGT, timeout=30000)
        sag_row = self._page.locator(
            f"{KYSelectors.Borgere.SAGSOVERSIGT} tbody tr", has_text=sagsnøgle
        ).first

        if sag_row.count() == 0:
            raise ValueError(f"Kunne ikke finde sag med sagsnøgle: {sagsnøgle}")

        row_click_target = sag_row.locator("a, button, td:not(.handlinger)").first
        if row_click_target.count() > 0:
            row_click_target.click()
        else:
            sag_row.click()

        self._page.click(
            "button[onclick=\"loadGenericModal('/entitet/sag/uploadfilesModal');\"]"
        )
        upload_file = file_path.resolve()
        self._page.set_input_files("input.upload-input[name='file']", str(upload_file))
        self._page.click(
            "button.btn-submit-form[data-url='/entitet/sag/submitUploads/']"
        )

    def indtast_indtægter(
        self,
        cpr: str,
        indtægter: Indtægter,
        journalnotat: Journalnotat | None = None,
    ) -> None:
        naviger_til_borger(self._page, cpr, timeout=30000)
        self._page.locator(KYSelectors.Borgere.HANDLINGER_DROPDOWN).click(timeout=30000)
        self._page.locator(KYSelectors.Borgere.HANDLINGER_SUBPROCESSER).click(
            timeout=30000
        )
        self._page.locator(KYSelectors.Borgere.HANDLINGER_SUBPROCESSER_INDTÆGTER).click(
            timeout=30000
        )
        self._page.locator(KYSelectors.Borgere.INDTÆGTER_MANUEL_INDTASTNING).click(
            timeout=30000
        )

        # Fill all possible fields if present, using selectors from KYSelectors.Borgere
        if indtægter.cvr_se_nummer:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_CVR_SE_NUMMER, indtægter.cvr_se_nummer
            )
        if indtægter.virksomhedsnavn:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_VIRKSOMHEDSNAVN,
                indtægter.virksomhedsnavn,
            )
        if indtægter.indtaegtstype:
            self._page.wait_for_selector(
                KYSelectors.Borgere.INDTÆGTER_TYPE, timeout=30000
            )
            self._page.select_option(
                KYSelectors.Borgere.INDTÆGTER_TYPE, label=indtægter.indtaegtstype.value
            )
        if indtægter.beloeb is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_BELOEB,
                _to_danish_decimal(indtægter.beloeb.quantize(Decimal("0.01"))),
            )
        if indtægter.dispositionsdato:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_DISPOSITIONSDATO,
                indtægter.dispositionsdato,
            )
        if indtægter.periode_fra:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_PERIODE_FRA, indtægter.periode_fra
            )
        if indtægter.periode_til:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_PERIODE_TIL, indtægter.periode_til
            )
        if indtægter.pensionsbidrag_eget is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_PENSIONSBIDRAG_EGET,
                _to_danish_decimal(indtægter.pensionsbidrag_eget),
            )
        if indtægter.pensionsbidrag_arbejdsgiver is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_PENSIONSBIDRAG_ARBEJDSGIVER,
                _to_danish_decimal(indtægter.pensionsbidrag_arbejdsgiver),
            )
        if indtægter.atp_bidrag_eget is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_ATP_BIDRAG_EGET,
                _to_danish_decimal(indtægter.atp_bidrag_eget),
            )
        if indtægter.atp_bidrag_arbejdsgiver is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_ATP_BIDRAG_ARBEJDSGIVER,
                _to_danish_decimal(indtægter.atp_bidrag_arbejdsgiver),
            )
        if indtægter.am_bidrag is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_AM_BIDRAG,
                _to_danish_decimal(indtægter.am_bidrag),
            )
        if indtægter.timer_i_perioden is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_TIMER_I_PERIODEN,
                str(indtægter.timer_i_perioden),
            )
        if indtægter.nettoferiepenge is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_NETTOFERIEPENGE,
                _to_danish_decimal(indtægter.nettoferiepenge),
            )
        if indtægter.bruttoficerede_nettoferiepenge is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_BRUTTOFICEREDE_NETTOFERIEPENGE,
                _to_danish_decimal(indtægter.bruttoficerede_nettoferiepenge),
            )
        if indtægter.bruttoferiepenge_timeloennede is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_BRUTTOFERIEPENGE_TIMELOENNDE,
                _to_danish_decimal(indtægter.bruttoferiepenge_timeloennede),
            )
        if indtægter.a_indkomst_som_feriepenge is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_A_INDKOMST_SOM_FERIEPENGE,
                _to_danish_decimal(indtægter.a_indkomst_som_feriepenge),
            )
        if indtægter.soegne_og_helligdagsbetaling is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_SOEGNE_OG_HELLIGDAGSBETALING,
                _to_danish_decimal(indtægter.soegne_og_helligdagsbetaling),
            )
        if indtægter.fri_kost_og_logi is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_FRI_KOST_OG_LOGI,
                _to_danish_decimal(indtægter.fri_kost_og_logi),
            )
        if indtægter.fri_bil is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_FRI_BIL,
                _to_danish_decimal(indtægter.fri_bil),
            )
        if indtægter.fri_telefon is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_FRI_TELEFON,
                _to_danish_decimal(indtægter.fri_telefon),
            )
        if indtægter.sundhedsforsikring_og_gruppeliv is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_SUNDHEDSFORSIKRING_OG_GRUPPELIV,
                _to_danish_decimal(indtægter.sundhedsforsikring_og_gruppeliv),
            )
        if indtægter.skattefri_rejse_og_befordringsgodtgoerelse is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_SKATTEFRI_REJSE_OG_BEFORDRINGSGODTGOERELSE,
                _to_danish_decimal(
                    indtægter.skattefri_rejse_og_befordringsgodtgoerelse
                ),
            )
        if indtægter.opsparet_feriefridage is not None:
            self._page.fill(
                KYSelectors.Borgere.INDTÆGTER_OPSPARET_FERIEFRIDAGE,
                _to_danish_decimal(indtægter.opsparet_feriefridage),
            )
        if indtægter.ydelsesarter:
            self._page.wait_for_selector(
                KYSelectors.Borgere.INDTÆGTER_YDELSESARTER, timeout=30000
            )
            self._page.select_option(
                KYSelectors.Borgere.INDTÆGTER_YDELSESARTER,
                label=indtægter.ydelsesarter.value,
            )

        self._page.locator(KYSelectors.Borgere.INDTÆGTER_GEM).click(timeout=30000)
        # GEM is accepted when the indtægter form closes and fields disappear
        self._page.wait_for_selector(
            KYSelectors.Borgere.INDTÆGTER_BELOEB, state="hidden", timeout=30000
        )

        if journalnotat:
            self._opret_journalnotat(journalnotat)

        self._page.locator(KYSelectors.Borgere.INDTÆGTER_GODKEND).click(timeout=30000)
        # Wait for Godkend to disappear before clicking Luk to avoid async race
        # TODO: Endless spinner appeared here.
        self._page.wait_for_selector(
            KYSelectors.Borgere.INDTÆGTER_GODKEND, state="detached", timeout=30000
        )
        self._wait_for_opgave_loader_to_clear(timeout=30000)
        self._page.locator(KYSelectors.Borgere.INDTÆGTER_LUK).click(timeout=30000)
        self._page.wait_for_selector(
            KYSelectors.Borgere.UBEHANDLEDE_OPGAVER, timeout=30000
        )

    def åbn_opgave(self, cpr: str, opgave_id: str) -> list[dict]:
        naviger_til_borger(self._page, cpr, timeout=30000)
        self._page.click(
            f'{KYSelectors.Borgere.UBEHANDLEDE_OPGAVER} tbody tr[data-id="{opgave_id}"]',
            timeout=30000,
        )

        self._page.wait_for_selector("div#initierende_haendelser", timeout=30000)
        self._page.wait_for_selector(
            "table#initierende-haendelser-table", timeout=30000
        )

        return extract_header_table(self._page, "table#initierende-haendelser-table")

    def åben_opgave_og_hent_info(
        self, cpr: str, opgave_id: str, tabelnavn: str
    ) -> dict:
        """Åbner en opgave og returnerer indholdet af en navngiven tabel samt de initierende hændelser.

        Args:
            cpr: CPR-nummer på den borger hvis opgave skal åbnes.
            opgave_id: ID på den opgave der skal åbnes (data-id på tabelrækken).
            tabelnavn: Den synlige overskrift på den tabel der skal hentes, f.eks.
                "Løbende indtægter der skal medtages ved beregning".

        Returns:
            En dict med to nøgler:
                - "Initierende hændelser": list[dict] med rækkerne fra initierende-haendelser-table.
                - tabelnavn: list[dict] med rækkerne fra den fundne tabel.

        Raises:
            ValueError: Hvis ingen tabel med den givne overskrift findes på siden.
        """
        if self._page.locator("div#initierende_haendelser").count() == 0:
            initierende_hændelser = self.åbn_opgave(cpr, opgave_id)
        else:
            extract_header_table(self._page, "table#initierende-haendelser-table")

        table_id = self._page.evaluate(
            """(tabelnavn) => {
                const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const wanted = normalize(tabelnavn);
                const headers = document.querySelectorAll('span.header-title');
                for (const header of headers) {
                    if (normalize(header.textContent) === wanted) {
                        const blockHeading = header.closest('.block-heading');
                        if (blockHeading) {
                            const resetLink = blockHeading.querySelector('a.reset-table[data-table-id]');
                            if (resetLink) {
                                return resetLink.getAttribute('data-table-id');
                            }
                        }
                    }
                }
                return null;
            }""",
            tabelnavn,
        )

        if not table_id:
            raise ValueError(f"Kunne ikke finde tabel med header: '{tabelnavn}'")

        return {
            "Initierende hændelser": initierende_hændelser,
            tabelnavn: extract_header_table(self._page, f"table#{table_id}"),
        }

    def afbryd_opgave(self, cpr: str, opgave_id: str, afbryd_type: AfbrydType) -> None:
        if self._page.locator("div#initierende_haendelser").count() == 0:
            self.åbn_opgave(cpr, opgave_id)
        self._page.click(KYSelectors.Borgere.AFBRYD_OPGAVE_AABN_MODAL, timeout=30000)

        afbryd_selector_map = {
            AfbrydType.AFBRYD: KYSelectors.Borgere.AFBRYD_OPGAVE_ANNULLER,
            AfbrydType.AFBRYD_OG_SLET: KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_SLET,
            AfbrydType.AFBRYD_OG_GEM: KYSelectors.Borgere.AFBRYD_OPGAVE_AFBRYD_OG_GEM,
        }
        selected_selector = afbryd_selector_map[afbryd_type]
        try:
            # Some afbryd flows complete immediately; only click modal option when popup appears.
            self._page.wait_for_selector(selected_selector, timeout=3000)
            self._page.click(selected_selector, timeout=30000)
        except PlaywrightTimeoutError:
            pass

    def godkend_opgave(self, cpr: str, opgave_id: str) -> None:
        if self._page.locator("div#initierende_haendelser").count() == 0:
            self.åbn_opgave(cpr, opgave_id)

        # Some tasks require multiple approval steps before the close action is available.
        for _ in range(10):
            try:
                self._page.wait_for_selector(
                    KYSelectors.Borgere.GODKEND_OPGAVE_LUK,
                    timeout=1500,
                )
                self._wait_for_opgave_loader_to_clear(timeout=30000)
                self._page.click(KYSelectors.Borgere.GODKEND_OPGAVE_LUK, timeout=30000)
                return
            except PlaywrightTimeoutError:
                self._page.click(
                    KYSelectors.Borgere.GODKEND_OPGAVE_GODKEND,
                    timeout=30000,
                )

        raise RuntimeError(
            "Kunne ikke afslutte opgaven: 'Luk' knappen blev ikke tilgængelig"
        )

    def rediger_opgave(
        self, cpr: str, opgave_id: str, ændringer: RedigerOpgave
    ) -> None:
        naviger_til_borger(self._page, cpr, timeout=30000)
        self._page.click(
            f'{KYSelectors.Borgere.UBEHANDLEDE_OPGAVER} tbody tr[data-id="{opgave_id}"] a.overblik-modal-button[data-target="#opgaveEditForm"]',
            timeout=30000,
        )

        # Wait for the edit form to be open before filling fields.
        self._page.wait_for_selector("select#priority", timeout=30000)

        # Mandatory fields
        if not ændringer.forfalds_dato:
            raise ValueError("forfalds_dato er påkrævet for redigering af opgave")

        self._select_styled_or_native_dropdown("select#priority", ændringer.prioritet)
        self._page.fill("input#command\\.forfaldsdato", ændringer.forfalds_dato)
        self._select_styled_or_native_dropdown(
            "select#opgaveFrekvens", ændringer.frekvens
        )

        # Optional fields
        if ændringer.opfølgningsopgavetype:
            self._select_styled_or_native_dropdown(
                "select#subType", ændringer.opfølgningsopgavetype
            )

        if ændringer.sagsbehandler:
            sagsbehandler_input = self._page.locator("input#typeahead")
            sagsbehandler_input.fill(ændringer.sagsbehandler)
            # Typeahead usually supports keyboard confirmation when only one hit exists.
            sagsbehandler_input.press("ArrowDown")
            sagsbehandler_input.press("Enter")

        self._page.click(KYSelectors.Borgere.REDIGER_OPGAVE_GEM, timeout=30000)
        self._page.wait_for_selector(
            KYSelectors.Borgere.REDIGER_OPGAVE_LUK, timeout=30000
        )
        self._page.click(KYSelectors.Borgere.REDIGER_OPGAVE_LUK, timeout=30000)

    # TODO: Slet opgave

    def _select_styled_or_native_dropdown(
        self, select_selector: str, option_label: str
    ) -> None:
        """Select option via JS-styled dropdown UI when present, else fall back to native select."""
        select_locator = self._page.locator(select_selector)
        select_locator.wait_for(state="visible", timeout=30000)

        option_value = self._page.eval_on_selector(
            select_selector,
            """(el, targetLabel) => {
                const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const wanted = normalize(targetLabel);
                const options = Array.from(el.options || []);
                const exact = options.find(o => normalize(o.text) === wanted);
                if (exact) return exact.value;
                const contains = options.find(o => normalize(o.text).includes(wanted));
                return contains ? contains.value : null;
            }""",
            option_label,
        )
        if option_value is None:
            raise ValueError(
                f"Kunne ikke finde option '{option_label}' i dropdown {select_selector}"
            )

        # Preferred path for bootstrap-select widgets: set through plugin API and fire events.
        did_set_via_plugin = self._page.evaluate(
            """({ selector, value }) => {
                const el = document.querySelector(selector);
                if (!el) return false;
                const jq = window.jQuery || window.$;
                if (jq && typeof jq(el).selectpicker === 'function') {
                    jq(el).selectpicker('val', value);
                    jq(el).trigger('changed.bs.select');
                    jq(el).trigger('change');
                    return true;
                }
                return false;
            }""",
            {"selector": select_selector, "value": option_value},
        )
        if did_set_via_plugin:
            return

        # Try styled dropdown first (commonly rendered as bootstrap-select button[data-id=<select-id>]).
        select_id = self._page.eval_on_selector(select_selector, "el => el.id")
        if select_id:
            styled_button = self._page.locator(
                f'button.dropdown-toggle[data-id="{select_id}"]'
            )
            if styled_button.count() > 0:
                styled_button.first.click(timeout=30000)
                option_in_open_menu = self._page.locator(
                    ".bootstrap-select.open .dropdown-menu.inner li a span.text",
                    has_text=re.compile(rf"^\\s*{re.escape(option_label)}\\s*$"),
                ).first
                if option_in_open_menu.count() > 0:
                    option_in_open_menu.click(timeout=30000)
                    return

        # Fallback for native select controls.
        self._page.select_option(select_selector, value=option_value)
        self._page.eval_on_selector(
            select_selector,
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
        )


def _to_danish_decimal(val: float | Decimal) -> str:
    # Converts 6509.73 -> '6.509,73' (Danish format)
    return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
