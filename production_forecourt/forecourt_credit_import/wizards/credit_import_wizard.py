# -*- coding: utf-8 -*-
import re
import base64
import io
from datetime import datetime
from odoo import models, fields, api
from odoo.exceptions import UserError

try:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar
    PDFMINER_AVAILABLE = True
except ImportError:
    PDFMINER_AVAILABLE = False


BRANCH_PREFIX_MAP = {
    'ARC': 'Arcades',
    'LUA': 'Luanshya',
    'CHI': 'Chililabombwe',
}


def _extract_rows_from_pdf(filedata):
    """Extract text rows from a PDF using raw character positions
    (validated against real NetPOS reports â€” this PDF format has no
    LTTextLine groupings, characters must be grouped into rows manually
    using y-coordinate clustering, then spaced using x-coordinate gaps)."""
    if not PDFMINER_AVAILABLE:
        raise UserError(
            'pdfminer.six is not installed on the server. '
            'Install it with: pip3 install pdfminer.six --break-system-packages'
        )

    def extract_chars(page_layout):
        chars = []
        def walk(obj):
            if isinstance(obj, LTChar):
                chars.append(obj)
            elif hasattr(obj, '__iter__'):
                try:
                    for child in obj:
                        walk(child)
                except TypeError:
                    pass
        walk(page_layout)
        return chars

    all_rows = []
    for page_layout in extract_pages(io.BytesIO(filedata)):
        chars = extract_chars(page_layout)
        if not chars:
            continue
        chars.sort(key=lambda c: -c.y0)
        rows = []
        current_row = []
        current_y = None
        Y_TOLERANCE = 2.5
        for c in chars:
            if current_y is None or abs(c.y0 - current_y) <= Y_TOLERANCE:
                current_row.append(c)
                current_y = c.y0 if current_y is None else current_y
            else:
                rows.append(current_row)
                current_row = [c]
                current_y = c.y0
        if current_row:
            rows.append(current_row)

        for row in rows:
            row.sort(key=lambda c: c.x0)
            out = []
            prev_x1 = None
            avg_width = sum(c.width for c in row) / len(row) if row else 1
            for c in row:
                if prev_x1 is not None and (c.x0 - prev_x1) > avg_width * 1.3:
                    out.append(' ')
                out.append(c.get_text())
                prev_x1 = c.x1
            all_rows.append(''.join(out))
    return all_rows


def _detect_report_type(rows):
    full_text = '\n'.join(rows)
    if re.search(r'Detailed Debtor Transaction List', full_text, re.IGNORECASE):
        return 'transactions'
    if re.search(r'Debtors Summary', full_text, re.IGNORECASE):
        return 'balances'
    raise UserError(
        'Could not determine report type â€” expected "Detailed Debtor Transaction '
        'List" or "Debtors Summary" somewhere in the PDF text.'
    )


def _branch_from_code(code):
    prefix = code.split()[0].upper() if code and ' ' in code else (code or '')[:3].upper()
    return BRANCH_PREFIX_MAP.get(prefix)


# Transaction row pattern:
# ARC 002 GOPA Infra 000 01/07/2026 82323 Diesel-50  P:10 N:1 17.78 28.11 0.00 500.00
_TXN_ROW_RE = re.compile(
    r'^(?P<code>[A-Z]{2,4}\s?\d{2,4}|\d{3})\s+'
    r'(?P<name>.+?)\s+'
    r'(?:\d{3}\s+)?'
    r'(?P<date>\d{2}/\d{2}/\d{4})\s+'
    r'(?P<invoice>\d+)\s+'
    r'(?P<stock>(?:Diesel|Petrol|LPG|Lubricant)[-\w]*)\s*'
    r'(?P<pump>P:\d+\s*N:\d+)?\s*'
    r'(?P<qty>[\d,]+\.\d+)\s+'
    r'(?P<ppu>[\d,]+\.\d+)\s+'
    r'(?P<discount>[\d,]+\.\d+)\s+'
    r'(?P<amount>[\d,]+\.\d+)\s*$'
)

# Balance row pattern:
# ARC 002 GOPA Infra -14538.03
_BAL_ROW_RE = re.compile(
    r'^(?P<code>[A-Z]{2,4}\s?\d{2,4}|\d{3})\s+'
    r'(?P<name>.+?)\s+'
    r'(?P<amount>-?[\d,]+\.\d+)\s*$'
)


def _clean_num(s):
    if not s:
        return 0.0
    try:
        return float(s.replace(',', ''))
    except ValueError:
        return 0.0


def _parse_transactions(rows):
    results = []
    for row in rows:
        m = _TXN_ROW_RE.match(row.strip())
        if not m:
            continue
        d = m.groupdict()
        stock = d['stock'].lower()
        if 'diesel' in stock:
            product_type = 'diesel'
        elif 'petrol' in stock:
            product_type = 'petrol'
        else:
            product_type = 'other'
        try:
            txn_date = datetime.strptime(d['date'], '%d/%m/%Y').date()
        except ValueError:
            continue
        results.append({
            'code': d['code'].strip(),
            'name': re.sub(r'(?<=[A-Za-z])\d+|\d+(?=[A-Za-z])', '', d['name']).strip(),  # strip stray digit-interleave artifacts
            'date': txn_date,
            'invoice_number': d['invoice'],
            'product_type': product_type,
            'stock_description': d['stock'],
            'pump_code': (d['pump'] or '').strip(),
            'quantity': _clean_num(d['qty']),
            'unit_price': _clean_num(d['ppu']),
            'discount': _clean_num(d['discount']),
            'amount': _clean_num(d['amount']),
        })
    return results


