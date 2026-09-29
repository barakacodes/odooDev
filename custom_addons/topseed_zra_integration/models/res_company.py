from odoo import models, fields

class ResCompany(models.Model):
    _inherit = 'res.company'
    
    zra_enabled = fields.Boolean(
        string='Enable ZRA Integration',
        default=False
    )
    zra_tpin = fields.Char(
        string='ZRA TPIN',
        size=10
    )
    zra_branch_id = fields.Char(
        string='ZRA Branch ID',
        size=3
    )
    zra_device_serial = fields.Char(
        string='ZRA Device Serial Number'
    )
    zra_api_url = fields.Char(
        string='ZRA API URL',
        default='https://sandboxportal.zra.org.zm/vsdc/api'
    )
