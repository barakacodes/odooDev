# -*- coding: utf-8 -*-
import io
import base64
import xlsxwriter
from odoo import models, fields, api


class ForecourtShiftReportWizard(models.TransientModel):
    _name = 'forecourt.shift.report.wizard'
    _description = 'Forecourt Shift Report Wizard'

    shift_id = fields.Many2one(
        'forecourt.shift', string='Shift', required=True,
        default=lambda self: self._default_shift(),
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )

    def _default_shift(self):
        return self.env['forecourt.shift'].search(
            [('company_id', '=', self.env.company.id)],
            order='date_start desc', limit=1,
        )

    def get_report_values(self):
        shift = self.shift_id
        sales = shift.sale_ids.filtered(lambda s: s.state == 'posted')

        # Group sales by product type
        fuel_lines = sales.filtered(lambda s: s.product_type == 'fuel')
        lpg_lines = sales.filtered(lambda s: s.product_type == 'lpg')
        lub_lines = sales.filtered(lambda s: s.product_type == 'lubricant')

        # Payment breakdown
        payment_totals = {
            'cash': sum(s.total_amount for s in sales if s.payment_method == 'cash'),
            'mobile_money': sum(s.total_amount for s in sales if s.payment_method == 'mobile_money'),
            'card': sum(s.total_amount for s in sales if s.payment_method == 'card'),
            'credit': sum(s.total_amount for s in sales if s.payment_method == 'credit'),
        }

        # Meter readings for this shift
        meters = shift.meter_ids

        return {
            'wizard': self,
            'shift': shift,
            'company': self.company_id,
            'sales': sales,
            'fuel_lines': fuel_lines,
            'lpg_lines': lpg_lines,
            'lub_lines': lub_lines,
            'payment_totals': payment_totals,
            'total_revenue': sum(sales.mapped('total_amount')),
            'total_fuel_litres': sum(fuel_lines.mapped('quantity')),
            'total_lpg_units': sum(lpg_lines.mapped('quantity')),
            'meters': meters,
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'forecourt_operations.action_forecourt_shift_report_pdf'
        ).report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        data = self.get_report_values()
        shift = data['shift']
        company = data['company']
        sales = data['sales']

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Shift Report')

        def f(**kw): return wb.add_format(kw)
        f_title  = f(bold=True, font_size=12)
        f_plain  = f(font_size=11)
        f_head   = f(bold=True, border=1, bg_color='#D9D9D9', align='center',
                     valign='vcenter', font_size=11)
        f_data   = f(border=1, valign='vcenter', font_size=11)
        f_date   = f(border=1, valign='vcenter', font_size=11,
                     num_format='d-mmm-yy hh:mm')
        f_num    = f(border=1, valign='vcenter', font_size=11,
                     num_format='#,##0.00', align='right')
        f_bold_b = f(bold=True, border=1, bg_color='#F2F2F2',
                     num_format='#,##0.00', align='right', font_size=11)
        f_bold_l = f(bold=True, border=1, bg_color='#F2F2F2',
                     align='center', font_size=11)

        col_w = [16, 12, 18, 18, 12, 10, 14, 14, 16]
        for i, w in enumerate(col_w):
            ws.set_column(i, i, w)

        row = 0
        ws.write(row, 0, company.name or '', f_title); row += 1
        ws.write(row, 0, company.street or '', f_plain); row += 1
        ws.write(row, 0, f'TPIN: {company.vat or ""}', f_plain); row += 1
        ws.write(row, 0, f'Email: {company.email or ""}', f_plain); row += 2
        ws.write(row, 0, 'Forecourt Shift Report', f_title); row += 1
        ws.write(row, 0, f'Shift: {shift.name} — {shift.get_shift_type_label()}', f_plain); row += 1
        ws.write(row, 0, f'Supervisor: {shift.supervisor_id.name}', f_plain); row += 2

        headers = ['Date/Time', 'Ref', 'Product Type', 'Product',
                   'Pump/Ref', 'Qty', 'Unit Price', 'Total (ZMW)', 'Payment']
        ws.set_row(row, 28)
        for ci, h in enumerate(headers):
            ws.write(row, ci, h, f_head)
        row += 1

        type_colors = {'fuel': '#E2EFDA', 'lpg': '#FFF2CC', 'lubricant': '#DDEBF7'}
        for sale in sales:
            color = type_colors.get(sale.product_type, '#FFFFFF')
            _fd = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11,
                                  'num_format': 'dd-mmm-yy hh:mm', 'bg_color': color})
            _ft = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11,
                                  'bg_color': color})
            _fn = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11,
                                  'num_format': '#,##0.0000', 'align': 'right',
                                  'bg_color': color})
            pump_ref = sale.nozzle_id.name if sale.nozzle_id else (
                sale.pump_id.name if sale.pump_id else '—'
            )
            ws.write(row, 0, sale.sale_datetime, _fd)
            ws.write(row, 1, sale.name, _ft)
            ws.write(row, 2, dict(sale._fields['product_type'].selection).get(sale.product_type, ''), _ft)
            ws.write(row, 3, sale.product_id.name if sale.product_id else '', _ft)
            ws.write(row, 4, pump_ref, _ft)
            ws.write(row, 5, sale.quantity, _fn)
            ws.write(row, 6, sale.unit_price, _fn)
            ws.write(row, 7, sale.total_amount, _fn)
            ws.write(row, 8, dict(sale._fields['payment_method'].selection).get(sale.payment_method, ''), _ft)
            row += 1

        ws.merge_range(row, 0, row, 6, 'Grand Total', f_bold_l)
        ws.write(row, 7, data['total_revenue'], f_bold_b)
        row += 2

        # Payment summary
        ws.write(row, 0, 'Payment Summary', f_title); row += 1
        for method, label in [
            ('cash', 'Cash'), ('mobile_money', 'Mobile Money'),
            ('card', 'Card / POS'), ('credit', 'Fleet / Credit'),
        ]:
            ws.write(row, 0, label, f_data)
            ws.write(row, 1, data['payment_totals'].get(method, 0.0), f_num)
            row += 1

        wb.close(); output.seek(0)
        fname = f'Shift_Report_{shift.name}.xlsx'
        att = self.env['ir.attachment'].create({
            'name': fname, 'type': 'binary',
            'datas': base64.b64encode(output.read()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name, 'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
