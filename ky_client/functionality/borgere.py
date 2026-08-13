import re
from decimal import Decimal
from pathlib import Path

from playwright.async_api import Page as AsyncPage
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

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

    def aabn_dokumenter_panel(self, timeout: int = 120000):
        """Udvid Dokumenter-panelet og returner den synlige dokumenttabel."""

        toggle = self._page.locator(
            KYSelectors.Borgere.DOKUMENTER_TOGGLE
        ).first

        toggle.wait_for(
            state="visible",
            timeout=timeout,
        )
        toggle.scroll_into_view_if_needed()

        synlig_tekst = re.sub(
            r"\s+",
            " ",
            toggle.inner_text(),
        ).strip()

        print()
        print("=" * 70)
        print("DOKUMENTER-KNAPPEN ER FUNDET")
        print(f"Synlig tekst: {synlig_tekst}")
        print(f"Aktiv URL: {self._page.url}")
        print("=" * 70)

        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click(timeout=30000)

        self._wait_for_toggle_expanded(
            toggle=toggle,
            timeout=30000,
        )

        print("Dokumenter-panelet er udvidet.")

        return self._wait_for_dokumenttabel(timeout=timeout)

    def aabn_pdf_dokument(
        self,
        dokumentnavn: str,
        timeout: int = 120000,
    ) -> Page:

        dokumentnavn = dokumentnavn.strip()

        if not dokumentnavn:
            raise ValueError("dokumentnavn må ikke være tomt.")

        table = self.aabn_dokumenter_panel(timeout=timeout)
        rows = table.locator(
            KYSelectors.Borgere.DOKUMENTER_RAEKKER
        )

        row_count = rows.count()

        print()
        print("=" * 70)
        print("SYNLIGE DOKUMENTRÆKKER")
        print(f"Antal rækker fundet: {row_count}")
        print(f"Søger efter: {dokumentnavn}")
        print("=" * 70)

        selected_row = None
        wanted_text = dokumentnavn.casefold()

        for index in range(row_count):
            row = rows.nth(index)

            try:
                if not row.is_visible():
                    continue

                row_text = re.sub(
                    r"\s+",
                    " ",
                    row.inner_text(timeout=5000),
                ).strip()
            except PlaywrightTimeoutError:
                print(f"Række {index + 1}: kunne ikke læses inden timeout")
                continue

            print(f"Række {index + 1}: {row_text}")

            if wanted_text in row_text.casefold():
                selected_row = row
                print(
                    f"MATCH: Række {index + 1} indeholder "
                    f"'{dokumentnavn}'."
                )
                break

        if selected_row is None:
            raise ValueError(
                f"Kunne ikke finde en synlig dokumentrække med "
                f"'{dokumentnavn}'."
            )

        open_links = selected_row.locator(
            KYSelectors.Borgere.DOKUMENTER_AABN_LINK
        )

        selected_link = None

        for index in range(open_links.count()):
            link = open_links.nth(index)

            if not link.is_visible():
                continue

            if not link.is_enabled():
                continue

            link_text = re.sub(
                r"\s+",
                " ",
                link.inner_text(timeout=5000),
            ).strip()

            print(
                f"Link {index + 1} i dokumentrækken: "
                f"{link_text or '[uden synlig tekst]'}"
            )

            if "åbn dokument" in link_text.casefold():
                selected_link = link
                break

        if selected_link is None:
            raise ValueError(
                f"Rækken med '{dokumentnavn}' indeholder ikke et synligt "
                "og aktivt link med teksten 'Åbn dokument'."
            )

        selected_link.scroll_into_view_if_needed()

        print()
        print("Klikker på 'Åbn dokument'...")
        print(f"URL før klik: {self._page.url}")

        with self._page.context.expect_page(timeout=timeout) as page_info:
            selected_link.click(timeout=30000)

        pdf_page = page_info.value
        pdf_page.bring_to_front()

        self._wait_for_pdf_url(
            pdf_page=pdf_page,
            timeout=timeout,
        )

        try:
            pdf_page.wait_for_load_state(
                "domcontentloaded",
                timeout=timeout,
            )
        except PlaywrightTimeoutError:
            # Chromium PDF-vieweren sender ikke altid domcontentloaded.
            pass

        pdf_page.bring_to_front()

        print()
        print("=" * 70)
        print("PDF-DOKUMENTET ER ÅBNET")
        print(f"Dokumentnavn: {dokumentnavn}")
        print(f"Ny PDF-URL: {pdf_page.url}")
        print("=" * 70)

        return pdf_page

    def _wait_for_dokumenttabel(self, timeout: int = 120000):
        """Vent på dokumenttabellen og print alle synlige rækker."""

        elapsed_ms = 0
        poll_interval_ms = 500

        while elapsed_ms < timeout:
            tables = self._page.locator(
                KYSelectors.Borgere.DOKUMENTER_TABELLER
            )

            for table_index in range(tables.count()):
                table = tables.nth(table_index)

                try:
                    if not table.is_visible():
                        continue

                    rows = table.locator(
                        KYSelectors.Borgere.DOKUMENTER_RAEKKER
                    )

                    visible_row_texts = []

                    for row_index in range(rows.count()):
                        row = rows.nth(row_index)

                        if not row.is_visible():
                            continue

                        row_text = re.sub(
                            r"\s+",
                            " ",
                            row.inner_text(timeout=5000),
                        ).strip()

                        if row_text:
                            visible_row_texts.append(row_text)

                    if not visible_row_texts:
                        continue

                    joined_text = " ".join(visible_row_texts).casefold()

                    looks_like_document_table = any(
                        marker in joined_text
                        for marker in (
                            "åbn dokument",
                            "dokumentnavn",
                            "kontrolark p10",
                        )
                    )

                    if not looks_like_document_table:
                        continue

                    print()
                    print("=" * 70)
                    print("DOKUMENTTABEL FUNDET")
                    print(f"Tabelnummer: {table_index + 1}")
                    print(f"Synlige rækker: {len(visible_row_texts)}")

                    for row_number, row_text in enumerate(
                        visible_row_texts,
                        start=1,
                    ):
                        print(f"  {row_number}: {row_text}")

                    print("=" * 70)
                    return table

                except PlaywrightTimeoutError:
                    continue

            self._page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        raise PlaywrightTimeoutError(
            "Dokumenttabellen blev ikke synlig med læsbare rækker inden "
            f"for {timeout / 1000:.0f} sekunder. URL: {self._page.url}"
        )

    def _wait_for_toggle_expanded(
        self,
        toggle,
        timeout: int,
    ) -> None:
        """Vent på at Dokumenter-panelet har aria-expanded=true."""

        elapsed_ms = 0
        poll_interval_ms = 250

        while elapsed_ms < timeout:
            try:
                if toggle.get_attribute("aria-expanded") == "true":
                    return
            except PlaywrightTimeoutError:
                pass

            self._page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        raise PlaywrightTimeoutError(
            "Dokumenter-panelet blev ikke udvidet efter klik."
        )

    def _wait_for_pdf_url(
        self,
        pdf_page: Page,
        timeout: int,
    ) -> None:
        """Vent på at den nye PDF-fane får en reel URL, og print URL-skift."""

        elapsed_ms = 0
        poll_interval_ms = 250
        previous_url = pdf_page.url

        print(f"PDF-fanens start-URL: {previous_url}")

        while elapsed_ms < timeout:
            if pdf_page.is_closed():
                raise RuntimeError(
                    "PDF-browserfanen blev lukket under åbningen."
                )

            current_url = pdf_page.url

            if current_url != previous_url:
                print(
                    "PDF-URL skiftede: "
                    f"{previous_url} -> {current_url}"
                )
                previous_url = current_url

            if current_url not in ("", "about:blank"):
                print(f"PDF-URL er indlæst: {current_url}")
                return

            pdf_page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        raise PlaywrightTimeoutError(
            "PDF-browserfanen fik ikke en reel URL inden for "
            f"{timeout / 1000:.0f} sekunder."
        )

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

