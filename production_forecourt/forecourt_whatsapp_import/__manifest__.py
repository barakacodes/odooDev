# -*- coding: utf-8 -*-
{
    'name': 'Forecourt WhatsApp Import',
    'version':  '18.0.1.3.0',
    'summary': 'Import shift reports from WhatsApp text format',
    'description': """
        Parses unstructured WhatsApp reports from Luanshya, Chililabombwe, and Arcades.
        Automates stock chaining, variance calculations, and Genset consumption.
        Integrates directly into the Production Forecourt Models.
    """,
    'category': 'Industries',
    'author': 'Process Dial',
    'license': 'LGPL-3',
    'depends': [
        'forecourt_operations',
        'forecourt_shift_capture',
        'forecourt_credit_import',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/import_wizard_views.xml',
        
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

