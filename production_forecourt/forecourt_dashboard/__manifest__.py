# -*- coding: utf-8 -*-
{
    'name': 'Forecourt Dashboard',
    'version':  '18.0.1.3.0',
    'summary': 'Interactive dashboards for Aziz Petroleum â€” Sales, Inventory, Deposits, Variances',
    'category': 'Industries',
    'author': 'Process Dial',
    'website': 'https://processdial.org',
    'depends': ['forecourt_operations', 'forecourt_shift_capture', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/forecourt_dashboard_views.xml',
        'views/forecourt_dashboard_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'forecourt_dashboard/static/src/xml/forecourt_dashboard.xml',
            'forecourt_dashboard/static/src/js/forecourt_dashboard.js',
            'forecourt_dashboard/static/src/css/forecourt_dashboard.css',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}


