from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Borrower, Product, ProductCategory, Protocol, RentalCase, RentalCaseItem


class RentalCaseModelTests(TestCase):
    def test_case_number_is_generated(self):
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
        )

        self.assertRegex(case.number, r'^VF-\d{4}-0001$')

    def test_case_can_store_items_and_protocol(self):
        user = get_user_model().objects.create_user(username='helfer', password='testpass123')
        category = ProductCategory.objects.create(name='Festzelt')
        product = Product.objects.create(name='Bierzeltgarnitur', category=category, stock_quantity=10)
        borrower = Borrower.objects.create(name='Erika Beispiel', email='erika@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.RESERVED,
        )
        RentalCaseItem.objects.create(rental_case=case, product=product, quantity=2)
        protocol = Protocol.objects.create(
            rental_case=case,
            protocol_type=Protocol.ProtocolType.HANDOVER,
            performed_by=user,
        )

        self.assertEqual(case.items.count(), 1)
        self.assertEqual(protocol.protocol_type, Protocol.ProtocolType.HANDOVER)
