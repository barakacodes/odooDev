from odoo import models, fields, api, _

class TopseedCommissionLine(models.Model):
    _name = 'topseed.commission.line'
    _description = 'TopSeed Commission Line'
    _rec_name = 'display_name'
    _order = 'date desc, id desc'
    
    display_name = fields.Char(
        string='Display Name',
        compute='_compute_display_name',
        store=True
    )
    
    # Relationships
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        ondelete='cascade'
    )
    
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        ondelete='cascade'
    )
    
    agent_id = fields.Many2one(
        'res.partner',
        string='Agent',
        required=True,
        domain="[('is_agent', '=', True)]"
    )
    
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True
    )
    
    rule_id = fields.Many2one(
        'topseed.commission.rule',
        string='Commission Rule'
    )
    
    # Commission details
    sale_amount = fields.Monetary(
        string='Sale Amount',
        required=True,
        currency_field='currency_id'
    )
    
    commission_rate = fields.Float(
        string='Commission Rate (%)',
        digits=(5, 2)
    )
    
    commission_amount = fields.Monetary(
        string='Commission Amount',
        required=True,
        currency_field='currency_id'
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    # Status
    state = fields.Selection([
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='pending', required=True)
    
    date = fields.Date(string='Commission Date', default=fields.Date.today)
    
    due_date = fields.Date(
        string='Due Date',
        compute='_compute_due_date',
        store=True
    )
    
    payment_id = fields.Many2one(
        'topseed.commission.payment',
        string='Payment'
    )
    
    note = fields.Text(string='Notes')
    
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )
    
    @api.depends('sale_order_id', 'agent_id', 'product_id')
    def _compute_display_name(self):
        for line in self:
            line.display_name = f"{line.agent_id.name} - {line.product_id.name} - {line.sale_order_id.name}"
    
    @api.depends('date')
    def _compute_due_date(self):
        config = self.env['topseed.commission.config'].get_default_config()
        for line in self:
            if config:
                line.due_date = fields.Date.add(line.date, days=config.commission_due_days)
            else:
                line.due_date = fields.Date.add(line.date, days=30)
    
    def action_approve(self):
        self.write({'state': 'approved'})
    
    def action_pay(self):
        self.write({'state': 'paid'})
    
    def action_cancel(self):
        self.write({'state': 'cancelled'})