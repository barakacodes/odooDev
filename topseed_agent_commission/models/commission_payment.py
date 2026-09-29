from odoo import models, fields, api, _
from odoo.exceptions import UserError

class TopseedCommissionPayment(models.Model):
    _name = 'topseed.commission.payment'
    _description = 'TopSeed Commission Payment'
    _rec_name = 'payment_number'
    _order = 'payment_date desc, id desc'
    
    payment_number = fields.Char(
        string='Payment Number',
        required=True,
        copy=False,
        default=lambda self: _('New')
    )
    
    agent_id = fields.Many2one(
        'res.partner',
        string='Agent',
        required=True,
        domain="[('is_agent', '=', True)]"
    )
    
    payment_date = fields.Date(
        string='Payment Date',
        required=True,
        default=fields.Date.today
    )
    
    payment_amount = fields.Monetary(
        string='Payment Amount',
        required=True,
        currency_field='currency_id'
    )
    
    commission_line_ids = fields.Many2many(
        'topseed.commission.line',
        string='Commission Lines'
    )
    
    payment_method = fields.Selection([
        ('bank_transfer', 'Bank Transfer'),
        ('cash', 'Cash'),
        ('cheque', 'Cheque'),
        ('mobile_money', 'Mobile Money'),
    ], string='Payment Method', required=True, default='bank_transfer')
    
    reference = fields.Char(string='Reference Number')
    bank_account = fields.Char(string='Bank Account')
    bank_name = fields.Char(string='Bank Name')
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft')
    
    note = fields.Text(string='Notes')
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )
    
    @api.model
    def create(self, vals):
        if vals.get('payment_number', _('New')) == _('New'):
            vals['payment_number'] = self.env['ir.sequence'].next_by_code('topseed.commission.payment') or _('New')
        return super().create(vals)
    
    def action_approve(self):
        self.state = 'approved'
    
    def action_confirm_paid(self):
        self.state = 'paid'
        self.commission_line_ids.write({'state': 'paid', 'payment_id': self.id})
    
    def action_cancel(self):
        self.state = 'cancelled'
        self.commission_line_ids.write({'state': 'pending', 'payment_id': False})