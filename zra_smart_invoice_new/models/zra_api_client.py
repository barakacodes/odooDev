# -*- coding: utf-8 -*-
import requests
import json
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ── ZRA tax categories, per VSDC API Specification v1.0.7 §5.8 / §6.1 ────────
# "VAT-style" categories share the item-level `vatCatCd` field and the
# taxblAmt{X}/taxAmt{X}/taxRt{X} header buckets below (X in this list,
# including F = "Service Charge 10%" and RVAT — both are vatCatCd values
# per the spec's Save Sales item schema, not separate levies).
ZRA_VAT_CATEGORIES = ['A', 'B', 'C1', 'C2', 'C3', 'D', 'E', 'F', 'RVAT']
# Additional levies each have their own item-level cat-code field
# (iplCatCd / tlCatCd / exciseTxCatCd) and their own header buckets —
# they are NOT vatCatCd values.
ZRA_IPL_CODES = ['IPL1', 'IPL2']
ZRA_TL_CODES = ['TL']
ZRA_EXCISE_CODES = ['ECM', 'EXEEG']
# TOT (Turnover Tax) has header buckets in the spec but no item-level cat
# code field — it never shows tax on a VAT-registered taxpayer's invoice.
ZRA_TOT_CODES = ['TOT']

ALL_ZRA_TAX_CODES = ZRA_VAT_CATEGORIES + ZRA_IPL_CODES + ZRA_TL_CODES + ZRA_EXCISE_CODES + ZRA_TOT_CODES

# Suffix used in taxblAmt{suffix}/taxAmt{suffix}/taxRt{suffix} header keys,
# keyed by the code value that appears on the invoice line (spec pp. 43-46).
ZRA_TAX_SUFFIX = {
    'A': 'A', 'B': 'B', 'C1': 'C1', 'C2': 'C2', 'C3': 'C3',
    'D': 'D', 'E': 'E', 'F': 'F', 'RVAT': 'Rvat',
    'IPL1': 'Ipl1', 'IPL2': 'Ipl2', 'TL': 'Tl',
    'ECM': 'Ecm', 'EXEEG': 'Exeeg', 'TOT': 'Tot',
}

# Default rates — these are the commonly published ZRA statutory rates but
# MUST be confirmed against the business's own registration (or a live
# 'Get Standard Codes' sync, see zra_config.action_sync_standard_codes)
# before relying on them in production. ECM/EXEEG are per-unit excise
# duties, not percentages; they default to 0.0 and should be set explicitly
# per product if the business is excise-registered.
ZRA_DEFAULT_TAX_RATES = {
    'A': 16.0, 'B': 16.0, 'C1': 0.0, 'C2': 0.0, 'C3': 0.0,
    'D': 0.0, 'E': 0.0, 'F': 10.0, 'RVAT': 16.0,
    'IPL1': 3.0, 'IPL2': 0.0, 'TL': 1.5,
    'ECM': 0.0, 'EXEEG': 0.0, 'TOT': 0.0,
}


def _empty_zra_tax_header():
    """Empty header tax block with every taxblAmt*/taxAmt*/taxRt* key the
    VSDC 'Save Sales' / 'Save Purchase' schema requires (spec marks all of
    them Required: Y — 'pass 0.0 if not applicable')."""
    header = {}
    
    for code, suffix in ZRA_TAX_SUFFIX.items():
        header[f'taxblAmt{suffix}'] = 0.0
        header[f'taxAmt{suffix}'] = 0.0
        header[f'taxRt{suffix}'] = ZRA_DEFAULT_TAX_RATES.get(code, 0.0)
    return header


