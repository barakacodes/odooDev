# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)

# ZRA Stock In/Out type codes — per VSDC spec §6.14 "Stock In/Out Type".
# NOTE: this used to be a fabricated 01-16 sequential list with meanings
# that did not match the spec at all (e.g. old '05' = "Sales to customer",
# real '05' = "Processing"/incoming). Corrected to the real 12-code table:
# only 01-06 (incoming) and 11-16 (outgoing) exist — there is no 07-10.
ZRA_MOVEMENT_CODES = [
    ('01', '01 - Import (incoming)'),
    ('02', '02 - Purchase (incoming)'),
    ('03', '03 - Return (incoming)'),
    ('04', '04 - Stock Movement (incoming)'),
    ('05', '05 - Processing (incoming)'),
    ('06', '06 - Adjustment (incoming)'),
    ('11', '11 - Sale (outgoing)'),
    ('12', '12 - Return (outgoing)'),
    ('13', '13 - Stock Movement (outgoing)'),
    ('14', '14 - Processing (outgoing)'),
    ('15', '15 - Discarding (outgoing)'),
    ('16', '16 - Adjustment (outgoing)'),
]


class StockMove(models.Model):
    _inherit = 'stock.move'

    zra_synced = fields.Boolean(string='Synced with ZRA', default=False, copy=False)
    zra_sync_date = fields.Datetime(string='ZRA Sync Date', readonly=True, copy=False)
    zra_movement_code = fields.Selection(ZRA_MOVEMENT_CODES, string='ZRA Movement Type',
                                         help='ZRA stock in/out type code per spec §6.14')

    def _action_done(self, cancel_backorder=False):
        """Override to sync stock with ZRA after move is done.

        Confirmed live against the sandbox: iterating `self` here (the
        recordset as it was *before* calling super()) is wrong on its own —
        `self` can include a move that super()._action_done() did NOT
        actually complete (e.g. a move whose lines were never marked
        `picked`, or an assignment that couldn't be fully satisfied) and
        which is left behind in a non-'done' state such as 'assigned'.
        Without checking the resulting state, this synced such a move to
        ZRA anyway — both stock/saveStockItems and stockMaster/saveStockMaster
        came back resultCd 000, telling ZRA a stock quantity change happened
        that never actually took effect in Odoo. The same gap also matters
        for a backordered/split move, where the record that actually ends
        up 'done' is not guaranteed to be the same recordset passed in.
        """
        res = super(StockMove, self)._action_done(cancel_backorder=cancel_backorder)

        for move in self:
            if move.zra_synced or move.state != 'done':
                continue
            try:
                config = self.env['zra.config'].get_active_config(move.company_id.id)
                if config and config.auto_sync_stock and config.is_initialized:
                    move._sync_with_zra(config)
            except Exception as e:
                # Never block stock movement due to ZRA error
                _logger.error(f"ZRA stock sync error for move {move.id}: {str(e)}")

        return res

    def _sync_with_zra(self, config):
        """Sync stock movement with ZRA.

        Per spec dependency note (§5.11): stock/saveStockItems (the movement
        record) must be called first, and only once it succeeds should
        stockMaster/saveStockMaster be called to set the item's new
        remaining quantity (rsdQty = absolute balance, not the moved amount).
        """
        self.ensure_one()

        product = self.product_id
        if not product:
            return

        if not product.zra_classification_code:
            _logger.warning(
                f"ZRA stock sync skipped for move {self.id}: "
                f"product {product.name} has no classification code"
            )
            return

        move_code = self.zra_movement_code or self._guess_movement_code()
        qty = self.product_qty or self.quantity_done or 0.0
        if qty <= 0:
            return

        unit_cost = float(product.standard_price or 0.0)
        occurrence_date = (self.date.strftime('%Y%m%d') if self.date
                           else fields.Date.today().strftime('%Y%m%d'))

        api_client = self.env['zra.api.client']
        try:
            items_result = api_client.save_stock_items(
                config, self.id, move_code, occurrence_date,
                [{'product': product, 'qty': qty, 'unit_cost': unit_cost}],
                remark=self.origin or '',
            )
            if items_result.get('resultCd') != '000':
                _logger.warning(
                    f"ZRA stock sync (saveStockItems) failed for move {self.id}: "
                    f"{items_result.get('resultMsg')}"
                )
                return

            item_cd = (product.zra_item_code or product.default_code
                       or f'ITEM{product.id:06d}')
            remaining_qty = product.with_company(self.company_id).qty_available
            master_result = api_client.save_stock_master(
                config, [(item_cd, remaining_qty)]
            )

            if master_result.get('resultCd') == '000':
                self.write({
                    'zra_synced': True,
                    'zra_sync_date': fields.Datetime.now(),
                })
                _logger.info(f"ZRA stock sync success for move {self.id}")
            else:
                _logger.warning(
                    f"ZRA stock sync (saveStockMaster) failed for move {self.id}: "
                    f"{master_result.get('resultMsg')}"
                )
        except Exception as e:
            _logger.error(f"ZRA stock sync exception for move {self.id}: {str(e)}")

    def _guess_movement_code(self):
        """Derive ZRA movement code from picking type, per spec §6.14."""
        picking = self.picking_id
        if not picking:
            return '04'  # generic incoming/outgoing Stock Movement fallback
        code = picking.picking_type_code
        if code == 'incoming':
            # Purchase-linked receipt vs a generic incoming movement.
            # purchase_line_id only exists if 'purchase' is installed
            # (not a hard dependency of this module) — check defensively.
            return '02' if getattr(self, 'purchase_line_id', False) else '04'
        elif code == 'outgoing':
            # Sale-linked delivery vs a generic outgoing movement
            return '11' if self.sale_line_id else '13'
        elif code == 'internal':
            # No dedicated "internal transfer" code exists in the spec —
            # treat as a generic incoming Stock Movement.
            return '04'
        return '04'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_sync_stock_with_zra(self):
        """Manually sync all done unsynced moves in this picking with ZRA."""
        self.ensure_one()
        config = self.env['zra.config'].get_active_config(self.company_id.id)
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        done_moves = self.move_ids.filtered(
            lambda m: m.state == 'done' and not m.zra_synced
        )
        if not done_moves:
            raise UserError(_('No unsynced done moves found in this picking.'))

        synced, failed = 0, 0
        for move in done_moves:
            try:
                move._sync_with_zra(config)
                synced += 1
            except Exception as e:
                failed += 1
                _logger.error(f"Manual ZRA sync failed for move {move.id}: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('ZRA Stock Sync Complete'),
                'message': _('Synced: %d | Failed: %d') % (synced, failed),
                'type': 'success' if failed == 0 else 'warning',
                'sticky': False,
            }
        }


