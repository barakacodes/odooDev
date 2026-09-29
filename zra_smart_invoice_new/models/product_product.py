# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # Expose template ZRA fields on variants (read/write via related)
    zra_item_code = fields.Char(related='product_tmpl_id.zra_item_code', readonly=False, store=True)
    zra_classification_code = fields.Char(related='product_tmpl_id.zra_classification_code', readonly=False, store=True)
    zra_tax_type = fields.Selection(related='product_tmpl_id.zra_tax_type', readonly=False, store=True)
    zra_registered = fields.Boolean(related='product_tmpl_id.zra_registered', readonly=False, store=True)
    zra_registration_date = fields.Datetime(related='product_tmpl_id.zra_registration_date', readonly=False, store=True)

    def action_register_with_zra(self):
        """Register product (variant) with ZRA.
        We register the *template* once, because ZRA item master is template-level in this module.
        """
        templates = self.mapped('product_tmpl_id')
        if not templates:
            raise UserError(_('No product template found to register.'))
        return templates.action_register_with_zra()
