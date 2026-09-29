from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class ResPartner(models.Model):
    _inherit = 'res.partner'
    
    # Agent Fields
    is_agent = fields.Boolean(
        string='Is Agent',
        default=False,
        help='Check if this contact is an agent'
    )
    
    agent_code = fields.Char(
        string='Agent Code',
        help='Unique code for agent identification'
    )
    
    agent_commission_rate = fields.Float(
        string='Default Commission Rate (%)',
        help='Default commission rate for this agent',
        digits=(5, 2)
    )
    
    agent_commission_type = fields.Selection([
        ('percentage', 'Percentage of Sale'),
        ('fixed', 'Fixed Amount'),
        ('tiered', 'Tiered Based on Sales Volume'),
    ], string='Commission Type', default='percentage')
    
    agent_fixed_commission = fields.Monetary(
        string='Fixed Commission Amount',
        help='Fixed commission amount per sale (if type is fixed)',
        currency_field='currency_id'
    )
    
    agent_min_sales_target = fields.Monetary(
        string='Minimum Sales Target',
        help='Minimum sales target to earn commission',
        currency_field='currency_id'
    )
    
    agent_tier1_rate = fields.Float(
        string='Tier 1 Rate (%)',
        help='Commission rate for sales up to Tier 1 threshold',
        digits=(5, 2)
    )
    
    agent_tier1_threshold = fields.Monetary(
        string='Tier 1 Threshold',
        help='Sales amount threshold for Tier 1',
        currency_field='currency_id'
    )
    
    agent_tier2_rate = fields.Float(
        string='Tier 2 Rate (%)',
        help='Commission rate for sales between Tier 1 and Tier 2 thresholds',
        digits=(5, 2)
    )
    
    agent_tier2_threshold = fields.Monetary(
        string='Tier 2 Threshold',
        help='Sales amount threshold for Tier 2',
        currency_field='currency_id'
    )
    
    agent_tier3_rate = fields.Float(
        string='Tier 3 Rate (%)',
        help='Commission rate for sales above Tier 2 threshold',
        digits=(5, 2)
    )
    
    agent_commission_product_id = fields.Many2one(
        'product.product',
        string='Commission Product',
        help='Product used for commission lines'
    )
    
    agent_bank_account = fields.Char(
        string='Bank Account Number',
        help='Agent bank account for commission payments'
    )
    
    agent_bank_name = fields.Char(
        string='Bank Name',
        help='Agent bank name'
    )
    
    # Statistics
    agent_total_sales = fields.Monetary(
        string='Total Sales',
        compute='_compute_agent_stats',
        currency_field='currency_id'
    )
    
    agent_total_commission = fields.Monetary(
        string='Total Commission Earned',
        compute='_compute_agent_stats',
        currency_field='currency_id'
    )
    
    agent_paid_commission = fields.Monetary(
        string='Paid Commission',
        compute='_compute_agent_stats',
        currency_field='currency_id'
    )
    
    agent_pending_commission = fields.Monetary(
        string='Pending Commission',
        compute='_compute_agent_stats',
        currency_field='currency_id'
    )
    
    agent_sale_count = fields.Integer(
        string='Number of Sales',
        compute='_compute_agent_stats'
    )
    
    agent_rank = fields.Integer(
        string='Agent Rank',
        compute='_compute_agent_rank',
        help='Rank based on total sales'
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    @api.constrains('agent_code')
    def _check_agent_code(self):
        for record in self:
            if record.is_agent and record.agent_code:
                existing = self.search([
                    ('agent_code', '=', record.agent_code),
                    ('id', '!=', record.id)
                ])
                if existing:
                    raise ValidationError(_('Agent Code must be unique!'))
    
    @api.depends('is_agent')
    def _compute_agent_stats(self):
        CommissionLine = self.env['topseed.commission.line']
        for partner in self:
            if partner.is_agent:
                lines = CommissionLine.search([('agent_id', '=', partner.id)])
                partner.agent_total_sales = sum(lines.mapped('sale_amount'))
                partner.agent_total_commission = sum(lines.mapped('commission_amount'))
                partner.agent_paid_commission = sum(lines.filtered(
                    lambda l: l.state == 'paid'
                ).mapped('commission_amount'))
                partner.agent_pending_commission = sum(lines.filtered(
                    lambda l: l.state in ['pending', 'approved']
                ).mapped('commission_amount'))
                partner.agent_sale_count = len(lines.mapped('sale_order_id'))
            else:
                partner.agent_total_sales = 0.0
                partner.agent_total_commission = 0.0
                partner.agent_paid_commission = 0.0
                partner.agent_pending_commission = 0.0
                partner.agent_sale_count = 0
    
    def _compute_agent_rank(self):
        agents = self.search([('is_agent', '=', True)])
        sorted_agents = agents.sorted(key=lambda a: a.agent_total_sales, reverse=True)
        for rank, agent in enumerate(sorted_agents, 1):
            agent.agent_rank = rank
    
    def action_view_agent_commissions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Commissions for %s') % self.name,
            'res_model': 'topseed.commission.line',
            'domain': [('agent_id', '=', self.id)],
            'view_mode': 'tree,form',
            'target': 'current',
        }
    
    def action_view_agent_sales(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sales for %s') % self.name,
            'res_model': 'sale.order',
            'domain': [('agent_id', '=', self.id)],
            'view_mode': 'tree,form',
            'target': 'current',
        }