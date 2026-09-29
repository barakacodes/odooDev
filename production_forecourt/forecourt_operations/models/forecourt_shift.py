# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError


class ForecourtShift(models.Model):
    _name = 'forecourt.shift'
    _description = 'Forecourt Shift'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc'

    name = fields.Char(string='Shift Reference', required=True, copy=False,
                       default='New')
    branch = fields.Selection([
        ('arcades', 'Arcades'),
        ('luanshya', 'Luanshya'),
        ('chililabombwe', 'Chililabombwe'),
    ], string='Branch (legacy)', tracking=True,
       help='Deprecated â€” kept for historical records. Use Branch (Warehouse) instead.')
    branch_id = fields.Many2one(
        'stock.warehouse', string='Branch',
        domain=[('is_forecourt_branch', '=', True)],
        tracking=True,
    )
    shift_type = fields.Selection([
        ('day', 'Day (06:00 â€“ 18:00)'),
        ('night', 'Night (18:00 â€“ 06:00)'),
    ], string='Shift', required=True)
    date_start = fields.Datetime(string='Start Date/Time', required=True,
                                 default=fields.Datetime.now)
    date_end = fields.Datetime(string='End Date/Time')
    supervisor_id = fields.Many2one('res.users', string='Supervisor', required=True,
                                    default=lambda self: self.env.user)
    cashier_ids = fields.Many2many('res.users', string='Cashiers')
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    state = fields.Selection([
        ('open', 'Open'),
        ('closed', 'Closed'),
    ], string='Status', default='open', required=True, tracking=True)
    source = fields.Selection([
        ('whatsapp', 'WhatsApp Import'),
        ('pos', 'Point of Sale'),
        ('manual', 'Manual Entry'),
    ], string='Data Source', default='manual', required=True, tracking=True,
       help='Which system this shift sales originate from. Used to prevent '
            'the same shift being double-counted from two different sources.')
    account_move_id = fields.Many2one(
        'account.move', string='Accounting Entry', readonly=True, copy=False,
        help='Journal entry posted for this shift sales, if any.',
    )
    notes = fields.Text(string='Handover Notes')

    # Computed totals
    sale_ids = fields.One2many('forecourt.sale', 'shift_id', string='Sales')
    meter_ids = fields.One2many('forecourt.meter.reading', 'shift_id', string='Meter Readings')

    total_revenue = fields.Monetary(
        string='Total Revenue', compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    total_fuel_litres = fields.Float(
        string='Fuel Volume (L)', compute='_compute_totals', store=True, digits=(16, 2),
    )
    total_lpg_units = fields.Float(
        string='LPG Units', compute='_compute_totals', store=True, digits=(16, 2),
    )
    total_lubricant_revenue = fields.Monetary(
        string='Lubricant Revenue', compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency',
    )
    cash_total = fields.Monetary(
        string='Cash Total', compute='_compute_payment_totals', store=True,
        currency_field='currency_id',
    )
    mobile_money_total = fields.Monetary(
        string='Mobile Money Total', compute='_compute_payment_totals', store=True,
        currency_field='currency_id',
    )
    card_total = fields.Monetary(
        string='Card / POS Total', compute='_compute_payment_totals', store=True,
        currency_field='currency_id',
    )
    credit_total = fields.Monetary(
        string='Credit / Fleet Total', compute='_compute_payment_totals', store=True,
        currency_field='currency_id',
    )

    @api.depends('sale_ids.total_amount', 'sale_ids.product_type',
                 'sale_ids.quantity')
    def _compute_totals(self):
        for rec in self:
            sales = rec.sale_ids
            rec.total_revenue = sum(sales.mapped('total_amount'))
            rec.total_fuel_litres = sum(
                s.quantity for s in sales
                if s.product_type == 'fuel'
            )
            rec.total_lpg_units = sum(
                s.quantity for s in sales
                if s.product_type == 'lpg'
            )
            rec.total_lubricant_revenue = sum(
                s.total_amount for s in sales
                if s.product_type == 'lubricant'
            )

    @api.depends('sale_ids.payment_method', 'sale_ids.total_amount')
    def _compute_payment_totals(self):
        for rec in self:
            sales = rec.sale_ids
            rec.cash_total = sum(
                s.total_amount for s in sales if s.payment_method == 'cash'
            )
            rec.mobile_money_total = sum(
                s.total_amount for s in sales if s.payment_method == 'mobile_money'
            )
            rec.card_total = sum(
                s.total_amount for s in sales if s.payment_method == 'card'
            )
            rec.credit_total = sum(
                s.total_amount for s in sales if s.payment_method == 'credit'
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.shift'
                ) or 'New'
        return super().create(vals_list)

    def action_close_shift(self):
        for rec in self:
            if rec.state != 'open':
                raise UserError('This shift is already closed.')
            rec.write({
                'state': 'closed',
                'date_end': fields.Datetime.now(),
            })

    def action_reopen_shift(self):
        for rec in self:
            rec.write({'state': 'open', 'date_end': False})

    def get_shift_type_label(self):
        """Return human-readable shift type label â€” used in reports."""
        self.ensure_one()
        return dict(self._fields['shift_type'].selection).get(self.shift_type, '')

    def action_print_shift_report(self):
        self.ensure_one()
        return self.env.ref(
            'forecourt_operations.action_forecourt_shift_direct_pdf'
        ).report_action(self)
