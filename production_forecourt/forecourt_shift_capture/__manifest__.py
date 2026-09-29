# -*- coding: utf-8 -*-
{
    'name': 'Forecourt Shift Capture & Reconciliation',
    'version': '18.0.1.4.0',
    'summary': 'Per-shift fuel data capture (Petrol/Diesel) with automatic '
               'opening, closing, ATG and cash reconciliation checks.',
    'category': 'Industries',
    'author': 'Process Dial',
    'website': 'https://processdial.org',
    'depends': ['forecourt_operations'],
    'data': [
        'security/ir.model.access.csv',
        'data/forecourt_fuel_rate_data.xml',
        'views/forecourt_fuel_rate_views.xml',
        'views/forecourt_shift_views.xml',
        'views/forecourt_shift_capture_menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
