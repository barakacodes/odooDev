# -*- coding: utf-8 -*-
import pytz
from datetime import datetime
from odoo import fields, models


class PosFuelReportWizard(models.TransientModel):
    _name = 'pos.fuel.report.wizard'
    _description = 'Daily Fuel Sales Report'

    report_date = fields.Date(string='Date', required=True, default=fields.Date.today)
    branch = fields.Selection(
        [('all', 'All Branches'), ('arcades', 'Arcades'), ('luanshya', 'Luanshya')],
        string='Branch', default='all', required=True)
    shift_type = fields.Selection(
        [('all', 'All Shifts'), ('day', 'Day Shift'), ('night', 'Night Shift')],
        string='Shift', default='all', required=True)
    section_id = fields.Many2one('pos.category', string='Section')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    line_ids = fields.One2many(
        'pos.fuel.report.line', 'wizard_id', string='Report Lines')

    # ── report generation ────────────────────────────────────────────

    def action_generate_report(self):
        self.ensure_one()
        # Remove previous lines
        self.line_ids.unlink()
        report_model = self.env['report.forecourt_operations.pos_fuel_report']
        data = report_model.get_report_data(self)

        tz = pytz.timezone('Africa/Lusaka')
        lines_vals = []
        for block in data['blocks']:
            p = block['petrol']
            d = block['diesel']
            dt = block.get('date_start')
            if dt:
                sess_date = pytz.utc.localize(dt).astimezone(tz).date()
            else:
                sess_date = self.report_date
            lines_vals.append({
                'wizard_id': self.id,
                'branch': block['branch_label'],
                'shift_label': block['shift_label'],
                'session_name': block['session_name'],
                'session_date': sess_date,
                # Petrol
                'petrol_opening': p['opening_stock'],
                'petrol_received': p['received'],
                'petrol_sold_litres': p['litres_sold'],
                'petrol_cash_litres': p['cash_litres'],
                'petrol_card_litres': p['card_litres'],
                'petrol_cash_amount': p['cash_amount'],
                'petrol_card_amount': p['card_amount'],
                'petrol_total_amount': p['total_amount'],
                'petrol_prepaid_litres': p['prepaid_litres'],
                'petrol_prepaid_amount': p['prepaid_amount'],
                'petrol_account_litres': p['account_litres'],
                'petrol_account_amount': p['account_amount'],
                'petrol_test_litres': p.get('test_litres', 0.0),
                'petrol_test_amount': p.get('test_amount', 0.0),
                'petrol_closing': p['closing_stock'],
                # Diesel
                'diesel_opening': d['opening_stock'],
                'diesel_received': d['received'],
                'diesel_sold_litres': d['litres_sold'],
                'diesel_cash_litres': d['cash_litres'],
                'diesel_card_litres': d['card_litres'],
                'diesel_cash_amount': d['cash_amount'],
                'diesel_card_amount': d['card_amount'],
                'diesel_total_amount': d['total_amount'],
                'diesel_prepaid_litres': d['prepaid_litres'],
                'diesel_prepaid_amount': d['prepaid_amount'],
                'diesel_account_litres': d['account_litres'],
                'diesel_account_amount': d['account_amount'],
                'diesel_test_litres': d.get('test_litres', 0.0),
                'diesel_test_amount': d.get('test_amount', 0.0),
                'diesel_closing': d['closing_stock'],
                # Totals
                'grand_litres': block['grand_total_litres'],
                'grand_amount': block['grand_total_amount'],
                'grand_cash': block['grand_cash_amount'],
                'grand_card': block['grand_card_amount'],
            })
        self.env['pos.fuel.report.line'].create(lines_vals)
        # Return to the same full-page form so the user sees the updated table
        return {
            'type': 'ir.actions.act_window',
            'name': 'Daily Fuel Sales Report',
            'res_model': 'pos.fuel.report.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }

    def action_print_pdf(self):
        self.ensure_one()
        data = {
            'report_date': str(self.report_date),
            'branch': self.branch,
            'shift_type': self.shift_type,
            'section_id': self.section_id.id if self.section_id else False,
            'company_id': self.company_id.id,
        }
        return self.env.ref(
            'forecourt_operations.action_pos_fuel_report_pdf'
        ).report_action(self, data=data)

    def action_export_excel(self):
        self.ensure_one()
        return self.env[
            'report.forecourt_operations.pos_fuel_report'
        ].action_export_excel(self)
