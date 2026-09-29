from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class TopseedCommissionConfig(models.Model):
    _name = 'topseed.commission.config'
    _description = 'TopSeed Commission Configuration'
    _rec_name = 'name'
    
    name = fields.Char(
        string='Configuration Name',
        required=True,
        default='Default Commission Config'
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )
    
    commission_product_id = fields.Many2one(
        'product.product',
        string='Default Commission Product',
        required=True,
        help='Default product used for commission lines'
    )
    
    commission_account_id = fields.Many2one(
        'account.account',
        string='Commission Expense Account',
        required=True,
        help='Account used for commission expenses'
    )
    
    commission_journal_id = fields.Many2one(
        'account.journal',
        string='Commission Journal',
        required=True,
        help='Journal for commission entries'
    )
    
    default_rate = fields.Float(
        string='Default Commission Rate (%)',
        default=5.0,
        digits=(5, 2),
        help='Default rate if not specified on agent'
    )
    
    calculation_method = fields.Selection([
        ('percentage', 'Percentage of Sale'),
        ('fixed', 'Fixed Amount'),
        ('tiered', 'Tiered Based on Sales Volume'),
    ], string='Default Calculation Method', default='percentage')
    
    calculation_basis = fields.Selection([
        ('total', 'Total Sale Amount'),
        ('profit', 'Profit Margin'),
        ('product', 'Per Product'),
    ], string='Calculation Basis', default='total')
    
    commission_due_days = fields.Integer(
        string='Commission Due Days',
        default=30,
        help='Number of days after invoice payment when commission is due'
    )
    
    auto_approve_commission = fields.Boolean(
        string='Auto-Approve Commission',
        default=True,
        help='Automatically approve commissions when invoice is paid'
    )
    
    commission_threshold = fields.Monetary(
        string='Minimum Commission Amount',
        help='Minimum commission amount to trigger payment',
        currency_field='currency_id'
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    active = fields.Boolean(string='Active', default=True)
    
    @api.constrains('commission_due_days')
    def _check_commission_due_days(self):
        for record in self:
            if record.commission_due_days < 0:
                raise ValidationError(_('Commission due days cannot be negative.'))
    
    @api.model
    def get_default_config(self):
        return self.search([('active', '=', True)], limit=1)