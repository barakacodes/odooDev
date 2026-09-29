# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ForecourtFuelRate(models.Model):
    _name = 'forecourt.fuel.rate'
    _description = 'Forecourt Fuel Pump Rate (effective-dated)'
    _order = 'effective_date desc'
    _rec_name = 'effective_date'

    effective_date = fields.Date(
        string='Effective From',
        required=True,
        default=fields.Date.context_today,
        help='Rate applies to shifts on or after this date, until a later '
             'effective-dated record supersedes it.',
    )
    petrol_rate = fields.Float(
        string='Petrol Rate (ZMW/L)',
        digits=(16, 4),
        required=True,
    )
    diesel_rate = fields.Float(
        string='Diesel Rate (ZMW/L)',
        digits=(16, 4),
        required=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    petrol_cost = fields.Float(
        string='Petrol Cost Price (ZMW/L)', digits=(16, 4),
        help='Purchase cost per litre of petrol for stock valuation.',
    )
    diesel_cost = fields.Float(
        string='Diesel Cost Price (ZMW/L)', digits=(16, 4),
        help='Purchase cost per litre of diesel for stock valuation.',
    )
    notes = fields.Char(string='Notes')

    _sql_constraints = [
        ('uniq_effective_date_company',
         'unique(effective_date, company_id)',
         'Only one fuel-rate record per effective date per company.'),
    ]

    @api.constrains('petrol_rate', 'diesel_rate')
    def _check_rates_positive(self):
        for rec in self:
            if rec.petrol_rate <= 0 or rec.diesel_rate <= 0:
                raise ValidationError('Fuel rates must be greater than zero.')

    @api.model
    def get_rate_for(self, on_date, company=None, branch=None):
        """Return (petrol_rate, diesel_rate) effective on `on_date`.

        Resolution order (first match wins):
          1. A record in this model with an effective_date on or before
             `on_date`.
          2. A price from forecourt.fuel.price.history effective on or
             before `on_date` — populated automatically when list_price
             is edited via Configuration → Monthly Price Update.
          3. The product's current list_price (last resort).
          4. (0.0, 0.0) — nothing is configured anywhere.

        The `branch` argument is accepted for forward-compatibility with
        branch-specific rates. When branch-specific records are added
        later, this method will automatically prefer them; today it
        ignores it and resolves against the company-wide default.
        """
        company = company or self.env.company
        target_date = (
            fields.Date.to_date(on_date) if on_date
            else fields.Date.context_today(self)
        )

        # 1. Static rate table (company-wide)
        rate = self.search(
            [('effective_date', '<=', target_date),
             ('company_id', 'in', [company.id, False])],
            order='effective_date desc, company_id desc',
            limit=1,
        )
        if rate:
            return rate.petrol_rate, rate.diesel_rate

        # 2. Product price history
        History = self.env['forecourt.fuel.price.history'].sudo()
        Product = self.env['product.template'].sudo()

        def latest_price_for(grade):
            product = Product.search([
                ('forecourt_product_type', '=', 'fuel'),
                ('fuel_grade', '=', grade),
                ('active', '=', True),
            ], order='id asc', limit=1)

            if not product:
                product = Product.search([
                    ('name', 'ilike', grade),
                    ('active', '=', True),
                ], order='id asc', limit=1)

            if not product:
                return 0.0

            entry = History.search([
                ('product_id', '=', product.id),
                ('effective_date', '<=', target_date),
                ('company_id', 'in', [company.id, False]),
            ], order='effective_date desc, id desc', limit=1)

            if entry:
                return entry.new_price or 0.0

            # 3. Current list_price fallback
            return product.list_price or 0.0

        return latest_price_for('petrol'), latest_price_for('diesel')