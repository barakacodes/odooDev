# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def _run_branch_backfill(env):
    """Self-configuring migration, safe to run on install AND every upgrade:
    1. Flag existing warehouses as forecourt branches, based on tanks already
       resolvable to them via location_id.
    2. Backfill forecourt.tank.warehouse_id from existing stock.location links
       (forecourt.pump.warehouse_id is a related field, no backfill needed).
    3. Backfill forecourt.shift.branch_id by matching the legacy branch
       selection string against warehouse names in the same company.
    Idempotent â€” safe to re-run every time, never overwrites a value already set.
    """
    Warehouse = env['stock.warehouse']
    Tank = env['forecourt.tank']
    Shift = env['forecourt.shift']

    flagged_warehouses = env['stock.warehouse']

    tanks = Tank.search([('warehouse_id', '=', False), ('location_id', '!=', False)])
    for tank in tanks:
        wh = tank.location_id.warehouse_id
        if wh:
            tank.warehouse_id = wh.id
            if not wh.is_forecourt_branch:
                wh.is_forecourt_branch = True
                flagged_warehouses |= wh
            _logger.info(
                "forecourt branch backfill: linked tank '%s' to warehouse '%s'",
                tank.name, wh.name,
            )

    branch_label_map = {
        'arcades': 'arcades',
        'luanshya': 'luanshya',
        'chililabombwe': 'chililabombwe',
    }
    shifts = Shift.search([('branch_id', '=', False), ('branch', '!=', False)])
    for shift in shifts:
        candidate_name = branch_label_map.get(shift.branch)
        if not candidate_name:
            continue
        wh = Warehouse.search([
            ('company_id', '=', shift.company_id.id),
            ('name', 'ilike', candidate_name),
        ], limit=1)
        if wh:
            shift.branch_id = wh.id
            if not wh.is_forecourt_branch:
                wh.is_forecourt_branch = True
                flagged_warehouses |= wh
            _logger.info(
                "forecourt branch backfill: linked shift '%s' to branch '%s'",
                shift.name, wh.name,
            )

    if flagged_warehouses:
        _logger.info(
            "forecourt branch backfill: flagged %d warehouse(s) as forecourt branches: %s",
            len(flagged_warehouses), flagged_warehouses.mapped('name'),
        )

    env.cr.commit()


def post_init_hook(env):
    """Fires only on a genuine fresh install of this module (new instance)."""
    _run_branch_backfill(env)
