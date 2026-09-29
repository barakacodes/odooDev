# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

import logging
_logger = logging.getLogger(__name__)

# Fallback lists used only before "Sync Standard Codes" has ever been run
# (so the fields aren't empty on a fresh install). Once zra.standard.code
# has real ZRA data, the dynamic selection methods below take over — these
# codes were reverse-engineered from the spec and do NOT reliably match
# ZRA's actual code tables (e.g. real Packing Unit 'KG' = "KEG", a barrel
# container, not Kilogram; real Quantity Unit has no 'G' code at all —
# confirmed live against the ZRA sandbox on 2026-07-14).
ZRA_PKG_UNIT_CODES = [
    ('BG',  'BG - Bag'),
    ('BX',  'BX - Box'),
    ('CT',  'CT - Carton'),
    ('DO',  'DO - Dozen'),
    ('DR',  'DR - Drum'),
    ('GL',  'GL - Gallon'),
    ('GR',  'GR - Gross'),
    ('KG',  'KG - Kilogram'),
    ('LT',  'LT - Litre'),
    ('MT',  'MT - Metre'),
    ('NT',  'NT - Unit (each)'),
    ('PK',  'PK - Pack'),
    ('PR',  'PR - Pair'),
    ('RL',  'RL - Roll'),
    ('ST',  'ST - Set'),
    ('TN',  'TN - Tonne'),
    ('TU',  'TU - Tube'),
]

