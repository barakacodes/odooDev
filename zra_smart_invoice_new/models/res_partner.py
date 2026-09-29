# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

import logging
_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    zra_tpin = fields.Char(string='TPIN', size=10, index=True,
                           help='Taxpayer Identification Number for ZRA (10 digits, mandatory)')

    # ── Branch Customer Information (checklist #4, #5) ────────────────────────
    zra_customer_synced = fields.Boolean(
        string='Saved to ZRA', default=False, copy=False,
        help='Whether this contact has been saved as a branch customer on '
             'Smart Invoice via branches/saveBrancheCustomers.'
    )

    @api.constrains('zra_tpin')
    def _check_zra_tpin(self):
        for partner in self:
            if partner.zra_tpin:
                if len(partner.zra_tpin) != 10:
                    raise ValidationError(_('TPIN must be exactly 10 digits'))
                if not partner.zra_tpin.isdigit():
                    raise ValidationError(_('TPIN must contain only digits'))

                # FIX #7: Duplicate check (skip default placeholder TPIN)
                if partner.zra_tpin != '1000000000':
                    duplicate = self.search([
                        ('id', '!=', partner.id),
                        ('zra_tpin', '=', partner.zra_tpin)
                    ], limit=1)
                    if duplicate:
                        raise ValidationError(
                            _('TPIN %s is already used by %s')
                            % (partner.zra_tpin, duplicate.name)
                        )

    @api.model
    def get_partners_missing_tpin(self, partner_type='customer'):
        """Return count of partners missing TPIN for warnings."""
        domain = [('zra_tpin', '=', False), ('active', '=', True)]
        if partner_type == 'customer':
            domain.append(('customer_rank', '>', 0))
        elif partner_type == 'supplier':
            domain.append(('supplier_rank', '>', 0))
        return self.search_count(domain)

    # ── Branch Customer Information (checklist #4, #5) ────────────────────────
    def action_save_to_zra(self):
        """Save this contact as a branch customer on ZRA
        (branches/saveBrancheCustomers, checklist item #4)."""
        for partner in self:
            if not partner.zra_tpin:
                raise UserError(
                    _('Customer TPIN is required before saving "%s" to ZRA.')
                    % partner.name
                )
            config = self.env['zra.config'].get_active_config(
                partner.company_id.id if partner.company_id else None
            )
            if not config.is_initialized:
                raise UserError(_('ZRA device is not initialized.'))

            api_client = self.env['zra.api.client']
            result = api_client.save_branch_customer(config, partner)

            if result.get('resultCd') == '000':
                partner.zra_customer_synced = True
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Customer "%s" saved to ZRA!') % partner.name,
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(
                    _('ZRA Error: %s') % result.get('resultMsg', 'Unknown error')
                )

    def action_fetch_from_zra(self):
        """Fetch this customer's details from ZRA by TPIN
        (customers/selectCustomer, checklist item #5)."""
        for partner in self:
            if not partner.zra_tpin:
                raise UserError(
                    _('Customer TPIN is required to fetch "%s" from ZRA.')
                    % partner.name
                )
            config = self.env['zra.config'].get_active_config(
                partner.company_id.id if partner.company_id else None
            )
            if not config.is_initialized:
                raise UserError(_('ZRA device is not initialized.'))

            api_client = self.env['zra.api.client']
            result = api_client.get_branch_customer(config, partner.zra_tpin)

            # '001' = "There is no search result" (spec §6.13) — legitimate
            # empty response, not an error; falls through to "Not Found"
            # below.
            if result.get('resultCd') not in ('000', '001'):
                raise UserError(
                    _('ZRA Error: %s') % result.get('resultMsg', 'Unknown error')
                )

            cust_list = (result.get('data') or {}).get('custList', [])
            if not cust_list:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Not Found'),
                        'message': _('No customer record found on ZRA for TPIN %s.')
                                   % partner.zra_tpin,
                        'type': 'warning',
                        'sticky': False,
                    }
                }

            data = cust_list[0]
            # Read-only: shown for verification, doesn't silently overwrite
            # the local contact record.
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Fetched from ZRA'),
                    'message': _(
                        'ZRA has: %s | Status: %s | Phone: %s'
                    ) % (
                        data.get('custNm') or '',
                        data.get('taxprSttsCd') or '',
                        data.get('telNo') or '',
                    ),
                    'type': 'success',
                    'sticky': True,
                }
            }

    # ── Supplier Smart Invoice registration check (internal checklist #13) ────
    zra_si_registration = fields.Selection([
        ('unknown', 'Unknown'),
        ('registered', 'Registered on Smart Invoice'),
        ('not_registered', 'NOT Registered on Smart Invoice'),
    ], string='Smart Invoice Status', default='unknown', readonly=True, copy=False,
       help='Whether this TPIN is registered on the ZRA Smart Invoice system. '
            'Set via the "Verify TPIN on ZRA" button.')
    zra_si_check_date = fields.Datetime(
        string='SI Status Checked On', readonly=True, copy=False)

    def action_verify_tpin_on_zra(self):
        """Check whether this partner's TPIN is registered on Smart Invoice
        (customers/selectCustomer) — internal checklist #13: warn before
        recording purchases from suppliers not registered on Smart Invoice."""
        for partner in self:
            if not partner.zra_tpin:
                raise UserError(
                    _('Set a TPIN on "%s" before verifying.') % partner.name)
            config = self.env['zra.config'].get_active_config(
                partner.company_id.id if partner.company_id else None)
            if not config.is_initialized:
                raise UserError(_('ZRA device is not initialized.'))

            result = self.env['zra.api.client'].get_branch_customer(
                config, partner.zra_tpin)
            if result.get('resultCd') not in ('000', '001'):
                raise UserError(
                    _('ZRA Error: %s') % result.get('resultMsg', 'Unknown error'))

            cust_list = (result.get('data') or {}).get('custList', [])
            registered = bool(cust_list)
            partner.write({
                'zra_si_registration': 'registered' if registered else 'not_registered',
                'zra_si_check_date': fields.Datetime.now(),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': (_('Registered on Smart Invoice') if registered
                              else _('NOT Registered on Smart Invoice')),
                    'message': (
                        _('TPIN %s is registered on Smart Invoice (%s).')
                        % (partner.zra_tpin, cust_list[0].get('custNm') or partner.name)
                    ) if registered else (
                        _('TPIN %s is NOT registered on the Smart Invoice system. '
                          'Ask the supplier to register with ZRA Smart Invoice. '
                          'You may still record the purchase by setting the ZRA '
                          'Registration Type on the bill to "Manual".')
                        % partner.zra_tpin
                    ),
                    'type': 'success' if registered else 'warning',
                    'sticky': not registered,
                }
            }

