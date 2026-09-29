# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime

import logging
_logger = logging.getLogger(__name__)


class ZRAPurchaseFetch(models.TransientModel):
    """Wizard to fetch purchases from ZRA (checklist item #14 — mandatory).

    Retrieves purchases made by this business from other Smart
    Invoice-registered suppliers via trnsPurchase/selectTrnsPurchaseSales,
    and creates draft vendor bills from them, matched to products by ZRA
    item code and to the supplier by TPIN.
    """
    _name = 'zra.purchase.fetch'
    _description = 'Fetch ZRA Purchases'

    date_from = fields.Date(
        string='From Date', required=True,
        default=lambda self: fields.Date.today()
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company
    )

    def action_fetch(self):
        self.ensure_one()

        config = self.env['zra.config'].get_active_config(self.company_id.id)
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        api_client = self.env['zra.api.client']
        last_req_dt = self.date_from.strftime('%Y%m%d') + '000000'
        result = api_client.get_purchases(config, last_req_dt=last_req_dt)

        # '001' = "There is no search result" (spec §6.13) — legitimate
        # empty response, not an error; the loop below already handles an
        # empty sale_list gracefully. Confirmed live 2026-07-14: a clean
        # test branch with no purchase history returns exactly this.
        if result.get('resultCd') not in ('000', '001'):
            raise UserError(
                _('ZRA returned error: %s') % result.get('resultMsg', 'Unknown error')
            )

        sale_list = (result.get('data') or {}).get('saleList', [])
        Partner = self.env['res.partner']
        Product = self.env['product.product']
        Move = self.env['account.move']

        created, skipped = 0, 0

        for purchase in sale_list:
            spplr_tpin = purchase.get('spplrTpin')
            spplr_invc_no = str(purchase.get('spplrInvcNo') or '')
            if not spplr_tpin:
                skipped += 1
                continue

            existing = Move.search([
                ('move_type', '=', 'in_invoice'),
                ('company_id', '=', self.company_id.id),
                ('zra_supplier_invoice_no', '=', spplr_invc_no),
                ('partner_id.zra_tpin', '=', spplr_tpin),
            ], limit=1)
            if existing:
                skipped += 1
                continue

            partner = Partner.search([('zra_tpin', '=', spplr_tpin)], limit=1)
            if not partner:
                partner = Partner.create({
                    'name': purchase.get('spplrNm') or spplr_tpin,
                    'zra_tpin': spplr_tpin,
                    'supplier_rank': 1,
                    'company_type': 'company',
                })

            line_vals = []
            for item in purchase.get('itemList', []):
                product = Product.search(
                    [('zra_item_code', '=', item.get('itemCd'))], limit=1
                )
                line_vals.append((0, 0, {
                    'product_id': product.id if product else False,
                    'name': item.get('itemNm') or (product.name if product else 'Item'),
                    'quantity': item.get('qty') or 1.0,
                    'price_unit': item.get('prc') or 0.0,
                }))

            if not line_vals:
                skipped += 1
                continue

            cfm_dt = purchase.get('cfmDt') or purchase.get('salesDt') or ''
            invoice_date = fields.Date.today()
            if cfm_dt:
                try:
                    invoice_date = datetime.strptime(str(cfm_dt)[:8], '%Y%m%d').date()
                except Exception:
                    pass

            Move.create({
                'move_type': 'in_invoice',
                'partner_id': partner.id,
                'company_id': self.company_id.id,
                'invoice_date': invoice_date,
                'zra_purchase_reg_type': 'A',
                'zra_supplier_invoice_no': spplr_invc_no,
                'zra_payment_type': purchase.get('pmtTyCd') or '01',
                'narration': _('Fetched from ZRA Get Purchases on %s') % fields.Date.today(),
                'invoice_line_ids': line_vals,
            })
            created += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Purchases Fetched from ZRA'),
                'message': _('Draft bills created: %d | Already existed / skipped: %d')
                           % (created, skipped),
                'type': 'success' if created > 0 else 'warning',
                'sticky': False,
            }
        }
