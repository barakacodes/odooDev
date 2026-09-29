# -*- coding: utf-8 -*-
import io
import base64
import xlsxwriter
from datetime import date
from odoo import models, fields, api


class ForecourtSalesReportWizard(models.TransientModel):
    _name = 'forecourt.sales.report.wizard'
    _description = 'Forecourt Sales Report Wizard'

    date_from = fields.Date(string='Date From', required=True,
                            default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(string='Date To', required=True,
                          default=fields.Date.today)
    product_type = fields.Selection([
        ('all', 'All Products'),
        ('fuel', 'Fuel Only'),
        ('lpg', 'LPG Only'),
        ('lubricant', 'Lubricants Only'),
    ], string='Product Type', default='all', required=True)
    payment_method = fields.Selection([
        ('all', 'All Methods'),
        ('cash', 'Cash'),
        ('mobile_money', 'Mobile Money'),
        ('card', 'Card / POS'),
        ('credit', 'Fleet / Credit'),
    ], string='Payment Method', default='all')
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)

    def _get_report_lines(self):
        domain = [
            ('sale_datetime', '>=', fields.Datetime.from_string(
                f'{self.date_from} 00:00:00')),
            ('sale_datetime', '<=', fields.Datetime.from_string(
                f'{self.date_to} 23:59:59')),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.product_type != 'all':
            domain.append(('product_type', '=', self.product_type))
        if self.payment_method != 'all':
            domain.append(('payment_method', '=', self.payment_method))

        return self.env['forecourt.sale'].search(domain, order='sale_datetime asc')

    def get_report_values(self):
        sales = self._get_report_lines()
        type_labels = dict(
            self.env['forecourt.sale']._fields['product_type'].selection
        )
        pay_labels = dict(
            self.env['forecourt.sale']._fields['payment_method'].selection
        )

        lines = []
        for s in sales:
            pump_ref = s.nozzle_id.name if s.nozzle_id else (
                s.pump_id.name if s.pump_id else '—'
            )
            lines.append({
                'date': s.sale_datetime,
                'ref': s.name,
                'shift': s.shift_id.name if s.shift_id else '',
                'product_type': type_labels.get(s.product_type, ''),
                'product': s.product_id.name if s.product_id else '',
                'pump': pump_ref,
                'cashier': s.cashier_id.name if s.cashier_id else '',
                'payment': pay_labels.get(s.payment_method, ''),
                'qty': s.quantity,
                'unit_price': s.unit_price,
                'total': s.total_amount,
                'product_type_raw': s.product_type,
            })

        totals = {
            'revenue': sum(l['total'] for l in lines),
            'fuel_litres': sum(l['qty'] for l in lines if l['product_type_raw'] == 'fuel'),
            'lpg_units': sum(l['qty'] for l in lines if l['product_type_raw'] == 'lpg'),
            'lubricant_revenue': sum(l['total'] for l in lines if l['product_type_raw'] == 'lubricant'),
            'cash': sum(l['total'] for l in lines if l['payment'] == pay_labels.get('cash', 'Cash')),
            'mobile_money': sum(l['total'] for l in lines if l['payment'] == pay_labels.get('mobile_money', 'Mobile Money')),
            'card': sum(l['total'] for l in lines if l['payment'] == pay_labels.get('card', 'Card / POS')),
            'credit': sum(l['total'] for l in lines if l['payment'] == pay_labels.get('credit', 'Fleet / Credit Account')),
        }

        return {
            'wizard': self,
            'company': self.company_id,
            'lines': lines,
            'totals': totals,
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'forecourt_operations.action_forecourt_sales_report_pdf'
        ).report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        data = self.get_report_values()
        lines = data['lines']
        totals = data['totals']
        company = data['company']

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Forecourt Sales')

        def f(**kw): return wb.add_format(kw)
        f_title = f(bold=True, font_size=12)
        f_plain = f(font_size=11)
        f_head  = f(bold=True, border=1, bg_color='#D9D9D9', align='center',
                    valign='vcenter', font_size=11)
        f_data  = f(border=1, valign='vcenter', font_size=11)
        f_num   = f(border=1, valign='vcenter', font_size=11,
                    num_format='#,##0.0000', align='right')
        f_gl    = f(bold=True, border=1, bg_color='#F2F2F2',
                    align='center', font_size=11)
        f_gn    = f(bold=True, border=1, bg_color='#F2F2F2',
                    num_format='#,##0.0000', align='right', font_size=11)

        col_w = [18, 14, 12, 14, 20, 14, 14, 12, 14, 14, 16]
        for i, w in enumerate(col_w):
            ws.set_column(i, i, w)

        row = 0
        ws.write(row, 0, company.name or '', f_title); row += 1
        ws.write(row, 0, company.street or '', f_plain); row += 1
        ws.write(row, 0, f'TPIN: {company.vat or ""}', f_plain); row += 2
        ws.write(row, 0, 'Forecourt Sales Report', f_title); row += 1
        d_from = self.date_from.strftime('%d-%b-%Y') if self.date_from else ''
        d_to = self.date_to.strftime('%d-%b-%Y') if self.date_to else ''
        ws.write(row, 0, f'{d_from}  to  {d_to}', f_plain); row += 2

        headers = ['Date/Time', 'Ref', 'Shift', 'Type', 'Product',
                   'Pump/Ref', 'Cashier', 'Payment', 'Qty', 'Unit Price', 'Total (ZMW)']
        ws.set_row(row, 28)
        for ci, h in enumerate(headers):
            ws.write(row, ci, h, f_head)
        row += 1

        type_colors = {'Fuel': '#E2EFDA', 'LPG': '#FFF2CC', 'Lubricant / Oil': '#DDEBF7'}
        for line in lines:
            color = type_colors.get(line['product_type'], '#FFFFFF')
            _ft = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11, 'bg_color': color})
            _fn = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11,
                                  'num_format': '#,##0.0000', 'align': 'right', 'bg_color': color})
            _fd = wb.add_format({'border': 1, 'valign': 'vcenter', 'font_size': 11,
                                  'num_format': 'dd-mmm-yy hh:mm', 'bg_color': color})
            ws.write(row, 0, line['date'], _fd)
            ws.write(row, 1, line['ref'], _ft)
            ws.write(row, 2, line['shift'], _ft)
            ws.write(row, 3, line['product_type'], _ft)
            ws.write(row, 4, line['product'], _ft)
            ws.write(row, 5, line['pump'], _ft)
            ws.write(row, 6, line['cashier'], _ft)
            ws.write(row, 7, line['payment'], _ft)
            ws.write(row, 8, line['qty'], _fn)
            ws.write(row, 9, line['unit_price'], _fn)
            ws.write(row, 10, line['total'], _fn)
            row += 1

        ws.merge_range(row, 0, row, 9, 'Grand Total', f_gl)
        ws.write(row, 10, totals['revenue'], f_gn)
        row += 2

        # Summary block
        ws.write(row, 0, 'Summary', f_title); row += 1
        summary_rows = [
            ('Total Fuel Dispensed (L)', f'{totals["fuel_litres"]:.4f}'),
            ('Total LPG Units', f'{totals["lpg_units"]:.4f}'),
            ('Lubricant Revenue (ZMW)', f'{totals["lubricant_revenue"]:.4f}'),
            ('', ''),
            ('Cash', f'{totals["cash"]:.4f}'),
            ('Mobile Money', f'{totals["mobile_money"]:.4f}'),
            ('Card / POS', f'{totals["card"]:.4f}'),
            ('Fleet / Credit', f'{totals["credit"]:.4f}'),
        ]
        for label, val in summary_rows:
            ws.write(row, 0, label, f_data)
            ws.write(row, 1, val, f_data)
            row += 1

        wb.close(); output.seek(0)
        fname = f'Forecourt_Sales_{d_from}_to_{d_to}.xlsx'.replace(' ', '_')
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
