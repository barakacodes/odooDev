# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class ZRAImportDeclaration(models.Model):
    """
    PARITY #2: Import Declarations from ZRA customs.

    Fetches import declarations from the ZRA VSDC API endpoint
    (imports/selectImportItems), stores them locally, allows linking
    to products and triggering stock receipt creation.
    """
    _name = 'zra.import.declaration'
    _description = 'ZRA Import Declaration'
    _order = 'declaration_date desc, name'
    _rec_name = 'name'

    # ── Identity ─────────────────────────────────────────────────────────────
    name = fields.Char(
        string='Declaration Number', required=True, copy=False, index=True,
        help='ZRA customs declaration reference number'
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company
    )

    # ── Declaration header ────────────────────────────────────────────────────
    declaration_date = fields.Date(
        string='Declaration Date', required=True, index=True
    )
    task_cd = fields.Char(
        string='Task Code',
        help='ZRA task code for this declaration'
    )
    declarant_name = fields.Char(string='Declarant Name')
    declarant_tpin = fields.Char(string='Declarant TPIN', size=10)
    hscode = fields.Char(
        string='HS Code',
        help='Harmonised System commodity code'
    )
    origin_country_id = fields.Many2one(
        'res.country', string='Country of Origin'
    )
    export_country_id = fields.Many2one(
        'res.country', string='Country of Export'
    )
    invoice_foreign_currency_amount = fields.Float(
        string='Invoice Amount (Foreign Currency)'
    )
    foreign_currency_id = fields.Many2one(
        'res.currency', string='Foreign Currency'
    )
    exchange_rate = fields.Float(string='Exchange Rate', digits=(16, 6))
    invoice_zmw_amount = fields.Float(string='Invoice Amount (ZMW)')
    gross_mass = fields.Float(string='Gross Mass (kg)')
    net_mass = fields.Float(string='Net Mass (kg)')
    customs_value = fields.Float(string='Customs Value (ZMW)')
    duty_amount = fields.Float(string='Duty Amount (ZMW)')
    vat_amount = fields.Float(string='VAT Amount (ZMW)')

    # ── Status ────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft',    'Draft'),
        ('fetched',  'Fetched from ZRA'),
        ('linked',   'Product Linked'),
        ('received', 'Stock Received'),
    ], string='Status', default='draft', index=True)

    # ── Lines ─────────────────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'zra.import.declaration.line', 'declaration_id',
        string='Declaration Lines'
    )

    # ── Raw ZRA response (audit) ──────────────────────────────────────────────
    raw_response = fields.Text(
        string='Raw ZRA Response', readonly=True,
        help='Full JSON response stored for audit purposes'
    )
    last_fetch_date = fields.Datetime(string='Last Fetched', readonly=True)

    # ── Update Import Item (checklist #12 — MANDATORY) ────────────────────────
    has_pending_zra_updates = fields.Boolean(
        string='Pending ZRA Updates',
        compute='_compute_has_pending_zra_updates',
        help='True when at least one line is linked to a product but the '
             'update has not been transmitted back to ZRA yet '
             '(imports/updateImportItems).'
    )

    # ── Actions ───────────────────────────────────────────────────────────────
    def action_fetch_from_zra(self):
        """Fetch this declaration's details from ZRA."""
        self.ensure_one()
        config = self.env['zra.config'].get_active_config(self.company_id.id)
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        api_client = self.env['zra.api.client']
        result = api_client.fetch_import_declaration(config, self.name)

        # '001' = "There is no search result" (spec §6.13) — legitimate
        # empty response, not an error; handled explicitly below rather than
        # silently marking this declaration 'fetched' with blank data.
        if result.get('resultCd') == '001':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Not Found'),
                    'message': _('ZRA has no import declaration on file for "%s".') % self.name,
                    'type': 'warning',
                    'sticky': False,
                }
            }
        if result.get('resultCd') != '000':
            raise UserError(
                _('ZRA returned error: %s') % result.get('resultMsg', 'Unknown error')
            )

        import json
        data = result.get('data', {})
        self.write({
            'raw_response': json.dumps(data, indent=2),
            'last_fetch_date': fields.Datetime.now(),
            'state': 'fetched',
        })

        # Parse header fields
        header_map = {
            'taskCd': 'task_cd',
            'dclrNm': 'declarant_name',
            'dclrTpin': 'declarant_tpin',
            'hsCd': 'hscode',
            'invFcurAmt': 'invoice_foreign_currency_amount',
            'exptAmt': 'exchange_rate',
            'invZmwAmt': 'invoice_zmw_amount',
            'grssMass': 'gross_mass',
            'netMass': 'net_mass',
            'custmsVal': 'customs_value',
            'dutyAmt': 'duty_amount',
            'vatAmt': 'vat_amount',
        }
        write_vals = {}
        for zra_key, field_name in header_map.items():
            if zra_key in data:
                write_vals[field_name] = data[zra_key]

        # Resolve origin country by ISO code
        if data.get('orgnNatCd'):
            country = self.env['res.country'].search(
                [('code', '=', data['orgnNatCd'])], limit=1
            )
            if country:
                write_vals['origin_country_id'] = country.id

        if data.get('exptNatCd'):
            country = self.env['res.country'].search(
                [('code', '=', data['exptNatCd'])], limit=1
            )
            if country:
                write_vals['export_country_id'] = country.id

        if data.get('dclrDt'):
            try:
                from datetime import datetime as dt
                write_vals['declaration_date'] = dt.strptime(
                    str(data['dclrDt']), '%Y%m%d'
                ).date()
            except Exception:
                pass

        if write_vals:
            self.write(write_vals)

        # Create / refresh lines
        items = data.get('itemList', [])
        if items:
            self.line_ids.unlink()
            for item in items:
                self.env['zra.import.declaration.line'].create({
                    'declaration_id': self.id,
                    'item_seq': item.get('itemSeq', 0),
                    'item_cls_cd': item.get('itemClsCd', ''),
                    'item_cd': item.get('itemCd', ''),
                    'item_name': item.get('itemNm', ''),
                    'bcd': item.get('bcd', ''),
                    'qty_unit_cd': item.get('qtyUnitCd', 'U'),
                    'qty': float(item.get('qty', 0)),
                    'unit_price': float(item.get('unitPrc', 0)),
                    'supply_amount': float(item.get('splyAmt', 0)),
                    'duty_rate': float(item.get('dutyRt', 0)),
                    'duty_amount': float(item.get('dutyAmt', 0)),
                    'total_amount': float(item.get('totAmt', 0)),
                    'pkg_unit_cd': item.get('pkgUnitCd', 'NT'),
                    'pkg': float(item.get('pkg', 1)),
                })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Fetched from ZRA'),
                'message': _('Declaration %s fetched with %d line(s).')
                           % (self.name, len(items)),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_create_stock_receipt(self):
        """
        Create a stock picking (receipt) from this import declaration
        and sync each line as a stock movement to ZRA.
        """
        self.ensure_one()

        if self.state == 'received':
            raise UserError(_('Stock receipt already created for this declaration.'))

        unlinked = self.line_ids.filtered(lambda l: not l.product_id)
        if unlinked:
            raise UserError(
                _('%d declaration line(s) have no product linked. '
                  'Please link all products before creating the receipt.')
                % len(unlinked)
            )

        # Find or create the default import location
        supplier_location = self.env.ref('stock.stock_location_suppliers', raise_if_not_found=False)
        stock_location = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)

        if not supplier_location or not stock_location:
            raise UserError(
                _('Default supplier/stock locations not found. '
                  'Please check your warehouse configuration.')
            )

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

        if not picking_type:
            raise UserError(_('No incoming picking type found for this company.'))

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': supplier_location.id,
            'location_dest_id': stock_location.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'note': _('ZRA Import Declaration %s') % self.name,
        })

        for line in self.line_ids:
            self.env['stock.move'].create({
                'name': line.item_name or line.product_id.name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.qty,
                'product_uom': line.product_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': supplier_location.id,
                'location_dest_id': stock_location.id,
                'zra_movement_code': '01',  # Import (incoming) — spec §6.14
            })

        self.write({'state': 'received'})

        return {
            'type': 'ir.actions.act_window',
            'name': _('Import Receipt'),
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
        }


    @api.depends('line_ids.product_id', 'line_ids.zra_updated')
    def _compute_has_pending_zra_updates(self):
        for decl in self:
            decl.has_pending_zra_updates = any(
                line.product_id and not line.zra_updated
                for line in decl.line_ids
            )

    def action_update_zra_items(self):
        """Transmit confirmed import item details back to ZRA
        (imports/updateImportItems, checklist item #12 — MANDATORY).

        One API call per product-linked line not yet updated; the line's
        zra_updated flag is set on success.
        """
        self.ensure_one()
        if self.state not in ('linked', 'received'):
            raise UserError(_('Link the declaration lines to products first.'))

        config = self.env['zra.config'].get_active_config(self.company_id.id)
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        lines = self.line_ids.filtered(lambda l: l.product_id and not l.zra_updated)
        if not lines:
            raise UserError(_('No linked lines pending update on ZRA.'))

        api_client = self.env['zra.api.client']
        synced, failed = 0, 0
        errors = []
        for line in lines:
            if not line.product_id.zra_registered:
                failed += 1
                errors.append(
                    _('- Line %s: product "%s" is not registered with ZRA')
                    % (line.item_seq, line.product_id.name)
                )
                continue
            try:
                result = api_client.update_import_item(config, self, line)
                if result.get('resultCd') == '000':
                    line.zra_updated = True
                    synced += 1
                else:
                    failed += 1
                    errors.append(
                        _('- Line %s: %s')
                        % (line.item_seq, result.get('resultMsg', 'Unknown error'))
                    )
            except Exception as e:
                failed += 1
                errors.append(_('- Line %s: %s') % (line.item_seq, str(e)))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': (_('ZRA Import Items Updated') if failed == 0
                          else _('Update Completed with Errors')),
                'message': _('Updated: %d | Failed: %d%s') % (
                    synced, failed,
                    ('\n' + '\n'.join(errors)) if errors else ''
                ),
                'type': 'success' if failed == 0 else 'warning',
                'sticky': failed > 0,
            }
        }


