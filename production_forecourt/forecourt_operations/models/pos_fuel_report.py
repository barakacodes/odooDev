# -*- coding: utf-8 -*-
import io
import base64
from datetime import datetime, timedelta
import pytz

from odoo import api, models


# Warehouse lot_stock_id per branch (company 6)
BRANCH_LOCATION = {
    'arcades': 68,
    'luanshya': 74,
}
# POS config id per branch (company 6)
BRANCH_CONFIG = {
    'arcades': 5,
    'luanshya': 6,
}
# Day shift: start hour 06 â€“ 17  |  Night shift: start hour 18 â€“ 05
DAY_SHIFT_START = 6
DAY_SHIFT_END = 18


def _shift_of_session(session):
    """Return 'day' or 'night' based on session start_at (UTCâ†’local)."""
    dt = session.start_at
    if not dt:
        return 'day'
    # Use Africa/Lusaka (UTC+2)
    tz = pytz.timezone('Africa/Lusaka')
    local_dt = pytz.utc.localize(dt).astimezone(tz)
    hour = local_dt.hour
    return 'day' if DAY_SHIFT_START <= hour < DAY_SHIFT_END else 'night'


class ReportPosFuelReport(models.AbstractModel):
    _name = 'report.forecourt_operations.pos_fuel_report'
    _description = 'POS Daily Fuel Sales Report'

    # â”€â”€ public helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _get_sessions(self, wizard):
        """Return pos.sessions matching date / branch / shift_type."""
        report_date = wizard.report_date
        company_id = wizard.company_id.id

        # Date window in UTC (Africa/Lusaka = UTC+2)
        tz = pytz.timezone('Africa/Lusaka')
        day_start_local = tz.localize(
            datetime(report_date.year, report_date.month, report_date.day, 0, 0, 0))
        day_end_local = day_start_local + timedelta(days=1) - timedelta(seconds=1)
        day_start_utc = day_start_local.astimezone(pytz.utc).replace(tzinfo=None)
        day_end_utc = day_end_local.astimezone(pytz.utc).replace(tzinfo=None)

        domain = [
            ('company_id', '=', company_id),
            ('state', 'in', ['closed', 'closing_control']),
            ('start_at', '>=', day_start_utc),
            ('start_at', '<=', day_end_utc),
        ]
        if wizard.branch == 'arcades':
            domain.append(('config_id', '=', BRANCH_CONFIG['arcades']))
        elif wizard.branch == 'luanshya':
            domain.append(('config_id', '=', BRANCH_CONFIG['luanshya']))

        sessions = self.env['pos.session'].search(domain, order='start_at asc')

        # Filter by shift type
        if wizard.shift_type != 'all':
            sessions = sessions.filtered(
                lambda s: _shift_of_session(s) == wizard.shift_type)
        return sessions

    def _fuel_lines(self, session):
        """Return pos.order.lines for Petrol and Diesel within a session."""
        orders = self.env['pos.order'].search([
            ('session_id', '=', session.id),
            ('state', 'in', ['paid', 'invoiced', 'done']),
        ])
        lines = orders.mapped('lines').filtered(
            lambda l: l.product_id.product_tmpl_id.pos_categ_ids.filtered(
                lambda c: c.name.upper() in ('PETROL', 'DIESEL')
            )
        )
        return lines

    def _payment_method_type(self, payment):
        """Return 'cash', 'card', or 'account' for a pos.payment record."""
        name = (payment.payment_method_id.name or '').lower()
        if 'account' in name or 'credit' in name:
            return 'account'
        if 'card' in name:
            return 'card'
        return 'cash'

    def _stock_at(self, location_id, product_id, at_datetime):
        """
        Compute stock quantity of product_id at location_id
        as of at_datetime using stock moves.
        """
        cr = self.env.cr
        cr.execute("""
            SELECT COALESCE(SUM(sm.product_uom_qty), 0) AS qty
            FROM stock_move sm
            WHERE sm.state = 'done'
              AND sm.location_dest_id = %s
              AND sm.product_id = %s
              AND sm.date <= %s
        """, (location_id, product_id, at_datetime))
        incoming = cr.fetchone()[0]

        cr.execute("""
            SELECT COALESCE(SUM(sm.product_uom_qty), 0) AS qty
            FROM stock_move sm
            WHERE sm.state = 'done'
              AND sm.location_id = %s
              AND sm.product_id = %s
              AND sm.date <= %s
        """, (location_id, product_id, at_datetime))
        outgoing = cr.fetchone()[0]
        return float(incoming) - float(outgoing)

    def _received_in_window(self, location_id, product_id, dt_from, dt_to):
        """Fuel received into location (from vendors) within a time window."""
        cr = self.env.cr
        cr.execute("""
            SELECT COALESCE(SUM(sm.product_uom_qty), 0)
            FROM stock_move sm
            JOIN stock_location src ON src.id = sm.location_id
            WHERE sm.state = 'done'
              AND sm.location_dest_id = %s
              AND sm.product_id = %s
              AND sm.date >= %s
              AND sm.date <= %s
              AND src.usage = 'supplier'
        """, (location_id, product_id, dt_from, dt_to))
        return float(self.env.cr.fetchone()[0])

    # â”€â”€ main report data builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get_report_data(self, wizard):
        sessions = self._get_sessions(wizard)

        # Resolve Petrol / Diesel product ids (canonical, non-test)
        petrol_prod = self.env['product.product'].search([
            ('product_tmpl_id.name', '=', 'Petrol'),
            ('product_tmpl_id.active', '=', True),
        ], limit=1)
        diesel_prod = self.env['product.product'].search([
            ('product_tmpl_id.name', '=', 'Diesel'),
            ('product_tmpl_id.active', '=', True),
        ], limit=1)

        shift_blocks = []

        for session in sessions:
            config_name = session.config_id.name or ''
            branch_key = 'arcades' if 'arcades' in config_name.lower() else 'luanshya'
            loc_id = BRANCH_LOCATION.get(branch_key)
            shift_label = _shift_of_session(session)

            # Time window for this session
            dt_from = session.start_at
            dt_to = session.stop_at or datetime.utcnow()

            # â”€â”€ sales lines for this session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            orders = self.env['pos.order'].search([
                ('session_id', '=', session.id),
                ('state', 'in', ['paid', 'invoiced', 'done']),
            ])
            all_lines = orders.mapped('lines')

            def fuel_lines_for(prod):
                return all_lines.filtered(lambda l: l.product_id == prod)

            petrol_lines = fuel_lines_for(petrol_prod) if petrol_prod else self.env['pos.order.line']
            diesel_lines = fuel_lines_for(diesel_prod) if diesel_prod else self.env['pos.order.line']

            # Apply section filter (pos.category) if selected on wizard
            sel_cat = wizard.section_id.id if wizard.section_id else False
            if sel_cat:
                all_lines = all_lines.filtered(lambda l: sel_cat in l.product_id.pos_categ_ids.ids or sel_cat in l.product_id.product_tmpl_id.pos_categ_ids.ids)
                # Recompute per-product lines from filtered all_lines
                petrol_lines = all_lines.filtered(lambda l: l.product_id == petrol_prod) if petrol_prod else all_lines
                diesel_lines = all_lines.filtered(lambda l: l.product_id == diesel_prod) if diesel_prod else all_lines

            # â”€â”€ payments breakdown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            payments = self.env['pos.payment'].search(
                [('pos_order_id', 'in', orders.ids)])

            def pay_amount_for_lines(lines, pay_type):
                order_ids = lines.mapped('order_id').ids
                total = 0.0
                for pmt in payments.filtered(lambda p: p.pos_order_id.id in order_ids):
                    if self._payment_method_type(pmt) == pay_type:
                        # apportion by order total ratio
                        order = pmt.pos_order_id
                        order_lines = lines.filtered(lambda l: l.order_id == order)
                        if order.amount_total:
                            ratio = sum(order_lines.mapped('price_subtotal_incl')) / order.amount_total
                        else:
                            ratio = 0
                        total += pmt.amount * ratio
                return total

            def qty_for_pay_type(lines, pay_type):
                total_qty = 0.0
                for order in lines.mapped('order_id'):
                    o_lines = lines.filtered(lambda l: l.order_id == order)
                    has_pay = any(
                        self._payment_method_type(p) == pay_type
                        for p in payments.filtered(lambda p: p.pos_order_id == order)
                    )
                    if has_pay:
                        total_qty += sum(o_lines.mapped('qty'))
                return total_qty

            # Prepaid wallet customers
            def is_prepaid(order):
                return order.partner_id and order.partner_id.is_prepaid_customer

            # Account holder = Customer Account payment method
            def is_account(order):
                return any(
                    self._payment_method_type(p) == 'account'
                    for p in payments.filtered(lambda p: p.pos_order_id == order)
                )

            prepaid_orders = orders.filtered(is_prepaid)
            account_orders = orders.filtered(
                lambda o: not is_prepaid(o) and is_account(o))

            def fuel_qty_for_orders(order_set, prod):
                return sum(
                    all_lines.filtered(
                        lambda l: l.order_id in order_set and l.product_id == prod
                    ).mapped('qty')
                )

            def fuel_amt_for_orders(order_set, prod):
                return sum(
                    all_lines.filtered(
                        lambda l: l.order_id in order_set and l.product_id == prod
                    ).mapped('price_subtotal_incl')
                )

            # â”€â”€ per-product totals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            def prod_block(prod, lines):
                if not prod:
                    return {
                        'litres_sold': 0, 'cash_litres': 0, 'card_litres': 0,
                        'cash_amount': 0, 'card_amount': 0, 'total_amount': 0,
                        'prepaid_litres': 0, 'prepaid_amount': 0,
                        'account_litres': 0, 'account_amount': 0,
                        'received': 0, 'opening_stock': 0, 'closing_stock': 0,
                        'test_litres': 0, 'test_amount': 0,
                    }
                cash_orders = orders.filtered(
                    lambda o: any(self._payment_method_type(p) == 'cash'
                                  for p in payments.filtered(
                                      lambda p: p.pos_order_id == o)))
                card_orders = orders.filtered(
                    lambda o: any(self._payment_method_type(p) == 'card'
                                  for p in payments.filtered(
                                      lambda p: p.pos_order_id == o)))

                opening = self._stock_at(loc_id, prod.id, dt_from) if loc_id else 0
                closing = self._stock_at(loc_id, prod.id, dt_to) if loc_id else 0
                received = self._received_in_window(loc_id, prod.id, dt_from, dt_to) if loc_id else 0

                # test fuel detection: prefer order.is_test, then partner.is_test, then 'test' in order.note
                def is_test_order(order):
                    if hasattr(order, 'is_test'):
                        return bool(getattr(order, 'is_test'))
                    if order.partner_id and hasattr(order.partner_id, 'is_test'):
                        return bool(getattr(order.partner_id, 'is_test'))
                    note = getattr(order, 'note', '') or ''
                    return 'test' in str(note).lower()

                test_litres = 0.0
                test_amount = 0.0
                for order in orders:
                    if is_test_order(order):
                        # sum lines for this product in this order
                        olines = all_lines.filtered(lambda l: l.order_id == order and l.product_id == prod)
                        test_litres += sum(olines.mapped('qty'))
                        test_amount += sum(olines.mapped('price_subtotal_incl'))

                return {
                    'litres_sold': sum(lines.mapped('qty')),
                    'cash_litres': sum(lines.filtered(
                        lambda l: l.order_id in cash_orders).mapped('qty')),
                    'card_litres': sum(lines.filtered(
                        lambda l: l.order_id in card_orders).mapped('qty')),
                    'cash_amount': sum(lines.filtered(
                        lambda l: l.order_id in cash_orders).mapped('price_subtotal_incl')),
                    'card_amount': sum(lines.filtered(
                        lambda l: l.order_id in card_orders).mapped('price_subtotal_incl')),
                    'total_amount': sum(lines.mapped('price_subtotal_incl')),
                    'prepaid_litres': fuel_qty_for_orders(prepaid_orders, prod),
                    'prepaid_amount': fuel_amt_for_orders(prepaid_orders, prod),
                    'account_litres': fuel_qty_for_orders(account_orders, prod),
                    'account_amount': fuel_amt_for_orders(account_orders, prod),
                    'received': received,
                    'opening_stock': opening,
                    'closing_stock': closing,
                    'test_litres': test_litres,
                    'test_amount': test_amount,
                }


            petrol = prod_block(petrol_prod, petrol_lines)
            diesel = prod_block(diesel_prod, diesel_lines)

            shift_blocks.append({
                'session': session,
                'session_name': session.name,
                'config_name': config_name,
                'branch_label': config_name,
                'shift_label': 'Day Shift (06:00â€“18:00)' if shift_label == 'day' else 'Night Shift (18:00â€“06:00)',
                'date_start': session.start_at,
                'date_stop': session.stop_at,
                'petrol': petrol,
                'diesel': diesel,
                'grand_total_litres': petrol['litres_sold'] + diesel['litres_sold'],
                'grand_total_amount': petrol['total_amount'] + diesel['total_amount'],
                'grand_cash_amount': petrol['cash_amount'] + diesel['cash_amount'],
                'grand_card_amount': petrol['card_amount'] + diesel['card_amount'],
            })

        return {
            'wizard': wizard,
            'company': wizard.company_id,
            'report_date': wizard.report_date,
            'branch_label': dict(wizard._fields['branch'].selection).get(wizard.branch, 'All'),
            'shift_label': dict(wizard._fields['shift_type'].selection).get(wizard.shift_type, 'All'),
            'section_label': wizard.section_id.name if getattr(wizard, 'section_id', False) else 'All',
            'blocks': shift_blocks,
        }

    # â”€â”€ QWeb hook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['pos.fuel.report.wizard'].browse(docids)
        return self.get_report_data(wizard)

    # â”€â”€ Excel export â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def action_export_excel(self, wizard):
        try:
            import xlsxwriter
        except ImportError:
            raise ValueError("xlsxwriter is required for Excel export.")

        data = self.get_report_data(wizard)
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Daily Fuel Report')

        # â”€â”€ formats â”€â”€
        def f(**kw):
            return wb.add_format(kw)

        f_title = f(bold=True, font_size=13)
        f_plain = f(font_size=10)
        f_hdr = f(bold=True, font_size=10, bg_color='#1F497D',
                  font_color='#FFFFFF', border=1, align='center', valign='vcenter')
        f_sub = f(bold=True, font_size=10, bg_color='#D9E1F2',
                  border=1, align='center', valign='vcenter')
        f_label = f(font_size=10, border=1, valign='vcenter')
        f_num3 = f(font_size=10, border=1, num_format='#,##0.000',
                   align='right', valign='vcenter')
        f_num2 = f(font_size=10, border=1, num_format='#,##0.00',
                   align='right', valign='vcenter')
        f_bold_label = f(bold=True, font_size=10, border=1,
                         bg_color='#E2EFDA', valign='vcenter')
        f_bold_num3 = f(bold=True, font_size=10, border=1,
                        num_format='#,##0.000', align='right',
                        bg_color='#E2EFDA', valign='vcenter')
        f_bold_num2 = f(bold=True, font_size=10, border=1,
                        num_format='#,##0.00', align='right',
                        bg_color='#E2EFDA', valign='vcenter')
        f_yellow_lbl = f(font_size=10, border=1, bg_color='#FFF2CC', valign='vcenter')
        f_yellow_num3 = f(font_size=10, border=1, num_format='#,##0.000',
                          align='right', bg_color='#FFF2CC', valign='vcenter')
        f_yellow_num2 = f(font_size=10, border=1, num_format='#,##0.00',
                          align='right', bg_color='#FFF2CC', valign='vcenter')

        # Column widths: Label | Petrol | Diesel
        ws.set_column(0, 0, 38)
        ws.set_column(1, 1, 18)
        ws.set_column(2, 2, 18)

        row = 0
        company = data['company']
        ws.write(row, 0, company.name or '', f_title)
        row += 1
        ws.write(row, 0, company.street or '', f_plain)
        row += 1
        ws.write(row, 0, f'TPIN: {company.vat or ""}', f_plain)
        row += 2
        ws.write(row, 0,
                 f'Daily Fuel Sales Report â€” {data["report_date"].strftime("%d %B %Y")}',
                 f_title)
        row += 1
        ws.write(row, 0,
                 f'Branch: {data["branch_label"]}   |   Shift: {data["shift_label"]}   |   Section: {data.get("section_label", "All")}',
                 f_plain)
        row += 2

        for block in data['blocks']:
            p = block['petrol']
            d = block['diesel']

            # Shift header
            ws.merge_range(row, 0, row, 2,
                           f'{block["branch_label"]}  â€”  {block["shift_label"]}  '
                           f'|  Session: {block["session_name"]}',
                           f_hdr)
            ws.set_row(row, 22)
            row += 1

            # Column headers
            ws.write(row, 0, '', f_sub)
            ws.write(row, 1, 'PETROL (L / ZMW)', f_sub)
            ws.write(row, 2, 'DIESEL (L / ZMW)', f_sub)
            row += 1

            def write_row(label, pv, dv, lf, nf, bold=False):
                nonlocal row
                lbl_fmt = f_bold_label if bold else lf
                p_fmt = f_bold_num3 if bold else nf
                d_fmt = f_bold_num3 if bold else nf
                ws.write(row, 0, label, lbl_fmt)
                ws.write(row, 1, pv if pv is not None else 0, p_fmt)
                ws.write(row, 2, dv if dv is not None else 0, d_fmt)
                row += 1

            def write_row2(label, pv, dv, lf, nf, bold=False):
                nonlocal row
                ws.write(row, 0, label, f_bold_label if bold else lf)
                ws.write(row, 1, pv if pv is not None else 0,
                         f_bold_num2 if bold else nf)
                ws.write(row, 2, dv if dv is not None else 0,
                         f_bold_num2 if bold else nf)
                row += 1

            # Opening stock
            write_row('Opening Stock (L)', p['opening_stock'], d['opening_stock'],
                      f_label, f_num3)
            row += 0  # spacer handled inline

            # Litres sold
            write_row('Total Litres Sold', p['litres_sold'], d['litres_sold'],
                      f_bold_label, f_bold_num3, bold=True)
            write_row('    â€” Cash Sales (L)', p['cash_litres'], d['cash_litres'],
                      f_label, f_num3)
            write_row('    â€” Card Sales (L)', p['card_litres'], d['card_litres'],
                      f_label, f_num3)
            row += 1  # spacer

            # Revenue
            write_row2('Total Revenue (ZMW)', p['total_amount'], d['total_amount'],
                       f_bold_label, f_bold_num2, bold=True)
            write_row2('    â€” Cash Sales (ZMW)', p['cash_amount'], d['cash_amount'],
                       f_label, f_num2)
            write_row2('    â€” Card Sales (ZMW)', p['card_amount'], d['card_amount'],
                       f_label, f_num2)
            row += 1  # spacer

            # Prepaid wallet
            write_row('Prepaid Wallet â€” Litres Withdrawn',
                      p['prepaid_litres'], d['prepaid_litres'],
                      f_yellow_lbl, f_yellow_num3)
            write_row2('Prepaid Wallet â€” Amount Expected (ZMW)',
                       p['prepaid_amount'], d['prepaid_amount'],
                       f_yellow_lbl, f_yellow_num2)
            # Account holders
            write_row('Account Holders â€” Litres Withdrawn',
                      p['account_litres'], d['account_litres'],
                      f_yellow_lbl, f_yellow_num3)
            write_row2('Account Holders â€” Amount Expected (ZMW)',
                       p['account_amount'], d['account_amount'],
                       f_yellow_lbl, f_yellow_num2)
            row += 1  # spacer

            # Test fuel
            write_row('Fuel Used for Testing (L)',
                      p.get('test_litres', 0), d.get('test_litres', 0),
                      f_label, f_num3)
            write_row2('Fuel Used for Testing â€” Amount (ZMW)',
                       p.get('test_amount', 0), d.get('test_amount', 0),
                       f_label, f_num2)

            # Received
            write_row('Fuel Received During Shift (L)',
                      p['received'], d['received'], f_label, f_num3)

            # Closing stock
            write_row('Closing Stock (L)', p['closing_stock'], d['closing_stock'],
                      f_label, f_num3)

            row += 2  # gap between shifts

        wb.close()
        output.seek(0)
        fname = f'Fuel_Report_{wizard.report_date.strftime("%Y%m%d")}.xlsx'
        att = self.env['ir.attachment'].create({
            'name': fname,
            'type': 'binary',
            'datas': base64.b64encode(output.read()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': wizard._name,
            'res_id': wizard.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }
