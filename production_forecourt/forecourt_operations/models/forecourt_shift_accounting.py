# -*- coding: utf-8 -*-
#
# Feature 4: Accounting posting on shift close.
#
# Design notes (do not hardcode account/journal IDs -- this module installs
# on arbitrary client instances, not just the `dump` test DB):
#   - All accounts/journals are looked up at runtime via account_type / type
#     + name, scoped to shift.company_id.
#   - Variance line (WhatsApp-sourced shifts only) posts against the
#     standard Odoo CoA template account "Inventory Count Variance" --
#     it already exists on every instance built from the standard template,
#     so no new account needs to be created via module data.
#   - Materiality threshold (VARIANCE_REVIEW_THRESHOLD) only affects whether
#     a shift is flagged for human review -- it does NOT block posting.
#     The entry must balance either way, so the variance line is added
#     regardless of size.

from odoo import models, fields, api
from odoo.exceptions import UserError

VARIANCE_REVIEW_THRESHOLD = 5.0  # currency units; >= this triggers needs_review flag


class ForecourtShift(models.Model):
    _inherit = 'forecourt.shift'

    # --- new fields ---
    posting_state = fields.Selection(
        [
            ('not_posted', 'Not Posted'),
            ('posted', 'Posted'),
            ('posted_with_variance', 'Posted (Variance)'),
        ],
        string='Posting Status',
        default='not_posted',
        copy=False,
    )
    variance_amount = fields.Monetary(
        string='Cash/Stock Variance',
        currency_field='currency_id',
        copy=False,
        help="Debit-credit imbalance computed at posting time. "
             "Positive = debits exceeded credits (shortfall), "
             "negative = credits exceeded debits (surplus).",
    )
    needs_review = fields.Boolean(
        string='Needs Review',
        copy=False,
        help="Set when the posted variance meets or exceeds the review threshold.",
    )
    # account_move_id already exists from the prior session (POS sync groundwork) -- reused here.

    # --- account/journal lookup helpers ---

    def _get_forecourt_account(self, account_type, name, required=True):
        """Look up a CoA account by type + name, scoped to this shift's company.
        Raises a clear, specific error if the instance's CoA doesn't match
        the expected standard-template pattern, rather than guessing."""
        self.ensure_one()
        account = self.env['account.account'].search([
            ('company_ids', 'in', [self.company_id.id]),
            ('account_type', '=', account_type),
            ('name', '=', name),
        ], limit=2)
        if len(account) > 1:
            raise UserError(
                f"Multiple '{name}' ({account_type}) accounts found for "
                f"company {self.company_id.name}. Please ensure only one "
                f"account matches this name before posting."
            )
        if not account and required:
            raise UserError(
                f"Could not find the '{name}' account (type: {account_type}) "
                f"for company {self.company_id.name}. Please configure this "
                f"account in Accounting before posting this shift."
            )
        return account

    def _get_forecourt_sale_journal(self):
        self.ensure_one()
        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company_id.id),
            ('type', '=', 'sale'),
        ], limit=2)
        if len(journal) > 1:
            raise UserError(
                f"Multiple sale journals found for company {self.company_id.name}. "
                f"Please specify which journal to use before posting."
            )
        if not journal:
            raise UserError(
                f"No sale journal found for company {self.company_id.name}. "
                f"Please configure one in Accounting before posting this shift."
            )
        return journal

    # --- main posting entrypoint ---

    def action_post_to_accounts(self):
        for shift in self:
            if shift.posting_state != 'not_posted':
                raise UserError(
                    f"Shift {shift.name} has already been posted "
                    f"(status: {shift.posting_state}). Re-posting is not allowed."
                )
            if shift.source == 'pos':
                shift._post_pos_shift()
            elif shift.source == 'whatsapp':
                shift._post_whatsapp_shift()
            else:
                raise UserError(
                    f"Shift {shift.name} has source '{shift.source}', which "
                    f"is not currently supported for accounting posting "
                    f"(only 'pos' and 'whatsapp' are handled)."
                )
        return True

    # --- POS-sourced posting (should balance exactly, by construction) ---

    def _post_pos_shift(self):
        self.ensure_one()
        cash_acc = self._get_forecourt_account('asset_cash', 'Cash')
        bank_acc = self._get_forecourt_account('asset_cash', 'Bank')
        ar_acc = self._get_forecourt_account('asset_receivable', 'Customers')
        sales_acc = self._get_forecourt_account('income', 'Sales')
        journal = self._get_forecourt_sale_journal()

        # payment_method -> debit account
        method_account_map = {
            'cash': cash_acc,
            'mobile_money': bank_acc,
            'card': bank_acc,
            'credit': ar_acc,
        }

        lines = []
        totals_by_method = {}
        totals_by_product_type = {}

        for sale in self.sale_ids:
            totals_by_method.setdefault(sale.payment_method, 0.0)
            totals_by_method[sale.payment_method] += sale.total_amount
            totals_by_product_type.setdefault(sale.product_type, 0.0)
            totals_by_product_type[sale.product_type] += sale.total_amount

        for method, amount in totals_by_method.items():
            if not amount:
                continue
            acc = method_account_map.get(method)
            if not acc:
                raise UserError(
                    f"No account mapping configured for payment method "
                    f"'{method}' on shift {self.name}."
                )
            lines.append((0, 0, {
                'account_id': acc.id,
                'name': f"{self.name} - {method}",
                'debit': amount,
                'credit': 0.0,
            }))

        for product_type, amount in totals_by_product_type.items():
            if not amount:
                continue
            lines.append((0, 0, {
                'account_id': sales_acc.id,
                'name': f"{self.name} - {product_type} sales",
                'debit': 0.0,
                'credit': amount,
            }))

        total_debit = sum(l[2]['debit'] for l in lines)
        total_credit = sum(l[2]['credit'] for l in lines)
        variance = round(total_debit - total_credit, 2)

        if abs(variance) > 0.01:
            # Should not happen for POS-sourced shifts by construction --
            # treat as a real bug, not a variance to paper over.
            raise UserError(
                f"POS-sourced shift {self.name} does not balance "
                f"(debit {total_debit}, credit {total_credit}). This "
                f"indicates a bug in POS sync, not a real cash/stock "
                f"variance -- please investigate before posting."
            )

        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'ref': f"Forecourt shift {self.name} (POS)",
            'line_ids': lines,
        })
        move.action_post()

        self.write({
            'account_move_id': move.id,
            'posting_state': 'posted',
            'variance_amount': 0.0,
            'needs_review': False,
        })

    # --- WhatsApp-sourced posting (variance line likely needed) ---

    def _post_whatsapp_shift(self):
        self.ensure_one()
        cash_acc = self._get_forecourt_account('asset_cash', 'Cash')
        bank_acc = self._get_forecourt_account('asset_cash', 'Bank')
        ar_acc = self._get_forecourt_account('asset_receivable', 'Customers')
        sales_acc = self._get_forecourt_account('income', 'Sales')
        # "Inventory Count Variance" is account_type='expense_direct_cost'
        # on the standard CoA template (confirmed via diagnostic on Aziz UAT).
        variance_acc = self._get_forecourt_account(
            'expense_direct_cost', 'Inventory Count Variance', required=False
        )
        if not variance_acc:
            # Fall back to a name-only search scoped to company, in case a
            # different CoA template uses a different account_type for this.
            variance_acc = self.env['account.account'].search([
                ('company_ids', 'in', [self.company_id.id]),
                ('name', '=', 'Inventory Count Variance'),
            ], limit=1)
        if not variance_acc:
            raise UserError(
                f"Could not find an 'Inventory Count Variance' account for "
                f"company {self.company_id.name}. Please configure this "
                f"account in Accounting before posting this shift."
            )
        journal = self._get_forecourt_sale_journal()

        cash_amount = self.cash_on_hand_kwacha or 0.0
        bank_amount = self.pos_account_kwacha or 0.0

        credit_ar_total = 0.0
        sales_by_product_type = {}
        for entry in self.fuel_entry_ids:
            rate = entry.rate or 0.0
            credit_litres = entry.credit_litres_sold or 0.0
            credit_ar_total += credit_litres * rate
            # cash_sales_value is pre-computed on the entry itself
            fuel_revenue = (entry.cash_sales_value or 0.0) + (credit_litres * rate)
            # Aggregate all fuel_type entries (petrol/diesel/...) under the
            # single 'fuel' category, matching forecourt.sale.product_type
            # (fuel/lpg/lubricant) so both POS and WhatsApp postings itemize
            # Sales identically.
            sales_by_product_type.setdefault('fuel', 0.0)
            sales_by_product_type['fuel'] += fuel_revenue

        # LPG / Lubricant revenue lives as separate lump fields on the shift,
        # not in fuel_entry_ids -- itemize them as their own product-type lines.
        if self.lpg_sales_kwacha:
            sales_by_product_type.setdefault('lpg', 0.0)
            sales_by_product_type['lpg'] += self.lpg_sales_kwacha
        if self.lube_sales_kwacha:
            sales_by_product_type.setdefault('lubricant', 0.0)
            sales_by_product_type['lubricant'] += self.lube_sales_kwacha

        lines = []
        if cash_amount:
            lines.append((0, 0, {
                'account_id': cash_acc.id,
                'name': f"{self.name} - cash on hand",
                'debit': cash_amount,
                'credit': 0.0,
            }))
        if bank_amount:
            lines.append((0, 0, {
                'account_id': bank_acc.id,
                'name': f"{self.name} - MOMO/card",
                'debit': bank_amount,
                'credit': 0.0,
            }))
        if credit_ar_total:
            lines.append((0, 0, {
                'account_id': ar_acc.id,
                'name': f"{self.name} - credit sales",
                'debit': credit_ar_total,
                'credit': 0.0,
            }))
        for product_type, amount in sales_by_product_type.items():
            if not amount:
                continue
            lines.append((0, 0, {
                'account_id': sales_acc.id,
                'name': f"{self.name} - {product_type} sales",
                'debit': 0.0,
                'credit': amount,
            }))

        total_debit = sum(l[2]['debit'] for l in lines)
        total_credit = sum(l[2]['credit'] for l in lines)
        variance = round(total_debit - total_credit, 2)

        posting_state = 'posted'
        needs_review = False

        if abs(variance) > 0.01:
            if variance > 0:
                # debits exceed credits -> need an extra credit line
                lines.append((0, 0, {
                    'account_id': variance_acc.id,
                    'name': f"{self.name} - cash/stock variance",
                    'debit': 0.0,
                    'credit': variance,
                }))
            else:
                # credits exceed debits -> need an extra debit line
                lines.append((0, 0, {
                    'account_id': variance_acc.id,
                    'name': f"{self.name} - cash/stock variance",
                    'debit': -variance,
                    'credit': 0.0,
                }))
            posting_state = 'posted_with_variance'
            needs_review = abs(variance) >= VARIANCE_REVIEW_THRESHOLD

        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'ref': f"Forecourt shift {self.name} (WhatsApp)",
            'line_ids': lines,
        })
        move.action_post()

        self.write({
            'account_move_id': move.id,
            'posting_state': posting_state,
            'variance_amount': variance,
            'needs_review': needs_review,
        })
