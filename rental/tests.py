from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Borrower, Product, ProductAccessory, ProductCategory, Protocol, RentalCase, RentalCaseItem


def create_case(borrower, start, end, status=RentalCase.Status.RESERVED):
    return RentalCase.objects.create(
        borrower=borrower,
        reserved_from=start,
        reserved_until=end,
        status=status,
    )


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

    def test_overlapping_reserved_quantity_reduces_availability(self):
        category = ProductCategory.objects.create(name='Tische')
        product = Product.objects.create(name='Stehtisch', category=category, stock_quantity=5)
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        start = timezone.now()
        end = start + timezone.timedelta(days=1)
        case = create_case(borrower, start, end)
        RentalCaseItem.objects.create(rental_case=case, product=product, quantity=3)

        self.assertEqual(product.reserved_quantity(start + timezone.timedelta(hours=1), end), 3)
        self.assertEqual(product.available_quantity(start + timezone.timedelta(hours=1), end), 2)
        self.assertTrue(product.is_available(2, start + timezone.timedelta(hours=1), end))
        self.assertFalse(product.is_available(3, start + timezone.timedelta(hours=1), end))

    def test_non_overlapping_or_cancelled_cases_do_not_block_availability(self):
        category = ProductCategory.objects.create(name='Kabel')
        product = Product.objects.create(name='Kabeltrommel', category=category, stock_quantity=2)
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        start = timezone.now()
        end = start + timezone.timedelta(days=1)
        cancelled = create_case(borrower, start, end, status=RentalCase.Status.CANCELLED)
        later = create_case(borrower, end + timezone.timedelta(hours=1), end + timezone.timedelta(days=2))
        RentalCaseItem.objects.create(rental_case=cancelled, product=product, quantity=2)
        RentalCaseItem.objects.create(rental_case=later, product=product, quantity=2)

        self.assertEqual(product.reserved_quantity(start, end), 0)
        self.assertEqual(product.available_quantity(start, end), 2)

    def test_overbooking_is_blocked_for_overlapping_period(self):
        category = ProductCategory.objects.create(name='Bänke')
        product = Product.objects.create(name='Bierzeltbank', category=category, stock_quantity=4)
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        start = timezone.now()
        end = start + timezone.timedelta(days=1)
        existing_case = create_case(borrower, start, end)
        new_case = create_case(borrower, start + timezone.timedelta(hours=2), end + timezone.timedelta(hours=2), status=RentalCase.Status.REQUEST)
        RentalCaseItem.objects.create(rental_case=existing_case, product=product, quantity=3)
        item = RentalCaseItem(rental_case=new_case, product=product, quantity=2)

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_current_case_is_excluded_when_validating_existing_item(self):
        category = ProductCategory.objects.create(name='Geschirr')
        product = Product.objects.create(name='Teller', category=category, stock_quantity=10)
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        start = timezone.now()
        end = start + timezone.timedelta(days=1)
        case = create_case(borrower, start, end)
        item = RentalCaseItem.objects.create(rental_case=case, product=product, quantity=10)

        item.full_clean()
        self.assertEqual(product.available_quantity(start, end, exclude_case=case), 10)


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='helfer', password='testpass123')
        self.category = ProductCategory.objects.create(name='Mobile Ausstattung')
        self.product = Product.objects.create(name='Bierzeltgarnitur', category=self.category, stock_quantity=10)
        self.borrower = Borrower.objects.create(
            name='Förderverein Muster',
            organization='Förderverein',
            email='kontakt@example.org',
        )

    def _create_case(self, *, status, start=None, end=None):
        start = start or timezone.now()
        end = end or start + timezone.timedelta(hours=4)
        case = RentalCase.objects.create(
            borrower=self.borrower,
            reserved_from=start,
            reserved_until=end,
            status=status,
            expected_donation=25,
            received_donation=10,
        )
        RentalCaseItem.objects.create(rental_case=case, product=self.product, quantity=2)
        return case

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('rental:dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_dashboard_groups_mobile_process_lanes(self):
        today_start = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        pickup = self._create_case(status=RentalCase.Status.PREPARED, start=today_start)
        returned_due = self._create_case(
            status=RentalCase.Status.HANDED_OVER,
            start=today_start - timezone.timedelta(days=1),
            end=today_start + timezone.timedelta(hours=2),
        )
        donation = self._create_case(
            status=RentalCase.Status.DONATION_OPEN,
            start=today_start - timezone.timedelta(days=2),
            end=today_start - timezone.timedelta(days=1),
        )
        clarification = self._create_case(status=RentalCase.Status.CLARIFICATION)
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:dashboard'))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)
        self.assertIn('Abholung heute', content)
        self.assertIn('Rücknahme heute', content)
        self.assertIn('Spende offen', content)
        self.assertIn('Klärung nötig', content)
        self.assertIn(pickup.number, content)
        self.assertIn(returned_due.number, content)
        self.assertIn(donation.number, content)
        self.assertIn(clarification.number, content)
        self.assertIn('min-height: 54px', content)