"""Indsæt metoderne i den eksisterende BorgereClient-klasse i borgere.py.

Forudsætninger i borgere.py:
    import re
    from playwright.sync_api import Page
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from ky_client.selectors import KYSelectors

Funktionen går ud fra, at borgeren allerede er fremsøgt og aktiv i KY.
"""

def opret_opfoelgningsopgave(
        self,
        opfoelgningstype: str,
        opfoelgningsdato: str,
        sagsbehandler: str,
        titel: str | None = None,
        frekvens: str | None = None,
        haendelsestype: str | None = None,
        beskrivelse: str | None = None,
        journalnotat: Journalnotat | None = None,
        timeout: int = 120000,
    ) -> None:
        """Opret en opfølgningsopgave på den allerede fremsøgte borger.

        Args:
            opfoelgningstype:
                Den synlige tekst eller option-værdi i feltet
                ``select#opfoelgningsType``. Eksempler:
                - ``Ingen valgt``
                - ``Brugerdefineret``
                - ``Udbetal Refusion til arbejdsgiver``
                - ``manuel``
                - ``standard_opfoelgningsopgave-OJ0441``
            opfoelgningsdato:
                Dato til ``input#command\\.opfoelgningsdato``.
            sagsbehandler:
                Det fulde synlige navn, som skrives i ``input#typeahead``.
                Funktionen klikker derefter et præcist match i typeahead-listen.
            titel:
                Titel til ``input#title``. Kræves ved Brugerdefineret.
            frekvens:
                Synlig tekst eller værdi i ``select#frekvens``.
                Eksempler: ``Aldrig``, ``NEVER``, ``Månedligt``, ``MONTHLY``.
                Kræves ved Brugerdefineret.
            haendelsestype:
                Synlig tekst eller værdi i ``select#haendelseType``.
                Eksempler: ``Ingen valgt``, ``Skriv journalnotat`` eller
                ``HD_SKRIV_JOURNALNOTAT``.
            beskrivelse:
                Tekst til ``textarea#beskrivelse``.
            journalnotat:
                Eksisterende ``Journalnotat``-model. Journalnotat-sektionen
                udvides og udfyldes kun, når et objekt angives.
            timeout:
                Maksimal ventetid i millisekunder.
        """

        opfoelgningstype = opfoelgningstype.strip()
        opfoelgningsdato = opfoelgningsdato.strip()
        sagsbehandler = sagsbehandler.strip()
        titel = titel.strip() if titel else None
        frekvens = frekvens.strip() if frekvens else None
        haendelsestype = haendelsestype.strip() if haendelsestype else None
        beskrivelse = beskrivelse.strip() if beskrivelse else None

        if not opfoelgningstype:
            raise ValueError("opfoelgningstype må ikke være tom.")
        if not opfoelgningsdato:
            raise ValueError("opfoelgningsdato må ikke være tom.")
        if not sagsbehandler:
            raise ValueError("sagsbehandler må ikke være tom.")

        # ------------------------------------------------------
        # Handlinger > Administration > Opret opfølgningsopgave
        # ------------------------------------------------------

        handlinger = self._page.locator(
            KYSelectors.Borgere.HANDLINGER_DROPDOWN
        ).first
        handlinger.wait_for(state="visible", timeout=timeout)
        handlinger.scroll_into_view_if_needed()
        handlinger.click(timeout=30000)

        administration = self._find_handlinger_menu_item_sync(
            text="Administration",
            timeout=timeout,
        )
        administration.click(timeout=30000)

        opret_opgave = self._find_handlinger_menu_item_sync(
            text="Opret opfølgningsopgave",
            timeout=timeout,
        )
        opret_opgave.click(timeout=30000)

        # ------------------------------------------------------
        # Vent på at opgave-loaderen er væk og formularen er klar
        # ------------------------------------------------------

        self._wait_for_empty_opgave_loader_to_clear(timeout=timeout)

        opfoelgningstype_selector = "select#opfoelgningsType"
        self._page.locator(opfoelgningstype_selector).wait_for(
            state="attached",
            timeout=timeout,
        )

        self._select_option_by_value_or_label(
            selector=opfoelgningstype_selector,
            option=opfoelgningstype,
        )

        # Vent på at KY har reageret på opfølgningstypen.
        self._page.wait_for_timeout(500)
        self._wait_for_empty_opgave_loader_to_clear(timeout=timeout)

        is_custom = self._opfoelgningstype_er_brugerdefineret(
            opfoelgningstype_selector
        )

        # ------------------------------------------------------
        # Fælles obligatoriske felter
        # ------------------------------------------------------

        dato_input = self._page.locator(
            "input#command\\.opfoelgningsdato"
        ).first
        dato_input.wait_for(state="visible", timeout=timeout)
        dato_input.fill(opfoelgningsdato)

        self._vaelg_sagsbehandler_fra_typeahead(
            sagsbehandler=sagsbehandler,
            timeout=timeout,
        )

        # ------------------------------------------------------
        # Ekstra felter ved Brugerdefineret
        # ------------------------------------------------------

        if is_custom:
            if not titel:
                raise ValueError(
                    "titel er påkrævet, når opfoelgningstype er "
                    "Brugerdefineret."
                )
            if not frekvens:
                raise ValueError(
                    "frekvens er påkrævet, når opfoelgningstype er "
                    "Brugerdefineret."
                )

            title_input = self._page.locator("input#title").first
            title_input.wait_for(state="visible", timeout=timeout)
            title_input.fill(titel)

            self._select_option_by_value_or_label(
                selector="select#frekvens",
                option=frekvens,
            )

            if haendelsestype:
                self._select_option_by_value_or_label(
                    selector="select#haendelseType",
                    option=haendelsestype,
                )

            if beskrivelse:
                beskrivelse_input = self._page.locator(
                    "textarea#beskrivelse"
                ).first
                beskrivelse_input.wait_for(
                    state="visible",
                    timeout=timeout,
                )
                beskrivelse_input.fill(beskrivelse)

            # Genbrug den eksisterende journalnotatfunktion. Sektionen
            # udvides ikke, hvis journalnotat ikke er angivet.
            if journalnotat is not None:
                self._opret_journalnotat(journalnotat)

        elif any(
            value is not None
            for value in (
                titel,
                frekvens,
                haendelsestype,
                beskrivelse,
                journalnotat,
            )
        ):
            raise ValueError(
                "titel, frekvens, haendelsestype, beskrivelse og "
                "journalnotat må kun angives ved Brugerdefineret."
            )

        # ------------------------------------------------------
        # Gem
        # ------------------------------------------------------

        gem = self._find_visible_submit_button(
            text="Gem",
            timeout=timeout,
        )
        gem.scroll_into_view_if_needed()
        gem.click(timeout=30000)

        self._wait_for_empty_opgave_loader_to_clear(timeout=timeout)

        # Succes: formularen eller Gem-knappen forsvinder. Hvis KY viser en
        # valideringsfejl, vil knappen typisk forblive synlig.
        try:
            gem.wait_for(state="hidden", timeout=30000)
        except PlaywrightTimeoutError:
            validation_text = self._visible_validation_text()
            raise PlaywrightTimeoutError(
                "Opfølgningsopgaven blev ikke gemt. "
                f"Synlig validering: {validation_text or 'ukendt fejl'}"
            )

