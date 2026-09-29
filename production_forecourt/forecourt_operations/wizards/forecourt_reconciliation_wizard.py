# -*- coding: utf-8 -*-
import io
import base64
import xlsxwriter
from datetime import date
from odoo import models, fields, api


class ForecourtReconciliationWizard(models.TransientModel):
    _name = 'forecourt.reconciliation.wizard'
    _description = 'Tank Reconciliation Report Wizard'

    date_from = fields.Date(string='Date From', required=True,
                            default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(string='Date To', required=True,
                          default=fields.Date.today)
    tank_ids = fields.Many2many('forecourt.tank', string='Tanks',
                                help='Leave empty for all tanks')
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)

    def get_report_values(self):
        tanks = self.tank_ids or self.env['forecourt.tank'].search(
            [('company_id', '=', self.company_id.id), ('active', '=', True)]
        )

        lines = []
        for tank in tanks:
            # Opening dip in range
            opening_dip = self.env['forecourt.dip.reading'].search([
                ('tank_id', '=', tank.id),
                ('reading_datetime', '>=', fields.Datetime.from_string(
                    f'{self.date_from} 00:00:00')),
                ('reading_datetime', '<=', fields.Datetime.from_string(
                    f'{self.date_to} 23:59:59')),
                ('reading_type', '=', 'opening'),
            ], order='reading_datetime asc', limit=1)

            closing_dip = self.env['forecourt.dip.reading'].search([
                ('tank_id', '=', tank.id),
                ('reading_datetime', '>=', fields.Datetime.from_string(
                    f'{self.date_from} 00:00:00')),
                ('reading_datetime', '<=', fields.Datetime.from_string(
                    f'{self.date_to} 23:59:59')),
                ('reading_type', '=', 'closing'),
            ], order='reading_datetime desc', limit=1)

            # Deliveries in range
            deliveries = self.env['forecourt.delivery'].search([
                ('tank_id', '=', tank.id),
                ('delivery_date', '>=', self.date_from),
                ('delivery_date', '<=', self.date_to),
                ('state', '=', 'received'),
            ])
            total_deliveries = sum(deliveries.mapped('quantity_received'))

            # Sales from pumps attached to this tank in range
            pump_ids = tank.pump_ids.ids
            sales = self.env['forecourt.sale'].search([
                ('pump_id', 'in', pump_ids),
                ('sale_datetime', '>=', fields.Datetime.from_string(
                    f'{self.date_from} 00:00:00')),
                ('sale_datetime', '<=', fields.Datetime.from_string(
                    f'{self.date_to} 23:59:59')),
                ('state', '=', 'posted'),
            ])
            total_sales = sum(sales.mapped('quantity'))

            # Meter volume from pumps in range
            meter_volume = 0.0
            for pump in tank.pump_ids:
                meters = self.env['forecourt.meter.reading'].search([
                    ('pump_id', '=', pump.id),
                    ('reading_type', '=', 'closing'),
                    ('reading_datetime', '>=', fields.Datetime.from_string(
                        f'{self.date_from} 00:00:00')),
                    ('reading_datetime', '<=', fields.Datetime.from_string(
                        f'{self.date_to} 23:59:59')),
                ])
                meter_volume += sum(meters.mapped('volume'))

            # Reconciliation maths
            opening_litres = opening_dip.litres if opening_dip else 0.0
            closing_litres = closing_dip.litres if closing_dip else tank.current_level

            # Expected closing = opening + deliveries - sales
            expected_closing = opening_litres + total_deliveries - total_sales
            dip_variance = closing_litres - expected_closing  # +ve = gain, -ve = loss
            meter_variance = total_sales - meter_volume        # +ve = unmetered, -ve = meter > sales

            lines.append({
                'tank': tank.name,
                'product': tank.product_id.name if tank.product_id else '',
                'opening_dip': opening_litres,
                'deliveries': total_deliveries,
                'sales_litres': total_sales,
                'meter_volume': meter_volume,
                'expected_closing': expected_closing,
                'actual_closing': closing_litres,
                'dip_variance': dip_variance,
                'meter_variance': meter_variance,
            })

        return {
            'wizard': self,
            'company': self.company_id,
            'lines': lines,
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'forecourt_operations.action_forecourt_reconciliation_report_pdf'
        ).report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        data = self.get_report_values()
        lines = data['lines']
        company = data['company']

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Reconciliation')

        def f(**kw): return wb.add_format(kw)
        f_title = f(bold=True, font_size=12)
        f_plain = f(font_size=11)
        f_head  = f(bold=True, border=1, bg_color='#D9D9D9', align='center',
                    valign='vcenter', font_size=11, text_wrap=True)
        f_data  = f(border=1, valign='vcenter', font_size=11)
        f_num   = f(border=1, valign='vcenter', font_size=11,
                    num_format='#,##0.00', align='right')
        f_pos   = f(border=1, valign='vcenter', font_size=11,
                    num_format='#,##0.00', align='right', font_color='#276221')
        f_neg   = f(border=1, valign='vcenter', font_size=11,
                    num_format='#,##0.00', align='right', font_color='#C00000')

        col_w = [20, 14, 14, 14, 14, 14, 16, 16, 14, 14]
        for i, w in enumerate(col_w):
            ws.set_column(i, i, w)

        row = 0
        ws.write(row, 0, company.name or '', f_title); row += 1
        ws.write(row, 0, f'TPIN: {company.vat or ""}', f_plain); row += 2
        ws.write(row, 0, 'Tank Reconciliation Report', f_title); row += 1
        d_from = self.date_from.strftime('%d-%b-%Y') if self.date_from else ''
        d_to = self.date_to.strftime('%d-%b-%Y') if self.date_to else ''
        ws.write(row, 0, f'{d_from}  to  {d_to}', f_plain); row += 2

        headers = [
            'Tank', 'Product', 'Opening Dip (L)', 'Deliveries (L)',
            'Sales (L)', 'Meter Vol. (L)', 'Expected Close (L)',
            'Actual Close (L)', 'Dip Variance (L)', 'Meter Variance (L)',
        ]
        ws.set_row(row, 36)
        for ci, h in enumerate(headers):
            ws.write(row, ci, h, f_head)
        row += 1

        for line in lines:
            ws.write(row, 0, line['tank'], f_data)
            ws.write(row, 1, line['product'], f_data)
            ws.write(row, 2, line['opening_dip'], f_num)
            ws.write(row, 3, line['deliveries'], f_num)
            ws.write(row, 4, line['sales_litres'], f_num)
            ws.write(row, 5, line['meter_volume'], f_num)
            ws.write(row, 6, line['expected_closing'], f_num)
            ws.write(row, 7, line['actual_closing'], f_num)
            # Variance: green = gain, red = loss
            dv = line['dip_variance']
            mv = line['meter_variance']
            ws.write(row, 8, dv, f_pos if dv >= 0 else f_neg)
            ws.write(row, 9, mv, f_pos if mv >= 0 else f_neg)
            row += 1

        wb.close(); output.seek(0)
        fname = f'Tank_Reconciliation_{d_from}_to_{d_to}.xlsx'.replace(' ', '_')
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
