from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class TopseedCommissionRule(models.Model):
    _name = 'topseed.commission.rule'
    _description = 'TopSeed Commission Rule'
    _rec_name = 'name'
    _order = 'sequence'
    
    name = fields.Char(string='Rule Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    
    active = fields.Boolean(string='Active', default=True)
    
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )
    
    # Product applicability
    product_ids = fields.Many2many(
        'product.product',
        string='Specific Products',
        help='If empty, rule applies to all products'
    )
    
    product_category_ids = fields.Many2many(
        'product.category',
        string='Product Categories',
        help='If empty, rule applies to all categories'
    )
    
    # Agent applicability
    agent_ids = fields.Many2many(
        'res.partner',
        string='Specific Agents',
        help='If empty, rule applies to all agents'
    )
    
    # Commission calculation
    commission_type = fields.Selection([
        ('percentage', 'Percentage of Sale'),
        ('fixed', 'Fixed Amount'),
        ('tiered', 'Tiered Based on Sales Volume'),
    ], string='Commission Type', required=True, default='percentage')
    
    commission_rate = fields.Float(
        string='Commission Rate (%)',
        digits=(5, 2),
        help='Percentage of sale amount'
    )
    
    fixed_amount = fields.Monetary(
        string='Fixed Amount',
        help='Fixed commission amount per sale',
        currency_field='currency_id'
    )
    
    # Tiered rates
    tier1_rate = fields.Float(
        string='Tier 1 Rate (%)',
        digits=(5, 2)
    )
    
    tier1_threshold = fields.Monetary(
        string='Tier 1 Threshold',
        currency_field='currency_id'
    )
    
    tier2_rate = fields.Float(
        string='Tier 2 Rate (%)',
        digits=(5, 2)
    )
    
    tier2_threshold = fields.Monetary(
        string='Tier 2 Threshold',
        currency_field='currency_id'
    )
    
    tier3_rate = fields.Float(
        string='Tier 3 Rate (%)',
        digits=(5, 2)
    )
    
    # Minimum requirements
    min_sales_amount = fields.Monetary(
        string='Minimum Sale Amount',
        help='Minimum sale amount to qualify for commission',
        currency_field='currency_id'
    )
    
    min_quantity = fields.Float(
        string='Minimum Quantity',
        help='Minimum product quantity to qualify for commission'
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    @api.constrains('commission_type', 'commission_rate', 'fixed_amount')
    def _check_commission_fields(self):
        for rule in self:
            if rule.commission_type == 'percentage' and rule.commission_rate <= 0:
                raise ValidationError(_('Commission rate must be greater than 0 for percentage type.'))
            if rule.commission_type == 'fixed' and rule.fixed_amount <= 0:
                raise ValidationError(_('Fixed amount must be greater than 0 for fixed type.'))
    
    def calculate_commission(self, sale_order, line=None):
        """
        Calculate commission for a sale order or line
        Returns: commission amount
        """
        self.ensure_one()
        base_amount = 0.0
        
        # Calculate base amount
        if line:
            base_amount = line.price_subtotal
        else:
            base_amount = sale_order.amount_untaxed
        
        # Check minimum requirements
        if self.min_sales_amount and base_amount < self.min_sales_amount:
            return 0.0
        if self.min_quantity and line and line.product_uom_qty < self.min_quantity:
            return 0.0
        
        # Calculate commission based on type
        if self.commission_type == 'percentage':
            return base_amount * (self.commission_rate / 100)
        elif self.commission_type == 'fixed':
            return self.fixed_amount
        elif self.commission_type == 'tiered':
            return self._calculate_tiered_commission(base_amount)
        
        return 0.0
    
    def _calculate_tiered_commission(self, amount):
        """Calculate tiered commission based on amount"""
        commission = 0.0
        
        # Tier 1
        if self.tier1_rate and self.tier1_threshold:
            tier1_amount = min(amount, self.tier1_threshold)
            commission += tier1_amount * (self.tier1_rate / 100)
            remaining = amount - tier1_amount
        else:
            remaining = amount
        
        # Tier 2
        if self.tier2_rate and self.tier2_threshold and remaining > 0:
            tier2_amount = min(remaining, self.tier2_threshold - self.tier1_threshold)
            commission += tier2_amount * (self.tier2_rate / 100)
            remaining = remaining - tier2_amount
        
        # Tier 3
        if self.tier3_rate and remaining > 0:
            commission += remaining * (self.tier3_rate / 100)
        
        return commission