class ZRAAPIClient(models.AbstractModel):
    """ZRA VSDC API Client"""
    _name = 'zra.api.client'
    _description = 'ZRA VSDC API Client'

    # ── Core request / log helpers ────────────────────────────────────────────
    def _round_decimal(self, amount):
        return float(Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _get_zra_exchange_rate(self, invoice):
        """ZMW units per 1 unit of the invoice's currency.

        ZRA rejects exchangeRt=1 for any non-ZMW currency (confirmed live
        against the sandbox: resultCd 910 "Exchange rate for USD Cannot be
        1"). 1.0 is only correct when the invoice is already in ZMW.

        Two more failure modes confirmed live against a fresh Odoo company
        (default demo data, currency USD):
        - Odoo ships ZMW `active=False` (it's not one of the handful of
          currencies pre-activated on install), so a plain search() for it
          — search() filters on active by default — silently found nothing
          and this fell through to the "not zmw" branch, returning 1.0
          anyway. That 1.0 is exactly what triggers the ZRA rejection
          above, just one step removed: the bug this comment used to warn
          about was never actually fixed for a company that hasn't
          manually activated ZMW in Settings > Currencies.
        - Odoo's shipped seed rate table for ZMW has exactly one row,
          dated 2010-01-01. `_get_conversion_rate` picks the latest rate
          at-or-before the invoice date, so *any* invoice dated after 2010
          silently uses that 15+-year-old rate — ZRA would accept the
          request (it's a real number, not 1), but every tax amount
          reported would be computed off a rate that's off by an order of
          magnitude from reality. Failing loudly here is safer than
          reporting wrong tax figures to the tax authority.
        """
        zmw = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', 'ZMW')], limit=1)
        if not zmw or invoice.currency_id == zmw:
            return 1.0
        if not zmw.active:
            raise UserError(_(
                'This invoice is in %s, but the ZMW currency is inactive '
                '(Settings > General Settings > Currencies). ZRA rejects '
                'exchangeRt=1 for any non-ZMW invoice, so a real ZMW rate '
                'is required — activate ZMW and set a current exchange '
                'rate before submitting this invoice.'
            ) % invoice.currency_id.name)

        rate_date = invoice.invoice_date or fields.Date.context_today(invoice)
        latest_rate = self.env['res.currency.rate'].search([
            ('currency_id', '=', zmw.id),
            ('name', '<=', rate_date),
        ], order='name desc', limit=1)
        if not latest_rate or (rate_date - latest_rate.name).days > 30:
            raise UserError(_(
                'No ZMW exchange rate on or before %s is recent enough '
                '(within 30 days) to submit this %s invoice to ZRA. The '
                'most recent rate found is dated %s — add a current ZMW '
                'rate (Settings > General Settings > Currencies > ZMW) '
                'before submitting.'
            ) % (rate_date, invoice.currency_id.name,
                 latest_rate.name if latest_rate else _('none')))

        rate = invoice.currency_id._get_conversion_rate(
            invoice.currency_id, zmw, invoice.company_id, rate_date
        )
        return self._round_decimal(rate)

    def _make_request(self, config, endpoint, data, method='POST'):
        url = f"{config.vsdc_url}/{endpoint}"
        try:
            _logger.info(f"ZRA API Request to {endpoint}: {json.dumps(data, indent=2)}")
            response = requests.request(
                method=method, url=url, json=data,
                headers={'Content-Type': 'application/json'},
                timeout=config.timeout,
            )
            response.raise_for_status()
            result = response.json()
            _logger.info(f"ZRA API Response from {endpoint}: {json.dumps(result, indent=2)}")
            self._log_api_call(config, endpoint, data, result, 'success')
            return result
        except requests.exceptions.Timeout:
            msg = _('Request timeout. Please check VSDC connection.')
            self._log_api_call(config, endpoint, data, {'error': str(msg)}, 'failed')
            raise UserError(msg)
        except requests.exceptions.ConnectionError:
            msg = _('Cannot connect to VSDC at: %s') % config.vsdc_url
            self._log_api_call(config, endpoint, data, {'error': str(msg)}, 'failed')
            raise UserError(msg)
        except requests.exceptions.HTTPError as e:
            # The response body usually carries ZRA's actual error detail
            # (e.g. a resultMsg explaining which field failed) — surfacing
            # only the bare status code here would hide that entirely.
            body = e.response.text if e.response is not None else ''
            try:
                body_json = e.response.json() if e.response is not None else None
            except ValueError:
                body_json = None
            detail = (body_json or {}).get('resultMsg') if body_json else None
            detail = detail or body[:500] or str(e)
            msg = _('API Error (%s): %s') % (
                e.response.status_code if e.response is not None else '?', detail
            )
            self._log_api_call(
                config, endpoint, data,
                body_json or {'error': str(msg), 'raw_response': body[:2000]},
                'failed',
            )
            raise UserError(msg)
        except Exception as e:
            msg = _('API Error: %s') % str(e)
            self._log_api_call(config, endpoint, data, {'error': str(msg)}, 'failed')
            raise UserError(msg)

    def _log_api_call(self, config, endpoint, request_data, response_data, status):
        # Callers routinely log a 'failed' call and then raise UserError
        # (e.g. on a non-'000' resultCd) so the caller's own transaction can
        # surface the error to the user. Odoo rolls back the ENTIRE cursor
        # when an exception escapes a button/action call — which would wipe
        # out this very log record along with it, silently erasing the audit
        # trail for exactly the failures it exists to capture (checklist
        # #32). Confirmed live 2026-07-14: 'Fetch Purchases' failing with a
        # benign 'no results' response left zero trace in API Logs. A fresh,
        # independently-committed cursor makes the log survive regardless of
        # what the caller does afterward.
        # Surface ZRA's own business result separately from the transport
        # 'status' above — a resultCd 'success' logged as HTTP status
        # 'success' looks identical to one where the invoice/record itself
        # ended up zra_sync_status='failed' (e.g. resultCd 90x rejected the
        # payload), which lets the two tables silently disagree. Populate
        # this whenever response_data is a dict with a resultCd, regardless
        # of the transport status.
        result_code = None
        result_message = None
        if isinstance(response_data, dict):
            result_code = response_data.get('resultCd')
            result_message = response_data.get('resultMsg')

        try:
            with self.pool.cursor() as log_cr:
                log_env = api.Environment(log_cr, self.env.uid, self.env.context)
                log_env['zra.api.log'].sudo().create({
                    'company_id': config.company_id.id,
                    'endpoint': endpoint,
                    'request_data': json.dumps(request_data, indent=2),
                    'response_data': json.dumps(response_data, indent=2),
                    'status': status,
                    'result_code': result_code,
                    'result_message': result_message,
                    'call_date': fields.Datetime.now(),
                })
        except Exception as e:
            _logger.error(f"Failed to log API call: {str(e)}")

    # ── PARITY #1: Multi-tax breakdown helper ─────────────────────────────────
    def _build_tax_block(self, line_tax_buckets):
        """
        Given a dict of {tax_code: (taxable_amount, tax_amount)} accumulated
        across all invoice lines, return a fully populated ZRA header tax
        dict (every taxblAmt*/taxAmt*/taxRt* key from spec §5.8, plus totals).

        tax_code is one of ALL_ZRA_TAX_CODES, e.g.:
            {'A': (800.0, 128.0), 'TL': (200.0, 3.0)}
        """
        header = _empty_zra_tax_header()
        tot_taxable = 0.0
        tot_tax = 0.0

        for tax_code, (taxable, tax_amt) in line_tax_buckets.items():
            suffix = ZRA_TAX_SUFFIX.get(tax_code)
            if not suffix:
                _logger.warning(f"Unknown ZRA tax code '{tax_code}' — skipping bucket")
                continue
            header[f'taxblAmt{suffix}'] = self._round_decimal(
                header[f'taxblAmt{suffix}'] + taxable
            )
            header[f'taxAmt{suffix}'] = self._round_decimal(
                header[f'taxAmt{suffix}'] + tax_amt
            )
            tot_taxable += taxable
            tot_tax += tax_amt

        header['totTaxblAmt'] = self._round_decimal(tot_taxable)
        header['totTaxAmt'] = self._round_decimal(tot_tax)
        return header

    def _accumulate_line_taxes(self, line, existing_buckets):
        """
        Add one invoice/bill line's tax contribution to the running header
        buckets dict. The line's primary ZRA tax type (VAT-style: one of
        ZRA_VAT_CATEGORIES) carries the line's full taxable base and VAT
        amount. An optional secondary levy (IPL1/IPL2/TL/ECM/EXEEG) shares
        the same taxable base; its tax amount comes from a dedicated Odoo
        tax line for that levy if present, else 0.0.

        Returns the updated buckets dict.
        """
        net = self._round_decimal(line.price_subtotal or 0.0)
        gross = self._round_decimal(line.price_total or 0.0)
        vat = self._round_decimal(gross - net)

        primary = getattr(line.product_id, 'zra_tax_type', 'A') or 'A'
        secondary = getattr(line.product_id, 'zra_secondary_tax_type', None)

        existing_buckets.setdefault(primary, [0.0, 0.0])
        existing_buckets[primary][0] += net
        existing_buckets[primary][1] += vat

        if secondary:
            existing_buckets.setdefault(secondary, [0.0, 0.0])
            existing_buckets[secondary][0] += net
            # Secondary levy tax amount stays 0.0 here — VAT is already
            # fully accounted for in `primary` above. Add a dedicated Odoo
            # tax for the levy on the line to have it costed in this bucket.

        return existing_buckets

    # ── PARITY #3: Unit code helpers ──────────────────────────────────────────
    def _get_pkg_unit_code(self, product):
        """Return ZRA packaging unit code from product, falling back to NT."""
        return getattr(product, 'zra_pkg_unit_code', None) or 'NT'

    def _get_qty_unit_code(self, product, uom=None):
        """Return ZRA quantity unit code from product or UoM."""
        if product and getattr(product, 'zra_qty_unit_code', None):
            return product.zra_qty_unit_code
        if uom:
            name = uom.name.lower()
            if 'kg' in name:
                return 'KG'
            elif 'gram' in name:
                return 'G'
            elif 'liter' in name or 'litre' in name:
                return 'L'
            elif 'meter' in name:
                return 'M'
        return 'U'

    def _get_origin_country_code(self, product):
        """Return ISO country code for product origin, defaulting to ZM."""
        country = getattr(product, 'zra_origin_country_id', None)
        if country and country.code:
            return country.code
        return 'ZM'

    # ── Device / code helpers ─────────────────────────────────────────────────
    def initialize_device(self, config):
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'dvcSrlNo': config.device_serial,
        }
        return self._make_request(config, 'initializer/selectInitInfo', data)

    def test_connection(self, config):
        try:
            response = requests.get(config.vsdc_url, timeout=5)
            return response.status_code in (200, 302)
        except Exception:
            return False

    def get_standard_codes(self, config):
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': '20160523000000',
        }
        return self._make_request(config, 'code/selectCodes', data)

    def get_classification_codes(self, config):
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': '20160523000000',
        }
        return self._make_request(config, 'itemClass/selectItemsClass', data)

    # ── Item-level tax category split (spec §5.6/§5.8 item schema) ────────────
    def _split_item_tax_categories(self, product):
        """Return (vatCatCd, iplCatCd, tlCatCd, exciseTxCatCd) for a product.

        These are four independent fields in the VSDC spec, not one combined
        tax type — a product's VAT category and its optional secondary levy
        (IPL/TL/Excise) are reported separately.
        """
        vat_cat = product.zra_tax_type or 'A'
        secondary = product.zra_secondary_tax_type or None
        ipl_cat = secondary if secondary in ZRA_IPL_CODES else None
        tl_cat = secondary if secondary in ZRA_TL_CODES else None
        excise_cat = secondary if secondary in ZRA_EXCISE_CODES else None
        return vat_cat, ipl_cat, tl_cat, excise_cat

    def _build_item_payload(self, config, product):
        """Shared field-set for items/saveItem and items/updateItem
        (spec §5.6, pp. 21-24)."""
        origin = self._get_origin_country_code(product)
        pkg_unit = self._get_pkg_unit_code(product)
        qty_unit = self._get_qty_unit_code(product)
        vat_cat, ipl_cat, tl_cat, excise_cat = self._split_item_tax_categories(product)

        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'itemCd': (product.zra_item_code
                       or product.default_code
                       or f'ITEM{product.id:06d}'),
            'itemClsCd': product.zra_classification_code or '',
            'itemTyCd': product.zra_item_type or '2',
            'itemNm': product.name[:200],
            'itemStdNm': product.name[:200],
            'orgnNatCd': origin,
            'pkgUnitCd': pkg_unit,
            'qtyUnitCd': qty_unit,
            'vatCatCd': vat_cat,
            'iplCatCd': ipl_cat,
            'tlCatCd': tl_cat,
            'exciseTxCatCd': excise_cat,
            'btchNo': '',
            'bcd': product.barcode or '',
            'dftPrc': float(product.list_price),
            'addInfo': (product.description_sale or '')[:100],
            'sftyQty': float(product.zra_safety_qty or 0.0),
            # Spec v1.0.7 is inconsistent: the attribute table spells this
            # 'manufactuterTpin' (transposed letters) but every JSON sample
            # spells it 'manufacturerTpin'. Send both — harmless if one is
            # ignored, avoids silently dropping the value if we guess wrong.
            'manufactuterTpin': product.zra_manufacturer_tpin or None,
            'manufacturerTpin': product.zra_manufacturer_tpin or None,
            'manufacturerItemCd': product.zra_manufacturer_item_code or None,
            'rrp': float(product.zra_rrp) if product.zra_rrp else None,
            'svcChargeYn': 'Y' if product.zra_has_service_charge else 'N',
            'rentalYn': 'Y' if product.zra_is_rental else 'N',
            'isrcAplcbYn': 'N',
            'useYn': 'Y',
            'regrNm': self.env.user.name,
            'regrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
        }
        return data

    def register_item(self, config, product):
        """Register a new product with ZRA (items/saveItem, MANDATORY)."""
        data = self._build_item_payload(config, product)
        return self._make_request(config, 'items/saveItem', data)

    def update_item(self, config, product):
        """Push updated product details to ZRA (items/updateItem, MANDATORY).

        Call this whenever a previously registered product's ZRA-relevant
        fields change — the spec requires re-syncing edits, not just the
        initial save.
        """
        data = self._build_item_payload(config, product)
        return self._make_request(config, 'items/updateItem', data)

    def get_item_list(self, config, last_req_dt='20160523000000'):
        """Retrieve all items saved on Smart Invoice (items/selectItems,
        OPTIONAL — checklist item #10 'Get Item List')."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': last_req_dt,
        }
        return self._make_request(config, 'items/selectItems', data)

    def save_item_composition(self, config, parent_item_cd, component_item_cd, qty):
        """Record that `component_item_cd` is used as an ingredient/component
        of `parent_item_cd` (items/saveItemComposition, OPTIONAL — used for
        raw material processing or repackaging, checklist item #9).

        The endpoint accepts one parent/component pair per call.
        """
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'itemCd': parent_item_cd,
            'cpstItemCd': component_item_cd,
            'cpstQty': float(qty),
            'regrId': str(self.env.user.id),
            'regrNm': self.env.user.name,
        }
        return self._make_request(config, 'items/saveItemComposition', data)

    def _check_zra_vat_rate(self, line, vat_cat, net, vat):
        """Catch a mismatch between the Odoo tax actually applied to this
        line and the rate ZRA requires for its vatCatCd BEFORE calling ZRA.

        Confirmed live against the sandbox: a category-'A' line taxed at
        Odoo's demo-data 15% ("15%" sale tax) was rejected outright —
        resultCd 910 "Wrong Amount computation at vatTaxblAmt/vatAmt" —
        the exact same line re-sent with a real 16% tax was accepted
        (resultCd 000). ZRA validates vatAmt against its own fixed rate per
        category, not whatever rate Odoo happened to compute it with, so a
        misconfigured/legacy/mistakenly-picked tax on a product silently
        breaks fiscalization with an error that gives no hint it's a rate
        mismatch. Only categories with a fixed nonzero rate are checked —
        the zero-rated ones (C1/C2/C3/D/E) have no rate to get wrong.
        """
        expected_rate = ZRA_DEFAULT_TAX_RATES.get(vat_cat, 0.0)
        if not expected_rate or not net:
            return
        effective_rate = (vat / net) * 100.0
        if abs(effective_rate - expected_rate) > 0.5:
            raise UserError(_(
                'Product "%s" is tagged ZRA Tax Type "%s", which ZRA '
                'requires to be taxed at %.1f%% — but the tax(es) actually '
                'applied to this line work out to %.2f%%. ZRA will reject '
                'the whole sale with a cryptic "Wrong Amount computation" '
                'error rather than explain this. Fix the tax on this '
                'invoice line (or the ZRA Tax Type on the product) before '
                'submitting.'
            ) % (line.product_id.name, vat_cat, expected_rate, effective_rate))

    # ── Sales invoice / credit note / debit note ──────────────────────────────
    def _build_sales_item_list(self, lines):
        """Build the itemList array for trnsSales/saveSales (spec §5.8),
        shared across normal sales, credit notes, and debit notes.

        Returns (item_list, tax_buckets, total_gross).
        """
        item_list = []
        tax_buckets = {}   # {tax_code: [taxable_amt, tax_amt]}
        tot_gross = 0.0

        for seq, line in enumerate(lines, start=1):
            if line.display_type in ('line_section', 'line_note'):
                continue
            if not line.product_id or line.quantity <= 0:
                continue

            net = self._round_decimal(line.price_subtotal or 0.0)
            gross = self._round_decimal(line.price_total or 0.0)
            vat = self._round_decimal(gross - net)
            qty = float(line.quantity)
            unit_gross = self._round_decimal(gross / qty) if qty else 0.0

            vat_cat, ipl_cat, tl_cat, excise_cat = self._split_item_tax_categories(line.product_id)
            self._check_zra_vat_rate(line, vat_cat, net, vat)
            pkg_unit = self._get_pkg_unit_code(line.product_id)
            qty_unit = self._get_qty_unit_code(line.product_id, line.product_uom_id)

            item_list.append({
                'itemSeq': seq,
                'itemCd': (line.product_id.zra_item_code
                           or line.product_id.default_code
                           or f'ITEM{line.product_id.id:06d}'),
                'itemClsCd': line.product_id.zra_classification_code or '',
                'itemNm': (line.product_id.name or line.name or '')[:200],
                'bcd': line.product_id.barcode or '',
                'pkgUnitCd': pkg_unit,
                'pkg': 1,
                'qtyUnitCd': qty_unit,
                'qty': qty,
                'prc': unit_gross,
                'splyAmt': gross,
                'dcRt': 0.0,
                'dcAmt': 0.0,
                'isrccCd': '',
                'isrccNm': '',
                'isrcRt': 0.0,
                'isrcAmt': 0.0,
                # Four independent tax-category fields per spec item schema —
                # only the categories this product actually attracts are set.
                'vatCatCd': vat_cat,
                'iplCatCd': ipl_cat,
                'tlCatCd': tl_cat,
                'exciseTxCatCd': excise_cat,
                'vatTaxblAmt': net if vat_cat else 0.0,
                'exciseTaxblAmt': net if excise_cat else 0.0,
                'tlTaxblAmt': net if tl_cat else 0.0,
                'iplTaxblAmt': net if ipl_cat else 0.0,
                'vatAmt': vat if vat_cat else 0.0,
                # Secondary-levy line amounts stay 0.0 — see _accumulate_line_taxes.
                'iplAmt': 0.0,
                'tlAmt': 0.0,
                'exciseTxAmt': 0.0,
                'totAmt': gross,
            })

            self._accumulate_line_taxes(line, tax_buckets)
            tot_gross += gross

        return item_list, tax_buckets, tot_gross

    def _build_sales_header(self, config, invoice, rcpt_ty_cd, item_list, tax_block,
                             tot_gross, org_invc_no=0, org_sdc_id=None,
                             rfnd_rsn_cd=None, dbt_rsn_cd=None, invc_adjust_reason=None):
        """Build the trnsSales/saveSales request body (spec §5.8, pp. 41-59),
        shared across normal sales (S), credit notes (R) and debit notes (D).

        Note: 'invcNo' does NOT appear anywhere in the official spec — the
        taxpayer-side invoice identity ZRA expects is 'cisInvcNo'; ZRA assigns
        its own sequential receipt number ('rcptNo') server-side and returns
        it in the response. Sending a fabricated invcNo (as earlier versions
        of this module did) has no basis in the spec and is not sent here.
        """
        cust_tpin = invoice.partner_id.zra_tpin or ''
        if not cust_tpin:
            _logger.warning(
                f"Invoice {invoice.name}: customer {invoice.partner_id.name} "
                f"has no TPIN — using default 1000000000"
            )
            cust_tpin = '1000000000'

        sales_type_cd = 'N'
        for line in invoice.invoice_line_ids:
            if line.product_id and getattr(line.product_id, 'zra_sale_type', None):
                if line.product_id.zra_sale_type != 'N':
                    sales_type_cd = line.product_id.zra_sale_type
                    break

        pmt_type_cd = invoice.zra_payment_type or '01'
        inv_date = invoice.invoice_date or fields.Date.context_today(invoice)
        cfm_dt = (invoice.invoice_date.strftime('%Y%m%d%H%M%S')
                  if invoice.invoice_date
                  else datetime.now().strftime('%Y%m%d%H%M%S'))
        rfd_dt = fields.Datetime.now().strftime('%Y%m%d%H%M%S') if rfnd_rsn_cd else None

        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'orgInvcNo': org_invc_no,
            'cisInvcNo': invoice.name or '',
            'custTpin': cust_tpin,
            'custNm': (invoice.partner_id.name or '')[:60],
            'salesTyCd': sales_type_cd,
            'rcptTyCd': rcpt_ty_cd,
            'pmtTyCd': pmt_type_cd,
            'salesSttsCd': '02',
            'cfmDt': cfm_dt,
            'salesDt': inv_date.strftime('%Y%m%d'),
            'stockRlsDt': None,
            'cnclReqDt': None,
            'cnclDt': None,
            'rfdDt': rfd_dt,
            'rfdRsnCd': rfnd_rsn_cd or '',
            'totItemCnt': len(item_list),
            **tax_block,
            'totAmt': self._round_decimal(tot_gross),
            'prchrAcptcYn': 'N',
            'remark': (invoice.narration or '')[:400],
            'regrId': str(self.env.user.id),
            'regrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
            'saleCtyCd': '1',
            'lpoNumber': None,
            'currencyTyCd': invoice.currency_id.name or 'ZMW',
            'exchangeRt': self._get_zra_exchange_rate(invoice),
            'destnCountryCd': '',
            'dbtRsnCd': dbt_rsn_cd or '',
            # Spec's attribute table calls this 'invcAdjustReason' but the
            # JSON sample uses 'invcAdjust' — send both, harmless if one is ignored.
            'invcAdjustReason': invc_adjust_reason or '',
            'invcAdjust': invc_adjust_reason or '',
            'cashDcRt': 0.0,
            'cashDcAmt': 0.0,
            # Spec samples show two different shapes for this nested object
            # across sections — one with just prchrAcptcYn, one with
            # custTpin/custMblNo/rptNo. Send the union; harmless if extra
            # keys are ignored.
            'receipt': {
                'prchrAcptcYn': 'N',
                'custTpin': cust_tpin,
                'custMblNo': (invoice.partner_id.mobile
                              or invoice.partner_id.phone or ''),
                'rptNo': 0,
            },
            'itemList': item_list,
        }
        if org_sdc_id:
            data['orgSdcId'] = org_sdc_id
        return data

    def submit_sale(self, config, invoice):
        """Submit a normal sales invoice or credit note to ZRA
        (trnsSales/saveSales, MANDATORY)."""
        _logger.info("========== ZRA: submit_sale START ==========")

        org_invc_no = 0
        org_sdc_id = None
        rfnd_rsn_cd = None

        if invoice.move_type == 'out_refund':
            rcpt_ty_cd = 'R'
            original = invoice.zra_original_invoice_id
            if original:
                # orgInvcNo must reference ZRA's own assigned receipt number
                # (rcptNo) — NOT Odoo's internal database id, which means
                # nothing to ZRA's server.
                org_invc_no = (int(original.zra_receipt_number)
                               if (original.zra_receipt_number or '').isdigit()
                               else 0)
                org_sdc_id = original.zra_sdc_id or config.device_serial or None
            rfnd_rsn_cd = invoice.zra_refund_reason or None
        else:
            rcpt_ty_cd = 'S'

        item_list, tax_buckets, tot_gross = self._build_sales_item_list(
            invoice.invoice_line_ids
        )
        if not item_list:
            raise UserError(_("No valid invoice lines to send to ZRA"))

        tax_block = self._build_tax_block(
            {k: tuple(v) for k, v in tax_buckets.items()}
        )

        data = self._build_sales_header(
            config, invoice, rcpt_ty_cd, item_list, tax_block, tot_gross,
            org_invc_no=org_invc_no, org_sdc_id=org_sdc_id, rfnd_rsn_cd=rfnd_rsn_cd,
        )

        return self._make_request(config, 'trnsSales/saveSales', data)

    # ── Debit note ────────────────────────────────────────────────────────────
    def submit_debit_note(self, config, invoice):
        """Submit a debit note to ZRA (trnsSales/saveSales, rcptTyCd='D')."""
        _logger.info("========== ZRA: submit_debit_note START ==========")

        if not invoice.zra_debit_reason:
            raise UserError(_('Debit note reason code is required.'))
        if not invoice.zra_original_invoice_id:
            raise UserError(_('Original invoice must be linked for a debit note.'))

        original = invoice.zra_original_invoice_id
        org_invc_no = (int(original.zra_receipt_number)
                        if (original.zra_receipt_number or '').isdigit()
                        else 0)
        org_sdc_id = original.zra_sdc_id or config.device_serial or None

        item_list, tax_buckets, tot_gross = self._build_sales_item_list(
            invoice.invoice_line_ids
        )
        if not item_list:
            raise UserError(_("No valid lines on debit note to send to ZRA"))

        tax_block = self._build_tax_block(
            {k: tuple(v) for k, v in tax_buckets.items()}
        )

        reason_label = dict(
            invoice._fields['zra_debit_reason']._description_selection(invoice.env)
        ).get(invoice.zra_debit_reason, '')

        data = self._build_sales_header(
            config, invoice, 'D', item_list, tax_block, tot_gross,
            org_invc_no=org_invc_no, org_sdc_id=org_sdc_id,
            dbt_rsn_cd=invoice.zra_debit_reason,
            invc_adjust_reason=reason_label,
        )

        return self._make_request(config, 'trnsSales/saveSales', data)

    # ── Purchase / vendor bill ────────────────────────────────────────────────
    def submit_purchase(self, config, bill):
        """Submit vendor bill to ZRA (trnsPurchase/savePurchase, MANDATORY).

        Note: the Purchase item/header schema is NOT the same as Sales —
        e.g. the header has no per-VAT-category taxblAmtA/taxRtA/... buckets
        (those are Sales-only), and the Excise category field is spelled
        'exciseCatCd' here vs 'exciseTxCatCd' on Sales. Both are sent for
        the excise field as a hedge against the spec's own inconsistency.
        """
        _logger.info("========== ZRA: submit_purchase START ==========")

        if bill.move_type != 'in_invoice':
            raise UserError(_('Only Vendor Bills can be sent to ZRA'))

        supplier = bill.partner_id
        # spplrTpin is OPTIONAL per spec (§5.9 Save Purchase Request,
        # Required: N) — a supplier who isn't registered on Smart Invoice
        # simply has no TPIN, which is exactly the checklist #15 "manual
        # capture from an unregistered supplier" case. Only validate the
        # format when a TPIN IS provided; don't block the sync when it's not.
        supplier_tpin = (supplier.zra_tpin or '').strip()
        if supplier_tpin and len(supplier_tpin) != 10:
            raise UserError(
                _('Supplier TPIN "%s" is not valid (must be exactly 10 digits).\n'
                  'Vendor: %s')
                % (supplier_tpin, supplier.display_name)
            )

        item_list = []
        tot_taxbl = 0.0
        tot_tax = 0.0
        tot_gross = 0.0

        for seq, line in enumerate(bill.invoice_line_ids, start=1):
            if line.display_type in ('line_section', 'line_note'):
                continue
            if not line.product_id or line.quantity <= 0:
                continue

            net = self._round_decimal(line.price_subtotal or 0.0)
            gross = self._round_decimal(line.price_total or 0.0)
            vat = self._round_decimal(gross - net)
            qty = float(line.quantity)
            unit_gross = self._round_decimal(gross / qty) if qty else 0.0

            vat_cat, ipl_cat, tl_cat, excise_cat = self._split_item_tax_categories(line.product_id)
            self._check_zra_vat_rate(line, vat_cat, net, vat)
            pkg_unit = self._get_pkg_unit_code(line.product_id)
            qty_unit = self._get_qty_unit_code(line.product_id)

            item_list.append({
                'itemSeq': seq,
                'itemCd': (line.product_id.zra_item_code
                           or line.product_id.default_code or 'ITEM001'),
                'itemClsCd': line.product_id.zra_classification_code or '',
                'itemNm': line.name or line.product_id.display_name,
                'bcd': line.product_id.barcode or '',
                'spplrItemClsCd': line.product_id.zra_classification_code or '',
                'spplrItemCd': line.product_id.default_code or '',
                'spplrItemNm': line.product_id.display_name or '',
                'pkgUnitCd': pkg_unit,
                'pkg': 1,
                'qtyUnitCd': qty_unit,
                'qty': qty,
                'prc': unit_gross,
                'splyAmt': gross,
                'dcRt': 0.0,
                'dcAmt': 0.0,
                'vatCatCd': vat_cat,
                'iplCatCd': ipl_cat,
                'tlCatCd': tl_cat,
                'exciseCatCd': excise_cat,
                'exciseTxCatCd': excise_cat,
                # Legacy hedge — one purchase sample still shows this field
                # alongside vatCatCd even though it's absent from the table.
                'taxTyCd': vat_cat,
                'taxblAmt': net if vat_cat else 0.0,
                'iplTaxblAmt': net if ipl_cat else 0.0,
                'tlTaxblAmt': net if tl_cat else 0.0,
                'exciseTaxblAmt': net if excise_cat else 0.0,
                'taxAmt': vat if vat_cat else 0.0,
                'iplAmt': 0.0,
                'tlAmt': 0.0,
                'exciseTxAmt': 0.0,
                'totAmt': gross,
            })

            tot_taxbl += net
            tot_tax += vat
            tot_gross += gross

        if not item_list:
            raise UserError(_('No valid bill lines to send to ZRA'))

        inv_date = bill.invoice_date or fields.Date.context_today(bill)
        cfm_dt = datetime.combine(inv_date, datetime.min.time()).strftime('%Y%m%d%H%M%S')
        # spplrInvcNo is NUMBER-typed on ZRA's side — confirmed live 2026-07-14:
        # a non-numeric value (e.g. Odoo's own alphanumeric bill name like
        # "BILL/2026/07/0001") triggers a bare Jackson-deserialization 400
        # with no resultMsg, while a digit-only string succeeds. Falling back
        # to bill.name here was the bug; only ever send digits, or omit the
        # field entirely (it's optional per spec) if none are present.
        digits_only = ''.join(ch for ch in (bill.zra_supplier_invoice_no or '') if ch.isdigit())
        spplr_invc_no = digits_only or None
        pmt_type_cd = bill.zra_payment_type or '01'
        reg_ty_cd = bill.zra_purchase_reg_type or 'M'

        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'cisInvcNo': bill.name or '',
            'orgInvcNo': 0,
            # Empty string ('') on a date/numeric-typed field triggers a bare
            # Jackson deserialization 400 on ZRA's side with no resultMsg —
            # confirmed live 2026-07-14. None (-> JSON null) is what the
            # working sales header sends for the equivalent cnclReqDt/cnclDt
            # fields, so purchase now matches that proven-safe pattern.
            'spplrTpin': supplier_tpin or None,
            'spplrBhfId': '000',
            'spplrNm': supplier.name or '',
            'spplrInvcNo': spplr_invc_no,
            'regTyCd': reg_ty_cd,
            'pchsTyCd': 'N',
            'rcptTyCd': 'P',
            'pmtTyCd': pmt_type_cd,
            'pchsSttsCd': '02',
            'cfmDt': cfm_dt,
            'pchsDt': inv_date.strftime('%Y%m%d'),
            'cnclReqDt': None,
            'cnclDt': None,
            'totItemCnt': len(item_list),
            'totTaxblAmt': self._round_decimal(tot_taxbl),
            'totTaxAmt': self._round_decimal(tot_tax),
            'totAmt': self._round_decimal(tot_gross),
            'remark': bill.narration or '',
            'regrNm': self.env.user.name or '',
            'regrId': str(self.env.user.id),
            'modrNm': self.env.user.name or '',
            'modrId': str(self.env.user.id),
            'itemList': item_list,
        }

        return self._make_request(config, 'trnsPurchase/savePurchase', data)

    def get_purchases(self, config, last_req_dt='20160523000000'):
        """Retrieve purchases made by this business from other Smart
        Invoice-registered suppliers (trnsPurchase/selectTrnsPurchaseSales,
        MANDATORY — checklist item #14 'Get Purchases')."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': last_req_dt,
        }
        return self._make_request(config, 'trnsPurchase/selectTrnsPurchaseSales', data)

    # ── PARITY #2: Import declaration API calls ───────────────────────────────
    def fetch_import_declaration(self, config, declaration_no):
        """Fetch a single import declaration by declaration number."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'dclrNo': declaration_no,
        }
        return self._make_request(config, 'imports/selectImportItems', data)

    def fetch_import_declarations_range(self, config, date_from, date_to):
        """
        Fetch all import declarations between two dates (YYYYMMDD strings).
        Uses lastReqDt to pull anything newer than date_from.
        """
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': f"{date_from}000000",
        }
        return self._make_request(config, 'imports/selectImportItems', data)

    def update_import_item(self, config, declaration, line, status_cd='2', remark=''):
        """Transmit a confirmed imported item's details back to ZRA
        (imports/updateImportItems, MANDATORY — checklist item #12).

        Sent once the declaration line has been linked to a registered
        Odoo product: confirms the internal item code (itemCd) and
        classification (itemClsCd) mapping for the imported item so it
        becomes usable for stock/sales on Smart Invoice.

        `status_cd` is the Import Item Status Code (imptItemSttsCd);
        '2' = Confirmed. Verify against the current VSDC spec code
        table if ZRA publishes different values.
        """
        product = line.product_id
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'taskCd': declaration.task_cd or '',
            'dclrDe': (declaration.declaration_date.strftime('%Y%m%d')
                       if declaration.declaration_date else ''),
            'itemSeq': line.item_seq or 0,
            'hsCd': line.hs_code or declaration.hscode or '',
            'itemClsCd': product.zra_classification_code or line.item_cls_cd or '',
            'itemCd': product.zra_item_code or line.item_cd or '',
            'imptItemSttsCd': status_cd,
            'remark': remark or '',
            'modrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
        }
        return self._make_request(config, 'imports/updateImportItems', data)

    # ── Stock ─────────────────────────────────────────────────────────────────
    def submit_stock_movement(self, config, stock_moves):
        for move in stock_moves:
            try:
                move._sync_with_zra(config)
            except Exception as e:
                _logger.error(
                    f"submit_stock_movement failed for move {move.id}: {str(e)}"
                )
        return {'resultCd': '000', 'resultMsg': 'Done'}

    def save_stock_items(self, config, sar_no, movement_code, occurrence_date,
                          item_lines, remark=''):
        """Record a stock movement (stock/saveStockItems, MANDATORY except
        service industry — checklist item #27).

        `item_lines` is a list of dicts: {'product': product.product,
        'qty': float, 'unit_cost': float}. Per spec, this call must be
        followed by save_stock_master() to update the resulting balance.
        """
        itm_list = []
        tot_taxbl = 0.0
        tot_gross = 0.0

        for seq, line in enumerate(item_lines, start=1):
            product = line['product']
            qty = float(line['qty'])
            unit_cost = float(line.get('unit_cost') or product.standard_price or 0.0)
            gross = self._round_decimal(qty * unit_cost)

            vat_cat, ipl_cat, tl_cat, excise_cat = self._split_item_tax_categories(product)
            pkg_unit = self._get_pkg_unit_code(product)
            qty_unit = self._get_qty_unit_code(product)

            itm_list.append({
                'itemSeq': seq,
                'itemCd': (product.zra_item_code or product.default_code
                           or f'ITEM{product.id:06d}'),
                'itemClsCd': product.zra_classification_code or '',
                'itemNm': product.name[:200],
                'bcd': product.barcode or '',
                'pkgUnitCd': pkg_unit,
                'pkg': 1,
                'qtyUnitCd': qty_unit,
                'qty': qty,
                'itemExprDt': None,
                'prc': unit_cost,
                'splyAmt': gross,
                'totDcAmt': 0.0,
                'vatCatCd': vat_cat,
                'iplCatCd': ipl_cat,
                'tlCatCd': tl_cat,
                'exciseTxCatCd': excise_cat,
                'taxblAmt': gross if vat_cat else 0.0,
                'taxAmt': 0.0,
                'totAmt': gross,
            })
            tot_taxbl += gross if vat_cat else 0.0
            tot_gross += gross

        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'sarNo': sar_no,
            'orgSarNo': 0,
            'regTyCd': 'M',
            'custTpin': None,
            'custNm': None,
            'custBhfId': None,
            'sarTyCd': movement_code,
            'ocrnDt': occurrence_date,
            'totItemCnt': len(itm_list),
            'totTaxblAmt': self._round_decimal(tot_taxbl),
            'totTaxAmt': 0.0,
            'totAmt': self._round_decimal(tot_gross),
            'remark': remark or '',
            'regrId': str(self.env.user.id),
            'regrNm': self.env.user.name,
            'modrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
            'itemList': itm_list,
        }
        return self._make_request(config, 'stock/saveStockItems', data)

    def save_stock_master(self, config, item_qty_pairs):
        """Update remaining stock quantities on ZRA (stockMaster/saveStockMaster,
        MANDATORY — checklist item #29 'update stock item quantities').

        `item_qty_pairs` is a list of (item_code, remaining_qty) tuples.
        rsdQty is the item's ABSOLUTE remaining quantity after the movement,
        not the movement amount itself.
        """
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'regrNm': self.env.user.name,
            'regrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
            'stockItemList': [
                {'itemCd': item_cd, 'rsdQty': float(qty)}
                for item_cd, qty in item_qty_pairs
            ],
        }
        return self._make_request(config, 'stockMaster/saveStockMaster', data)

    def get_stock_items(self, config, last_req_dt='20160523000000'):
        """Retrieve stock items recorded on Smart Invoice
        (stock/selectStockItems, OPTIONAL — checklist item #28
        'Get Stock Item List')."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': last_req_dt,
        }
        return self._make_request(config, 'stock/selectStockItems', data)

    # ── Branch & Customer Information (spec §5.4/§5.5) ─────────────────────────
    def save_branch_user(self, config, user):
        """Save a branch (system) user to ZRA (branches/saveBrancheUser,
        OPTIONAL — checklist item #6). Note the spec's own endpoint spelling
        is 'saveBrancheUser', not 'saveBranchUser'."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'userId': str(user.id),
            'userNm': (user.login or user.name or '')[:60],
            'adrs': '',
            'useYn': 'Y' if user.active else 'N',
            'regrNm': self.env.user.name,
            'regrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
        }
        return self._make_request(config, 'branches/saveBrancheUser', data)

    def get_branch_info(self, config, last_req_dt='20160523000000'):
        """Retrieve this taxpayer's registered branch details from Smart
        Invoice (branches/selectBranches, checklist item #7 — MANDATORY)."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'lastReqDt': last_req_dt,
        }
        return self._make_request(config, 'branches/selectBranches', data)

    def get_branch_customer(self, config, customer_tpin):
        """Retrieve a previously saved customer's details by TPIN
        (customers/selectCustomer, checklist item #5)."""
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'custmTpin': customer_tpin,
        }
        return self._make_request(config, 'customers/selectCustomer', data)

    def save_branch_customer(self, config, partner):
        """Save a customer's details to ZRA (branches/saveBrancheCustomers,
        checklist item #4). Note the spec's own endpoint spelling is
        'saveBrancheCustomers', not 'saveBranchCustomers'."""
        cust_no = ''.join(ch for ch in (partner.mobile or partner.phone or '') if ch.isdigit())
        data = {
            'tpin': config.tpin,
            'bhfId': config.branch_id,
            'custNo': cust_no,
            'custTpin': partner.zra_tpin or '',
            'custNm': (partner.name or '')[:60],
            'adrs': partner.contact_address or None,
            'email': partner.email or None,
            'faxNo': None,
            'useYn': 'Y' if partner.active else 'N',
            'remark': None,
            'regrNm': self.env.user.name,
            'regrId': str(self.env.user.id),
            'modrNm': self.env.user.name,
            'modrId': str(self.env.user.id),
        }
        return self._make_request(config, 'branches/saveBrancheCustomers', data)
