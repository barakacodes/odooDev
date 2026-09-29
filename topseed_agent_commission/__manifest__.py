{
    'name': 'TopSeed Agent Commission',
    'version': '1.0.0',
    'category': 'Sales',
    'summary': 'Agent Commission Management System for TopSeed',
    'description': """
        Comprehensive Agent Commission Management System for TopSeed Ltd.

        Features:
          - Agent registration and management
          - Flexible commission rules (percentage, fixed, tiered)
          - Automatic commission calculation on sales
          - Commission tracking and status management
          - Commission payment workflow
          - Agent performance dashboards
          - Commission reports and analytics
          - Integration with Sales and Accounting modules

        This module is designed to handle complex commission structures
        for agricultural product distributors and agents.
    """,
    'author': 'ProcessDial Business Solutions',
    'website': 'https://www.processdial.net',
    'depends': [
        'base',
        'sale',
        'account',
        'product',
        'mail',
        'contacts',
    ],
    'data': [
        'security/commission_security.xml',
        'security/ir.model.access.csv',
        'data/commission_data.xml',
        'views/res_partner_view.xml',
        'views/commission_config_view.xml',
        'views/commission_rule_view.xml',
        'views/sale_order_view.xml',
        'views/commission_line_view.xml',
        'views/commission_payment_view.xml',
        'views/commission_dashboard_view.xml',
        'views/menu_views.xml',
        'wizard/commission_payment_wizard_view.xml',
        'reports/commission_report_template.xml',
    ],
    'demo': [
        'data/demo_data.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}