from odoo import fields, models


class ForecourtDeliveryTankLine(models.Model):
    _name = 'forecourt.delivery.tank.line'
    _description = 'Forecourt Delivery Tank Allocation Line'

    delivery_id = fields.Many2one('forecourt.delivery', required=True, ondelete='cascade')
    tank_id = fields.Many2one('forecourt.tank', required=True, string='Tank')
    quantity = fields.Float(required=True, string='Quantity Received', digits=(16, 3))