def _find_handlinger_menu_item_sync(
        self,
        text: str,
        timeout: int,
    ):
        """Find et menupunkt kun inde i Handlinger-dropdownen."""

        wanted = re.compile(
            rf"^\\s*{re.escape(text)}\\s*$",
            re.IGNORECASE,
        )
        elapsed = 0
        poll_interval = 250

        while elapsed < timeout:
            container = self._page.locator(
                "li#handlinger-dropdown"
            ).first

            if container.count() > 0:
                candidates = container.locator(
                    "a, button, [role='menuitem']",
                    has_text=wanted,
                )

                for index in range(candidates.count()):
                    candidate = candidates.nth(index)

                    try:
                        if not candidate.is_visible():
                            continue
                        if not candidate.is_enabled():
                            continue

                        visible_text = re.sub(
                            r"\\s+",
                            " ",
                            candidate.inner_text(timeout=5000),
                        ).strip()

                        if wanted.fullmatch(visible_text):
                            return candidate
                    except PlaywrightTimeoutError:
                        continue

            self._page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

        raise PlaywrightTimeoutError(
            f"Menupunktet '{text}' blev ikke fundet inde i "
            "Handlinger-dropdownen."
        )

def _wait_for_empty_opgave_loader_to_clear(
        self,
        timeout: int = 120000,
    ) -> None:
        """Vent på at #empty_opgave_loader er skjult eller fjernet."""

        self._page.wait_for_function(
            """
            () => {
                const loader = document.querySelector('#empty_opgave_loader');

                if (!loader) {
                    return true;
                }

                const style = window.getComputedStyle(loader);

                return (
                    style.display === 'none' ||
                    style.visibility === 'hidden' ||
                    style.opacity === '0' ||
                    loader.offsetParent === null
                );
            }
            """,
            timeout=timeout,
        )

