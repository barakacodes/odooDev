# -*- coding: utf-8 -*-
from odoo import api, fields, models


# Reused tolerance (kept in sync with forecourt_shift_fuel_entry).
CASH_TOLERANCE_KWACHA = 1.00


class ForecourtShift(models.Model):
    """Extend `forecourt.shift` with the global per-shift data points
    (cash on hand, POS account, lube & LPG sales) and the cross-fuel cash
    reconciliation check (rule 4 of the spec)."""
    _inherit = 'forecourt.shift'

    supervisor_name_text = fields.Char(
        string='Supervisor (from report)',
        help='Raw supervisor name as written on the WhatsApp shift report. '
             'Shown on the dashboard when the name does not match an Odoo user.',
    )

    fuel_entry_ids = fields.One2many(
        'forecourt.shift.fuel.entry', 'shift_id',
        string='Fuel Entries',
    )

    cash_on_hand_kwacha = fields.Monetary(
        string='Cash on Hand (K)', currency_field='currency_id',
    )
    pos_account_kwacha = fields.Monetary(
        string='POS Account Total (K)', currency_field='currency_id',
        help='Total money in the POS account at end of shift.',
    )
    lube_sales_kwacha = fields.Monetary(
        string='Lube Sales (K)', currency_field='currency_id',
    )
    lpg_sales_kwacha = fields.Monetary(
        string='LPG Sales (K)', currency_field='currency_id',
    )

    expected_cash_minus_pos = fields.Monetary(
        string='Expected Cash (K)',
        compute='_compute_cash_check', store=True,
        currency_field='currency_id',
        help='Sum across both fuels of cash_sales_litres x rate.',
    )
    cash_check_variance = fields.Monetary(
        string='Cash Check Variance (K)',
        compute='_compute_cash_check', store=True,
        currency_field='currency_id',
    )
    cash_check_status = fields.Selection([
        ('na', 'Not entered'),
        ('ok', 'Match'),
        ('mismatch', 'Mismatch'),
    ], string='Cash Check', compute='_compute_cash_check', store=True)

    capture_status = fields.Selection([
        ('na', 'Incomplete'),
        ('ok', 'All checks pass'),
        ('mismatch', 'Variance flagged'),
    ], string='Capture Status', compute='_compute_capture_status', store=True)

    @api.depends('fuel_entry_ids.cash_sales_value',
                 'cash_on_hand_kwacha', 'pos_account_kwacha')
    def _compute_cash_check(self):
        for rec in self:
            rec.expected_cash_minus_pos = sum(
                rec.fuel_entry_ids.mapped('cash_sales_value')
            )
            actual = (rec.cash_on_hand_kwacha or 0.0) + (rec.pos_account_kwacha or 0.0)
            rec.cash_check_variance = actual - rec.expected_cash_minus_pos
            entered = (rec.cash_on_hand_kwacha or rec.pos_account_kwacha
                       or rec.expected_cash_minus_pos)
            if not entered:
                rec.cash_check_status = 'na'
            else:
                rec.cash_check_status = (
                    'ok' if abs(rec.cash_check_variance) <= CASH_TOLERANCE_KWACHA
                    else 'mismatch'
                )

    @api.depends('fuel_entry_ids.opening_check_status',
                 'fuel_entry_ids.closing_check_status',
                 'fuel_entry_ids.atg_dip_check_status',
                 'fuel_entry_ids.report_consistency_status',
                 'cash_check_status')
    def _compute_capture_status(self):
        for rec in self:
            statuses = (
                list(rec.fuel_entry_ids.mapped('opening_check_status'))
                + list(rec.fuel_entry_ids.mapped('closing_check_status'))
                + list(rec.fuel_entry_ids.mapped('atg_dip_check_status'))
                + list(rec.fuel_entry_ids.mapped('report_consistency_status'))
                + [rec.cash_check_status]
            )
            if not rec.fuel_entry_ids:
                rec.capture_status = 'na'
            elif 'mismatch' in statuses:
                rec.capture_status = 'mismatch'
            elif all(s in ('ok', 'na') for s in statuses) and 'ok' in statuses:
                rec.capture_status = 'ok'
            else:
                rec.capture_status = 'na'

    def action_seed_fuel_entries(self):
        """Create a Petrol and Diesel entry row for this shift if missing."""
        Entry = self.env['forecourt.shift.fuel.entry']
        for shift in self:
            existing = set(shift.fuel_entry_ids.mapped('fuel_type'))
            for fuel in ('petrol', 'diesel'):
                if fuel not in existing:
                    Entry.create({'shift_id': shift.id, 'fuel_type': fuel})
        return True