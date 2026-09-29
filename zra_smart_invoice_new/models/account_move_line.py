# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    zra_line_tax_amount = fields.Monetary(
        string='Tax Amount', compute='_compute_zra_line_tax_amount',
        currency_field='currency_id',
        help='Tax charged on this line (price_total - price_subtotal), '
             'shown for the ZRA Sales Register report (checklist #31).'
    )

    @api.depends('price_total', 'price_subtotal')
    def _compute_zra_line_tax_amount(self):
        for line in self:
            line.zra_line_tax_amount = (line.price_total or 0.0) - (line.price_subtotal or 0.0)

    # ── Immutability (checklist #18, #22, #23) ─────────────────────────────
    # account.move's own write()/unlink() guard (models/account_move.py) only
    # intercepts changes routed through the parent record (the
    # invoice_line_ids command). A line reached directly —
    # env['account.move.line'].browse(id).write(...), an RPC/XML-RPC call, a
    # bulk update, a data-import script — bypassed that guard entirely and
    # could still change a fiscalized invoice's reported totals (price_unit,
    # quantity, discount, tax_ids, ...) with no re-sync and no trace. Mirror
    # the same guard here, at the line level.
    #
    # Confirmed live against the sandbox: an unconditional version of this
    # guard (every write blocked, regardless of field) broke
    # printing/reprinting a synced invoice. Odoo core's own
    # `_sync_dynamic_line` (account/models/account_move.py) recomputes
    # totals as a normal side effect of rendering the report, and as part
    # of that issues ONE write() across a mixed batch of line ids —
    # confirmed live: ids of display_type 'product', 'tax' AND
    # 'payment_term' together, vals={'discount_allocation_dirty': False} —
    # an internal dirty-flag the ZRA payload never reads
    # (`_build_sales_item_list` only looks at product_id/quantity/
    # price_unit/discount/tax_ids/product_uom_id/name for its 'product'
    # lines). Scoping by display_type alone doesn't work — the genuine
    # product line is IN that batch — so scope by which fields are
    # actually being touched instead, the same way account.move's own
    # guard scopes by `_ZRA_PROTECTED_FIELDS`. A real edit to a product
    # line's content is still blocked; core's own bookkeeping isn't.
    _ZRA_PROTECTED_LINE_FIELDS = {
        'product_id', 'quantity', 'price_unit', 'discount', 'tax_ids',
        'product_uom_id', 'name', 'account_id', 'display_type',
        'currency_id', 'partner_id',
    }

    def write(self, vals):
        touched = self._ZRA_PROTECTED_LINE_FIELDS & set(vals.keys())
        if not touched:
            return super().write(vals)
        for line in self:
            move = line.move_id
            if move and move.zra_sync_status == 'synced':
                raise UserError(_(
                    'Invoice "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and its lines cannot be modified. '
                    'ZRA requires fiscalized invoices to be immutable — '
                    'issue a credit note or debit note instead.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().write(vals)

    def unlink(self):
        for line in self:
            move = line.move_id
            if line.display_type == 'product' and move and move.zra_sync_status == 'synced':
                raise UserError(_(
                    'Invoice "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and its lines cannot be deleted. ZRA '
                    'requires fiscalized invoices to be retained permanently.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        moves = self.env['account.move'].browse(
            {vals['move_id'] for vals in vals_list if vals.get('move_id')}
        )
        synced_by_id = {m.id: m for m in moves if m.zra_sync_status == 'synced'}
        for vals in vals_list:
            move = synced_by_id.get(vals.get('move_id'))
            if move and vals.get('display_type', 'product') == 'product':
                raise UserError(_(
                    'Invoice "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and new lines cannot be added to it.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().create(vals_list)
