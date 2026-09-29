# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class ForecourtTank(models.Model):
    _name = 'forecourt.tank'
    _description = 'Fuel Storage Tank'
    _order = 'name asc'

    name = fields.Char(string='Tank Name', required=True)
    code = fields.Char(string='Tank Code', required=True)
    product_id = fields.Many2one(
        'product.product', string='Product',
        required=True,
        domain=[('forecourt_product_type', 'in', ['fuel', 'lpg', 'lubricant'])],
    )
    capacity = fields.Float(string='Capacity (L)', required=True, digits=(16, 2))
    current_level = fields.Float(string='Current Level (L)', digits=(16, 2))
    reorder_level = fields.Float(string='Reorder Level (L)', digits=(16, 2))
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse / Branch',
        domain=[('is_forecourt_branch', '=', True)],
        help='The branch/warehouse this tank physically sits at.',
    )
    location_id = fields.Many2one(
        'stock.location', string='Stock Location',
        domain=[('usage', '=', 'internal')],
    )

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        if self.warehouse_id:
            return {'domain': {'location_id': [
                ('usage', '=', 'internal'),
                ('warehouse_id', '=', self.warehouse_id.id),
            ]}}
        return {'domain': {'location_id': [('usage', '=', 'internal')]}}
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notes')

    pump_ids = fields.One2many('forecourt.pump', 'tank_id', string='Pumps')
    dip_ids = fields.One2many('forecourt.dip.reading', 'tank_id', string='Dip Readings')
    delivery_ids = fields.One2many('forecourt.delivery', 'tank_id', string='Deliveries')

    # Computed
    level_percentage = fields.Float(
        string='Level %', compute='_compute_level_percentage', store=True,
    )
    stock_status = fields.Selection([
        ('ok', 'OK'),
        ('low', 'Low'),
        ('critical', 'Critical'),
    ], string='Status', compute='_compute_stock_status', store=True)

    @api.depends('current_level', 'capacity')
    def _compute_level_percentage(self):
        for rec in self:
            rec.level_percentage = (
                (rec.current_level / rec.capacity * 100.0)
                if rec.capacity > 0 else 0.0
            )

    @api.depends('current_level', 'reorder_level', 'capacity')
    def _compute_stock_status(self):
        for rec in self:
            pct = rec.level_percentage
            if pct <= 10:
                rec.stock_status = 'critical'
            elif rec.current_level <= rec.reorder_level:
                rec.stock_status = 'low'
            else:
                rec.stock_status = 'ok'

    @api.constrains('capacity', 'current_level')
    def _check_levels(self):
        for rec in self:
            if rec.capacity <= 0:
                raise ValidationError('Tank capacity must be greater than zero.')
            if rec.current_level < 0:
                raise ValidationError('Current level cannot be negative.')
            if rec.current_level > rec.capacity:
                raise ValidationError(
                    f'Current level ({rec.current_level:.2f} L) cannot exceed '
                    f'tank capacity ({rec.capacity:.2f} L).'
                )

    def action_view_dip_readings(self):
        return {
            'name': f'Dip Readings â€” {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'forecourt.dip.reading',
            'view_mode': 'list,form',
            'domain': [('tank_id', '=', self.id)],
            'context': {'default_tank_id': self.id},
        }
