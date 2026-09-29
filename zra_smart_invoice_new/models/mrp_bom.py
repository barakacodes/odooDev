# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    zra_composition_synced = fields.Boolean(
        string='Composition Synced to ZRA', copy=False, readonly=True,
        help='Whether this BOM\'s component list has been sent to ZRA via '
             'items/saveItemComposition (checklist item #9 — raw material '
             'processing / repackaging).'
    )

    def action_send_composition_to_zra(self):
        """Send this BOM's components to ZRA as item composition.

        The items/saveItemComposition endpoint accepts one parent/component
        pair per call, so one BOM with N component lines makes N API calls.
        """
        self.ensure_one()

        parent = self.product_tmpl_id
        if not parent.zra_item_code or not parent.zra_registered:
            raise UserError(_(
                'The finished product "%s" is not registered with ZRA yet. '
                'Register it first (see the "Register with ZRA" button on '
                'the product form).'
            ) % parent.name)

        errors = []
        lines = []
        for line in self.bom_line_ids:
            component = line.product_id.product_tmpl_id
            if not component.zra_item_code or not component.zra_registered:
                errors.append(_('- %s is not registered with ZRA') % component.name)
                continue
            if line.product_qty <= 0:
                errors.append(_('- %s has a non-positive quantity') % component.name)
                continue
            lines.append((component.zra_item_code, line.product_qty))

        if errors:
            raise UserError(
                _('Cannot sync item composition:\n%s') % '\n'.join(errors)
            )
        if not lines:
            raise UserError(_('This BOM has no valid component lines to sync.'))

        config = self.env['zra.config'].get_active_config(
            self.company_id.id if self.company_id else None
        )
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        api_client = self.env['zra.api.client']
        failed = 0
        for component_cd, qty in lines:
            try:
                result = api_client.save_item_composition(
                    config, parent.zra_item_code, component_cd, qty
                )
                if result.get('resultCd') != '000':
                    failed += 1
                    _logger.warning(
                        f"ZRA item composition failed for {parent.zra_item_code} "
                        f"<- {component_cd}: {result.get('resultMsg')}"
                    )
            except Exception as e:
                failed += 1
                _logger.error(f"ZRA item composition error: {str(e)}")

        self.zra_composition_synced = (failed == 0)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Item Composition Synced') if failed == 0 else _('Sync Completed with Errors'),
                'message': _('%d component(s) sent, %d failed.') % (len(lines) - failed, failed),
                'type': 'success' if failed == 0 else 'warning',
                'sticky': False,
            }
        }
