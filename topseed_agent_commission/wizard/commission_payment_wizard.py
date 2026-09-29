from odoo import models, fields, api, _
from odoo.exceptions import UserError

class CommissionPaymentWizard(models.TransientModel):
    _name = 'commission.payment.wizard'
    _description = 'Commission Payment Wizard'
    
    agent_id = fields.Many2one(
        'res.partner',
        string='Agent',
        required=True,
        domain="[('is_agent', '=', True)]"
    )
    
    commission_line_ids = fields.Many2many(
        'topseed.commission.line',
        string='Commission Lines',
        domain="[('agent_id', '=', agent_id), ('state', 'in', ['pending', 'approved'])]"
    )
    
    total_amount = fields.Monetary(
        string='Total Amount',
        compute='_compute_total_amount',
        currency_field='currency_id'
    )
    
    payment_method = fields.Selection([
        ('bank_transfer', 'Bank Transfer'),
        ('cash', 'Cash'),
        ('cheque', 'Cheque'),
        ('mobile_money', 'Mobile Money'),
    ], string='Payment Method', default='bank_transfer')
    
    payment_date = fields.Date(
        string='Payment Date',
        default=fields.Date.today
    )
    
    reference = fields.Char(string='Reference Number')
    note = fields.Text(string='Notes')
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id
    )
    
    @api.depends('commission_line_ids.commission_amount')
    def _compute_total_amount(self):
        for wizard in self:
            wizard.total_amount = sum(wizard.commission_line_ids.mapped('commission_amount'))
    
    def action_create_payment(self):
        if not self.commission_line_ids:
            raise UserError(_('Please select at least one commission line to pay.'))
        
        payment = self.env['topseed.commission.payment'].create({
            'agent_id': self.agent_id.id,
            'payment_date': self.payment_date,
            'payment_amount': self.total_amount,
            'commission_line_ids': [(6, 0, self.commission_line_ids.ids)],
            'payment_method': self.payment_method,
            'reference': self.reference,
            'note': self.note,
        })
        
        self.commission_line_ids.write({'state': 'approved'})
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'topseed.commission.payment',
            'res_id': payment.id,
            'view_mode': 'form',
            'target': 'current',
        }


