# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class ForecourtDelivery(models.Model):
    _name = 'forecourt.delivery'
    _description = 'Forecourt Stock Delivery / Receipt'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'delivery_date desc, name desc'

    name = fields.Char(string='Delivery Reference', required=True, copy=False,
                       default='New', readonly=True)
    delivery_date = fields.Date(string='Delivery Date', required=True,
                                default=fields.Date.today)
    product_type = fields.Selection([
        ('fuel', 'Fuel'),
        ('lpg', 'LPG'),
        ('lubricant', 'Lubricant / Oil'),
    ], string='Product Type', required=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    tank_id = fields.Many2one('forecourt.tank', string='Tank',
                              help='Used for single-tank fuel deliveries. Leave blank and '
                                   'use the Tanks tab below to split this receipt across '
                                   'multiple tanks.')
    tank_allocation_ids = fields.One2many(
        'forecourt.delivery.tank.line', 'delivery_id', string='Tank Allocation',
        help='Optional. If filled in, this delivery\'s quantity is split across these '
             'tanks instead of going entirely to the single Tank field above.',
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse / Branch', required=True,
        domain=[('is_forecourt_branch', '=', True)],
        help='Branch this delivery is received at. Auto-filled from the '
             'selected tank for fuel deliveries.',
    )
    picking_id = fields.Many2one(
        'stock.picking', string='Stock Receipt', readonly=True, copy=False,
        help='The actual Odoo stock receipt created when this delivery '
             'is confirmed â€” reflected in standard Inventory reports.',
    )
    quantity_received = fields.Float(string='Quantity Received', required=True,
                                     digits=(16, 3))
    unit_cost = fields.Float(string='Unit Cost (ZMW)', digits=(16, 4))
    total_cost = fields.Float(string='Total Cost (ZMW)', compute='_compute_total',
                               store=True, digits=(16, 4))
    supplier_id = fields.Many2one('res.partner', string='Supplier')
    invoice_number = fields.Char(string='Supplier Invoice No.')
    waybill_number = fields.Char(string='Waybill / BOL No.')
    received_by = fields.Many2one('res.users', string='Received By',
                                  default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)
    notes = fields.Text(string='Notes')

    @api.depends('quantity_received', 'unit_cost')
    def _compute_total(self):
        for rec in self:
            rec.total_cost = rec.quantity_received * rec.unit_cost

    @api.onchange('tank_id')
    def _onchange_tank_id(self):
        if self.tank_id and self.tank_id.warehouse_id:
            self.warehouse_id = self.tank_id.warehouse_id

    @api.constrains('tank_allocation_ids', 'quantity_received')
    def _check_tank_allocation_total(self):
        for rec in self:
            if not rec.tank_allocation_ids:
                continue
            allocated = sum(rec.tank_allocation_ids.mapped('quantity'))
            if abs(allocated - rec.quantity_received) > 0.01:
                raise ValidationError(
                    f'Tank allocation totals {allocated:.3f} but Quantity Received is '
                    f'{rec.quantity_received:.3f}. These must match before confirming.'
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.delivery'
                ) or 'New'
        return super().create(vals_list)

    def action_confirm_receipt(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Only draft deliveries can be confirmed.')
            if not rec.warehouse_id:
                raise UserError('Please select a Warehouse / Branch.')
            if rec.product_type == 'fuel':
                if not rec.tank_id and not rec.tank_allocation_ids:
                    raise UserError(
                        'Please select a tank, or allocate this delivery across '
                        'tanks in the Tanks tab.'
                    )
                for tank, qty in rec._get_tank_quantities():
                    new_level = tank.current_level + qty
                    if new_level > tank.capacity:
                        raise UserError(
                            f'Delivery would exceed capacity for tank {tank.name}. '
                            f'Current: {tank.current_level:.2f} L, '
                            f'Allocated: {qty:.2f} L, '
                            f'Capacity: {tank.capacity:.2f} L.'
                        )
            picking = rec._create_and_validate_receipt()
            rec.picking_id = picking.id
            if rec.product_type == 'fuel':
                for tank, qty in rec._get_tank_quantities():
                    tank.current_level = tank.current_level + qty
            rec.write({'state': 'received'})

    def _get_tank_quantities(self):
        """Returns a list of (tank, quantity) pairs for this delivery â€”
        either the allocation lines, or the single tank_id/quantity_received
        pair for backward compatibility."""
        self.ensure_one()
        if self.tank_allocation_ids:
            return [(line.tank_id, line.quantity) for line in self.tank_allocation_ids]
        elif self.tank_id:
            return [(self.tank_id, self.quantity_received)]
        return []

    def _create_and_validate_receipt(self):
        self.ensure_one()
        picking_type = self.warehouse_id.in_type_id
        tank_quantities = self._get_tank_quantities() if self.product_type == 'fuel' else []

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': self.warehouse_id.lot_stock_id.id,
            'origin': self.name,
            'partner_id': self.supplier_id.id or False,
            'company_id': self.company_id.id,
        })

        if tank_quantities:
            for tank, qty in tank_quantities:
                dest_location = tank.location_id or self.warehouse_id.lot_stock_id
                self.env['stock.move'].create({
                    'name': self.product_id.display_name,
                    'product_id': self.product_id.id,
                    'product_uom_qty': qty,
                    'product_uom': self.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking_type.default_location_src_id.id,
                    'location_dest_id': dest_location.id,
                    'company_id': self.company_id.id,
                    'price_unit': self.unit_cost,
                })
        else:
            dest_location = (
                self.tank_id.location_id
                if (self.product_type == 'fuel' and self.tank_id.location_id)
                else self.warehouse_id.lot_stock_id
            )
            self.env['stock.move'].create({
                'name': self.product_id.display_name,
                'product_id': self.product_id.id,
                'product_uom_qty': self.quantity_received,
                'product_uom': self.product_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': dest_location.id,
                'company_id': self.company_id.id,
                'price_unit': self.unit_cost,
            })

        picking.action_confirm()
        for mv in picking.move_ids:
            mv.quantity = mv.product_uom_qty
            mv.picked = True
        result = picking.with_context(
            skip_backorder=True, skip_sms=True,
        ).button_validate()
        if isinstance(result, dict) and result.get('res_model'):
            wizard = self.env[result['res_model']].with_context(
                result.get('context', {})
            ).create({})
            if hasattr(wizard, 'process'):
                wizard.process()
        return picking

    def action_view_receipt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_cancel(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state == 'done':
                raise UserError(
                    'This delivery has already posted a completed stock '
                    'receipt. Use Inventory > Returns to reverse the stock '
                    'movement first, then cancel this record.'
                )
            if rec.picking_id and rec.picking_id.state != 'cancel':
                rec.picking_id.action_cancel()
            rec.write({'state': 'cancelled'})
