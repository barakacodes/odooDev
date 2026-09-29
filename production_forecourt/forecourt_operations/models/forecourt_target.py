# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtTarget(models.Model):
    _name = 'forecourt.target'
    _description = 'Forecourt Monthly Target'
    _order = 'year desc, month desc, branch'
    _rec_name = 'display_name'

    branch = fields.Selection([
        ('arcades', 'Arcades'),
        ('luanshya', 'Luanshya'),
        ('chililabombwe', 'Chililabombwe'),
    ], string='Branch', required=True)

    month = fields.Selection([
        ('1','January'),('2','February'),('3','March'),('4','April'),
        ('5','May'),('6','June'),('7','July'),('8','August'),
        ('9','September'),('10','October'),('11','November'),('12','December'),
    ], string='Month', required=True)

    year = fields.Integer(string='Year', required=True,
                          default=lambda self: fields.Date.today().year)

    petrol_volume_target = fields.Float(string='Petrol Volume Target (L)', digits=(16, 2))
    diesel_volume_target = fields.Float(string='Diesel Volume Target (L)', digits=(16, 2))
    revenue_target = fields.Monetary(string='Revenue Target (K)', currency_field='currency_id')

    company_id = fields.Many2one('res.company', string='Company',
                                  default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    display_name = fields.Char(compute='_compute_display_name', store=True)

    _sql_constraints = [
        ('uniq_branch_month_year',
         'unique(branch, month, year, company_id)',
         'A target already exists for this branch and month.'),
    ]

    @api.depends('branch', 'month', 'year')
    def _compute_display_name(self):
        month_map = dict(self._fields['month'].selection)
        branch_map = dict(self._fields['branch'].selection)
        for rec in self:
            rec.display_name = f"{branch_map.get(rec.branch,'')} â€” {month_map.get(rec.month,'')} {rec.year}"