class ZRAImportDeclarationLine(models.Model):
    """Individual item line on an import declaration."""
    _name = 'zra.import.declaration.line'
    _description = 'ZRA Import Declaration Line'
    _order = 'item_seq'

    declaration_id = fields.Many2one(
        'zra.import.declaration', string='Declaration',
        required=True, ondelete='cascade', index=True
    )
    item_seq = fields.Integer(string='Seq')
    item_cls_cd = fields.Char(string='UNSPSC Code')
    item_cd = fields.Char(string='Item Code')
    item_name = fields.Char(string='Item Name')
    bcd = fields.Char(string='Barcode')
    qty_unit_cd = fields.Char(string='Qty Unit')
    qty = fields.Float(string='Quantity', digits=(16, 4))
    unit_price = fields.Float(string='Unit Price')
    supply_amount = fields.Float(string='Supply Amount')
    duty_rate = fields.Float(string='Duty Rate (%)')
    duty_amount = fields.Float(string='Duty Amount')
    total_amount = fields.Float(string='Total Amount')
    pkg_unit_cd = fields.Char(string='Pkg Unit')
    pkg = fields.Float(string='Packages')

    # Link to Odoo product
    product_id = fields.Many2one(
        'product.product', string='Odoo Product',
        help='Link this declaration line to an Odoo product to enable stock receipt creation.'
    )
    zra_updated = fields.Boolean(
        string='Updated on ZRA', default=False, copy=False,
        help='Set once imports/updateImportItems has been confirmed for this line.'
    )

    @api.onchange('item_cls_cd', 'item_cd', 'item_name')
    def _onchange_auto_match_product(self):
        """Try to auto-match a product by ZRA item code or classification code."""
        if self.item_cd:
            product = self.env['product.product'].search(
                [('zra_item_code', '=', self.item_cd)], limit=1
            )
            if product:
                self.product_id = product
                return

        if self.item_cls_cd:
            product = self.env['product.product'].search(
                [('zra_classification_code', '=', self.item_cls_cd)], limit=1
            )
            if product:
                self.product_id = product