class StockAdjustmentZRA(models.TransientModel):
    """Standalone wizard to report stock adjustments directly to ZRA (Issue #1)."""
    _name = 'zra.stock.adjustment'
    _description = 'ZRA Stock Adjustment Wizard'

    product_id = fields.Many2one('product.product', string='Product', required=True)
    movement_type = fields.Selection(ZRA_MOVEMENT_CODES, string='Movement Type',
                                     required=True, default='06')
    qty = fields.Float(string='Quantity', required=True, default=1.0)
    remark = fields.Char(string='Remark')
    occurrence_date = fields.Date(string='Occurrence Date', required=True,
                                  default=fields.Date.today)

    def action_submit_to_zra(self):
        """Submit stock adjustment directly to ZRA."""
        self.ensure_one()

        product = self.product_id
        if not product.zra_classification_code:
            raise UserError(
                _('Product "%s" is missing a UNSPSC classification code. '
                  'Please set it before syncing stock.')
                % product.name
            )

        config = self.env['zra.config'].get_active_config()
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        unit_cost = float(product.standard_price or 0.0)
        api_client = self.env['zra.api.client']

        items_result = api_client.save_stock_items(
            config, self.id, self.movement_type,
            self.occurrence_date.strftime('%Y%m%d'),
            [{'product': product, 'qty': self.qty, 'unit_cost': unit_cost}],
            remark=self.remark or '',
        )
        if items_result.get('resultCd') != '000':
            raise UserError(
                _('ZRA Error: %s') % items_result.get('resultMsg', 'Unknown error')
            )

        item_cd = product.zra_item_code or product.default_code or f'ITEM{product.id:06d}'
        remaining_qty = product.qty_available
        master_result = api_client.save_stock_master(config, [(item_cd, remaining_qty)])

        if master_result.get('resultCd') == '000':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('ZRA Stock Adjustment Submitted'),
                    'message': _('Adjustment for "%s" submitted successfully.') % product.name,
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            raise UserError(
                _('ZRA Error: %s') % master_result.get('resultMsg', 'Unknown error')
            )
