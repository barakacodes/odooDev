# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtFuelPriceHistory(models.Model):
    _name = 'forecourt.fuel.price.history'
    _description = 'Forecourt Fuel Price Change History'
    _order = 'effective_date desc, change_date desc'
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.template', string='Product', required=True, ondelete='cascade',
    )
    forecourt_product_type = fields.Selection(
        related='product_id.forecourt_product_type', store=True, string='Type',
    )
    old_price = fields.Float(string='Old Price (ZMW)', digits=(16, 4))
    new_price = fields.Float(string='New Price (ZMW)', digits=(16, 4))
    price_change = fields.Float(
        string='Change', digits=(16, 4), compute='_compute_price_change', store=True,
    )
    effective_date = fields.Date(
        string='Effective From',
        default=fields.Date.context_today,
        required=True,
        help='The date this new price takes effect. Defaults to today. '
             'Backdate this when entering a price that was set in the past '
             'so historical shift reconciliations use the correct rate.',
    )
    change_date = fields.Datetime(
        string='Changed On', default=fields.Datetime.now, required=True,
    )
    changed_by = fields.Many2one(
        'res.users', string='Changed By', default=lambda self: self.env.user,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
    )

    @api.depends('old_price', 'new_price')
    def _compute_price_change(self):
        for rec in self:
            rec.price_change = rec.new_price - rec.old_price