ZRA_QTY_UNIT_CODES = [
    ('U',   'U  - Unit'),
    ('KG',  'KG - Kilogram'),
    ('L',   'L  - Litre'),
    ('ML',  'ML - Millilitre'),
    ('M',   'M  - Metre'),
    ('CM',  'CM - Centimetre'),
    ('M2',  'M2 - Square metre'),
    ('M3',  'M3 - Cubic metre'),
    ('DZ',  'DZ - Dozen'),
    ('PR',  'PR - Pair'),
    ('PK',  'PK - Pack'),
    ('BX',  'BX - Box'),
    ('CT',  'CT - Carton'),
    ('TN',  'TN - Tonne'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ── Core ZRA fields ───────────────────────────────────────────────────────
    zra_item_code = fields.Char(
        string='ZRA Item Code', copy=False, index=True,
        help='Unique item code registered with ZRA'
    )
    zra_classification_code = fields.Char(
        string='UNSPSC Classification Code', size=8, index=True,
        help='UN Standard Products and Services Code — MANDATORY for all products sold in Zambia'
    )
    zra_classification_id = fields.Many2one(
        'zra.classification.code', string='Classification Code (lookup)',
        domain=[('item_cls_lvl', '=', 4)],
        help='Pick from the locally-synced UNSPSC code list (level 4 / '
             'commodity level, per spec) — this fills in the code above. '
             'Run "Sync Classification Codes" on the ZRA Settings screen '
             'first if the list is empty.'
    )
    zra_tax_type = fields.Selection([
        ('A',    'A - Standard Rated (16% VAT)'),
        ('B',    'B - MTV (Minimum Taxable Value)'),
        ('C1',   'C1 - Exports (0%)'),
        ('C2',   'C2 - Zero-rating LPO (0%)'),
        ('C3',   'C3 - Zero-rated by nature (0%)'),
        ('D',    'D - Exempt (0%)'),
        ('E',    'E - Disbursement'),
        ('F',    'F - Service Charge (10%)'),
        ('RVAT', 'RVAT - Reverse VAT'),
    ], string='ZRA Tax Type', default='A', required=True,
       help='VAT category for ZRA reporting (vatCatCd). Drives which '
            'taxblAmt/taxAmt/taxRt header bucket and item-level vatCatCd '
            'is populated. Per VSDC spec §6.1, these are the only valid '
            'vatCatCd values — IPL, Tourism Levy and Excise are separate '
            'levies, set them in "Secondary ZRA Tax Type" below.')

    zra_registered = fields.Boolean(
        string='Registered with ZRA', default=False, copy=False
    )
    zra_registration_date = fields.Datetime(
        string='ZRA Registration Date', readonly=True, copy=False
    )

    # ── Item Information detail fields (spec §5.6 items/saveItem) ────────────
    zra_item_type = fields.Selection([
        ('1', '1 - Raw Material'),
        ('2', '2 - Finished Product'),
        ('3', '3 - Service'),
    ], string='ZRA Item Type', default='2', required=True,
       help='itemTyCd per VSDC spec §6.2. Odoo has no native "raw material" '
            'concept — set this manually for inputs to manufacturing/repackaging.')
    zra_safety_qty = fields.Float(
        string='ZRA Safety Quantity', default=0.0,
        help='Buffer stock quantity reported to ZRA (sftyQty) to guard '
             'against demand fluctuations. Optional.'
    )
    zra_manufacturer_tpin = fields.Char(
        string='Manufacturer TPIN', size=10,
        help='Required by ZRA when ZRA Tax Type is B (MTV) — the TPIN of '
             'the manufacturer who set the Recommended Retail Price.'
    )
    zra_manufacturer_item_code = fields.Char(
        string='Manufacturer Item Code',
        help='Required by ZRA when ZRA Tax Type is B (MTV) — the '
             'manufacturer\'s own item code for this product.'
    )
    zra_rrp = fields.Float(
        string='Recommended Retail Price (ZRA)',
        help='Manufacturer-set Recommended Retail Price (rrp), used for MTV '
             '(B) items where 16% VAT is charged on the RRP rather than the '
             'selling price.'
    )
    zra_has_service_charge = fields.Boolean(
        string='Has Service Charge', default=False,
        help='svcChargeYn — whether this item carries a service charge.'
    )
    zra_is_rental = fields.Boolean(
        string='Is Rental', default=False,
        help='rentalYn — whether this item is offered on a rental basis.'
    )

    # ── Sale type (Fix #9 from previous round) ────────────────────────────────
    zra_sale_type = fields.Selection([
        ('N', 'N - Normal sale'),
        ('E', 'E - Export'),
        ('L', 'L - LPO (Local Purchase Order)'),
    ], string='ZRA Sale Type', default='N',
       help='ZRA sale type. Export requires destination country; LPO requires LPO number.')

    # ── PARITY #3: Product origin & packaging ────────────────────────────────
    zra_origin_country_id = fields.Many2one(
        'res.country', string='Country of Origin',
        help='Country where this product was manufactured or grown. '
             'Required by ZRA for product registration. '
             'Defaults to Zambia (ZM) — change for imported goods.'
    )
    zra_pkg_unit_code = fields.Selection(
        selection='_selection_zra_pkg_unit_code', string='ZRA Packaging Unit',
        default='NT', required=True,
        help='How this product is packaged for ZRA reporting. Sourced from '
             'the real ZRA "Packing Unit" codes once "Sync Standard Codes" '
             'has been run; falls back to a static list before that.'
    )
    zra_qty_unit_code = fields.Selection(
        selection='_selection_zra_qty_unit_code', string='ZRA Quantity Unit',
        default='U', required=True,
        help='Unit of measure used when reporting quantities to ZRA. Sourced '
             'from the real ZRA "Quantity Unit" codes once "Sync Standard '
             'Codes" has been run; falls back to a static list before that.'
    )

    @api.model
    def _selection_zra_pkg_unit_code(self):
        return self._zra_standard_code_selection('Packing Unit', ZRA_PKG_UNIT_CODES)

    @api.model
    def _selection_zra_qty_unit_code(self):
        return self._zra_standard_code_selection('Quantity Unit', ZRA_QTY_UNIT_CODES)

    def _zra_standard_code_selection(self, cd_cls_nm, fallback):
        codes = self.env['zra.standard.code'].sudo().search([('cd_cls_nm', '=', cd_cls_nm)])
        if not codes:
            return fallback
        return sorted(
            [(c.cd, f'{c.cd} - {c.cd_nm}') for c in codes],
            key=lambda pair: pair[1],
        )

    # ── PARITY #1: Secondary / additional levy ────────────────────────────────
    # Per VSDC spec §5.8, IPL/TL/Excise are independent of the VAT category
    # above — a product can carry VAT (zra_tax_type) AND one of these levies
    # at the same time. Example: hospitality item = A (16% VAT) + TL (Tourism Levy).
    zra_secondary_tax_type = fields.Selection([
        ('IPL1',  'IPL1 - Insurance Premium Levy'),
        ('IPL2',  'IPL2 - Re-Insurance'),
        ('TL',    'TL - Tourism Levy'),
        ('ECM',   'ECM - Excise on Coal'),
        ('EXEEG', 'EXEEG - Excise Electricity'),
    ], string='Secondary ZRA Tax Type',
       help='Optional additional levy on this product, reported via its own '
            'iplCatCd/tlCatCd/exciseTxCatCd field (not part of the VAT '
            'category above). Leave blank if the product only attracts VAT.')

    # ── Constraints ───────────────────────────────────────────────────────────
    @api.constrains('zra_classification_code')
    def _check_classification_code(self):
        for product in self:
            code = product.zra_classification_code
            if code and (len(code) != 8 or not code.isdigit()):
                raise ValidationError(
                    _('UNSPSC Classification Code must be exactly 8 digits. Got: "%s"') % code
                )

    @api.onchange('zra_classification_id')
    def _onchange_zra_classification_id(self):
        if self.zra_classification_id:
            self.zra_classification_code = self.zra_classification_id.item_cls_cd

    def _zra_validate_for_registration(self):
        """Raise UserError if any mandatory ZRA field is missing before registration."""
        self.ensure_one()
        errors = []
        if not self.zra_classification_code:
            errors.append(_('UNSPSC Classification Code'))
        if not self.zra_pkg_unit_code:
            errors.append(_('ZRA Packaging Unit'))
        if not self.zra_qty_unit_code:
            errors.append(_('ZRA Quantity Unit'))
        if self.zra_tax_type == 'B':
            # MTV items are taxed on the manufacturer's RRP — spec requires
            # manufactuterTpin/manufacturerItemCd/rrp for these registrations.
            if not self.zra_manufacturer_tpin:
                errors.append(_('Manufacturer TPIN (required for MTV items)'))
            if not self.zra_manufacturer_item_code:
                errors.append(_('Manufacturer Item Code (required for MTV items)'))
            if not self.zra_rrp:
                errors.append(_('Recommended Retail Price (required for MTV items)'))
        if errors:
            raise UserError(
                _('The following ZRA fields are required before registration:\n• %s')
                % '\n• '.join(errors)
            )

    # ── Actions ───────────────────────────────────────────────────────────────
    def action_register_with_zra(self):
        """Register product with ZRA."""
        for product in self:
            # PARITY #3: enforce origin + packaging before allowing registration
            product._zra_validate_for_registration()

            try:
                config = self.env['zra.config'].get_active_config()
                if not config.is_initialized:
                    raise UserError(_('ZRA device is not initialized'))

                if not product.zra_item_code:
                    product.zra_item_code = (
                        product.default_code or f'ITEM{product.id:06d}'
                    )

                api_client = self.env['zra.api.client']
                result = api_client.register_item(config, product)

                if result.get('resultCd') == '000':
                    product.write({
                        'zra_registered': True,
                        'zra_registration_date': fields.Datetime.now(),
                    })
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('Product "%s" registered with ZRA!') % product.name,
                            'type': 'success',
                        }
                    }
                else:
                    raise UserError(
                        _('Registration failed: %s') % result.get('resultMsg', 'Unknown error')
                    )
            except Exception as e:
                raise UserError(_('Error registering product: %s') % str(e))

    def action_update_zra_item(self):
        """Push updated item details to ZRA via items/updateItem.

        This is a MANDATORY endpoint per the VSDC spec — call it whenever a
        previously registered product's ZRA-relevant details change
        (name, classification, packaging, tax category, etc.).
        """
        for product in self:
            if not product.zra_registered:
                raise UserError(
                    _('Product "%s" is not registered with ZRA yet. '
                      'Use "Register with ZRA" first.') % product.name
                )
            product._zra_validate_for_registration()

            try:
                config = self.env['zra.config'].get_active_config()
                if not config.is_initialized:
                    raise UserError(_('ZRA device is not initialized'))

                api_client = self.env['zra.api.client']
                result = api_client.update_item(config, product)

                if result.get('resultCd') == '000':
                    product.write({'zra_registration_date': fields.Datetime.now()})
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('Product "%s" updated on ZRA!') % product.name,
                            'type': 'success',
                        }
                    }
                else:
                    raise UserError(
                        _('Update failed: %s') % result.get('resultMsg', 'Unknown error')
                    )
            except Exception as e:
                raise UserError(_('Error updating product on ZRA: %s') % str(e))

    def action_bulk_register_with_zra(self):
        """Bulk register selected products with ZRA."""
        products_to_register = self.filtered(
            lambda p: not p.zra_registered and p.zra_classification_code
        )
        if not products_to_register:
            raise UserError(
                _('No eligible products found. '
                  'Please set UNSPSC classification codes first.')
            )

        success_count, failed_count = 0, 0
        for product in products_to_register:
            try:
                product.action_register_with_zra()
                success_count += 1
            except Exception as e:
                failed_count += 1
                product.message_post(body=_('ZRA registration failed: %s') % str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Registration Complete'),
                'message': _('Registered: %d | Failed: %d') % (success_count, failed_count),
                'type': 'success' if failed_count == 0 else 'warning',
            }
        }

    @api.model
    def action_open_classification_wizard(self):
        return {
            'name': _('Products Missing UNSPSC Code'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('zra_classification_code', '=', False)],
        }


