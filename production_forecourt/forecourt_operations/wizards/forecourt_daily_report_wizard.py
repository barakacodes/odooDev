# -*- coding: utf-8 -*-
import io
import base64
import xlsxwriter
from datetime import date
from odoo import models, fields, api


class ForecourtDailyReportWizard(models.TransientModel):
    _name = 'forecourt.daily.report.wizard'
    _description = 'Forecourt Daily Sales Report'

    report_date = fields.Date(
        string='Date', required=True, default=fields.Date.today,
    )
    branch = fields.Selection([
        ('all', 'All Branches'),
        ('arcades', 'Arcades'),
        ('luanshya', 'Luanshya'),
    ], string='Branch', default='all', required=True)
    shift_type = fields.Selection([
        ('all', 'All Shifts'),
        ('day', 'Day Shift (06:00–18:00)'),
        ('night', 'Night Shift (18:00–06:00)'),
    ], string='Shift', default='all', required=True)
    section = fields.Selection([
        ('fuel', 'Fuel Sales'),
        ('lubricant', 'Lubricant Sales'),
        ('lpg', 'LPG Sales'),
    ], string='Section', default='fuel', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )


    def _get_shifts(self):
        """Return matching shifts for the selected date/branch/shift_type."""
        domain = [
            ('company_id', '=', self.company_id.id),
            ('date_start', '>=', fields.Datetime.from_string(
                f'{self.report_date} 00:00:00')),
            ('date_start', '<=', fields.Datetime.from_string(
                f'{self.report_date} 23:59:59')),
        ]
        if self.branch != 'all':
            domain.append(('branch', '=', self.branch))
        if self.shift_type != 'all':
            domain.append(('shift_type', '=', self.shift_type))
        return self.env['forecourt.shift'].search(domain)

    def get_report_values(self):
        """Compute all report data — used by both PDF template and Excel export."""
        shifts = self._get_shifts()
        section = self.section

        report_blocks = []

        for shift in shifts:
            sales = shift.sale_ids.filtered(
                lambda s: s.state == 'posted' and s.product_type == section
            )

            # Opening dip reading
            opening_dips = self.env['forecourt.dip.reading'].search([
                ('shift_id', '=', shift.id),
                ('reading_type', '=', 'opening'),
            ])
            # Closing dip reading
            closing_dips = self.env['forecourt.dip.reading'].search([
                ('shift_id', '=', shift.id),
                ('reading_type', '=', 'closing'),
            ])
            # Deliveries during this shift
            deliveries = self.env['forecourt.delivery'].search([
                ('delivery_date', '=', shift.date_start.date()
                 if shift.date_start else self.report_date),
                ('product_type', '=', section),
                ('state', '=', 'received'),
                ('company_id', '=', self.company_id.id),
            ])

            # Sales breakdown
            normal_sales = sales.filtered(
                lambda s: s.customer_type == 'normal' and not s.is_test)
            prepaid_sales = sales.filtered(
                lambda s: s.customer_type == 'prepaid')
            account_sales = sales.filtered(
                lambda s: s.customer_type == 'account_holder')
            test_sales = sales.filtered(lambda s: s.is_test)

            cash_sales = sales.filtered(
                lambda s: s.payment_method == 'cash' and not s.is_test)
            card_sales = sales.filtered(
                lambda s: s.payment_method == 'card' and not s.is_test)

            # Build product breakdown for fuel (Petrol / Diesel separately)
            if section == 'fuel':
                products = list({s.product_id for s in sales if s.product_id})
                product_lines = []
                for prod in sorted(products, key=lambda p: p.name):
                    prod_sales = sales.filtered(
                        lambda s: s.product_id == prod and not s.is_test)
                    opening_lit = sum(
                        d.litres for d in opening_dips
                        if d.product_id == prod
                    )
                    closing_lit = sum(
                        d.litres for d in closing_dips
                        if d.product_id == prod
                    )
                    received_lit = sum(
                        d.quantity_received for d in deliveries
                        if d.product_id == prod
                    )
                    product_lines.append({
                        'product': prod.name,
                        'opening_stock': opening_lit,
                        'litres_sold': sum(prod_sales.mapped('quantity')),
                        'closing_stock': closing_lit,
                        'received': received_lit,
                    })
            else:
                product_lines = []

            block = {
                'shift': shift,
                'branch_label': dict(
                    self.env['forecourt.shift']._fields['branch'].selection
                ).get(shift.branch, shift.branch or '—'),
                'shift_label': dict(
                    self.env['forecourt.shift']._fields['shift_type'].selection
                ).get(shift.shift_type, ''),
                'section': section,
                'product_lines': product_lines,
                # Totals
                'total_litres_sold': sum(
                    sales.filtered(lambda s: not s.is_test).mapped('quantity')),
                'total_revenue': sum(
                    sales.filtered(lambda s: not s.is_test).mapped('total_amount')),
                'cash_sales_amount': sum(cash_sales.mapped('total_amount')),
                'card_sales_amount': sum(card_sales.mapped('total_amount')),
                'cash_litres': sum(cash_sales.mapped('quantity')),
                'card_litres': sum(card_sales.mapped('quantity')),
                # Wallet customers
                'prepaid_litres': sum(prepaid_sales.mapped('quantity')),
                'prepaid_amount': sum(prepaid_sales.mapped('total_amount')),
                'account_litres': sum(account_sales.mapped('quantity')),
                'account_amount': sum(account_sales.mapped('total_amount')),
                # Test fuel
                'test_litres': sum(test_sales.mapped('quantity')),
                # Deliveries
                'received_litres': sum(deliveries.mapped('quantity_received')),
            }
            report_blocks.append(block)

        return {
            'wizard': self,
            'company': self.company_id,
            'report_date': self.report_date,
            'branch_label': dict(
                self._fields['branch'].selection
            ).get(self.branch, 'All'),
            'shift_label': dict(
                self._fields['shift_type'].selection
            ).get(self.shift_type, 'All'),
            'section_label': dict(
                self._fields['section'].selection
            ).get(self.section, ''),
            'blocks': report_blocks,
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'forecourt_operations.action_forecourt_daily_report_pdf'
        ).report_action(self)

    def action_export_excel(self):
        self.ensure_one()
        data = self.get_report_values()
        company = data['company']
        blocks = data['blocks']

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Daily Sales Report')

        # Formats
        def f(**kw):
            return wb.add_format(kw)

        f_title = f(bold=True, font_size=13)
        f_plain = f(font_size=11)
        f_section_hdr = f(bold=True, font_size=11, bg_color='#1F497D',
                          font_color='#FFFFFF', border=1, align='center',
                          valign='vcenter')
        f_sub_hdr = f(bold=True, font_size=10, bg_color='#D9D9D9',
                      border=1, align='center', valign='vcenter')
        f_label = f(font_size=10, border=1, valign='vcenter')
        f_num = f(font_size=10, border=1, num_format='#,##0.00',
                  align='right', valign='vcenter')
        f_num3 = f(font_size=10, border=1, num_format='#,##0.000',
                   align='right', valign='vcenter')
        f_bold_label = f(bold=True, font_size=10, border=1,
                         bg_color='#F2F2F2', valign='vcenter')
        f_bold_num = f(bold=True, font_size=10, border=1,
                       num_format='#,##0.00', align='right',
                       bg_color='#F2F2F2', valign='vcenter')
        f_bold_num3 = f(bold=True, font_size=10, border=1,
                        num_format='#,##0.000', align='right',
                        bg_color='#F2F2F2', valign='vcenter')

        col_w = [32, 18, 18]
        for i, w in enumerate(col_w):
            ws.set_column(i, i, w)

        row = 0
        ws.write(row, 0, company.name or '', f_title)
        row += 1
        ws.write(row, 0, company.street or '', f_plain)
        row += 1
        ws.write(row, 0, f'TPIN: {company.vat or ""}', f_plain)
        row += 2
        ws.write(row, 0,
                 f'Daily Sales Report — {data["report_date"].strftime("%d %B %Y")}',
                 f_title)
        row += 1
        ws.write(row, 0,
                 f'Branch: {data["branch_label"]}   |   Shift: {data["shift_label"]}   |   Section: {data["section_label"]}',
                 f_plain)
        row += 2

        for block in blocks:
            # Shift header
            ws.merge_range(row, 0, row, 2,
                           f'{block["branch_label"]} — {block["shift_label"]}  |  Shift: {block["shift"]["name"]}',
                           f_section_hdr)
            ws.set_row(row, 22)
            row += 1

            section = block['section']

            if section == 'fuel' and block['product_lines']:
                # Per-product stock table
                ws.write(row, 0, 'Product', f_sub_hdr)
                ws.write(row, 1, 'Opening Stock (L)', f_sub_hdr)
                ws.write(row, 2, 'Closing Stock (L)', f_sub_hdr)
                row += 1
                for pl in block['product_lines']:
                    ws.write(row, 0, pl['product'], f_label)
                    ws.write(row, 1, pl['opening_stock'], f_num3)
                    ws.write(row, 2, pl['closing_stock'], f_num3)
                    row += 1
                row += 1

            # Sales summary rows
            rows_data = [
                ('Total Litres Sold', block['total_litres_sold'], f_bold_num3),
                ('— Cash Sales (L)', block['cash_litres'], f_num3),
                ('— Card Sales (L)', block['card_litres'], f_num3),
            ] if section == 'fuel' else [
                ('Total Qty Sold', block['total_litres_sold'], f_bold_num3),
            ]

            rows_data += [
                ('Total Sales Revenue (ZMW)', block['total_revenue'], f_bold_num),
                ('  Cash Sales (ZMW)', block['cash_sales_amount'], f_num),
                ('  Card Sales (ZMW)', block['card_sales_amount'], f_num),
                (None, None, None),  # spacer
                ('Prepaid Wallet — Litres Withdrawn', block['prepaid_litres'], f_num3),
                ('Prepaid Wallet — Amount Expected (ZMW)', block['prepaid_amount'], f_num),
                ('Account Holders — Litres Withdrawn', block['account_litres'], f_num3),
                ('Account Holders — Amount Expected (ZMW)', block['account_amount'], f_num),
                (None, None, None),
                ('Test / Calibration Fuel (L)', block['test_litres'], f_num3),
                ('Fuel Received During Shift (L)', block['received_litres'], f_num3),
            ]

            for label, value, fmt in rows_data:
                if label is None:
                    row += 1
                    continue
                ws.write(row, 0, label, f_bold_label if 'Total' in label or 'Revenue' in label else f_label)
                ws.merge_range(row, 1, row, 2, value if value is not None else 0, fmt)
                row += 1

            row += 2

        wb.close()
        output.seek(0)
        fname = f'Daily_Sales_Report_{data["report_date"].strftime("%Y%m%d")}.xlsx'
        att = self.env['ir.attachment'].create({
            'name': fname,
            'type': 'binary',
            'datas': base64.b64encode(output.read()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name,
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
