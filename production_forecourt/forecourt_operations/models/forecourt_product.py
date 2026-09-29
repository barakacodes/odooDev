# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    forecourt_product_type = fields.Selection([
        ('fuel', 'Fuel'),
        ('lpg', 'LPG'),
        ('lubricant', 'Lubricant / Oil'),
        ('other', 'Other'),
    ], string='Forecourt Type',
       help='Classify this product for forecourt operations. '
            'Required for products used on the forecourt.')

    fuel_grade = fields.Selection([
        ('petrol', 'Petrol (Gasoline)'),
        ('diesel', 'Diesel'),
        ('kerosene', 'Kerosene / Paraffin'),
    ], string='Fuel Grade',
       help='Applicable when Forecourt Type is Fuel.')

    lpg_cylinder_size = fields.Selection([
        ('9kg', '9 kg'),
        ('19kg', '19 kg'),
        ('48kg', '48 kg'),
    ], string='Cylinder Size',
       help='Applicable when Forecourt Type is LPG.')

    unit_of_sale = fields.Selection([
        ('litres', 'Litres'),
        ('cylinders', 'Cylinders'),
        ('units', 'Units'),
    ], string='Unit of Sale', default='litres')

    forecourt_selling_price = fields.Float(
        string='Forecourt Selling Price (ZMW)',
        digits=(16, 4),
        help='Default pump/counter price. Can be overridden on individual sales.',
    )

    # Both list_price (edited via Configuration → Monthly Price Update)
    # and forecourt_selling_price (edited elsewhere) are logged to the
    # price history. The variance calculation reads the newest entry
    # effective on or before the shift date.
    _PRICE_TRACKED_FIELDS = ('list_price', 'forecourt_selling_price')

    def write(self, vals):
        # Capture old prices before the actual write, keyed by record id.
        old_prices = {}
        for field_name in self._PRICE_TRACKED_FIELDS:
            if field_name in vals:
                old_prices[field_name] = {
                    rec.id: (rec[field_name] or 0.0) for rec in self
                }

        result = super().write(vals)

        # Only log history after the write succeeded.
        history_vals = []
        for field_name, per_rec_old in old_prices.items():
            new_price = vals[field_name]
            for rec in self:
                old_price = per_rec_old.get(rec.id) or 0.0
                if abs(old_price - new_price) > 1e-6:
                    history_vals.append({
                        'product_id': rec.id,
                        'old_price': old_price,
                        'new_price': new_price,
                        'company_id': self.env.company.id,
                        'effective_date': fields.Date.context_today(self),
                    })

        if history_vals:
            self.env['forecourt.fuel.price.history'].sudo().create(history_vals)

        return result