class ProductProduct(models.Model):
    _inherit = 'product.product'

    zra_item_code = fields.Char(
        related='product_tmpl_id.zra_item_code', store=True, readonly=False
    )
    zra_classification_code = fields.Char(
        related='product_tmpl_id.zra_classification_code', store=True, readonly=False
    )
    zra_tax_type = fields.Selection(
        related='product_tmpl_id.zra_tax_type', store=True, readonly=False
    )
    zra_secondary_tax_type = fields.Selection(
        related='product_tmpl_id.zra_secondary_tax_type', store=True, readonly=False
    )
    zra_registered = fields.Boolean(
        related='product_tmpl_id.zra_registered', store=True
    )
    zra_origin_country_id = fields.Many2one(
        related='product_tmpl_id.zra_origin_country_id', store=True, readonly=False
    )
    zra_pkg_unit_code = fields.Selection(
        related='product_tmpl_id.zra_pkg_unit_code', store=True, readonly=False
    )
    zra_qty_unit_code = fields.Selection(
        related='product_tmpl_id.zra_qty_unit_code', store=True, readonly=False
    )
    zra_item_type = fields.Selection(
        related='product_tmpl_id.zra_item_type', store=True, readonly=False
    )
    zra_safety_qty = fields.Float(
        related='product_tmpl_id.zra_safety_qty', readonly=False
    )
    zra_manufacturer_tpin = fields.Char(
        related='product_tmpl_id.zra_manufacturer_tpin', readonly=False
    )
    zra_manufacturer_item_code = fields.Char(
        related='product_tmpl_id.zra_manufacturer_item_code', readonly=False
    )
    zra_rrp = fields.Float(
        related='product_tmpl_id.zra_rrp', readonly=False
    )
    zra_has_service_charge = fields.Boolean(
        related='product_tmpl_id.zra_has_service_charge', readonly=False
    )
    zra_is_rental = fields.Boolean(
        related='product_tmpl_id.zra_is_rental', readonly=False
    )

    def action_register_with_zra(self):
        templates = self.mapped('product_tmpl_id')
        if not templates:
            raise UserError(_('No product template found to register.'))
        return templates.action_register_with_zra()

    def action_update_zra_item(self):
        templates = self.mapped('product_tmpl_id')
        if not templates:
            raise UserError(_('No product template found to update.'))
        return templates.action_update_zra_item()
