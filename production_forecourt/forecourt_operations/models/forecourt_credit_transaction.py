# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtCreditTransaction(models.Model):
    _name = 'forecourt.credit.transaction'
    _description = 'Forecourt Prepaid/Credit Customer Transaction (from NetPOS)'
    _order = 'date desc, id desc'

    customer_id = fields.Many2one(
        'forecourt.credit.customer', string='Customer', required=True,
        ondelete='cascade', index=True,
    )
    date = fields.Date(string='Date', required=True, index=True)
    invoice_number = fields.Char(string='Invoice #')
    product_type = fields.Selection([
        ('petrol', 'Petrol'),
        ('diesel', 'Diesel'),
        ('other', 'Other'),
    ], string='Product', required=True)
    stock_description = fields.Char(string='Stock Description (raw)')
    pump_code = fields.Char(string='Pump / Nozzle')
    quantity = fields.Float(string='Quantity (L)', digits=(16, 3))
    unit_price = fields.Float(string='Unit Price (ZMW/L)', digits=(16, 4))
    discount = fields.Monetary(string='Discount (K)', currency_field='currency_id')
    amount = fields.Monetary(string='Amount (K)', currency_field='currency_id')
    shift_id = fields.Many2one(
        'forecourt.shift', string='Matched Shift',
        help='Auto-matched by branch + date when the corresponding WhatsApp '
             'shift report has already been imported.',
    )
    branch_id = fields.Many2one(
        related='customer_id.branch_id', store=True, string='Branch',
    )
    currency_id = fields.Many2one(
        related='customer_id.currency_id', string='Currency',
    )
    company_id = fields.Many2one(
        related='customer_id.company_id', store=True, string='Company',
    )
    import_source = fields.Char(
        string='Import Source', help='Filename of the PDF this row came from.',
    )

    _sql_constraints = [
        ('uniq_invoice_customer', 'unique(customer_id, invoice_number, product_type)',
         'This transaction (same customer, invoice, product) has already been imported.'),
    ]