def _select_option_by_value_or_label(
        self,
        selector: str,
        option: str,
    ) -> None:
        """Vælg en option via value, label eller normaliseret tekst."""

        select = self._page.locator(selector).first
        select.wait_for(state="attached", timeout=30000)

        option = option.strip()
        options = select.locator("option")
        selected_value = None

        for index in range(options.count()):
            current = options.nth(index)
            value = current.get_attribute("value") or ""
            label = re.sub(
                r"\\s+",
                " ",
                current.inner_text(timeout=5000),
            ).strip()

            if (
                value.casefold() == option.casefold()
                or label.casefold() == option.casefold()
            ):
                selected_value = value
                break

        if selected_value is None:
            available = [
                re.sub(
                    r"\\s+",
                    " ",
                    options.nth(index).inner_text(timeout=5000),
                ).strip()
                for index in range(options.count())
            ]
            raise ValueError(
                f"Kunne ikke finde '{option}' i {selector}. "
                f"Muligheder: {available}"
            )

        select.select_option(value=selected_value)
        select.dispatch_event("input")
        select.dispatch_event("change")

        # Understøt bootstrap-select, hvis KY har erstattet native select.
        self._page.evaluate(
            """
            ({ selector, value }) => {
                const element = document.querySelector(selector);
                const jq = window.jQuery || window.$;

                if (
                    element &&
                    jq &&
                    typeof jq(element).selectpicker === 'function'
                ) {
                    jq(element).selectpicker('val', value);
                    jq(element).trigger('changed.bs.select');
                    jq(element).trigger('change');
                }
            }
            """,
            {
                "selector": selector,
                "value": selected_value,
            },
        )

