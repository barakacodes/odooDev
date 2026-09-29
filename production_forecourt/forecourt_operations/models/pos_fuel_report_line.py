# -*- coding: utf-8 -*-
from odoo import fields, models


class PosFuelReportLine(models.TransientModel):
    _name = 'pos.fuel.report.line'
    _description = 'Daily Fuel Sales Report Line'

    wizard_id = fields.Many2one(
        'pos.fuel.report.wizard', string='Report', ondelete='cascade')

    branch = fields.Char(string='Branch')
    shift_label = fields.Char(string='Shift')
    session_name = fields.Char(string='Session')
    session_date = fields.Date(string='Date')

    # â”€â”€ Petrol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    petrol_opening = fields.Float(string='Petrol Opening (L)', digits=(16, 3))
    petrol_received = fields.Float(string='Petrol Received (L)', digits=(16, 3))
    petrol_sold_litres = fields.Float(string='Petrol Sold (L)', digits=(16, 3))
    petrol_cash_litres = fields.Float(string='P. Cash Litres', digits=(16, 3))
    petrol_card_litres = fields.Float(string='P. Card Litres', digits=(16, 3))
    petrol_cash_amount = fields.Float(string='P. Cash (ZMW)', digits=(16, 2))
    petrol_card_amount = fields.Float(string='P. Card (ZMW)', digits=(16, 2))
    petrol_total_amount = fields.Float(string='P. Total (ZMW)', digits=(16, 2))
    petrol_prepaid_litres = fields.Float(string='P. Prepaid (L)', digits=(16, 3))
    petrol_prepaid_amount = fields.Float(string='P. Prepaid (ZMW)', digits=(16, 2))
    petrol_account_litres = fields.Float(string='P. Account (L)', digits=(16, 3))
    petrol_account_amount = fields.Float(string='P. Account (ZMW)', digits=(16, 2))
    petrol_test_litres = fields.Float(string='P. Test (L)', digits=(16, 3))
    petrol_test_amount = fields.Float(string='P. Test (ZMW)', digits=(16, 2))
    petrol_closing = fields.Float(string='Petrol Closing (L)', digits=(16, 3))

    # â”€â”€ Diesel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    diesel_opening = fields.Float(string='Diesel Opening (L)', digits=(16, 3))
    diesel_received = fields.Float(string='Diesel Received (L)', digits=(16, 3))
    diesel_sold_litres = fields.Float(string='Diesel Sold (L)', digits=(16, 3))
    diesel_cash_litres = fields.Float(string='D. Cash Litres', digits=(16, 3))
    diesel_card_litres = fields.Float(string='D. Card Litres', digits=(16, 3))
    diesel_cash_amount = fields.Float(string='D. Cash (ZMW)', digits=(16, 2))
    diesel_card_amount = fields.Float(string='D. Card (ZMW)', digits=(16, 2))
    diesel_total_amount = fields.Float(string='D. Total (ZMW)', digits=(16, 2))
    diesel_prepaid_litres = fields.Float(string='D. Prepaid (L)', digits=(16, 3))
    diesel_prepaid_amount = fields.Float(string='D. Prepaid (ZMW)', digits=(16, 2))
    diesel_account_litres = fields.Float(string='D. Account (L)', digits=(16, 3))
    diesel_account_amount = fields.Float(string='D. Account (ZMW)', digits=(16, 2))
    diesel_test_litres = fields.Float(string='D. Test (L)', digits=(16, 3))
    diesel_test_amount = fields.Float(string='D. Test (ZMW)', digits=(16, 2))
    diesel_closing = fields.Float(string='Diesel Closing (L)', digits=(16, 3))

    # â”€â”€ Grand totals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    grand_litres = fields.Float(string='Total Litres', digits=(16, 3))
    grand_cash = fields.Float(string='Cash Total (ZMW)', digits=(16, 2))
    grand_card = fields.Float(string='Card Total (ZMW)', digits=(16, 2))
    grand_amount = fields.Float(string='Grand Total (ZMW)', digits=(16, 2))
