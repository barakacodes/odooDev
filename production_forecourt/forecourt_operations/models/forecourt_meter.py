# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtMeterReading(models.Model):
    _name = 'forecourt.meter.reading'
    _description = 'Pump Meter Reading'
    _order = 'reading_datetime desc'

    name = fields.Char(string='Reference', required=True, copy=False,
                       default='New', readonly=True)
    reading_datetime = fields.Datetime(string='Date/Time', required=True,
                                       default=fields.Datetime.now)
    shift_id = fields.Many2one('forecourt.shift', string='Shift')
    pump_id = fields.Many2one('forecourt.pump', string='Pump', required=True,
                              ondelete='restrict')
    nozzle_id = fields.Many2one('forecourt.nozzle', string='Nozzle',
                                domain="[('pump_id', '=', pump_id)]")
    reading_type = fields.Selection([
        ('opening', 'Opening'),
        ('closing', 'Closing'),
    ], string='Reading Type', required=True, default='opening')
    meter_value = fields.Float(string='Meter Reading', digits=(16, 3), required=True)
    previous_reading = fields.Float(string='Previous Reading', digits=(16, 3))
    volume = fields.Float(
        string='Volume Dispensed (L)',
        compute='_compute_volume', store=True, digits=(16, 3),
    )
    recorded_by = fields.Many2one('res.users', string='Recorded By',
                                  default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    notes = fields.Char(string='Notes')

    @api.depends('meter_value', 'previous_reading', 'reading_type')
    def _compute_volume(self):
        for rec in self:
            if rec.reading_type == 'closing' and rec.previous_reading > 0:
                rec.volume = max(0.0, rec.meter_value - rec.previous_reading)
            else:
                rec.volume = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'forecourt.meter.reading'
                ) or 'New'
        return super().create(vals_list)
