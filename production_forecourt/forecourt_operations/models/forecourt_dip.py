# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtDipReading(models.Model):
    _name = 'forecourt.dip.reading'
    _description = 'Tank Dip Reading'
    _order = 'reading_datetime desc'

    name = fields.Char(string='Reference', required=True, copy=False,
                       default='New', readonly=True)
    reading_datetime = fields.Datetime(string='Reading Date/Time', required=True,
                                       default=fields.Datetime.now)
    tank_id = fields.Many2one('forecourt.tank', string='Tank', required=True,
                              ondelete='restrict')
    product_id = fields.Many2one(
        related='tank_id.product_id', string='Product', store=True,
    )
    reading_type = fields.Selection([
        ('opening', 'Opening'),
        ('mid_shift', 'Mid-Shift'),
        ('closing', 'Closing'),
    ], string='Reading Type', required=True, default='opening')
    shift_id = fields.Many2one('forecourt.shift', string='Shift')
    dip_mm = fields.Float(string='Dip Reading (mm)', digits=(10, 1))
    litres = fields.Float(string='Litres (from dip chart)', digits=(16, 2))
    recorded_by = fields.Many2one('res.users', string='Recorded By',
                                  default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    notes = fields.Char(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.dip.reading'
                ) or 'New'
        return super().create(vals_list)

    def action_update_tank_level(self):
        """Push this dip reading as the current tank level."""
        for rec in self:
            if rec.litres > 0:
                rec.tank_id.current_level = rec.litres
