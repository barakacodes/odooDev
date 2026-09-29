# -*- coding: utf-8 -*-
"""Nightly database backup (internal checklist #21 / PDF item #21:
"Create a script that will be automatically downloading a backup copy of
each client database daily at night").

An earlier version of this requirement was satisfied only by
tools/odoo_daily_backup.sh — a real, well-written script, but one that does
nothing unless a human remembers to run `crontab -e` on every client's
server. That made the "automatic" half of the requirement false in
practice: nothing in the repo, the module, or the database itself ever
confirmed the schedule was actually installed anywhere.

This model + the ir.cron in data/zra_backup_cron.xml make backup an
Odoo-native, self-scheduling feature: it runs via the same cron engine as
every other scheduled action (visible and auditable under Settings >
Technical > Scheduled Actions), needs no per-server crontab setup, and each
run leaves a zra.backup.log record a ZRA reviewer (or Esau) can point to as
evidence backups actually happened — not just that a script exists.

Caveat that's worth stating plainly rather than repeating the module's own
past pattern of overclaiming: a dump written to the same host/volume as the
live database is NOT offsite disaster-recovery protection — it protects
against accidental data corruption or a bad migration, not against losing
the whole machine. tools/odoo_daily_backup.sh remains available for anyone
who wants to pull dumps to a separate host instead.
"""
import logging
import os
import subprocess
import time
from datetime import timedelta

from odoo import models, fields, api, tools, _

_logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = '/var/lib/odoo/backups'
DEFAULT_RETENTION_DAYS = 14


class ZraBackupLog(models.Model):
    _name = 'zra.backup.log'
    _description = 'ZRA Nightly Database Backup Log'
    _order = 'create_date desc'

    name = fields.Char(string='Database', required=True)
    status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], required=True, index=True)
    file_path = fields.Char(string='Dump File')
    file_size = fields.Integer(string='Size (bytes)')
    duration_seconds = fields.Float(string='Duration (s)')
    error_message = fields.Text(string='Error')

    @api.model
    def _get_param(self, key, default):
        return (self.env['ir.config_parameter'].sudo()
                .get_param(f'zra_smart_invoice_new.{key}', default))

    @api.model
    def _cron_nightly_backup(self):
        """Target of the 'ZRA: Nightly Database Backup' scheduled action.

        Dumps the CURRENT database (pg_dump custom format) to
        ir.config_parameter 'zra_smart_invoice_new.backup_dir' (defaults to
        /var/lib/odoo/backups — inside Odoo's own data_dir, so it lands on
        whatever volume already persists filestore/sessions across
        container restarts), then purges dumps older than
        'zra_smart_invoice_new.backup_retention_days' (default 14).

        Deliberately swallows and logs its own exceptions rather than
        letting them propagate: a failed backup must never be allowed to
        break the ir.cron scheduler for every other scheduled action in the
        database, and the failure is still fully visible as a
        status='failed' zra.backup.log record either way.
        """
        db_name = self.env.cr.dbname
        backup_dir = self._get_param('backup_dir', DEFAULT_BACKUP_DIR)
        retention_days = int(self._get_param('backup_retention_days', DEFAULT_RETENTION_DAYS))

        started = time.time()
        try:
            os.makedirs(backup_dir, mode=0o700, exist_ok=True)
            stamp = fields.Datetime.now().strftime('%Y%m%d_%H%M%S')
            dump_path = os.path.join(backup_dir, f'{db_name}_{stamp}.dump')

            # Credentials passed via env var (PGPASSWORD), never on the
            # command line or written to disk/logs — a process-list dump
            # (e.g. `ps aux` from another container sharing the host PID
            # namespace) must not be able to read the DB password.
            env = os.environ.copy()
            db_password = tools.config.get('db_password')
            if db_password:
                env['PGPASSWORD'] = db_password

            cmd = [
                'pg_dump',
                '-h', tools.config.get('db_host') or 'localhost',
                '-p', str(tools.config.get('db_port') or 5432),
                '-U', tools.config.get('db_user') or 'odoo',
                '-Fc', '-f', dump_path,
                db_name,
            ]
            subprocess.run(
                cmd, env=env, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=3600,
            )
            os.chmod(dump_path, 0o600)  # dumps contain full client data
            file_size = os.path.getsize(dump_path)

            self.sudo().create({
                'name': db_name,
                'status': 'success',
                'file_path': dump_path,
                'file_size': file_size,
                'duration_seconds': time.time() - started,
            })
            _logger.info('ZRA nightly backup succeeded: %s (%d bytes)', dump_path, file_size)

            self._purge_old_backups(backup_dir, retention_days)

        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b'').decode(errors='replace')[:2000]
            self.sudo().create({
                'name': db_name, 'status': 'failed',
                'error_message': f'pg_dump exit {e.returncode}: {stderr}',
                'duration_seconds': time.time() - started,
            })
            _logger.error('ZRA nightly backup FAILED for %s: %s', db_name, stderr)
        except Exception as e:  # noqa: BLE001 — must never crash the cron
            self.sudo().create({
                'name': db_name, 'status': 'failed',
                'error_message': str(e),
                'duration_seconds': time.time() - started,
            })
            _logger.error('ZRA nightly backup FAILED for %s: %s', db_name, str(e))

    def _purge_old_backups(self, backup_dir, retention_days):
        cutoff = time.time() - retention_days * 86400
        try:
            for fname in os.listdir(backup_dir):
                if not fname.endswith('.dump'):
                    continue
                fpath = os.path.join(backup_dir, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        _logger.info('ZRA backup retention: purged %s', fpath)
                except OSError as e:
                    _logger.warning('ZRA backup retention: could not purge %s: %s', fpath, e)
        except OSError as e:
            _logger.warning('ZRA backup retention: could not list %s: %s', backup_dir, e)