class ZRAImportDeclarationFetch(models.TransientModel):
    """Wizard to fetch one or more import declarations from ZRA by date range."""
    _name = 'zra.import.declaration.fetch'
    _description = 'Fetch ZRA Import Declarations'

    date_from = fields.Date(
        string='From Date', required=True,
        default=lambda self: fields.Date.today()
    )
    date_to = fields.Date(
        string='To Date', required=True,
        default=lambda self: fields.Date.today()
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company
    )

    def action_fetch(self):
        """Fetch all import declarations in the date range from ZRA."""
        self.ensure_one()

        config = self.env['zra.config'].get_active_config(self.company_id.id)
        if not config.is_initialized:
            raise UserError(_('ZRA device is not initialized.'))

        api_client = self.env['zra.api.client']
        result = api_client.fetch_import_declarations_range(
            config,
            self.date_from.strftime('%Y%m%d'),
            self.date_to.strftime('%Y%m%d'),
        )

        # '001' = "There is no search result" (spec §6.13) — legitimate
        # empty response, not an error; the loop below already handles an
        # empty declarations list gracefully.
        if result.get('resultCd') not in ('000', '001'):
            raise UserError(
                _('ZRA returned error: %s') % result.get('resultMsg', 'Unknown error')
            )

        import json
        declarations = (result.get('data') or {}).get('itemList', [])
        created, skipped = 0, 0

        for decl in declarations:
            ref = decl.get('dclrNo') or decl.get('taskCd') or ''
            if not ref:
                skipped += 1
                continue

            existing = self.env['zra.import.declaration'].search(
                [('name', '=', ref), ('company_id', '=', self.company_id.id)],
                limit=1
            )
            if existing:
                skipped += 1
                continue

            try:
                decl_date_raw = decl.get('dclrDt', '')
                from datetime import datetime as dt
                decl_date = (
                    dt.strptime(str(decl_date_raw), '%Y%m%d').date()
                    if decl_date_raw
                    else fields.Date.today()
                )
            except Exception:
                decl_date = fields.Date.today()

            new_decl = self.env['zra.import.declaration'].create({
                'name': ref,
                'company_id': self.company_id.id,
                'declaration_date': decl_date,
                'raw_response': json.dumps(decl, indent=2),
                'state': 'fetched',
            })
            # Trigger full detail fetch for each declaration
            try:
                new_decl.action_fetch_from_zra()
            except Exception as e:
                _logger.warning(f"Could not fetch full detail for {ref}: {str(e)}")

            created += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Declarations Fetched'),
                'message': _('Created: %d | Already existed: %d') % (created, skipped),
                'type': 'success' if created > 0 else 'warning',
                'sticky': False,
            }
        }
