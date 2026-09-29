# -*- coding: utf-8 -*-
from datetime import time
from odoo import models, fields, api
from odoo.exceptions import UserError


def _map_product_type(product):
    """Map a product's category to forecourt.sale's product_type selection."""
    categ_name = (product.categ_id.name or '').lower()
    if categ_name == 'petrol':
        return 'fuel'
    if 'lpg' in categ_name:
        return 'lpg'
    return 'lubricant'


def _map_payment_method(order):
    """Map a POS order's first payment to forecourt.sale's payment_method selection."""
    if not order.payment_ids:
        return 'cash'
    method_name = (order.payment_ids[0].payment_method_id.name or '').lower()
    if 'cash' in method_name:
        return 'cash'
    if 'momo' in method_name or 'mobile' in method_name:
        return 'mobile_money'
    if 'card' in method_name or 'bank' in method_name:
        return 'card'
    if 'credit' in method_name or 'account' in method_name:
        return 'credit'
    return 'cash'


class PosOrder(models.Model):
    _inherit = 'pos.order'

    forecourt_shift_id = fields.Many2one(
        'forecourt.shift', string='Forecourt Shift', readonly=True, copy=False,
        help='The forecourt shift this order was recorded against, for branches '
             'using forecourt tracking alongside POS.',
    )

    def _get_forecourt_branch(self):
        """Return the stock.warehouse (forecourt branch) for this order's session, or empty."""
        self.ensure_one()
        picking_type = self.session_id.config_id.picking_type_id
        warehouse = picking_type.warehouse_id if picking_type else self.env['stock.warehouse']
        if warehouse and warehouse.is_forecourt_branch:
            return warehouse
        return self.env['stock.warehouse']

    def _get_or_create_forecourt_shift(self, warehouse):
        """Find an existing matching forecourt.shift for this branch+date+shift_type,
        or create a new one with source='pos'. Raises if a conflicting WhatsApp-sourced
        shift already exists for the same branch/date/shift_type."""
        self.ensure_one()
        order_dt = fields.Datetime.from_string(self.date_order) if isinstance(self.date_order, str) else self.date_order
        shift_type = 'day' if time(6, 0) <= order_dt.time() < time(18, 0) else 'night'
        day_start = order_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = order_dt.replace(hour=23, minute=59, second=59, microsecond=0)

        Shift = self.env['forecourt.shift']
        existing = Shift.search([
            ('branch_id', '=', warehouse.id),
            ('shift_type', '=', shift_type),
            ('date_start', '>=', fields.Datetime.to_string(day_start)),
            ('date_start', '<=', fields.Datetime.to_string(day_end)),
        ], limit=1)

        if existing:
            if existing.source == 'whatsapp':
                raise UserError(
                    f"A {shift_type} shift for {warehouse.name} on "
                    f"{order_dt.strftime('%d/%m/%Y')} already exists ({existing.name}) "
                    f"from a WhatsApp report import. This POS sale cannot be recorded "
                    f"against it -- please resolve the conflicting shift record first."
                )
            return existing

        if shift_type == 'day':
            start_dt = order_dt.replace(hour=6, minute=0, second=0, microsecond=0)
        else:
            start_dt = order_dt.replace(hour=18, minute=0, second=0, microsecond=0)

        shift = Shift.create({
            'branch_id': warehouse.id,
            'shift_type': shift_type,
            'date_start': fields.Datetime.to_string(start_dt),
            'supervisor_id': self.env.user.id,
            'company_id': warehouse.company_id.id,
            'state': 'open',
            'source': 'pos',
        })
        return shift

    def _create_forecourt_sales(self, shift):
        """Create one forecourt.sale per order line, reusing the order's own stock move.
        Skips lines that already have a matching forecourt.sale (idempotent)."""
        self.ensure_one()
        Sale = self.env['forecourt.sale']
        payment_method = _map_payment_method(self)

        moves_by_product = {}
        for picking in self.picking_ids:
            for move in picking.move_ids:
                moves_by_product.setdefault(move.product_id.id, move)

        existing_sales = Sale.search([('notes', '=', f'Auto-created from POS order {self.name}')])
        already_done_products = set(existing_sales.mapped('product_id.id'))

        for line in self.lines:
            if line.qty <= 0:
                continue
            if line.product_id.id in already_done_products:
                continue
            move = moves_by_product.get(line.product_id.id)
            Sale.create({
                'name': f'{self.name} - {line.product_id.name}',
                'sale_datetime': self.date_order,
                'shift_id': shift.id,
                'cashier_id': self.user_id.id or self.env.user.id,
                'company_id': shift.company_id.id,
                'product_type': _map_product_type(line.product_id),
                'product_id': line.product_id.id,
                'quantity': line.qty,
                'unit_price': line.price_unit,
                'payment_method': payment_method,
                'customer_id': self.partner_id.id if self.partner_id else False,
                'stock_move_id': move.id if move else False,
                'notes': f'Auto-created from POS order {self.name}',
            })

    def _sync_to_forecourt(self):
        """Entry point: find/create the forecourt branch+shift and mirror this
        order's lines into forecourt.sale. Safe to call multiple times (idempotent)."""
        for order in self:
            warehouse = order._get_forecourt_branch()
            if not warehouse:
                continue  # not a forecourt branch, nothing to sync
            shift = order._get_or_create_forecourt_shift(warehouse)
            if order.forecourt_shift_id != shift:
                order.forecourt_shift_id = shift.id
            order._create_forecourt_sales(shift)

    def _create_order_picking(self):
        res = super()._create_order_picking()
        self._sync_to_forecourt()
        return res
