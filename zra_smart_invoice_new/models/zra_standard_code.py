# -*- coding: utf-8 -*-
from odoo import models, fields


class ZRAStandardCode(models.Model):
    """Standard codes (VSDC constants) synced from ZRA (code/selectCodes,
    checklist item #2 — 'is the CIS able to retrieve AND SAVE code data').

    Kept as a read-only reference/audit table so admins can cross-check the
    hardcoded Selection lists used elsewhere in this module (payment types,
    packaging units, tax types, etc.) against what ZRA's server currently
    reports — it does not dynamically drive those selections, since a sync
    isn't guaranteed to have run before the module is used.
    """
    _name = 'zra.standard.code'
    _description = 'ZRA Standard Code (VSDC Constants)'
    _rec_name = 'cd_nm'
    _order = 'cd_cls, cd'

    cd_cls = fields.Char(string='Code Class', required=True, index=True)
    cd_cls_nm = fields.Char(string='Code Class Name')
    cd = fields.Char(string='Code', required=True, index=True)
    cd_nm = fields.Char(string='Code Name')

    _sql_constraints = [
        ('cd_cls_cd_unique', 'unique(cd_cls, cd)',
         'This standard code already exists.'),
    ]
