from django.core.management.base import BaseCommand

from pinax.stripe.actions import charges


class Command(BaseCommand):

    help = "Check for newly available Charges."

    def handle(self, *args, **options):
        charges.update_charge_availability()
