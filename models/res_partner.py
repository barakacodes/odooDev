from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'
    is_agent = fields.Boolean(string='Is Agent', default=False)
