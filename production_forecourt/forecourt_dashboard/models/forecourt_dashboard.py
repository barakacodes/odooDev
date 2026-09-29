# -*- coding: utf-8 -*-
from odoo import models, api, fields
from datetime import date


class ForecourtDashboard(models.AbstractModel):
    _name = 'forecourt.dashboard'
    _description = 'Forecourt Dashboard Data Provider'

    BRANCHES = [
        ('arcades', 'Arcades'),
        ('luanshya', 'Luanshya'),
        ('chililabombwe', 'Chililabombwe'),
    ]

    # =========================================================================
    # Top-level entry point
    # =========================================================================
    @api.model
    def get_dashboard_data(self, branch='all', date_from=None, date_to=None):
        today = date.today()
        if not date_from:
            date_from = today.replace(day=1).isoformat()
        if not date_to:
            date_to = today.isoformat()

        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]

        cash_sales_rows, cash_totals = self._get_cash_sales(branch_keys, date_from, date_to)
        credit_rows, credit_totals = self._get_credit_sales(branch_keys, date_from, date_to)
        inventory_rows = self._get_inventory(branch_keys)
        deposit_rows, deposit_totals = self._get_deposits(branch_keys, date_from, date_to)
        variance_rows, variance_totals = self._get_variances(branch_keys, date_from, date_to)

        return {
            'cash_sales': cash_sales_rows,
            'cash_sales_total': cash_totals['grand'],
            'cash_sales_pos_total': cash_totals['pos'],
            'credit_sales': credit_rows,
            'credit_sales_total': credit_totals['grand'],
            'inventory': inventory_rows,
            'deposits': deposit_rows,
            'total_deposits': deposit_totals['grand'],
            'variances': variance_rows,
            'total_variance': variance_totals['stock'],
            'total_cash_variance': variance_totals['cash'],
            # ── Variances tab totals (used by grand total row) ──────────────
            'variance_by_branch_total_litres': round(
                variance_totals.get('grand_petrol_var', 0.0)
                + variance_totals.get('grand_diesel_var', 0.0), 2
            ),
            'variance_by_branch_total_value_k': variance_totals.get('grand_value_k', 0.0),
            'variance_report_issue_count': variance_totals.get('report_issue_count', 0),
            'variance_total_entries': variance_totals.get('total_entry_count', 0),
            # ── Existing ────────────────────────────────────────────────────
            'tanks': self._get_tank_status(),
            'branch_stock': self._get_branch_stock_summary(),
            'cash_by_branch': cash_totals['by_branch'],
            'credit_by_branch': credit_totals['by_branch'],
            'deposit_by_branch': deposit_totals['by_branch'],
            'variance_by_branch': variance_totals['by_branch'],
            'date_from': date_from,
            'date_to': date_to,
        }

    # -------------------------------------------------------------------------
    # Cash Sales
    # -------------------------------------------------------------------------
    @api.model
    def _get_cash_sales(self, branches, date_from, date_to):
        rows = []
        by_branch = {}
        grand = pos_grand = 0.0

        Shift = self.env['forecourt.shift']
        Entry = self.env['forecourt.shift.fuel.entry']
        for branch in branches:
            shifts = Shift.search([
                ('branch', '=', branch),
                ('date_start', '>=', date_from + ' 00:00:00'),
                ('date_start', '<=', date_to + ' 23:59:59'),
            ])
            entries = Entry.search([
                ('branch', '=', branch),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])

            petrol_k = round(sum(e.cash_sales_value for e in entries if e.fuel_type == 'petrol'), 2)
            diesel_k = round(sum(e.cash_sales_value for e in entries if e.fuel_type == 'diesel'), 2)
            lubes_k = round(sum(shifts.mapped('lube_sales_kwacha')), 2)
            lpg_k = round(sum(shifts.mapped('lpg_sales_kwacha')), 2)
            pos_k = round(sum(shifts.mapped('pos_account_kwacha')), 2)

            fuel_cash = petrol_k + diesel_k
            total_cash = fuel_cash + lubes_k + lpg_k
            gross = total_cash + pos_k

            by_branch[branch] = {
                'branch': dict(self.BRANCHES).get(branch, branch),
                'petrol_cash': petrol_k,
                'diesel_cash': diesel_k,
                'lubes': lubes_k,
                'lpg': lpg_k,
                'pos': pos_k,
                'total_cash': total_cash,
                'gross': gross,
            }
            grand += gross
            pos_grand += pos_k

            rows.append({
                'branch': dict(self.BRANCHES).get(branch, branch),
                'petrol_cash': petrol_k,
                'diesel_cash': diesel_k,
                'lubes': lubes_k,
                'lpg': lpg_k,
                'pos': pos_k,
                'total_cash': total_cash,
                'gross': gross,
            })

        totals = {
            'grand': round(grand, 2),
            'pos': round(pos_grand, 2),
            'by_branch': by_branch,
        }
        return rows, totals

    # -------------------------------------------------------------------------
    # Credit Sales
    # -------------------------------------------------------------------------
    @api.model
    def _get_credit_sales(self, branches, date_from, date_to):
        rows = []
        by_branch = {}
        grand = 0.0

        Entry = self.env['forecourt.shift.fuel.entry']

        for branch in branches:
            entries = Entry.search([
                ('branch', '=', branch),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])
            petrol_l = round(sum(e.credit_litres_sold for e in entries
                                 if e.fuel_type == 'petrol'), 2)
            diesel_l = round(sum(e.credit_litres_sold for e in entries
                                 if e.fuel_type == 'diesel'), 2)
            petrol_k = round(sum(
                (e.credit_litres_sold or 0.0) * (e.rate or 0.0)
                for e in entries if e.fuel_type == 'petrol'
            ), 2)
            diesel_k = round(sum(
                (e.credit_litres_sold or 0.0) * (e.rate or 0.0)
                for e in entries if e.fuel_type == 'diesel'
            ), 2)
            total = petrol_k + diesel_k

            by_branch[branch] = {
                'branch': dict(self.BRANCHES).get(branch, branch),
                'petrol_litres': petrol_l,
                'diesel_litres': diesel_l,
                'petrol_value': petrol_k,
                'diesel_value': diesel_k,
                'total': total,
            }
            grand += total

            rows.append({
                'branch': dict(self.BRANCHES).get(branch, branch),
                'petrol_litres': petrol_l,
                'diesel_litres': diesel_l,
                'petrol_value': petrol_k,
                'diesel_value': diesel_k,
                'total': total,
            })

        totals = {'grand': round(grand, 2), 'by_branch': by_branch}
        return rows, totals

    # -------------------------------------------------------------------------
    # Inventory
    # -------------------------------------------------------------------------
    @api.model
    def _get_inventory(self, branches):
        rows = []
        Entry = self.env['forecourt.shift.fuel.entry']

        for branch in branches:
            for fuel in ('petrol', 'diesel'):
                latest = Entry.search([
                    ('branch', '=', branch),
                    ('fuel_type', '=', fuel),
                ], order='shift_date desc, id desc', limit=1)

                rows.append({
                    'branch': dict(self.BRANCHES).get(branch, branch),
                    'product': fuel.capitalize(),
                    'opening': round(latest.opening_stock_litres, 2) if latest else 0,
                    'received': round(latest.received_stock_litres, 2) if latest else 0,
                    'sold_cash': round(latest.cash_sales_litres, 2) if latest else 0,
                    'sold_credit': round(latest.credit_litres_sold, 2) if latest else 0,
                    'sold': round(latest.total_sold_litres, 2) if latest else 0,
                    'closing': round(latest.closing_stock_litres, 2) if latest else 0,
                    'atg_closing': round(latest.atg_closing_stock_litres, 2) if latest else 0,
                    'date': latest.shift_date.isoformat() if latest and latest.shift_date else '',
                })

        return rows

    # -------------------------------------------------------------------------
    # Bank Deposits
    # -------------------------------------------------------------------------
    @api.model
    def _get_deposits(self, branches, date_from, date_to):
        rows = []
        by_branch = {}
        grand = 0.0

        Recon = self.env['forecourt.cash.reconciliation']
        Shift = self.env['forecourt.shift']

        for branch in branches:
            shifts = Shift.search([
                ('branch', '=', branch),
                ('date_start', '>=', date_from + ' 00:00:00'),
                ('date_start', '<=', date_to + ' 23:59:59'),
            ])
            recons = Recon.search([
                ('shift_id', 'in', shifts.ids),
                ('state', 'in', ['submitted', 'approved']),
            ])

            day_shifts = shifts.filtered(lambda s: s.shift_type == 'day')
            night_shifts = shifts.filtered(lambda s: s.shift_type == 'night')
            day_r = recons.filtered(lambda r: r.shift_id in day_shifts)
            night_r = recons.filtered(lambda r: r.shift_id in night_shifts)

            day_dep = round(sum(day_r.mapped('bank_deposit')), 2)
            night_dep = round(sum(night_r.mapped('bank_deposit')), 2)
            lubes = round(sum(recons.mapped('lubes_cash')), 2)
            lpg = round(sum(recons.mapped('lpg_cash')), 2)
            total = round(sum(recons.mapped('bank_deposit')), 2)

            by_branch[branch] = {
                'branch': dict(self.BRANCHES).get(branch, branch),
                'day_fuel': day_dep,
                'night_fuel': night_dep,
                'lubes': lubes,
                'lpg': lpg,
                'total': total,
            }
            grand += total

            rows.append({
                'branch': dict(self.BRANCHES).get(branch, branch),
                'day_fuel': day_dep,
                'night_fuel': night_dep,
                'lubes': lubes,
                'lpg': lpg,
                'total': total,
            })

        totals = {'grand': round(grand, 2), 'by_branch': by_branch}
        return rows, totals

    # -------------------------------------------------------------------------
    # Variances
    # -------------------------------------------------------------------------
    @api.model
    def _empty_shift_var_block(self):
        """Template dict for a single shift type (day or night) in the
        Variances tab. Keeping this in one place avoids key-mismatch bugs."""
        return {
            'cash_var': 0.0,
            'petrol_var': 0.0,
            'petrol_var_k': 0.0,
            'petrol_rate': 0.0,
            'diesel_var': 0.0,
            'diesel_var_k': 0.0,
            'diesel_rate': 0.0,
            'status': 'na',
        }

    @api.model
    def _get_variances(self, branches, date_from, date_to):
        rows = []
        by_branch = {}
        total_stock_var = total_cash_var = 0.0

        Entry = self.env['forecourt.shift.fuel.entry']
        Shift = self.env['forecourt.shift']

        for branch in branches:
            shifts = Shift.search([
                ('branch', '=', branch),
                ('date_start', '>=', date_from + ' 00:00:00'),
                ('date_start', '<=', date_to + ' 23:59:59'),
            ])
            entries = Entry.search([
                ('branch', '=', branch),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])

            # NOTE: we do NOT skip empty branches. For an audit tool, a branch
            # with zero recorded shifts is itself a finding and must remain
            # visible on the tab.
            has_data = bool(shifts) or bool(entries)

            report_issues = len(entries.filtered(
                lambda e: e.report_consistency_status == 'mismatch'
            ))

            branch_row = {
                'branch': dict(self.BRANCHES).get(branch, branch),
                'day': self._empty_shift_var_block(),
                'night': self._empty_shift_var_block(),
                'full_cash_var': 0.0,
                'full_petrol_var': 0.0,
                'full_petrol_var_k': 0.0,
                'full_diesel_var': 0.0,
                'full_diesel_var_k': 0.0,
                'status': 'na',
                'has_data': has_data,
                'report_issue_count': report_issues,
                'total_entry_count': len(entries),
            }

            if has_data:
                for shift_type in ('day', 'night'):
                    type_shifts = shifts.filtered(lambda s: s.shift_type == shift_type)
                    type_entries = entries.filtered(lambda e: e.shift_id in type_shifts)

                    cash_var = round(sum(type_shifts.mapped('cash_check_variance')), 2)

                    petrol_var = diesel_var = 0.0
                    petrol_var_k = diesel_var_k = 0.0
                    for e in type_entries:
                        if e.atg_closing_stock_litres > 0:
                            var_l = e.atg_dip_variance or 0.0
                        else:
                            var_l = e.closing_variance or 0.0
                        rate = e.rate or 0.0
                        if e.fuel_type == 'petrol':
                            petrol_var += var_l
                            petrol_var_k += var_l * rate
                        elif e.fuel_type == 'diesel':
                            diesel_var += var_l
                            diesel_var_k += var_l * rate

                    statuses = (
                        list(type_shifts.mapped('cash_check_status'))
                        + list(type_entries.mapped('closing_check_status'))
                        + list(type_entries.mapped('atg_dip_check_status'))
                        + list(type_entries.mapped('report_consistency_status'))
                    )
                    status = 'mismatch' if 'mismatch' in statuses else ('ok' if 'ok' in statuses else 'na')

                    branch_row[shift_type] = {
                        'cash_var': round(cash_var, 2),
                        'petrol_var': round(petrol_var, 2),
                        'petrol_var_k': round(petrol_var_k, 2),
                        'petrol_rate': round(petrol_var_k / petrol_var, 4) if petrol_var else 0.0,
                        'diesel_var': round(diesel_var, 2),
                        'diesel_var_k': round(diesel_var_k, 2),
                        'diesel_rate': round(diesel_var_k / diesel_var, 4) if diesel_var else 0.0,
                        'status': status,
                    }

                branch_row['full_cash_var'] = round(
                    branch_row['day']['cash_var'] + branch_row['night']['cash_var'], 2
                )
                branch_row['full_petrol_var'] = round(
                    branch_row['day']['petrol_var'] + branch_row['night']['petrol_var'], 2
                )
                branch_row['full_petrol_var_k'] = round(
                    branch_row['day']['petrol_var_k'] + branch_row['night']['petrol_var_k'], 2
                )
                branch_row['full_diesel_var'] = round(
                    branch_row['day']['diesel_var'] + branch_row['night']['diesel_var'], 2
                )
                branch_row['full_diesel_var_k'] = round(
                    branch_row['day']['diesel_var_k'] + branch_row['night']['diesel_var_k'], 2
                )

                all_statuses = [branch_row['day']['status'], branch_row['night']['status']]
                branch_row['status'] = (
                    'mismatch' if 'mismatch' in all_statuses
                    else ('ok' if 'ok' in all_statuses else 'na')
                )

                total_stock_var += branch_row['full_petrol_var'] + branch_row['full_diesel_var']
                total_cash_var += branch_row['full_cash_var']

            by_branch[branch] = branch_row
            rows.append(branch_row)

        totals = {
            'stock': round(total_stock_var, 2),
            'cash': round(total_cash_var, 2),
            'by_branch': by_branch,
            'grand_petrol_var': round(sum(r['full_petrol_var'] for r in rows), 2),
            'grand_diesel_var': round(sum(r['full_diesel_var'] for r in rows), 2),
            'grand_petrol_var_k': round(sum(r['full_petrol_var_k'] for r in rows), 2),
            'grand_diesel_var_k': round(sum(r['full_diesel_var_k'] for r in rows), 2),
            'grand_value_k': round(
                sum(r['full_petrol_var_k'] + r['full_diesel_var_k'] for r in rows), 2
            ),
            'report_issue_count': sum(r['report_issue_count'] for r in rows),
            'total_entry_count': sum(r['total_entry_count'] for r in rows),
        }
        return rows, totals

    # -------------------------------------------------------------------------
    # Tank Status
    # -------------------------------------------------------------------------
    @api.model
    def _get_tank_status(self):
        tanks = self.env['forecourt.tank'].search([('active', '=', True)])
        return [{
            'name': t.name,
            'product': t.product_id.name if t.product_id else '',
            'current': round(t.current_level, 1),
            'capacity': round(t.capacity, 1),
            'pct': round(t.level_percentage, 1),
            'status': t.stock_status,
        } for t in tanks]

    @api.model
    def _get_branch_stock_summary(self):
        Warehouse = self.env['stock.warehouse']
        branches = Warehouse.search([('is_forecourt_branch', '=', True)], order='name')

        result = []
        for wh in branches:
            tanks = wh.forecourt_tank_ids.filtered('active')
            tank_rows = [{
                'name': t.name,
                'product': t.product_id.name if t.product_id else '',
                'current': round(t.current_level, 1),
                'capacity': round(t.capacity, 1),
                'pct': round(t.level_percentage, 1),
                'status': t.stock_status,
            } for t in tanks]

            total_current = round(sum(tanks.mapped('current_level')), 1)
            total_capacity = round(sum(tanks.mapped('capacity')), 1)
            total_pct = round((total_current / total_capacity * 100) if total_capacity else 0, 1)

            result.append({
                'branch': wh.name,
                'warehouse_id': wh.id,
                'total_current': total_current,
                'total_capacity': total_capacity,
                'total_pct': total_pct,
                'tank_count': len(tanks),
                'tanks': tank_rows,
            })
        return result

    # -------------------------------------------------------------------------
    # Monthly Targets
    # -------------------------------------------------------------------------
    @api.model
    def get_targets_data(self, branch='all', year=None, month=None):
        from datetime import date as dt
        today = dt.today()
        year = year or today.year
        month = month or str(today.month)

        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Target = self.env['forecourt.target']

        import calendar
        last_day = calendar.monthrange(year, int(month))[1]
        date_from = f"{year}-{int(month):02d}-01"
        date_to = f"{year}-{int(month):02d}-{last_day:02d}"

        rows = []
        for b in branch_keys:
            target = Target.search([
                ('branch', '=', b),
                ('month', '=', str(int(month))),
                ('year', '=', year),
            ], limit=1)

            entries = Entry.search([
                ('branch', '=', b),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])

            actual_petrol_l = round(sum(e.total_sold_litres for e in entries if e.fuel_type == 'petrol'), 2)
            actual_diesel_l = round(sum(e.total_sold_litres for e in entries if e.fuel_type == 'diesel'), 2)
            actual_revenue = round(
                sum(e.cash_sales_value for e in entries)
                + sum((e.credit_litres_sold or 0.0) * (e.rate or 0.0) for e in entries),
                2,
            )

            petrol_target = target.petrol_volume_target if target else 0
            diesel_target = target.diesel_volume_target if target else 0
            revenue_target = target.revenue_target if target else 0

            def pct(actual, tgt):
                return round((actual / tgt * 100) if tgt else 0, 1)

            rows.append({
                'branch': dict(self.BRANCHES).get(b, b),
                'petrol_target': petrol_target,
                'petrol_actual': actual_petrol_l,
                'petrol_pct': pct(actual_petrol_l, petrol_target),
                'diesel_target': diesel_target,
                'diesel_actual': actual_diesel_l,
                'diesel_pct': pct(actual_diesel_l, diesel_target),
                'revenue_target': revenue_target,
                'revenue_actual': actual_revenue,
                'revenue_pct': pct(actual_revenue, revenue_target),
                'has_target': bool(target),
            })

        return {'rows': rows, 'year': year, 'month': month}

       # -------------------------------------------------------------------------
    # Profitability
    # -------------------------------------------------------------------------
    @api.model
    def get_profitability_data(self, branch='all', date_from=None, date_to=None):
        """Profitability per branch.

        Cost resolution (first match wins):
          1. Recorded deliveries for the branch in the period.
          2. Petrol/diesel cost-per-litre from the effective fuel rate
             multiplied by litres sold.
          3. No basis → cost/profit/margin returned as 0.0, `has_cost=False`
             flag drives the UI to show 'n/a'.
        """
        from datetime import date as dt
        today = dt.today()
        if not date_from:
            date_from = today.replace(day=1).isoformat()
        if not date_to:
            date_to = today.isoformat()

        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Delivery = self.env['forecourt.delivery']
        Rate = self.env['forecourt.fuel.rate'].sudo()
        Warehouse = self.env['stock.warehouse'].sudo()

        rows = []
        grand_revenue = 0.0
        grand_cost = 0.0
        any_missing_cost = False
        all_have_cost = True

        for b in branch_keys:
            entries = Entry.search([
                ('branch', '=', b),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])

            # Revenue — always computable from entry data
            cash_rev = sum(e.cash_sales_value for e in entries)
            credit_rev = sum(
                (e.credit_litres_sold or 0.0) * (e.rate or 0.0)
                for e in entries
            )
            revenue = round(cash_rev + credit_rev, 2)

            # ── Cost source 1: recorded deliveries ──────────────────────
            cost = 0.0
            cost_source = 'none'

            branch_label = dict(self.BRANCHES).get(b, b)
            warehouse = Warehouse.search([
                ('is_forecourt_branch', '=', True),
                ('name', 'ilike', branch_label),
            ], limit=1)

            if warehouse:
                branch_deliveries = Delivery.search([
                    ('warehouse_id', '=', warehouse.id),
                    ('delivery_date', '>=', date_from),
                    ('delivery_date', '<=', date_to),
                    ('state', '=', 'received'),
                ])
                delivery_cost = sum(branch_deliveries.mapped('total_cost'))
                if delivery_cost > 0:
                    cost = round(delivery_cost, 2)
                    cost_source = 'delivery'

            # ── Cost source 2: effective fuel-rate cost price ───────────
            if cost_source == 'none' and entries:
                rate_cost = 0.0
                has_any_rate = False
                for e in entries:
                    on_date = e.shift_date
                    if not on_date:
                        continue
                    company = e.company_id or self.env.company
                    rate_rec = Rate.search([
                        ('company_id', 'in', [company.id, False]),
                        ('effective_date', '<=', on_date),
                    ], order='effective_date desc, id desc', limit=1)
                    if not rate_rec:
                        continue
                    cost_per_litre = (
                        rate_rec.petrol_cost if e.fuel_type == 'petrol'
                        else rate_rec.diesel_cost
                    ) or 0.0
                    if cost_per_litre > 0:
                        has_any_rate = True
                    rate_cost += (e.total_sold_litres or 0.0) * cost_per_litre
                if has_any_rate:
                    cost = round(rate_cost, 2)
                    cost_source = 'rate'

            has_cost = cost_source != 'none'

            # Only warn when there is actual revenue to protect against
            if not has_cost and revenue > 0:
                any_missing_cost = True
                all_have_cost = False

            if has_cost:
                gross_profit = round(revenue - cost, 2)
                margin = round((gross_profit / revenue * 100) if revenue else 0.0, 1)
            else:
                gross_profit = 0.0
                margin = 0.0

            rows.append({
                'branch': dict(self.BRANCHES).get(b, b),
                'revenue': revenue,
                'cost': cost,
                'cost_source': cost_source,
                'has_cost': has_cost,
                'gross_profit': gross_profit,
                'margin': margin,
            })

            grand_revenue += revenue
            if has_cost:
                grand_cost += cost

        grand_has_cost = all_have_cost and not any_missing_cost
        if grand_has_cost and grand_revenue > 0:
            grand_profit = round(grand_revenue - grand_cost, 2)
            grand_margin = round((grand_profit / grand_revenue * 100), 1)
        else:
            grand_profit = 0.0
            grand_margin = 0.0

        return {
            'rows': rows,
            'grand_revenue': round(grand_revenue, 2),
            'grand_cost': round(grand_cost, 2),
            'grand_profit': grand_profit,
            'grand_margin': grand_margin,
            'has_cost_warning': any_missing_cost,
            'grand_has_cost': grand_has_cost,
        }
    
    # -------------------------------------------------------------------------
    # Losses & Gains
    # -------------------------------------------------------------------------
    @api.model
    def get_losses_gains_data(self, branch='all', date_from=None, date_to=None):
        from datetime import date as dt
        today = dt.today()
        if not date_from:
            date_from = today.replace(day=1).isoformat()
        if not date_to:
            date_to = today.isoformat()

        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Shift = self.env['forecourt.shift']

        rows = []
        grand_stock_loss = grand_cash_var = 0.0

        for b in branch_keys:
            entries = Entry.search([
                ('branch', '=', b),
                ('shift_date', '>=', date_from),
                ('shift_date', '<=', date_to),
            ])
            shifts = Shift.search([
                ('branch', '=', b),
                ('date_start', '>=', date_from + ' 00:00:00'),
                ('date_start', '<=', date_to + ' 23:59:59'),
            ])

            petrol_var_l = diesel_var_l = 0.0
            petrol_var_k = diesel_var_k = 0.0
            for e in entries:
                if e.atg_closing_stock_litres > 0:
                    var_l = e.atg_dip_variance or 0.0
                else:
                    var_l = e.closing_variance or 0.0
                rate = e.rate or 0.0
                if e.fuel_type == 'petrol':
                    petrol_var_l += var_l
                    petrol_var_k += var_l * rate
                elif e.fuel_type == 'diesel':
                    diesel_var_l += var_l
                    diesel_var_k += var_l * rate

            petrol_var_l = round(petrol_var_l, 2)
            diesel_var_l = round(diesel_var_l, 2)
            petrol_var_k = round(petrol_var_k, 2)
            diesel_var_k = round(diesel_var_k, 2)
            stock_var_k = round(petrol_var_k + diesel_var_k, 2)

            cash_var = round(sum(shifts.mapped('cash_check_variance')), 2)
            net = round(stock_var_k + cash_var, 2)

            rows.append({
                'branch': dict(self.BRANCHES).get(b, b),
                'petrol_var_l': petrol_var_l,
                'petrol_var_k': petrol_var_k,
                'diesel_var_l': diesel_var_l,
                'diesel_var_k': diesel_var_k,
                'stock_var_k': stock_var_k,
                'cash_var': cash_var,
                'closing_var_k': stock_var_k,
                'net': net,
                'status': 'loss' if net < -100 else ('gain' if net > 100 else 'ok'),
            })
            grand_stock_loss += stock_var_k
            grand_cash_var += cash_var

        return {
            'rows': rows,
            'grand_stock_loss': round(grand_stock_loss, 2),
            'grand_cash_var': round(grand_cash_var, 2),
            'grand_net': round(grand_stock_loss + grand_cash_var, 2),
        }

    # -------------------------------------------------------------------------
    # Stock Valuation
    # -------------------------------------------------------------------------
    @api.model
    def get_stock_valuation(self, branch='all'):
        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Rate = self.env['forecourt.fuel.rate']
        rate_rec = Rate.sudo().search(
            [('company_id', 'in', [self.env.company.id, False])],
            order='effective_date desc', limit=1,
        )
        p_cost = rate_rec.petrol_cost if rate_rec and hasattr(rate_rec, 'petrol_cost') else 0.0
        d_cost = rate_rec.diesel_cost if rate_rec and hasattr(rate_rec, 'diesel_cost') else 0.0
        p_pump = rate_rec.petrol_rate if rate_rec else 0.0
        d_pump = rate_rec.diesel_rate if rate_rec else 0.0
        rows = []
        total_cost_value = total_pump_value = 0.0
        for b in branch_keys:
            for fuel, cost, pump in [('petrol', p_cost, p_pump), ('diesel', d_cost, d_pump)]:
                latest = Entry.search([
                    ('branch', '=', b), ('fuel_type', '=', fuel),
                ], order='shift_date desc, id desc', limit=1)
                qty = round(latest.closing_stock_litres, 2) if latest else 0.0
                cost_value = round(qty * cost, 2)
                pump_value = round(qty * pump, 2)
                rows.append({
                    'branch': dict(self.BRANCHES).get(b, b),
                    'product': 'Petrol (ULP)' if fuel == 'petrol' else 'Diesel (LSD)',
                    'qty': qty,
                    'cost_price': cost,
                    'pump_price': pump,
                    'cost_value': cost_value,
                    'pump_value': pump_value,
                    'as_of': latest.shift_date.isoformat() if latest and latest.shift_date else '',
                })
                total_cost_value += cost_value
                total_pump_value += pump_value
        return {
            'rows': rows,
            'total_cost_value': round(total_cost_value, 2),
            'total_pump_value': round(total_pump_value, 2),
        }

    # -------------------------------------------------------------------------
    # Trends
    # -------------------------------------------------------------------------
    @api.model
    def get_trends_data(self, branch='all', months=6):
        from datetime import date as dt
        import calendar
        today = dt.today()
        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Shift = self.env['forecourt.shift']

        labels = []
        cash_data = []
        variance_data = []
        stock_var_data = []

        for i in range(months - 1, -1, -1):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            last_day = calendar.monthrange(y, m)[1]
            date_from = f"{y}-{m:02d}-01"
            date_to = f"{y}-{m:02d}-{last_day:02d}"
            label = f"{calendar.month_abbr[m]} {y}"

            total_cash = 0.0
            total_cash_var = 0.0
            total_stock_var = 0.0

            for b in branch_keys:
                entries = Entry.search([
                    ('branch', '=', b),
                    ('shift_date', '>=', date_from),
                    ('shift_date', '<=', date_to),
                ])
                shifts = Shift.search([
                    ('branch', '=', b),
                    ('date_start', '>=', date_from + ' 00:00:00'),
                    ('date_start', '<=', date_to + ' 23:59:59'),
                ])
                total_cash += sum(e.cash_sales_value for e in entries)
                total_cash += sum(shifts.mapped('pos_account_kwacha'))
                total_cash_var += sum(shifts.mapped('cash_check_variance'))
                for e in entries:
                    if e.atg_closing_stock_litres > 0:
                        var_l = e.atg_dip_variance or 0.0
                    else:
                        var_l = e.closing_variance or 0.0
                    total_stock_var += var_l * (e.rate or 0.0)

            labels.append(label)
            cash_data.append(round(total_cash, 2))
            variance_data.append(round(total_cash_var, 2))
            stock_var_data.append(round(total_stock_var, 2))

        return {
            'labels': labels,
            'cash': cash_data,
            'variance': variance_data,
            'stock_var': stock_var_data,
        }

    # =========================================================================
    # Fuel Detail — per-grade breakdown + shift-level WhatsApp fields
    # =========================================================================
    @api.model
    def _empty_fuel_block(self):
        return {
            'opening_litres': 0.0,
            'received_litres': 0.0,
            'cash_litres': 0.0,
            'credit_litres': 0.0,
            'sold_litres': 0.0,               # reported (from total_sold_litres)
            'derived_sold_litres': 0.0,        # credit + cash (Excel semantics)
            'report_consistency_variance': 0.0,
            'report_consistency_status': 'ok',
            'expected_closing_litres': 0.0,
            'actual_closing_litres': 0.0,
            'variance_litres': 0.0,
            'cash_value_k': 0.0,
            'credit_value_k': 0.0,
            'variance_value_k': 0.0,
            'rate_used': 0.0,
        }

    @api.model
    def _sum_entries_for_grade(self, entries, grade, rate):
        block = self._empty_fuel_block()

        grade_entries = entries.filtered(lambda e: e.fuel_type == grade)

        for e in grade_entries:
            block['opening_litres'] += e.opening_stock_litres or 0.0
            block['received_litres'] += e.received_stock_litres or 0.0
            block['cash_litres'] += e.cash_sales_litres or 0.0
            block['credit_litres'] += e.credit_litres_sold or 0.0
            block['sold_litres'] += e.total_sold_litres or 0.0
            block['derived_sold_litres'] += e.derived_sold_litres or 0.0
            block['actual_closing_litres'] += e.closing_stock_litres or 0.0

            if e.atg_closing_stock_litres and e.atg_closing_stock_litres > 0:
                block['variance_litres'] += e.atg_dip_variance or 0.0
            else:
                block['variance_litres'] += e.closing_variance or 0.0

        # Report consistency: reported total vs credit+cash
        block['report_consistency_variance'] = round(
            block['sold_litres'] - block['derived_sold_litres'], 3
        )
        block['report_consistency_status'] = (
            'ok'
            if abs(block['report_consistency_variance']) <= 0.5
            else 'mismatch'
        )

        # Expected Close — Excel semantics: Opening + Received − (Credit + Cash)
        block['expected_closing_litres'] = (
            block['opening_litres']
            + block['received_litres']
            - block['derived_sold_litres']
        )

        rate_val = rate or 0.0
        if not rate_val and grade_entries:
            for e in grade_entries:
                if e.rate and e.rate > 0:
                    rate_val = e.rate
                    break
        block['rate_used'] = rate_val

        block['cash_value_k'] = round(block['cash_litres'] * rate_val, 2)
        block['credit_value_k'] = round(block['credit_litres'] * rate_val, 2)
        block['variance_value_k'] = round(block['variance_litres'] * rate_val, 2)

        for key in ('opening_litres', 'received_litres', 'cash_litres',
                    'credit_litres', 'sold_litres', 'derived_sold_litres',
                    'expected_closing_litres',
                    'actual_closing_litres', 'variance_litres'):
            block[key] = round(block[key], 3)

        return block

    @api.model
    def get_fuel_detail_data(self, branch='all', date_from=None, date_to=None):
        """Return one row per shift with petrol and diesel blocks side by side.

        Each row also carries the shift-level cash reconciliation block
        as per the Aziz Excel workbook:

            Cash Value (K)           = sum(cash_litres × rate) across both fuels
            Expected Cash @ Hand (K) = Cash Value − POS
            Cash Variance (K)        = Cash @ Hand − Expected Cash @ Hand
        """
        from datetime import date as dt
        today = dt.today()
        if not date_from:
            date_from = today.replace(day=1).isoformat()
        if not date_to:
            date_to = today.isoformat()

        branch_keys = [b[0] for b in self.BRANCHES] if branch == 'all' else [branch]
        Entry = self.env['forecourt.shift.fuel.entry']
        Shift = self.env['forecourt.shift']
        Rate = self.env['forecourt.fuel.rate']
        Warehouse = self.env['stock.warehouse'].sudo()

        shifts = Shift.search([
            ('branch', 'in', branch_keys),
            ('date_start', '>=', date_from + ' 00:00:00'),
            ('date_start', '<=', date_to + ' 23:59:59'),
        ], order='date_start asc, shift_type asc')

        rows = []
        grand = {
            'petrol': self._empty_fuel_block(),
            'diesel': self._empty_fuel_block(),
        }
        shift_grand = {
            'pos_k': 0.0,
            'lubes_k': 0.0,
            'lpg_k': 0.0,
            'cash_on_hand_k': 0.0,
            'expected_cash_k': 0.0,
            'expected_cash_at_hand_k': 0.0,
            'cash_check_variance_k': 0.0,
        }
        report_issue_count = 0

        for shift in shifts:
            entries = Entry.search([('shift_id', '=', shift.id)])

            warehouse = Warehouse.search([
                ('is_forecourt_branch', '=', True),
                ('name', 'ilike', dict(self.BRANCHES).get(shift.branch, shift.branch or '')),
            ], limit=1)

            on_date = shift.date_start.date() if shift.date_start else today
            company = shift.company_id or self.env.company
            p_rate, d_rate = Rate.sudo().get_rate_for(
                on_date, company, branch=warehouse or None,
            )

            petrol_block = self._sum_entries_for_grade(entries, 'petrol', p_rate)
            diesel_block = self._sum_entries_for_grade(entries, 'diesel', d_rate)

            # ── Cash reconciliation block (matches the Excel) ─────────────
            expected_cash_k = round(
                petrol_block['cash_value_k'] + diesel_block['cash_value_k'], 2
            )
            pos_k = round(shift.pos_account_kwacha or 0.0, 2)
            cash_on_hand_k = round(shift.cash_on_hand_kwacha or 0.0, 2)
            expected_cash_at_hand_k = round(expected_cash_k - pos_k, 2)
            cash_variance_k = round(cash_on_hand_k - expected_cash_at_hand_k, 2)

            row_has_report_issue = (
                petrol_block['report_consistency_status'] == 'mismatch'
                or diesel_block['report_consistency_status'] == 'mismatch'
            )
            if row_has_report_issue:
                report_issue_count += 1

            row = {
                'shift_id': shift.id,
                'shift_name': shift.name,
                'date': on_date.isoformat(),
                'shift_type': shift.shift_type,
                'shift_type_label': 'Day' if shift.shift_type == 'day' else 'Night',
                'supervisor': (
                    shift.supervisor_name_text
                    or (shift.supervisor_id.name if shift.supervisor_id else '—')
                ),
                'branch': dict(self.BRANCHES).get(shift.branch, shift.branch or '—'),
                'petrol': petrol_block,
                'diesel': diesel_block,
                # ── Shift-level WhatsApp fields ──────────────────────────
                'pos_k': pos_k,
                'lubes_k': round(shift.lube_sales_kwacha or 0.0, 2),
                'lpg_k': round(shift.lpg_sales_kwacha or 0.0, 2),
                'cash_on_hand_k': cash_on_hand_k,
                # ── Cash reconciliation ─────────────────────────────────
                'expected_cash_k': expected_cash_k,
                'expected_cash_at_hand_k': expected_cash_at_hand_k,
                'cash_check_variance_k': cash_variance_k,
                # ── Report consistency flag ─────────────────────────────
                'has_report_issue': row_has_report_issue,
            }
            rows.append(row)

            for key in petrol_block:
                if key in ('report_consistency_status',):
                    continue
                grand['petrol'][key] += petrol_block[key]
                grand['diesel'][key] += diesel_block[key]

            shift_grand['pos_k'] += pos_k
            shift_grand['lubes_k'] += row['lubes_k']
            shift_grand['lpg_k'] += row['lpg_k']
            shift_grand['cash_on_hand_k'] += cash_on_hand_k
            shift_grand['expected_cash_k'] += expected_cash_k
            shift_grand['expected_cash_at_hand_k'] += expected_cash_at_hand_k
            shift_grand['cash_check_variance_k'] += cash_variance_k

        # Round grand totals (skip non-numeric keys like report_consistency_status)
        for fuel in ('petrol', 'diesel'):
            for key in grand[fuel]:
                if isinstance(grand[fuel][key], (int, float)):
                    grand[fuel][key] = round(grand[fuel][key], 3)
        for key in shift_grand:
            if isinstance(shift_grand[key], (int, float)):
                shift_grand[key] = round(shift_grand[key], 2)

        total_report_diff = round(
            grand['petrol']['report_consistency_variance']
            + grand['diesel']['report_consistency_variance'], 3
        )
        combined = {
            'stock_variance_litres': round(
                grand['petrol']['variance_litres']
                + grand['diesel']['variance_litres'], 3
            ),
            'stock_variance_k': round(
                grand['petrol']['variance_value_k']
                + grand['diesel']['variance_value_k'], 2
            ),
            'cash_variance_k': round(shift_grand['cash_check_variance_k'], 2),
            'report_consistency_variance_litres': total_report_diff,
            'report_issue_count': report_issue_count,
        }

        return {
            'rows': rows,
            'grand': grand,
            'shift_grand': shift_grand,
            'combined': combined,
            'date_from': date_from,
            'date_to': date_to,
        }