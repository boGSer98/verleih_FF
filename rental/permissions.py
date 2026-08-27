from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


GROUP_ADMIN = 'Verleih Admin'
GROUP_MANAGEMENT = 'Verleih Verwaltung/Vorstand'
GROUP_HELPERS = 'Verleih Helfer Ausgabe/Rücknahme'
GROUP_READONLY = 'Verleih Lesen/Auswertung'

RENTAL_MODELS = [
    'borrower',
    'document',
    'product',
    'productaccessory',
    'productcategory',
    'protocol',
    'protocolphoto',
    'rentalcase',
    'rentalcaseitem',
]

ALL_ACTIONS = ['add', 'change', 'delete', 'view']
VIEW_ACTIONS = ['view']

GROUP_PERMISSION_MATRIX = {
    GROUP_ADMIN: {model: ALL_ACTIONS for model in RENTAL_MODELS},
    GROUP_MANAGEMENT: {model: ALL_ACTIONS for model in RENTAL_MODELS},
    GROUP_HELPERS: {
        'borrower': ['view'],
        'document': ['add', 'change', 'view'],
        'product': ['view'],
        'productaccessory': ['view'],
        'productcategory': ['view'],
        'protocol': ['add', 'view'],
        'protocolphoto': ['add', 'view'],
        'rentalcase': ['change', 'view'],
        'rentalcaseitem': ['change', 'view'],
    },
    GROUP_READONLY: {model: VIEW_ACTIONS for model in RENTAL_MODELS},
}


def permission_codes_for_group(group_name):
    model_matrix = GROUP_PERMISSION_MATRIX[group_name]
    return {
        f'{action}_{model}'
        for model, actions in model_matrix.items()
        for action in actions
    }


def ensure_rental_groups(app_config=None, verbosity=0, **kwargs):
    """Create/update sample groups for the Verleih workflow after migrations.

    Django creates model permissions in the post_migrate phase. Hooking this
    function into that signal keeps the groups idempotent and safe for fresh
    installations as well as later permission adjustments.
    """
    app_label = app_config.label if app_config else 'rental'
    content_types = ContentType.objects.filter(app_label=app_label)
    permissions = {
        permission.codename: permission
        for permission in Permission.objects.filter(content_type__in=content_types)
    }

    for group_name in GROUP_PERMISSION_MATRIX:
        group, _created = Group.objects.get_or_create(name=group_name)
        expected_codes = permission_codes_for_group(group_name)
        expected_permissions = [
            permissions[code]
            for code in sorted(expected_codes)
            if code in permissions
        ]
        group.permissions.set(expected_permissions)
        if verbosity >= 2:
            print(f'Gruppe „{group_name}“ mit {len(expected_permissions)} Recht(en) aktualisiert.')
