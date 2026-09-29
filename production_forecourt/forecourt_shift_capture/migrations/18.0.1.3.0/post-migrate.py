# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Force-recompute all reconciliation fields on existing entries.

    The semantic change: Expected Closing now derives from
    (Credit + Cash) instead of the supervisor's reported Total Sold,
    matching the Aziz Excel. Existing stored values must be recomputed.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    entries = env['forecourt.shift.fuel.entry'].search([])
    if entries:
        entries.modified([
            'credit_litres_sold',
            'cash_sales_litres',
            'total_sold_litres',
            'opening_stock_litres',
            'received_stock_litres',
            'closing_stock_litres',
        ])
        env.flush_all()
        cr.commit()