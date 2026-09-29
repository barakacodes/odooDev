# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.forecourt_operations.hooks import _run_branch_backfill
    _run_branch_backfill(env)