def _opfoelgningstype_er_brugerdefineret(
        self,
        selector: str,
    ) -> bool:
        """Kontrollér den faktisk valgte option i Opfølgningstype."""

        selected = self._page.locator(
            f"{selector} option:checked"
        ).first
        value = selected.get_attribute("value") or ""
        label = selected.inner_text(timeout=5000).strip()

        return (
            value.casefold() == "manuel"
            or label.casefold() == "brugerdefineret"
        )

def _vaelg_sagsbehandler_fra_typeahead(
        self,
        sagsbehandler: str,
        timeout: int,
    ) -> None:
        """Skriv fuldt navn og klik den matchende typeahead-mulighed."""

        typeahead = self._page.locator("input#typeahead").first
        typeahead.wait_for(state="visible", timeout=timeout)
        typeahead.fill(sagsbehandler)

        wanted = re.compile(
            rf"^\\s*{re.escape(sagsbehandler)}(?:\\s|\\().*$",
            re.IGNORECASE,
        )
        elapsed = 0
        poll_interval = 250

        while elapsed < timeout:
            suggestions = self._page.locator(
                ".tt-menu:visible .tt-suggestion.tt-selectable"
            )

            for index in range(suggestions.count()):
                suggestion = suggestions.nth(index)

                try:
                    if not suggestion.is_visible():
                        continue

                    suggestion_text = re.sub(
                        r"\\s+",
                        " ",
                        suggestion.inner_text(timeout=5000),
                    ).strip()

                    if wanted.match(suggestion_text):
                        suggestion.click(timeout=30000)
                        return
                except PlaywrightTimeoutError:
                    continue

            self._page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

        raise PlaywrightTimeoutError(
            "Kunne ikke finde sagsbehandleren i typeahead-listen: "
            f"{sagsbehandler}"
        )

