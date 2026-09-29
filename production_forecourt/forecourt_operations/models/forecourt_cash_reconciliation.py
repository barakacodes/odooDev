# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, AccessError


class ForecourtCashReconciliation(models.Model):
    _name = 'forecourt.cash.reconciliation'
    _description = 'Forecourt Cash Reconciliation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'recon_date desc, name desc'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default='New', readonly=True,
    )
    recon_date = fields.Date(
        string='Date', required=True, default=fields.Date.today,
    )
    shift_id = fields.Many2one(
        'forecourt.shift', string='Shift', required=True,
        domain=[('state', '=', 'closed')],
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )
    prepared_by = fields.Many2one(
        'res.users', string='Prepared By',
        default=lambda self: self.env.user,
    )

    # â”€â”€ Fuel Cash â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    fuel_cash = fields.Monetary(
        string='Fuel Cash Sales (K)', currency_field='currency_id',
    )
    pos_amount = fields.Monetary(
        string='POS / Card Amount (K)', currency_field='currency_id',
    )
    mobile_money_amount = fields.Monetary(
        string='Mobile Money (K)', currency_field='currency_id',
    )
    fuel_cash_at_hand = fields.Monetary(
        string='Fuel Cash @ Hand (K)', currency_field='currency_id',
    )

    # â”€â”€ Lubes & LPG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    lubes_cash = fields.Monetary(
        string='Lubes Cash (K)', currency_field='currency_id',
    )
    lpg_cash = fields.Monetary(
        string='LPG Cash (K)', currency_field='currency_id',
    )

    # â”€â”€ Bank â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    bank_deposit = fields.Monetary(
        string='Bank Deposit (K)', currency_field='currency_id',
    )
    bank_reference = fields.Char(string='Bank Slip / Reference')

    # â”€â”€ Computed â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    total_cash_collected = fields.Monetary(
        string='Total Cash Collected',
        compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    total_expected = fields.Monetary(
        string='Total Expected',
        compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    variance = fields.Monetary(
        string='Variance (Over/Short)',
        compute='_compute_totals', store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency',
    )

    # â”€â”€ Status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
    ], string='Status', default='draft', required=True, tracking=True)

    notes = fields.Text(string='Notes / Discrepancies')

    # â”€â”€ Journal entry reference â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    move_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True,
    )

    @api.depends(
        'fuel_cash', 'pos_amount', 'mobile_money_amount',
        'lubes_cash', 'lpg_cash', 'bank_deposit', 'fuel_cash_at_hand',
    )
    def _compute_totals(self):
        for rec in self:
            rec.total_cash_collected = (
                rec.fuel_cash_at_hand + rec.lubes_cash + rec.lpg_cash
            )
            rec.total_expected = (
                rec.fuel_cash + rec.lubes_cash + rec.lpg_cash
            )
            rec.variance = rec.total_cash_collected - rec.bank_deposit

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.cash.reconciliation'
                ) or 'New'
        return super().create(vals_list)

    @api.onchange('shift_id')
    def _onchange_shift(self):
        """Auto-fill from shift totals when shift is selected."""
        if self.shift_id:
            shift = self.shift_id
            self.recon_date = shift.date_start.date() if shift.date_start else fields.Date.today()
            self.fuel_cash = shift.cash_total
            self.pos_amount = shift.card_total
            self.mobile_money_amount = shift.mobile_money_total
            self.lubes_cash = sum(
                s.total_amount for s in shift.sale_ids
                if s.product_type == 'lubricant'
            )
            self.lpg_cash = sum(
                s.total_amount for s in shift.sale_ids
                if s.product_type == 'lpg'
            )

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Only draft reconciliations can be submitted.')
            rec.write({'state': 'submitted'})

    def action_approve(self):
        if not self.env.user.has_group('forecourt_operations.group_forecourt_supervisor'):
            raise AccessError('Only a Forecourt Supervisor or Manager can approve reconciliations.')
        for rec in self:
            if rec.state != 'submitted':
                raise UserError('Only submitted reconciliations can be approved.')
            rec.write({'state': 'approved'})

    def action_reset_draft(self):
        if not self.env.user.has_group('forecourt_operations.group_forecourt_manager'):
            raise AccessError('Only a Forecourt Manager can reset a reconciliation to draft.')
        for rec in self:
            rec.write({'state': 'draft'})

    def action_create_journal_entry(self):
        """Create a Cashâ†’Bank journal entry for this reconciliation."""
        self.ensure_one()
        if self.move_id:
            raise UserError('Journal entry already created for this reconciliation.')
        if not self.bank_deposit:
            raise UserError('Bank deposit amount is required.')

        # Get journals and accounts
        c2b_journal = self.env['account.journal'].search([
            ('code', '=', 'C2B'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not c2b_journal:
            raise UserError('Cash To Bank journal (C2B) not found for this company.')

        # Get cash and bank accounts from journal
        cash_account = c2b_journal.default_account_id
        bank_account = self.env['account.account'].search([
            ('account_type', '=', 'asset_cash'),
            ('company_id', '=', self.company_id.id),
            ('name', 'ilike', 'Main'),
        ], limit=1)

        if not cash_account or not bank_account:
            raise UserError('Could not find Cash or Bank accounts. Please configure journals.')

        move = self.env['account.move'].create({
            'journal_id': c2b_journal.id,
            'company_id': self.company_id.id,
            'date': self.recon_date,
            'ref': f'{self.name} â€” {self.shift_id.name} Bank Deposit',
            'line_ids': [
                (0, 0, {
                    'account_id': bank_account.id,
                    'debit': self.bank_deposit,
                    'credit': 0.0,
                    'name': f'{self.name} â€” Bank Deposit',
                }),
                (0, 0, {
                    'account_id': cash_account.id,
                    'debit': 0.0,
                    'credit': self.bank_deposit,
                    'name': f'{self.name} â€” Bank Deposit',
                }),
            ],
        })
        move.action_post()
        self.move_id = move
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }
