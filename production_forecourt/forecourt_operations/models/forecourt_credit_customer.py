# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtCreditCustomer(models.Model):
    _name = 'forecourt.credit.customer'
    _description = 'Forecourt Prepaid/Credit Customer Account'
    _order = 'code'
    _rec_name = 'display_name'

    code = fields.Char(string='Account Code', required=True, index=True,
                       help='NetPOS debtor account code, e.g. "ARC 002".')
    name = fields.Char(string='Customer Name', required=True)
    display_name = fields.Char(compute='_compute_display_name', store=True)
    branch_id = fields.Many2one(
        'stock.warehouse', string='Branch',
        domain=[('is_forecourt_branch', '=', True)],
        help='Derived from the account code prefix on import (e.g. ARC -> Arcades).',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Linked Contact',
        help='Optional link to a full contact record for invoicing/communication.',
    )
    opening_balance = fields.Monetary(
        string='Opening Balance (K)', currency_field='currency_id',
        help='Debt balance carried over from before this system started tracking '
             'transactions â€” set from the first Account Balances import for this '
             'customer, if any.',
    )
    transaction_ids = fields.One2many(
        'forecourt.credit.transaction', 'customer_id', string='Transactions',
    )
    computed_balance = fields.Monetary(
        string='Computed Balance (K)', currency_field='currency_id',
        compute='_compute_balance', store=True,
        help='opening_balance + sum of all imported transaction amounts.',
    )
    netpos_reported_balance = fields.Monetary(
        string='NetPOS Reported Balance (K)', currency_field='currency_id',
        help='Latest balance as stated by NetPOS itself, from the most recent '
             'Account Balances report import â€” for reconciliation only.',
    )
    netpos_balance_date = fields.Date(string='NetPOS Balance As Of')
    balance_variance = fields.Monetary(
        string='Balance Variance (K)', currency_field='currency_id',
        compute='_compute_balance_variance', store=True,
        help='computed_balance - netpos_reported_balance. Non-zero means our '
             'records and NetPOS disagree â€” investigate before trusting either.',
    )
    currency_id = fields.Many2one(
        'res.currency', string='Currency', default=lambda self: self.env.company.currency_id,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('uniq_code_company', 'unique(code, company_id)',
         'A credit customer account code must be unique per company.'),
    ]

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else rec.name

    @api.depends('opening_balance', 'transaction_ids.amount')
    def _compute_balance(self):
        for rec in self:
            rec.computed_balance = rec.opening_balance + sum(
                rec.transaction_ids.mapped('amount')
            )

    @api.depends('computed_balance', 'netpos_reported_balance')
    def _compute_balance_variance(self):
        for rec in self:
            rec.balance_variance = rec.computed_balance - rec.netpos_reported_balance

    def action_set_opening_balance_from_netpos(self):
        """Absorb the gap between our transaction history and NetPOS's
        stated balance into opening_balance, so computed_balance reconciles
        to netpos_reported_balance exactly at this moment. Use this once
        you're confident all currently-available transaction history has
        been imported â€” any NEW transactions imported after this point will
        then correctly show as fresh variance if something doesn't add up."""
        for rec in self:
            txn_sum = sum(rec.transaction_ids.mapped('amount'))
            rec.opening_balance = rec.netpos_reported_balance - txn_sum
        return True