def _find_visible_submit_button(
        self,
        text: str,
        timeout: int,
    ):
        """Find en synlig Gem-knap i den aktive opfølgningsformular."""

        wanted = re.compile(
            rf"^\\s*{re.escape(text)}\\s*$",
            re.IGNORECASE,
        )
        elapsed = 0
        poll_interval = 250

        while elapsed < timeout:
            candidates = self._page.locator(
                "button[type='submit'], input[type='submit'], "
                "button.btn-submit-form, a.btn-submit-form"
            )

            for index in range(candidates.count()):
                candidate = candidates.nth(index)

                try:
                    if not candidate.is_visible():
                        continue
                    if not candidate.is_enabled():
                        continue

                    visible_text = (
                        candidate.get_attribute("value")
                        or candidate.inner_text(timeout=5000)
                        or ""
                    )
                    visible_text = re.sub(
                        r"\\s+",
                        " ",
                        visible_text,
                    ).strip()

                    if wanted.fullmatch(visible_text):
                        return candidate
                except PlaywrightTimeoutError:
                    continue

            self._page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

        raise PlaywrightTimeoutError(
            "Kunne ikke finde en synlig og aktiv Gem-knap i "
            "opfølgningsformularen."
        )

def _visible_validation_text(self) -> str:
        """Returnér synlige valideringsbeskeder fra formularen."""

        messages = self._page.locator(
            ".has-error:visible, .help-block:visible, "
            ".alert-danger:visible, .field-validation-error:visible"
        )
        values = []

        for index in range(messages.count()):
            try:
                text = re.sub(
                    r"\\s+",
                    " ",
                    messages.nth(index).inner_text(timeout=3000),
                ).strip()
                if text and text not in values:
                    values.append(text)
            except PlaywrightTimeoutError:
                continue

        return " | ".join(values)