def _parse_balances(rows):
    results = []
    for row in rows:
        row = row.strip()
        if not row or row.lower().startswith('total'):
            continue
        m = _BAL_ROW_RE.match(row)
        if not m:
            continue
        d = m.groupdict()
        results.append({
            'code': d['code'].strip(),
            'name': re.sub(r'(?<=[A-Za-z])\d+|\d+(?=[A-Za-z])', '', d['name']).strip(),
            # NetPOS stores debt as negative; flip sign so it matches our own
            # positive "amount owed" convention used by computed_balance.
            'balance': -_clean_num(d['amount']),
        })
    return results


class ForecourtCreditImportWizard(models.TransientModel):
    _name = 'forecourt.credit.import.wizard'
    _description = 'Import NetPOS Prepaid Customer Report'

    report_file = fields.Binary(string='NetPOS PDF Report', required=True)
    report_filename = fields.Char(string='Filename')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft')
    result_summary = fields.Text(string='Import Result', readonly=True)

    def _return_self(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'forecourt.credit.import.wizard',
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def _get_or_create_customer(self, code, name):
        Customer = self.env['forecourt.credit.customer'].sudo()
        customer = Customer.search([('code', '=', code)], limit=1)
        if customer:
            return customer
        branch_name = _branch_from_code(code)
        warehouse = False
        if branch_name:
            warehouse = self.env['stock.warehouse'].sudo().search([
                ('is_forecourt_branch', '=', True),
                ('name', 'ilike', branch_name),
            ], limit=1)
        return Customer.create({
            'code': code,
            'name': name,
            'branch_id': warehouse.id if warehouse else False,
            'company_id': (warehouse.company_id.id if warehouse else self.env.company.id),
        })

    def action_import(self):
        self.ensure_one()
        if not self.report_file:
            raise UserError('Please upload a PDF file.')

        filedata = base64.b64decode(self.report_file)
        rows = _extract_rows_from_pdf(filedata)
        report_type = _detect_report_type(rows)

        if report_type == 'transactions':
            parsed = _parse_transactions(rows)
            if not parsed:
                raise UserError('No transaction rows could be parsed from this PDF.')
            created = updated = skipped = 0
            Transaction = self.env['forecourt.credit.transaction'].sudo()
            for item in parsed:
                customer = self._get_or_create_customer(item['code'], item['name'])
                existing = Transaction.search([
                    ('customer_id', '=', customer.id),
                    ('invoice_number', '=', item['invoice_number']),
                    ('product_type', '=', item['product_type']),
                ], limit=1)
                if existing:
                    skipped += 1
                    continue
                # Try to match to an existing shift by branch + date
                shift = False
                if customer.branch_id:
                    shift = self.env['forecourt.shift'].sudo().search([
                        ('branch_id', '=', customer.branch_id.id),
                        ('date_start', '>=', str(item['date']) + ' 00:00:00'),
                        ('date_start', '<=', str(item['date']) + ' 23:59:59'),
                    ], limit=1)
                Transaction.create({
                    'customer_id': customer.id,
                    'date': item['date'],
                    'invoice_number': item['invoice_number'],
                    'product_type': item['product_type'],
                    'stock_description': item['stock_description'],
                    'pump_code': item['pump_code'],
                    'quantity': item['quantity'],
                    'unit_price': item['unit_price'],
                    'discount': item['discount'],
                    'amount': item['amount'],
                    'shift_id': shift.id if shift else False,
                    'import_source': self.report_filename,
                })
                created += 1
            summary = (
                f"TRANSACTIONS IMPORT COMPLETE\n"
                f"Rows parsed: {len(parsed)} | Created: {created} | "
                f"Already existed (skipped): {skipped}"
            )

        else:  # balances
            parsed = _parse_balances(rows)
            if not parsed:
                raise UserError('No balance rows could be parsed from this PDF.')
            matched = unmatched = 0
            variances = []
            for item in parsed:
                customer = self.env['forecourt.credit.customer'].sudo().search(
                    [('code', '=', item['code'])], limit=1
                )
                if not customer:
                    customer = self._get_or_create_customer(item['code'], item['name'])
                    unmatched += 1
                else:
                    matched += 1
                customer.write({
                    'netpos_reported_balance': item['balance'],
                    'netpos_balance_date': fields.Date.context_today(self),
                })
                if abs(customer.balance_variance) > 1.0:
                    variances.append(
                        f"  {customer.code} ({customer.name}): "
                        f"our={customer.computed_balance:.2f} netpos={customer.netpos_reported_balance:.2f} "
                        f"variance={customer.balance_variance:.2f}"
                    )
            summary = (
                f"ACCOUNT BALANCES IMPORT COMPLETE\n"
                f"Rows parsed: {len(parsed)} | Existing customers matched: {matched} | "
                f"New customers created: {unmatched}\n\n"
            )
            if variances:
                summary += f"BALANCE VARIANCES FOUND ({len(variances)}):\n" + '\n'.join(variances)
            else:
                summary += "No variances â€” our records match NetPOS exactly."

        self.write({'result_summary': summary, 'state': 'done'})
        return self._return_self()
