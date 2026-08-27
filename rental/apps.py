from django.apps import AppConfig


class RentalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rental'
    verbose_name = 'Verleih'

    def ready(self):
        from django.db.models.signals import post_migrate

        from .permissions import ensure_rental_groups

        post_migrate.connect(ensure_rental_groups, sender=self, dispatch_uid='rental.ensure_rental_groups')
