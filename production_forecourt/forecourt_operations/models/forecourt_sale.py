# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError


class ForecourtSale(models.Model):
    _name = 'forecourt.sale'
    _description = 'Forecourt Sale Transaction'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sale_datetime desc, name desc'

    name = fields.Char(string='Transaction Ref', required=True, copy=False,
                       default='New', readonly=True)
    sale_datetime = fields.Datetime(string='Date / Time', required=True,
                                    default=fields.Datetime.now)
    shift_id = fields.Many2one('forecourt.shift', string='Shift', required=True,
                               domain=[('state', '=', 'open')],
                               default=lambda self: self._default_shift())
    cashier_id = fields.Many2one('res.users', string='Cashier', required=True,
                                 default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)

    # Product classification
    product_type = fields.Selection([
        ('fuel', 'Fuel'),
        ('lpg', 'LPG'),
        ('lubricant', 'Lubricant / Oil'),
    ], string='Product Type', required=True)

    product_id = fields.Many2one('product.product', string='Product', required=True)

    # Fuel-specific
    pump_id = fields.Many2one('forecourt.pump', string='Pump',
                              domain=[('status', 'in', ['active', 'idle'])])
    nozzle_id = fields.Many2one('forecourt.nozzle', string='Nozzle')

    # Quantities and pricing
    quantity = fields.Float(string='Quantity', required=True, digits=(16, 3))
    unit_label = fields.Char(string='Unit', compute='_compute_unit_label')
    unit_price = fields.Float(string='Unit Price (ZMW)', required=True, digits=(16, 4))
    total_amount = fields.Float(string='Total (ZMW)', compute='_compute_total',
                                store=True, digits=(16, 4))

    # Payment
    payment_method = fields.Selection([
        ('cash', 'Cash'),
        ('mobile_money', 'Mobile Money'),
        ('card', 'Card / POS'),
        ('credit', 'Fleet / Credit Account'),
    ], string='Payment Method', required=True, default='cash')
    customer_id = fields.Many2one('res.partner', string='Customer / Vehicle Reg')
    customer_type = fields.Selection([
        ('normal', 'Normal'),
        ('prepaid', 'Prepaid Wallet'),
        ('account_holder', 'Account Holder'),
    ], string='Customer Type', default='normal', required=True)
    is_test = fields.Boolean(
        string='Test / Calibration', default=False,
        help='Tick if fuel was dispensed for pump testing or calibration.',
    )

    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', required=True, tracking=True)

    notes = fields.Char(string='Notes')

    # Stock move reference
    stock_move_id = fields.Many2one('stock.move', string='Stock Move', readonly=True)

    # ---------------------------------------------------------------
    @api.model
    def _default_shift(self):
        return self.env['forecourt.shift'].search(
            [('state', '=', 'open'),
             ('company_id', '=', self.env.company.id)],
            order='date_start desc', limit=1,
        )

    @api.depends('product_type')
    def _compute_unit_label(self):
        for rec in self:
            if rec.product_type == 'fuel':
                rec.unit_label = 'Litres'
            elif rec.product_type == 'lpg':
                rec.unit_label = 'Cylinders'
            else:
                rec.unit_label = 'Units'

    @api.depends('quantity', 'unit_price')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = rec.quantity * rec.unit_price

    @api.onchange('pump_id')
    def _onchange_pump(self):
        if self.pump_id:
            self.product_id = self.pump_id.product_id

    @api.onchange('nozzle_id')
    def _onchange_nozzle(self):
        if self.nozzle_id and self.nozzle_id.pump_id:
            self.pump_id = self.nozzle_id.pump_id

    @api.constrains('quantity')
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError('Quantity must be greater than zero.')

    @api.constrains('unit_price')
    def _check_price(self):
        for rec in self:
            if rec.unit_price <= 0:
                raise ValidationError('Unit price must be greater than zero.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.sale'
                ) or 'New'
        return super().create(vals_list)

    def action_post(self):
        """Post the sale â€” deduct from tank and create stock move."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Only draft transactions can be posted.')
            if rec.product_type == 'fuel' and rec.pump_id:
                tank = rec.pump_id.tank_id
                if tank.current_level < rec.quantity:
                    raise UserError(
                        f'Insufficient fuel in {tank.name}. '
                        f'Available: {tank.current_level:.2f} L, '
                        f'Required: {rec.quantity:.2f} L.'
                    )
                tank.current_level -= rec.quantity
            elif rec.product_type == 'lpg':
                # Stock adjustment handled via stock.quant
                self._adjust_lpg_stock(rec)

            rec.write({'state': 'posted'})

    def _adjust_lpg_stock(self, rec):
        """Reduce LPG inventory on hand."""
        quant = self.env['stock.quant'].search([
            ('product_id', '=', rec.product_id.id),
            ('location_id.usage', '=', 'internal'),
        ], limit=1)
        if quant and quant.quantity >= rec.quantity:
            quant.quantity -= rec.quantity

    def action_cancel(self):
        for rec in self:
            if rec.state == 'cancelled':
                raise UserError('Already cancelled.')
            if rec.state == 'posted' and rec.product_type == 'fuel' and rec.pump_id:
                # Reverse tank deduction
                rec.pump_id.tank_id.current_level += rec.quantity
            rec.write({'state': 'cancelled'})

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'cancelled':
                rec.write({'state': 'draft'})
