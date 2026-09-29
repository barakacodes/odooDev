# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


# Reconciliation tolerances. Variances within these bounds are reported as OK.
STOCK_TOLERANCE_LITRES = 0.5      # 500 ml dip-chart slack
CASH_TOLERANCE_KWACHA = 1.00       # K1 rounding slack


class ForecourtShiftFuelEntry(models.Model):
    """Per-fuel-grade shift capture line."""
    _name = 'forecourt.shift.fuel.entry'
    _description = 'Forecourt Shift Fuel Entry (per fuel grade)'
    _order = 'shift_date desc, fuel_type'

    shift_id = fields.Many2one(
        'forecourt.shift', string='Shift', required=True,
        ondelete='cascade', index=True,
    )
    fuel_type = fields.Selection([
        ('petrol', 'Petrol'),
        ('diesel', 'Diesel'),
    ], string='Fuel', required=True)

    shift_date = fields.Date(
        string='Date', store=True, compute='_compute_shift_date',
    )
    branch = fields.Selection(
        related='shift_id.branch', store=True, string='Branch',
    )
    company_id = fields.Many2one(
        related='shift_id.company_id', store=True, string='Company',
    )
    currency_id = fields.Many2one(
        related='shift_id.currency_id', string='Currency',
    )

    # ── Manager-entered data points ──────────────────────────────────────────
    opening_stock_litres = fields.Float(string='Opening Stock (L)', digits=(16, 3))
    total_sold_litres = fields.Float(string='Total Sold (L)', digits=(16, 3))
    credit_litres_sold = fields.Float(string='Credit Sold (L)', digits=(16, 3))
    coupon_litres_sold = fields.Float(
        string='Coupon Sold (L)', digits=(16, 3),
        help='Fuel dispensed against coupons/vouchers — part of Total Sold '
             'but NOT of Cash Sales. Kept separate so the report consistency '
             'check (Total = Credit + Cash + Coupon) is accurate.',
    )
    test_stock_litres = fields.Float(string='Test Stock (L)', digits=(16, 3))
    cash_sales_litres = fields.Float(string='Cash Sales (L)', digits=(16, 3))
    received_stock_litres = fields.Float(string='Received Stock (L)', digits=(16, 3))
    atg_gain_loss_litres = fields.Float(string='ATG Gain/Loss (L)', digits=(16, 3))
    dip_gain_loss_litres = fields.Float(string='DIP Gain/Loss (L)', digits=(16, 3))
    closing_stock_litres = fields.Float(string='Closing Stock (L)', digits=(16, 3))
    atg_closing_stock_litres = fields.Float(string='ATG Closing Stock (L)', digits=(16, 3))

    # ── Report consistency: does the supervisor's report internally reconcile? ─
    # The Aziz Excel derives Total Sold = Credit + Cash + Coupon. The WhatsApp
    # report gives a "total sales" line separately. When those disagree, we keep
    # the reported total (audit trail) but flag the discrepancy.
    derived_sold_litres = fields.Float(
        string='Derived Sold (Credit + Cash + Coupon) (L)', digits=(16, 3),
        compute='_compute_report_consistency', store=True,
    )
    report_consistency_variance = fields.Float(
        string='Report Consistency Variance (L)', digits=(16, 3),
        compute='_compute_report_consistency', store=True,
        help='Reported Total Sold − (Credit + Cash + Coupon). Non-zero means '
             'the supervisor\'s report does not internally reconcile.',
    )
    report_consistency_status = fields.Selection([
        ('ok', 'Consistent'),
        ('mismatch', 'Report Inconsistent'),
    ], string='Report Consistency',
        compute='_compute_report_consistency', store=True)

    # ── Rate (looked up by date) ──────────────────────────────────────────────
    rate = fields.Float(
        string='Pump Rate (ZMW/L)', digits=(16, 4),
        compute='_compute_rate', store=True,
    )
    cash_sales_value = fields.Monetary(
        string='Cash Sales Value', currency_field='currency_id',
        compute='_compute_rate', store=True,
    )

    # ── Monetary Stock Variance (K) ───────────────────────────────────────────
    stock_variance_value = fields.Monetary(
        string='Stock Variance (K)', currency_field='currency_id',
        compute='_compute_stock_variance_value', store=True,
    )

    # ── Rule 1: opening vs previous closing ───────────────────────────────────
    system_opening_litres = fields.Float(
        string='System Opening (L)', digits=(16, 3),
        compute='_compute_system_opening', store=True,
    )
    opening_variance = fields.Float(
        string='Opening Variance (L)', digits=(16, 3),
        compute='_compute_opening_check', store=True,
    )
    opening_check_status = fields.Selection([
        ('na', 'No prior shift'),
        ('ok', 'Match'),
        ('mismatch', 'Mismatch'),
    ], string='Opening Check', compute='_compute_opening_check', store=True)

    # ── Rule 2: opening + received - derived sold = closing ──────────────────
    expected_closing_litres = fields.Float(
        string='Expected Closing (L)', digits=(16, 3),
        compute='_compute_closing_check', store=True,
    )
    closing_variance = fields.Float(
        string='Closing Variance (L)', digits=(16, 3),
        compute='_compute_closing_check', store=True,
    )
    closing_variance_literal = fields.Float(
        string='Closing Variance (if reported total used) (L)', digits=(16, 3),
        compute='_compute_closing_check', store=True,
        help='What the variance would have been if we trusted the '
             'supervisor\'s reported Total Sold figure instead of deriving '
             'it from Credit + Cash + Coupon.',
    )
    closing_check_status = fields.Selection([
        ('ok', 'Match'),
        ('mismatch', 'Mismatch'),
    ], string='Closing Check', compute='_compute_closing_check', store=True)

    # ── Rule 3: ATG closing vs expected closing (matches Excel) ──────────────
    atg_dip_variance = fields.Float(
        string='ATG vs Expected Variance (L)', digits=(16, 3),
        compute='_compute_atg_dip_check', store=True,
    )
    atg_dip_check_status = fields.Selection([
        ('na', 'Not entered'),
        ('ok', 'Match'),
        ('mismatch', 'Mismatch'),
    ], string='ATG vs Expected Check', compute='_compute_atg_dip_check', store=True)

    notes = fields.Char(string='Notes')

    _sql_constraints = [
        ('uniq_shift_fuel',
         'unique(shift_id, fuel_type)',
         'Each fuel grade may only be entered once per shift.'),
    ]

    @api.depends('shift_id.date_start')
    def _compute_shift_date(self):
        for rec in self:
            rec.shift_date = (
                rec.shift_id.date_start.date()
                if rec.shift_id.date_start else False
            )

    @api.depends('total_sold_litres', 'credit_litres_sold',
                 'cash_sales_litres', 'coupon_litres_sold')
    def _compute_report_consistency(self):
        for rec in self:
            rec.derived_sold_litres = (
                (rec.credit_litres_sold or 0.0)
                + (rec.cash_sales_litres or 0.0)
                + (rec.coupon_litres_sold or 0.0)
            )
            rec.report_consistency_variance = (
                (rec.total_sold_litres or 0.0) - rec.derived_sold_litres
            )
            rec.report_consistency_status = (
                'ok'
                if abs(rec.report_consistency_variance) <= STOCK_TOLERANCE_LITRES
                else 'mismatch'
            )

    @api.depends('shift_id.date_start', 'fuel_type', 'cash_sales_litres')
    def _compute_rate(self):
        Rate = self.env['forecourt.fuel.rate']
        for rec in self:
            on_date = rec.shift_id.date_start.date() if rec.shift_id.date_start \
                else fields.Date.context_today(rec)
            petrol, diesel = Rate.get_rate_for(on_date, rec.company_id)
            rec.rate = petrol if rec.fuel_type == 'petrol' else diesel
            rec.cash_sales_value = rec.cash_sales_litres * rec.rate

    @api.depends('closing_variance', 'rate')
    def _compute_stock_variance_value(self):
        for rec in self:
            rec.stock_variance_value = rec.closing_variance * rec.rate

    @api.depends('shift_id.branch', 'shift_id.date_start', 'fuel_type')
    def _compute_system_opening(self):
        for rec in self:
            rec.system_opening_litres = rec._lookup_previous_closing()

    def _lookup_previous_closing(self):
        self.ensure_one()
        if not (self.shift_id and self.shift_id.date_start and self.branch
                and self.fuel_type):
            return 0.0
        prev_date = self.shift_id.date_start.date()
        prev = self.search([
            ('id', '!=', self.id or 0),
            ('branch', '=', self.branch),
            ('fuel_type', '=', self.fuel_type),
            ('shift_date', '<=', prev_date),
        ], order='shift_date desc, id desc', limit=5)
        for cand in prev:
            if cand.shift_id.id != self.shift_id.id \
                    and cand.shift_id.date_start \
                    and cand.shift_id.date_start < self.shift_id.date_start:
                return cand.closing_stock_litres
        return 0.0

    @api.depends('opening_stock_litres', 'system_opening_litres')
    def _compute_opening_check(self):
        for rec in self:
            has_prior = bool(rec.system_opening_litres) or rec._has_prior_shift()
            if not has_prior:
                rec.opening_variance = 0.0
                rec.opening_check_status = 'na'
                continue
            rec.opening_variance = (
                rec.opening_stock_litres - rec.system_opening_litres
            )
            rec.opening_check_status = (
                'ok' if abs(rec.opening_variance) <= STOCK_TOLERANCE_LITRES
                else 'mismatch'
            )

    def _has_prior_shift(self):
        self.ensure_one()
        if not (self.shift_id and self.shift_id.date_start and self.branch
                and self.fuel_type):
            return False
        prev_date = self.shift_id.date_start.date()
        prev = self.search([
            ('id', '!=', self.id or 0),
            ('branch', '=', self.branch),
            ('fuel_type', '=', self.fuel_type),
            ('shift_date', '<=', prev_date),
        ], limit=10)
        return any(
            p.shift_id.id != self.shift_id.id
            and p.shift_id.date_start
            and p.shift_id.date_start < self.shift_id.date_start
            for p in prev
        )

    @api.depends('opening_stock_litres', 'received_stock_litres',
                 'derived_sold_litres', 'total_sold_litres',
                 'closing_stock_litres')
    def _compute_closing_check(self):
        for rec in self:
            # Expected Closing matches the Aziz Excel:
            #   Opening + Received − (Credit + Cash + Coupon)
            rec.expected_closing_litres = (
                rec.opening_stock_litres
                + rec.received_stock_litres
                - rec.derived_sold_litres
            )
            rec.closing_variance = (
                rec.closing_stock_litres - rec.expected_closing_litres
            )
            # Literal: variance if the reported Total Sold had been used.
            rec.closing_variance_literal = (
                rec.closing_stock_litres
                - (rec.opening_stock_litres
                   + rec.received_stock_litres
                   - (rec.total_sold_litres or 0.0))
            )
            rec.closing_check_status = (
                'ok' if abs(rec.closing_variance) <= STOCK_TOLERANCE_LITRES
                else 'mismatch'
            )

    @api.depends('atg_closing_stock_litres', 'expected_closing_litres')
    def _compute_atg_dip_check(self):
        for rec in self:
            if not rec.atg_closing_stock_litres:
                rec.atg_dip_variance = 0.0
                rec.atg_dip_check_status = 'na'
                continue
            rec.atg_dip_variance = (
                rec.atg_closing_stock_litres - rec.expected_closing_litres
            )
            rec.atg_dip_check_status = (
                'ok' if abs(rec.atg_dip_variance) <= STOCK_TOLERANCE_LITRES
                else 'mismatch'
            )

    @api.constrains('total_sold_litres', 'credit_litres_sold',
                    'cash_sales_litres', 'coupon_litres_sold',
                    'test_stock_litres')
    def _check_non_negative(self):
        for rec in self:
            for fname in ('opening_stock_litres', 'total_sold_litres',
                          'credit_litres_sold', 'cash_sales_litres',
                          'coupon_litres_sold',
                          'test_stock_litres', 'received_stock_litres',
                          'closing_stock_litres', 'atg_closing_stock_litres'):
                if rec[fname] < 0:
                    raise ValidationError(
                        f'{rec._fields[fname].string} cannot be negative.'
                    )