# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ForecourtPump(models.Model):
    _name = 'forecourt.pump'
    _description = 'Forecourt Fuel Pump'
    _order = 'name asc'

    name = fields.Char(string='Pump Name', required=True)
    code = fields.Char(string='Pump Code', required=True)
    tank_id = fields.Many2one(
        'forecourt.tank', string='Tank', required=True, ondelete='restrict',
    )
    product_id = fields.Many2one(
        related='tank_id.product_id', string='Product', store=True,
    )
    warehouse_id = fields.Many2one(
        related='tank_id.warehouse_id', string='Warehouse / Branch', store=True,
    )
    status = fields.Selection([
        ('active', 'Active'),
        ('idle', 'Idle'),
        ('maintenance', 'Under Maintenance'),
        ('decommissioned', 'Decommissioned'),
    ], string='Status', default='idle', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notes')

    nozzle_ids = fields.One2many('forecourt.nozzle', 'pump_id', string='Nozzles')
    meter_ids = fields.One2many('forecourt.meter.reading', 'pump_id', string='Meter Readings')

    nozzle_count = fields.Integer(compute='_compute_nozzle_count', string='Nozzles')

    @api.depends('nozzle_ids')
    def _compute_nozzle_count(self):
        for rec in self:
            rec.nozzle_count = len(rec.nozzle_ids)


class ForecourtNozzle(models.Model):
    _name = 'forecourt.nozzle'
    _description = 'Pump Nozzle'
    _order = 'pump_id, name'

    name = fields.Char(string='Nozzle Name', required=True)
    code = fields.Char(string='Nozzle Code')
    pump_id = fields.Many2one(
        'forecourt.pump', string='Pump', required=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        related='pump_id.product_id', string='Product', store=True,
    )
    active = fields.Boolean(default=True)

    # Opening meter reading (set at start of shift)
    opening_reading = fields.Float(
        string='Opening Reading', digits=(16, 3), default=0.0,
    )
    closing_reading = fields.Float(
        string='Closing Reading', digits=(16, 3), default=0.0,
    )
    volume_dispensed = fields.Float(
        string='Volume Dispensed (L)',
        compute='_compute_volume',
        store=True,
        digits=(16, 3),
    )

    @api.depends('opening_reading', 'closing_reading')
    def _compute_volume(self):
        for rec in self:
            rec.volume_dispensed = max(0.0, rec.closing_reading - rec.opening_reading)
