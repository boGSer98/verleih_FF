from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import Borrower, Product, ProductAccessory, ProductCategory, Protocol, RentalCase, RentalCaseItem


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

    def test_product_accessories_and_reservable_status(self):
        category = ProductCategory.objects.create(name='Kühlung')
        product = Product.objects.create(name='Kühlschrank', category=category, stock_quantity=1)
        ProductAccessory.objects.create(product=product, name='Stromkabel', quantity=1)

        self.assertTrue(product.can_be_reserved)
        self.assertEqual(product.accessories.count(), 1)

        product.status = Product.Status.DEFECTIVE
        self.assertFalse(product.can_be_reserved)

    def test_inactive_or_defective_product_cannot_be_added_to_case(self):
        category = ProductCategory.objects.create(name='Pavillon')
        product = Product.objects.create(name='Faltpavillon', category=category, status=Product.Status.MAINTENANCE)
        borrower = Borrower.objects.create(name='Test Entleiher', email='test@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
        )
        item = RentalCaseItem(rental_case=case, product=product, quantity=1)

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_status_transition_sets_closed_at(self):
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.RETURNED,
        )

        case.transition_to(RentalCase.Status.COMPLETED)

        self.assertEqual(case.status, RentalCase.Status.COMPLETED)
        self.assertIsNotNone(case.closed_at)

    def test_invalid_status_transition_is_blocked(self):
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.REQUEST,
        )

        with self.assertRaises(ValidationError):
            case.transition_to(RentalCase.Status.COMPLETED)
