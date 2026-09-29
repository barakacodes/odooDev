# -*- coding: utf-8 -*-
{
    'name': 'Forecourt Credit Customer Import (NetPOS)',
    'version':  '18.0.1.3.0',
    'summary': 'Import prepaid/credit customer transactions and balances from NetPOS PDF reports',
    'category': 'Industries',
    'author': 'Esau Ngoma',
    'maintainer': 'Esau Ngoma, Full Stack Developer <esaungoma004@gmail.com>',
    'license': 'LGPL-3',
    'depends': ['forecourt_operations'],
    'data': [
        'security/ir.model.access.csv',
        'views/credit_import_wizard_views.xml',
        'views/forecourt_credit_menu.xml',
    ],
    'installable': True,
    'application': False,
}
