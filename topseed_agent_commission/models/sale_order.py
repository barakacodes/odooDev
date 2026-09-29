from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    
    # Agent fields
    agent_id = fields.Many2one(
        'res.partner',
        string='Agent',
        domain="[('is_agent', '=', True)]",
        help='Agent responsible for this sale'
    )
    
    commission_ids = fields.One2many(
        'topseed.commission.line',
        'sale_order_id',
        string='Commissions'
    )
    
    commission_total = fields.Monetary(
        string='Total Commission',
        compute='_compute_commission_total',
        store=True,
        currency_field='currency_id'
    )
    
    commission_status = fields.Selection([
        ('pending', 'Pending Calculation'),
        ('calculated', 'Calculated'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
    ], string='Commission Status', default='pending')
    
    @api.depends('commission_ids.commission_amount')
    def _compute_commission_total(self):
        for order in self:
            order.commission_total = sum(order.commission_ids.mapped('commission_amount'))
    
    def action_confirm(self):
        result = super().action_confirm()
        for order in self:
            if order.agent_id:
                order._calculate_commission()
        return result
    
    def action_calculate_commission(self):
        """Manually trigger commission calculation"""
        self.ensure_one()
        if not self.agent_id:
            raise UserError(_('Please assign an agent to this sale order.'))
        self._calculate_commission()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Commission calculated successfully.'),
                'sticky': False,
                'type': 'success'
            }
        }
    
    def _calculate_commission(self):
        """Calculate commission for this sale order"""
        self.ensure_one()
        
        # Clear existing commission lines
        self.commission_ids.unlink()
        
        # Get commission rules
        rules = self.env['topseed.commission.rule'].search([
            ('active', '=', True)
        ])
        
        config = self.env['topseed.commission.config'].get_default_config()
        
        for line in self.order_line:
            rule = self._get_applicable_rule(line, rules)
            if rule:
                commission_amount = rule.calculate_commission(self, line)
                if commission_amount > 0:
                    self._create_commission_line(line, rule, commission_amount, config)
            else:
                # Use default agent rate if no rule applies
                if self.agent_id.agent_commission_rate > 0:
                    commission_amount = line.price_subtotal * (self.agent_id.agent_commission_rate / 100)
                    if commission_amount > 0:
                        self._create_default_commission_line(line, commission_amount, config)
        
        self.commission_status = 'calculated'
    
    def _get_applicable_rule(self, line, rules):
        """Find the first applicable rule for a line"""
        for rule in rules:
            # Check product applicability
            if rule.product_ids and line.product_id not in rule.product_ids:
                continue
            if rule.product_category_ids and line.product_id.categ_id not in rule.product_category_ids:
                continue
            
            # Check agent applicability
            if rule.agent_ids and self.agent_id not in rule.agent_ids:
                continue
            
            return rule
        
        return None
    
    def _create_commission_line(self, line, rule, commission_amount, config):
        """Create a commission line"""
        vals = {
            'sale_order_id': self.id,
            'sale_line_id': line.id,
            'agent_id': self.agent_id.id,
            'product_id': line.product_id.id,
            'rule_id': rule.id,
            'sale_amount': line.price_subtotal,
            'commission_amount': commission_amount,
            'state': 'pending',
            'date': fields.Date.today(),
        }
        self.env['topseed.commission.line'].create(vals)
    
    def _create_default_commission_line(self, line, commission_amount, config):
        """Create a commission line using default agent rate"""
        vals = {
            'sale_order_id': self.id,
            'sale_line_id': line.id,
            'agent_id': self.agent_id.id,
            'product_id': line.product_id.id,
            'sale_amount': line.price_subtotal,
            'commission_amount': commission_amount,
            'commission_rate': self.agent_id.agent_commission_rate,
            'state': 'pending',
            'date': fields.Date.today(),
        }
        self.env['topseed.commission.line'].create(vals)

        