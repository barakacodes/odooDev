# -*- coding: utf-8 -*-
from odoo import models, fields


class ZRAClassificationCode(models.Model):
    """UNSPSC classification codes synced from ZRA (itemClass/selectItemsClass,
    checklist item #3 — 'is the CIS able to retrieve AND SAVE the
    classification codes'). Populated by zra.config.action_sync_classification_codes.
    """
    _name = 'zra.classification.code'
    _description = 'ZRA UNSPSC Classification Code'
    _rec_name = 'item_cls_nm'
    _order = 'item_cls_cd'

    item_cls_cd = fields.Char(string='Code', required=True, size=10, index=True)
    item_cls_nm = fields.Char(string='Name', required=True)
    item_cls_lvl = fields.Integer(
        string='Level',
        help='UNSPSC hierarchy level. Products must be classified at level 4 '
             '(commodity level) per spec — use the closest level-4 code where '
             'an exact match does not exist.'
    )
    tax_ty_cd = fields.Char(string='Default Tax Type Code')
    use_yn = fields.Char(string='Used (Y/N)')

    _sql_constraints = [
        ('item_cls_cd_unique', 'unique(item_cls_cd)',
         'This classification code already exists.'),
    ]


