# -*- coding: utf-8 -*-
from odoo import models, fields


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    is_forecourt_branch = fields.Boolean(
        string='Is Forecourt Branch',
        default=False,
        help='Mark this warehouse as a forecourt/station branch. '
             'Only warehouses flagged here appear as branch options '
             'on shifts, tanks, and pumps.',
    )
    forecourt_tank_ids = fields.One2many(
        'forecourt.tank', 'warehouse_id', string='Tanks',
    )
    forecourt_pump_ids = fields.One2many(
        'forecourt.pump', 'warehouse_id', string='Pumps',
    )
    forecourt_tank_count = fields.Integer(
        compute='_compute_forecourt_counts', string='Tank Count',
    )
    forecourt_pump_count = fields.Integer(
        compute='_compute_forecourt_counts', string='Pump Count',
    )

    def _compute_forecourt_counts(self):
        for wh in self:
            wh.forecourt_tank_count = len(wh.forecourt_tank_ids)
            wh.forecourt_pump_count = len(wh.forecourt_pump_ids)

    def action_view_forecourt_tanks(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'forecourt_operations.action_forecourt_tank')
        action['domain'] = [('warehouse_id', '=', self.id)]
        action['context'] = {'default_warehouse_id': self.id}
        return action

    def action_view_forecourt_pumps(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'forecourt_operations.action_forecourt_pump')
        action['domain'] = [('warehouse_id', '=', self.id)]
        return action
