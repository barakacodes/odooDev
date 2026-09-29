# -*- coding: utf-8 -*-
from odoo import models, fields, _
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = 'res.users'

    zra_branch_user_synced = fields.Boolean(
        string='Saved as ZRA Branch User', default=False, copy=False,
        help='Whether this user has been registered as a branch user on '
             'Smart Invoice via branches/saveBrancheUser.'
    )

    def action_save_as_zra_branch_user(self):
        """Register this system user as a branch user on ZRA
        (branches/saveBrancheUser, checklist item #6)."""
        for user in self:
            config = self.env['zra.config'].get_active_config(
                user.company_id.id if user.company_id else None
            )
            if not config.is_initialized:
                raise UserError(_('ZRA device is not initialized.'))

            api_client = self.env['zra.api.client']
            result = api_client.save_branch_user(config, user)

            if result.get('resultCd') == '000':
                user.zra_branch_user_synced = True
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('User "%s" saved as a ZRA branch user!') % user.name,
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(
                    _('ZRA Error: %s') % result.get('resultMsg', 'Unknown error')
                )
