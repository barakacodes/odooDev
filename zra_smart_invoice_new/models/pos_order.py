# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
import base64
from io import BytesIO

_logger = logging.getLogger(__name__)

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    _logger.warning("qrcode library not installed. QR code generation will be disabled.")


class PosOrder(models.Model):
    _inherit = 'pos.order'

    zra_receipt_number = fields.Char(string='ZRA Receipt Number', readonly=True, copy=False)
    zra_internal_data = fields.Char(string='ZRA Internal Data', readonly=True, copy=False)
    zra_signature = fields.Char(string='ZRA Receipt Signature', readonly=True, copy=False)
    zra_sdc_id = fields.Char(string='SDC ID', readonly=True, copy=False)
    zra_mrc_no = fields.Char(string='MRC Number', readonly=True, copy=False)
    zra_invoice_number = fields.Char(string='ZRA Invoice Number', readonly=True, copy=False)
    zra_qr_code = fields.Char(string='ZRA QR Code Data', readonly=True, copy=False)
    zra_qr_code_image = fields.Binary(string='ZRA QR Code Image', readonly=True, copy=False)
    zra_sync_status = fields.Selection([
        ('pending', 'Pending'),
        ('synced', 'Synced'),
        ('failed', 'Failed'),
    ], string='ZRA Status', default='pending', copy=False)
    zra_sync_date = fields.Datetime(string='ZRA Sync Date', readonly=True, copy=False)
    zra_error_message = fields.Text(string='ZRA Error Message', readonly=True, copy=False)

    # ── Immutability ────────────────────────────────────────────────────
    # The authoritative fiscal document is the linked account.move, which
    # already has its own unconditional write()/unlink() guard — so what
    # ZRA actually has on file can't be silently changed. But pos.order
    # itself (and its lines) had no guard at all: a synced order's lines
    # could still be freely edited/deleted, which can't alter what ZRA
    # received but does let the POS receipt/reprint UI show something that
    # no longer matches what was fiscalized. Mirror account.move's guard
    # here too, excluding the zra_* bookkeeping fields this module's own
    # sync flow writes after the fact.
    _POS_ZRA_PROTECTED_FIELDS = {
        'lines', 'partner_id', 'date_order', 'currency_id', 'amount_total',
        'amount_tax', 'amount_paid',
    }

    def write(self, vals):
        touched = self._POS_ZRA_PROTECTED_FIELDS & set(vals.keys())
        if touched:
            for order in self:
                if order.zra_sync_status == 'synced':
                    raise UserError(_(
                        'POS order "%s" has already been submitted to ZRA '
                        '(Receipt No: %s) and its sale details cannot be '
                        'modified.'
                    ) % (order.name, order.zra_receipt_number or ''))
        return super().write(vals)

    def unlink(self):
        for order in self:
            if order.zra_sync_status == 'synced':
                raise UserError(_(
                    'POS order "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and cannot be deleted.'
                ) % (order.name, order.zra_receipt_number or ''))
        return super().unlink()

    def _generate_qr_code(self):
        self.ensure_one()
        if not HAS_QRCODE or not self.zra_receipt_number:
            return False
        try:
            parts = []
            if self.zra_invoice_number:
                parts.append(f"INV:{self.zra_invoice_number}")
            if self.zra_receipt_number:
                parts.append(f"RCPT:{self.zra_receipt_number}")
            if self.zra_sdc_id:
                parts.append(f"SDC:{self.zra_sdc_id}")
            if self.zra_internal_data:
                parts.append(f"DATA:{self.zra_internal_data}")
            if self.amount_total:
                parts.append(f"AMT:{self.amount_total:.2f}")
            qr_data = "|".join(parts)
            if qr_data:
                qr = qrcode.QRCode(version=1,
                                   error_correction=qrcode.constants.ERROR_CORRECT_M,
                                   box_size=10, border=4)
                qr.add_data(qr_data)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = BytesIO()
                img.save(buf, format='PNG')
                return base64.b64encode(buf.getvalue()).decode('utf-8')
        except Exception as e:
            _logger.error(f"QR code generation error: {str(e)}")
        return False

    def _export_for_ui(self, order):
        result = super()._export_for_ui(order)
        order.invalidate_recordset([
            'zra_receipt_number', 'zra_internal_data', 'zra_signature',
            'zra_sdc_id', 'zra_mrc_no', 'zra_invoice_number',
            'zra_sync_status', 'zra_qr_code_image',
        ])
        result['id'] = order.id
        result['server_id'] = order.id
        result['pos_order_id'] = order.id
        result['zra_receipt_number'] = order.zra_receipt_number or ''
        result['zra_internal_data'] = order.zra_internal_data or ''
        result['zra_signature'] = order.zra_signature or ''
        result['zra_sdc_id'] = order.zra_sdc_id or ''
        result['zra_mrc_no'] = order.zra_mrc_no or ''
        result['zra_invoice_number'] = order.zra_invoice_number or ''
        result['zra_sync_status'] = order.zra_sync_status or ''
        if order.zra_qr_code_image:
            result['zra_qr_code_image'] = (
                order.zra_qr_code_image.decode('utf-8')
                if isinstance(order.zra_qr_code_image, bytes)
                else order.zra_qr_code_image
            )
        else:
            result['zra_qr_code_image'] = ''
        return result

    def action_pos_order_paid(self):
        """Override to sync with ZRA after payment."""
        _logger.info(f"========== POS ORDER PAID: {self.name} ==========")
        res = super(PosOrder, self).action_pos_order_paid()

        if res and self:
            for order in self:
                _logger.info(f"Processing ZRA sync for POS order: {order.name}")
                try:
                    config = self.env['zra.config'].get_active_config(order.company_id.id)
                    if config and config.auto_sync_pos and config.is_initialized:
                        # FIX #6: Always use sudo() for POS sync
                        sync_result = order.sudo().action_send_to_zra()
                        _logger.info(f"ZRA sync completed for {order.name}: {sync_result}")
                        self.env.cr.commit()
                    else:
                        _logger.warning(
                            f"ZRA not configured/initialized for order {order.name}"
                        )
                except Exception as e:
                    _logger.error(
                        f"ZRA POS sync failed for {order.name}: {str(e)}", exc_info=True
                    )
                    try:
                        order.sudo().write({
                            'zra_sync_status': 'failed',
                            'zra_error_message': str(e),
                        })
                        self.env.cr.commit()
                    except Exception:
                        pass

        return res

    @api.model
    def get_order_with_zra_data(self, order_id):
        order = self.browse(order_id)
        if not order.exists():
            return {}
        order.invalidate_recordset([
            'zra_receipt_number', 'zra_internal_data', 'zra_signature',
            'zra_sdc_id', 'zra_mrc_no', 'zra_invoice_number',
            'zra_sync_status', 'zra_qr_code_image',
        ])
        return self._export_for_ui(order)

    def action_send_to_zra(self):
        """Send POS order to ZRA."""
        self.ensure_one()
        _logger.info(f"========== action_send_to_zra START for {self.name} ==========")

        if self.zra_sync_status == 'synced':
            _logger.info(f"Order {self.name} already synced")
            return True

        try:
            # Ensure partner
            if not self.partner_id:
                default_partner = self.env['res.partner'].search(
                    [('customer_rank', '>', 0)], limit=1
                )
                if not default_partner:
                    default_partner = self.env['res.partner'].search([], limit=1)
                if not default_partner:
                    raise Exception("No partner available for POS order ZRA sync")
                _logger.info(f"Using partner {default_partner.name} for {self.name}")
                self.write({'partner_id': default_partner.id})

            # Create invoice if missing
            if not self.account_move:
                _logger.info(f"Creating invoice for {self.name}")
                self.action_pos_order_invoice()

            if not self.account_move:
                raise Exception("Failed to create invoice for POS order")

            # Post invoice
            if self.account_move.state != 'posted':
                _logger.info(f"Posting invoice {self.account_move.name}")
                self.account_move.sudo().action_post()

            # Sync invoice with ZRA
            _logger.info(f"Syncing invoice {self.account_move.name} with ZRA")
            self.account_move.sudo().action_send_to_zra()

            # Copy ZRA data from invoice to POS order
            if self.account_move.zra_sync_status == 'synced':
                _logger.info(f"Copying ZRA data to POS order {self.name}")
                self.write({
                    'zra_receipt_number': self.account_move.zra_receipt_number,
                    'zra_internal_data': self.account_move.zra_internal_data,
                    'zra_signature': self.account_move.zra_signature,
                    'zra_sdc_id': self.account_move.zra_sdc_id,
                    'zra_mrc_no': self.account_move.zra_mrc_no,
                    'zra_invoice_number': self.account_move.zra_invoice_number,
                    'zra_qr_code': self.account_move.zra_qr_code,
                    'zra_qr_code_image': self.account_move.zra_qr_code_image,
                    'zra_sync_status': 'synced',
                    'zra_sync_date': self.account_move.zra_sync_date,
                    'zra_error_message': False,
                })
                _logger.info(f"✅ POS order {self.name} synced successfully!")
                return True
            else:
                self.write({
                    'zra_sync_status': 'failed',
                    'zra_error_message': self.account_move.zra_error_message,
                })
                _logger.error(f"❌ Invoice sync failed: {self.account_move.zra_error_message}")
                return False

        except Exception as e:
            error_msg = str(e)
            _logger.error(f"❌ ZRA sync exception for {self.name}: {error_msg}", exc_info=True)
            self.write({
                'zra_sync_status': 'failed',
                'zra_error_message': error_msg,
            })
            return False

    def get_zra_receipt_data_for_pos(self):
        self.ensure_one()
        qr = self.zra_qr_code_image
        if qr and isinstance(qr, bytes):
            qr = qr.decode('utf-8')
        return {
            'zra_invoice_number': self.zra_invoice_number or '',
            'zra_receipt_number': self.zra_receipt_number or '',
            'zra_internal_data': self.zra_internal_data or '',
            'zra_signature': self.zra_signature or '',
            'zra_sdc_id': self.zra_sdc_id or '',
            'zra_mrc_no': self.zra_mrc_no or '',
            'zra_qr_code_image': qr or '',
            'zra_sync_status': self.zra_sync_status,
            'amount_tax': self.amount_tax,
            'amount_total': self.amount_total,
        }

    @api.model
    def get_zra_receipt_by_order_id(self, order_id):
        order = self.browse(order_id)
        if order.exists():
            return order.get_zra_receipt_data_for_pos()
        return {}

    @api.model
    def sync_zra_data_to_order(self, order_id):
        order = self.browse(order_id)
        if order.exists() and order.zra_sync_status == 'synced':
            return order.get_zra_receipt_data_for_pos()
        return {}


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    # See PosOrder._POS_ZRA_PROTECTED_FIELDS above — a line reached
    # directly (env['pos.order.line'].browse(id).write(...), RPC, a bulk
    # update) bypasses the parent's write() guard entirely, the same gap
    # account.move.line had.
    def write(self, vals):
        for line in self:
            order = line.order_id
            if order and order.zra_sync_status == 'synced':
                raise UserError(_(
                    'POS order "%s" has already been submitted to ZRA and '
                    'its lines cannot be modified.'
                ) % order.name)
        return super().write(vals)

    def unlink(self):
        for line in self:
            order = line.order_id
            if order and order.zra_sync_status == 'synced':
                raise UserError(_(
                    'POS order "%s" has already been submitted to ZRA and '
                    'its lines cannot be deleted.'
                ) % order.name)
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['pos.order'].browse(
            {vals['order_id'] for vals in vals_list if vals.get('order_id')}
        )
        synced_by_id = {o.id: o for o in orders if o.zra_sync_status == 'synced'}
        for vals in vals_list:
            order = synced_by_id.get(vals.get('order_id'))
            if order:
                raise UserError(_(
                    'POS order "%s" has already been submitted to ZRA and '
                    'new lines cannot be added to it.'
                ) % order.name)
        return super().create(vals_list)