async def naviger_til_borger_async(
    page: AsyncPage,
    cpr: str,
    timeout: int = 120_000,
) -> str:
    """Fremsøg en borger på en eksisterende async KY-side.

    Funktionen indsætter CPR i topsearch, venter på PERSON_OPLYSNINGER,
    sammenligner CPR-værdien i tabellen med det fremsøgte CPR og returnerer
    den aktuelle borger-URL som ``borger_url``.
    """

    cpr = _normaliser_cpr_async(cpr)

    if not cpr.isdigit() or len(cpr) != 10:
        raise ValueError("CPR skal bestå af præcis 10 cifre.")

    if page.is_closed():
        raise RuntimeError("KY-siden er lukket før borgeropslaget.")

    search_input = page.locator(KYSelectors.Main.TOP_SEARCH).first
    await search_input.wait_for(state="visible", timeout=timeout)
    await search_input.scroll_into_view_if_needed()
    await search_input.fill(cpr)

    actual_cpr = _normaliser_cpr_async(await search_input.input_value())
    if actual_cpr != cpr:
        raise RuntimeError("CPR blev ikke indsat korrekt i topsearch.")

    await search_input.press("Enter")

    try:
        table = await _vent_paa_personoplysninger_async(
            page=page,
            timeout_ms=15_000,
        )
    except AsyncPlaywrightTimeoutError:
        # Første Enter kan kun vælge autocomplete-resultatet.
        await search_input.press("Enter")
        table = await _vent_paa_personoplysninger_async(
            page=page,
            timeout_ms=timeout,
        )

    cpr_fra_personoplysninger = await _hent_cpr_fra_personoplysninger_async(
        table
    )

    if cpr_fra_personoplysninger != cpr:
        raise RuntimeError(
            "Den viste borger matcher ikke det fremsøgte CPR."
        )

    borger_url = page.url
    if not borger_url:
        raise RuntimeError("Borgerens URL kunne ikke læses efter opslaget.")

    return borger_url


async def _vent_paa_personoplysninger_async(
    page: AsyncPage,
    timeout_ms: int,
):
    """Vent på en synlig PERSON_OPLYSNINGER-tabel med læsbare rækker."""

    elapsed_ms = 0
    poll_interval_ms = 250

    while elapsed_ms < timeout_ms:
        if page.is_closed():
            raise RuntimeError("KY-siden blev lukket under borgeropslaget.")

        for frame in page.frames:
            try:
                tables = frame.locator(
                    KYSelectors.Borgere.PERSON_OPLYSNINGER
                )
                for table_index in range(await tables.count()):
                    table = tables.nth(table_index)
                    if not await table.is_visible():
                        continue

                    rows = table.locator("tbody tr")
                    for row_index in range(await rows.count()):
                        row = rows.nth(row_index)
                        if await row.is_visible() and (await row.inner_text()).strip():
                            return table
            except Exception:
                continue

        await page.wait_for_timeout(poll_interval_ms)
        elapsed_ms += poll_interval_ms

    raise AsyncPlaywrightTimeoutError(
        "PERSON_OPLYSNINGER blev ikke synlig inden for "
        f"{timeout_ms / 1000:.0f} sekunder. URL: {page.url}"
    )


async def _hent_cpr_fra_personoplysninger_async(table) -> str:
    """Læs og normalisér CPR-værdien fra PERSON_OPLYSNINGER."""

    value = await table.evaluate(
        r"""
        table => {
            const normalize = value =>
                (value || '').replace(/\s+/g, ' ').trim();
            const labels = new Set([
                'cpr', 'cpr-nummer', 'cpr nummer', 'personnummer'
            ]);

            for (const row of table.querySelectorAll('tbody tr')) {
                const cells = Array.from(
                    row.querySelectorAll('th, td:not(.handlinger)')
                ).map(cell => normalize(cell.innerText));

                for (let index = 0; index < cells.length - 1; index += 1) {
                    const label = cells[index]
                        .replace(/:$/, '')
                        .trim()
                        .toLocaleLowerCase('da-DK');
                    if (labels.has(label) && cells[index + 1]) {
                        return cells[index + 1];
                    }
                }
            }
            return null;
        }
        """
    )

    cpr = _normaliser_cpr_async(str(value or ""))
    if not cpr.isdigit() or len(cpr) != 10:
        raise RuntimeError(
            "CPR-rækken kunne ikke læses korrekt fra PERSON_OPLYSNINGER."
        )
    return cpr


def _normaliser_cpr_async(value: str) -> str:
    """Fjern alle tegn, der ikke er cifre."""

    return re.sub(r"\D", "", value)

