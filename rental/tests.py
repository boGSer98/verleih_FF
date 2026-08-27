import base64
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Borrower, Document, Product, ProductAccessory, ProductCategory, Protocol, ProtocolPhoto, RentalCase, RentalCaseItem
from .pdf import create_or_replace_document
from .permissions import (
    GROUP_ADMIN,
    GROUP_HELPERS,
    GROUP_MANAGEMENT,
    GROUP_READONLY,
    permission_codes_for_group,
)


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

    def test_completion_is_blocked_while_donation_decision_is_open(self):
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.RETURNED,
            expected_donation=25,
            donation_decision=RentalCase.DonationDecision.OPEN,
        )

        with self.assertRaisesMessage(ValidationError, 'Spendenentscheidung dokumentiert'):
            case.transition_to(RentalCase.Status.COMPLETED)

        self.assertEqual(case.status, RentalCase.Status.RETURNED)
        self.assertIsNone(case.closed_at)

    def test_completion_is_allowed_after_donation_decision(self):
        borrower = Borrower.objects.create(name='Max Muster', email='max@example.org')
        case = RentalCase.objects.create(
            borrower=borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.RETURNED,
            expected_donation=25,
            donation_decision=RentalCase.DonationDecision.WAIVED,
            donation_note='Vorstand verzichtet.',
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


class RentalPermissionGroupTests(TestCase):
    def _group_permission_codes(self, group_name):
        return set(
            Group.objects.get(name=group_name)
            .permissions.filter(content_type__app_label='rental')
            .values_list('codename', flat=True)
        )

    def test_sample_groups_are_created_with_expected_permissions(self):
        for group_name in [GROUP_ADMIN, GROUP_MANAGEMENT, GROUP_HELPERS, GROUP_READONLY]:
            with self.subTest(group_name=group_name):
                self.assertTrue(Group.objects.filter(name=group_name).exists())
                self.assertEqual(self._group_permission_codes(group_name), permission_codes_for_group(group_name))

    def test_helper_group_has_process_permissions_but_no_delete_permissions(self):
        permissions = self._group_permission_codes(GROUP_HELPERS)

        self.assertIn('view_rentalcase', permissions)
        self.assertIn('change_rentalcase', permissions)
        self.assertIn('change_rentalcaseitem', permissions)
        self.assertIn('add_protocol', permissions)
        self.assertIn('add_document', permissions)
        self.assertNotIn('delete_rentalcase', permissions)
        self.assertNotIn('delete_document', permissions)

    def test_readonly_group_only_has_view_permissions(self):
        permissions = self._group_permission_codes(GROUP_READONLY)

        self.assertTrue(permissions)
        self.assertTrue(all(code.startswith('view_') for code in permissions))


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='helfer', password='testpass123')
        self.user.groups.add(Group.objects.get(name=GROUP_HELPERS))
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
        completed = self._create_case(status=RentalCase.Status.COMPLETED)
        completed.closed_at = timezone.now()
        completed.save(update_fields=['closed_at', 'updated_at'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:dashboard'))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)
        self.assertIn('Abholung heute', content)
        self.assertIn('Rücknahme heute', content)
        self.assertIn('Spende offen', content)
        self.assertIn('Klärung nötig', content)
        self.assertIn('Kürzlich abgeschlossen', content)
        self.assertIn(pickup.number, content)
        self.assertIn(returned_due.number, content)
        self.assertIn(donation.number, content)
        self.assertIn(clarification.number, content)
        self.assertIn(completed.number, content)
        self.assertIn('min-height: 54px', content)
        self.assertIn(reverse('rental:reservation_document_send', args=[pickup.pk]), content)
        self.assertIn('Reservierung mailen', content)
        self.assertIn(reverse('rental:donation_received', args=[donation.pk]), content)
        self.assertIn('Spendenentscheidung speichern', content)
        self.assertIn('name="decision"', content)
        self.assertIn('Teilweise erhalten', content)
        self.assertIn('name="payment_method"', content)
        self.assertIn('Überweisung', content)
        self.assertIn('name="donation_note"', content)
        self.assertIn(reverse('rental:case_create'), content)
        self.assertIn(reverse('rental:calendar'), content)
        self.assertIn(reverse('rental:case_detail', args=[pickup.pk]), content)

    def test_case_detail_page_shows_mobile_actions_documents_and_protocol_context(self):
        case = self._create_case(status=RentalCase.Status.HANDED_OVER)
        protocol = Protocol.objects.create(
            rental_case=case,
            protocol_type=Protocol.ProtocolType.RETURN,
            performed_by=self.user,
            notes='Rücknahme mit Schadensfoto dokumentiert.',
        )
        ProtocolPhoto.objects.create(
            protocol=protocol,
            image=SimpleUploadedFile('schaden.jpg', b'fake-image-bytes', content_type='image/jpeg'),
            caption='Kratzer am Gestell',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:case_detail', args=[case.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)
        self.assertIn(f'Vorgang {case.number}', content)
        self.assertIn('Nächste Aktionen', content)
        self.assertIn('Rücknahme starten', content)
        self.assertIn(reverse('rental:return', args=[case.pk]), content)
        self.assertIn(reverse('rental:reservation_document', args=[case.pk]), content)
        self.assertIn('Dokumente & Mailversand', content)
        self.assertIn('Rücknahmefotos', content)
        self.assertIn('Kratzer am Gestell', content)

    def test_case_detail_requires_login(self):
        case = self._create_case(status=RentalCase.Status.RESERVED)

        response = self.client.get(reverse('rental:case_detail', args=[case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_case_create_page_uses_minute_time_inputs_and_creates_reserved_case(self):
        manager = get_user_model().objects.create_user(username='verwaltung', password='testpass123')
        manager.groups.add(Group.objects.get(name=GROUP_MANAGEMENT))
        start = timezone.localtime(timezone.now()).replace(hour=10, minute=15, second=0, microsecond=0)
        end = start + timezone.timedelta(hours=3)
        self.client.force_login(manager)

        response = self.client.get(reverse('rental:case_create'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('type="time"', content)
        self.assertIn('step="60"', content)
        self.assertIn('Format: HH:MM', content)
        self.assertIn('Weitere Artikelposition hinzufügen', content)
        self.assertIn('Vorgang speichern und öffnen', content)

        response = self.client.post(reverse('rental:case_create'), {
            'borrower_name': 'Web Entleiher',
            'borrower_email': 'web@example.org',
            'reserved_from_date': start.date().isoformat(),
            'reserved_from_time': start.strftime('%H:%M'),
            'reserved_until_date': end.date().isoformat(),
            'reserved_until_time': end.strftime('%H:%M'),
            'product_1': str(self.product.pk),
            'quantity_1': '2',
            'notes': 'per Webseite angelegt',
        })

        rental_case = RentalCase.objects.get(borrower__name='Web Entleiher')
        self.assertRedirects(response, reverse('rental:case_detail', args=[rental_case.pk]))
        self.assertEqual(rental_case.status, RentalCase.Status.RESERVED)
        self.assertEqual(rental_case.reserved_from.second, 0)
        self.assertEqual(rental_case.items.get().quantity, 2)

    def test_case_create_accepts_dynamically_added_item_rows(self):
        manager = get_user_model().objects.create_user(username='verwaltung-dynamisch', password='testpass123')
        manager.groups.add(Group.objects.get(name=GROUP_MANAGEMENT))
        extra_product = Product.objects.create(name='Stehtisch', category=self.category, stock_quantity=4)
        start = timezone.localtime(timezone.now()).replace(hour=11, minute=0, second=0, microsecond=0)
        end = start + timezone.timedelta(hours=2)
        self.client.force_login(manager)

        response = self.client.post(reverse('rental:case_create'), {
            'borrower_name': 'Dynamischer Entleiher',
            'borrower_email': 'dynamisch@example.org',
            'reserved_from_date': start.date().isoformat(),
            'reserved_from_time': start.strftime('%H:%M'),
            'reserved_until_date': end.date().isoformat(),
            'reserved_until_time': end.strftime('%H:%M'),
            'product_1': str(self.product.pk),
            'quantity_1': '1',
            'product_6': str(extra_product.pk),
            'quantity_6': '3',
        })

        rental_case = RentalCase.objects.get(borrower__name='Dynamischer Entleiher')
        self.assertRedirects(response, reverse('rental:case_detail', args=[rental_case.pk]))
        self.assertEqual(rental_case.items.count(), 2)
        self.assertEqual(rental_case.items.get(product=extra_product).quantity, 3)

    def test_calendar_shows_month_grid_with_active_cases(self):
        current_month = timezone.localdate().replace(day=1)
        start = timezone.make_aware(timezone.datetime.combine(current_month, timezone.datetime.min.time())).replace(hour=9)
        case = self._create_case(status=RentalCase.Status.RESERVED, start=start, end=start + timezone.timedelta(days=2))
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:calendar'), {'year': current_month.year, 'month': current_month.month})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Kalenderübersicht', content)
        self.assertIn('Monatskalender', content)
        for weekday in ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']:
            self.assertIn(f'<div class="weekday">{weekday}</div>', content)
        self.assertIn('Vorheriger Monat', content)
        self.assertIn('Nächster Monat', content)
        self.assertIn('case-chip', content)
        self.assertIn(case.number, content)
        self.assertIn('Aktive Vorgänge im sichtbaren Kalender', content)
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)

    def test_dashboard_search_finds_case_by_number_borrower_and_product(self):
        matching = self._create_case(status=RentalCase.Status.RESERVED)
        other_borrower = Borrower.objects.create(name='Andere Gruppe', email='andere@example.org')
        other_start = timezone.now() + timezone.timedelta(days=7)
        other = RentalCase.objects.create(
            borrower=other_borrower,
            reserved_from=other_start,
            reserved_until=other_start + timezone.timedelta(hours=4),
            status=RentalCase.Status.RESERVED,
        )
        other_product = Product.objects.create(name='Klapptisch', category=self.category, stock_quantity=3)
        RentalCaseItem.objects.create(rental_case=other, product=other_product, quantity=1)
        self.client.force_login(self.user)

        for query in [matching.number, 'Förderverein Muster', 'Bierzeltgarnitur']:
            with self.subTest(query=query):
                response = self.client.get(reverse('rental:dashboard'), {'q': query})

                self.assertEqual(response.status_code, 200)
                content = response.content.decode('utf-8')
                self.assertIn('Suchergebnisse', content)
                self.assertIn(matching.number, content)
                self.assertNotIn(other.number, content)

    def test_dashboard_search_requires_login(self):
        response = self.client.get(reverse('rental:dashboard'), {'q': 'Bierzeltgarnitur'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_dashboard_requires_view_permission_after_login(self):
        user_without_group = get_user_model().objects.create_user(username='ohne_rechte', password='testpass123')
        self.client.force_login(user_without_group)

        response = self.client.get(reverse('rental:dashboard'))

        self.assertEqual(response.status_code, 403)

    def test_donation_received_action_requires_login(self):
        donation = self._create_case(status=RentalCase.Status.DONATION_OPEN)

        response = self.client.post(reverse('rental:donation_received', args=[donation.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])
        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_OPEN)

    def test_donation_received_action_books_expected_amount(self):
        donation = self._create_case(status=RentalCase.Status.DONATION_OPEN)
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:donation_received', args=[donation.pk]))

        self.assertEqual(response.status_code, 302)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_RECEIVED)
        self.assertEqual(donation.received_donation, Decimal('25.00'))
        self.assertEqual(donation.donation_decision, RentalCase.DonationDecision.RECEIVED)
        self.assertEqual(donation.donation_payment_method, '')
        self.assertIsNotNone(donation.donation_received_at)

    def test_donation_received_action_accepts_manual_amount(self):
        donation = self._create_case(status=RentalCase.Status.DONATION_OPEN)
        self.client.force_login(self.user)

        self.client.post(reverse('rental:donation_received', args=[donation.pk]), {
            'amount': '30,50',
            'decision': RentalCase.DonationDecision.PARTIAL,
            'payment_method': RentalCase.DonationPaymentMethod.BANK_TRANSFER,
            'donation_note': 'Teilbetrag vorab überwiesen.',
        })

        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_RECEIVED)
        self.assertEqual(donation.received_donation, Decimal('30.50'))
        self.assertEqual(donation.donation_decision, RentalCase.DonationDecision.PARTIAL)
        self.assertEqual(donation.donation_payment_method, RentalCase.DonationPaymentMethod.BANK_TRANSFER)
        self.assertEqual(donation.donation_note, 'Teilbetrag vorab überwiesen.')

    def test_donation_received_action_documents_waiver_without_amount_or_payment_method(self):
        donation = self._create_case(status=RentalCase.Status.DONATION_OPEN)
        self.client.force_login(self.user)

        self.client.post(reverse('rental:donation_received', args=[donation.pk]), {
            'amount': '25,00',
            'decision': RentalCase.DonationDecision.WAIVED,
            'payment_method': RentalCase.DonationPaymentMethod.CASH,
            'donation_note': 'Vorstand verzichtet auf Spende.',
        })

        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_RECEIVED)
        self.assertEqual(donation.received_donation, Decimal('0.00'))
        self.assertEqual(donation.donation_decision, RentalCase.DonationDecision.WAIVED)
        self.assertEqual(donation.donation_payment_method, '')
        self.assertEqual(donation.donation_note, 'Vorstand verzichtet auf Spende.')

    def test_donation_received_action_rejects_invalid_decision_and_payment_method(self):
        donation = self._create_case(status=RentalCase.Status.DONATION_OPEN)
        self.client.force_login(self.user)

        self.client.post(reverse('rental:donation_received', args=[donation.pk]), {
            'decision': 'unknown',
            'payment_method': RentalCase.DonationPaymentMethod.CASH,
        })
        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_OPEN)
        self.assertEqual(donation.donation_decision, RentalCase.DonationDecision.OPEN)

        self.client.post(reverse('rental:donation_received', args=[donation.pk]), {
            'decision': RentalCase.DonationDecision.RECEIVED,
            'payment_method': 'crypto',
        })
        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.DONATION_OPEN)
        self.assertEqual(donation.donation_payment_method, '')

    def test_donation_received_action_rejects_invalid_status(self):
        donation = self._create_case(status=RentalCase.Status.RETURNED)
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:donation_received', args=[donation.pk]))

        self.assertEqual(response.status_code, 302)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RentalCase.Status.RETURNED)
        self.assertEqual(donation.received_donation, Decimal('10.00'))


class HandoverViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='helfer2', password='testpass123')
        self.user.groups.add(Group.objects.get(name=GROUP_HELPERS))
        self.category = ProductCategory.objects.create(name='Übergabeausstattung')
        self.product = Product.objects.create(
            name='Pavillon',
            category=self.category,
            stock_quantity=2,
            storage_location='Garage',
        )
        self.borrower = Borrower.objects.create(name='Erika Beispiel', email='erika@example.org')
        self.case = RentalCase.objects.create(
            borrower=self.borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(hours=6),
            status=RentalCase.Status.PREPARED,
        )
        self.item = RentalCaseItem.objects.create(rental_case=self.case, product=self.product, quantity=1)
        self.signature_data = 'data:image/png;base64,' + base64.b64encode(b'png-signature-bytes' * 10).decode('ascii')

    def test_handover_requires_login(self):
        response = self.client.get(reverse('rental:handover', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_handover_page_is_mobile_signature_form(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:handover', args=[self.case.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)
        self.assertIn('Unterschrift Entleiher', content)
        self.assertIn('Unterschrift Verein / Helfer', content)
        self.assertIn('touch-action:none', content)
        self.assertIn('min-height:54px', content)

    def test_handover_post_creates_protocol_signatures_and_updates_status(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:handover', args=[self.case.pk]), {
            f'condition_{self.item.pk}': 'vollständig und sauber',
            f'note_{self.item.pk}': 'direkt vor Ort geprüft',
            'notes': 'Übergabe am Vereinsheim',
            'borrower_signature_data': self.signature_data,
            'club_signature_data': self.signature_data,
        })

        self.assertEqual(response.status_code, 302)
        self.case.refresh_from_db()
        self.item.refresh_from_db()
        protocol = self.case.protocols.get(protocol_type=Protocol.ProtocolType.HANDOVER)
        self.assertEqual(self.case.status, RentalCase.Status.HANDED_OVER)
        self.assertEqual(self.item.handover_condition, 'vollständig und sauber')
        self.assertEqual(self.item.notes, 'direkt vor Ort geprüft')
        self.assertEqual(protocol.notes, 'Übergabe am Vereinsheim')
        self.assertTrue(protocol.borrower_signature.name.startswith('signatures/signature-'))
        self.assertTrue(protocol.club_signature.name.startswith('signatures/signature-'))

    def test_handover_post_requires_signatures(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:handover', args=[self.case.pk]), {
            f'condition_{self.item.pk}': 'vollständig',
            'borrower_signature_data': '',
            'club_signature_data': '',
        })

        self.assertEqual(response.status_code, 200)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, RentalCase.Status.PREPARED)
        self.assertEqual(self.case.protocols.count(), 0)


class ReturnViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ruecknahme', password='testpass123')
        self.user.groups.add(Group.objects.get(name=GROUP_HELPERS))
        self.category = ProductCategory.objects.create(name='Rückgabeausstattung')
        self.product = Product.objects.create(
            name='Bierzeltgarnitur',
            category=self.category,
            stock_quantity=4,
            storage_location='Halle',
        )
        ProductAccessory.objects.create(product=self.product, name='Tischplatte', quantity=1)
        ProductAccessory.objects.create(product=self.product, name='Bank', quantity=2)
        self.borrower = Borrower.objects.create(name='Max Rückgabe', email='max@example.org')
        self.case = RentalCase.objects.create(
            borrower=self.borrower,
            reserved_from=timezone.now() - timezone.timedelta(days=1),
            reserved_until=timezone.now(),
            status=RentalCase.Status.HANDED_OVER,
        )
        self.item = RentalCaseItem.objects.create(
            rental_case=self.case,
            product=self.product,
            quantity=1,
            handover_condition='vollständig und sauber',
        )
        self.signature_data = 'data:image/png;base64,' + base64.b64encode(b'return-signature-bytes' * 10).decode('ascii')

    def _valid_post_data(self, **overrides):
        data = {
            'identity_confirmed': 'yes',
            'all_items_checked': 'yes',
            f'return_status_{self.item.pk}': 'ok',
            f'accessory_status_{self.item.pk}': 'complete',
            f'damage_amount_{self.item.pk}': '0',
            f'return_note_{self.item.pk}': 'vor Ort geprüft',
            'notes': 'Rücknahme am Vereinsheim',
            'borrower_signature_data': self.signature_data,
            'club_signature_data': self.signature_data,
        }
        data.update(overrides)
        return data

    def test_return_requires_login(self):
        response = self.client.get(reverse('rental:return', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_return_page_is_guided_mobile_signature_form(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:return', args=[self.case.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', content)
        self.assertIn('Geführte Rücknahme', content)
        self.assertIn('Entleiher und Vorgang geprüft', content)
        self.assertIn('Artikel, Schäden und Zubehör prüfen', content)
        self.assertIn('Alle Artikel, Schäden und Zubehöre wurden geprüft', content)
        self.assertIn('data-step="2" disabled', content)
        self.assertIn('touch-action:none', content)
        self.assertIn('Tischplatte', content)
        self.assertIn('type="file"', content)
        self.assertIn('accept="image/*"', content)
        self.assertIn('capture="environment"', content)

    def test_return_post_without_issues_sets_returned_and_creates_protocol(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:return', args=[self.case.pk]), self._valid_post_data())

        self.assertEqual(response.status_code, 302)
        self.case.refresh_from_db()
        self.item.refresh_from_db()
        protocol = self.case.protocols.get(protocol_type=Protocol.ProtocolType.RETURN)
        self.assertEqual(self.case.status, RentalCase.Status.RETURNED)
        self.assertFalse(self.item.missing)
        self.assertFalse(self.item.damaged)
        self.assertIn('Artikel vollständig', self.item.return_condition)
        self.assertIn('Zubehör vollständig', self.item.return_condition)
        self.assertEqual(protocol.notes, 'Rücknahme am Vereinsheim')
        self.assertTrue(protocol.borrower_signature.name.startswith('signatures/signature-'))
        self.assertTrue(protocol.club_signature.name.startswith('signatures/signature-'))

    def test_return_post_stores_uploaded_damage_photos(self):
        self.client.force_login(self.user)
        upload = SimpleUploadedFile('schaden.jpg', b'fake-image-bytes', content_type='image/jpeg')
        data = self._valid_post_data(photo_caption='Delle an Tischplatte')
        data['return_photos'] = upload

        response = self.client.post(reverse('rental:return', args=[self.case.pk]), data)

        self.assertEqual(response.status_code, 302)
        protocol = self.case.protocols.get(protocol_type=Protocol.ProtocolType.RETURN)
        photo = protocol.photos.get()
        self.assertEqual(photo.caption, 'Delle an Tischplatte')
        self.assertTrue(photo.image.name.startswith('protocol-photos/'))

    def test_return_post_with_damage_sets_clarification(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:return', args=[self.case.pk]), self._valid_post_data(
            **{
                f'return_status_{self.item.pk}': 'damaged',
                f'accessory_status_{self.item.pk}': 'damaged',
                f'damage_amount_{self.item.pk}': '12.50',
                f'return_note_{self.item.pk}': 'Bank gebrochen',
            }
        ))

        self.assertEqual(response.status_code, 302)
        self.case.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.case.status, RentalCase.Status.CLARIFICATION)
        self.assertTrue(self.item.damaged)
        self.assertEqual(self.item.damage_amount, Decimal('12.50'))
        self.assertIn('Zubehör beschädigt', self.item.return_condition)
        self.assertIn('Bank gebrochen', self.item.return_condition)

    def test_return_post_requires_guided_confirmations_and_signatures(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:return', args=[self.case.pk]), self._valid_post_data(
            identity_confirmed='',
            borrower_signature_data='',
        ))

        self.assertEqual(response.status_code, 200)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, RentalCase.Status.HANDED_OVER)
        self.assertEqual(self.case.protocols.count(), 0)


class DocumentPdfTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='dokumente', password='testpass123')
        self.user.groups.add(Group.objects.get(name=GROUP_HELPERS))
        self.category = ProductCategory.objects.create(name='PDF-Ausstattung')
        self.product = Product.objects.create(
            name='Getränkekühlschrank',
            category=self.category,
            stock_quantity=1,
            storage_location='Lager 2',
        )
        ProductAccessory.objects.create(product=self.product, name='Stromkabel', quantity=1)
        self.borrower = Borrower.objects.create(
            name='PDF Entleiher',
            organization='Förderverein PDF',
            email='pdf@example.org',
            street='Musterstraße 1',
            postal_code='12345',
            city='Musterstadt',
        )
        self.case = RentalCase.objects.create(
            borrower=self.borrower,
            reserved_from=timezone.now(),
            reserved_until=timezone.now() + timezone.timedelta(days=1),
            status=RentalCase.Status.RESERVED,
            expected_donation=20,
            notes='Bitte sauber zurückgeben.',
        )
        RentalCaseItem.objects.create(rental_case=self.case, product=self.product, quantity=1)

    def _create_handover_protocol(self):
        protocol = Protocol.objects.create(
            rental_case=self.case,
            protocol_type=Protocol.ProtocolType.HANDOVER,
            performed_by=self.user,
            notes='Übergabe vor Ort protokolliert.',
        )
        protocol.borrower_signature.save('borrower.png', ContentFile(b'borrower-signature-bytes' * 10), save=False)
        protocol.club_signature.save('club.png', ContentFile(b'club-signature-bytes' * 10), save=False)
        protocol.save(update_fields=['borrower_signature', 'club_signature', 'updated_at'])
        self.case.items.update(handover_condition='sauber und vollständig', notes='Zubehör geprüft')
        return protocol

    def _create_return_protocol(self):
        protocol = Protocol.objects.create(
            rental_case=self.case,
            protocol_type=Protocol.ProtocolType.RETURN,
            performed_by=self.user,
            notes='Rücknahme mit Klärbetrag protokolliert.',
        )
        protocol.borrower_signature.save('borrower-return.png', ContentFile(b'borrower-return-signature-bytes' * 10), save=False)
        protocol.club_signature.save('club-return.png', ContentFile(b'club-return-signature-bytes' * 10), save=False)
        protocol.save(update_fields=['borrower_signature', 'club_signature', 'updated_at'])
        self.case.items.update(
            return_condition=(
                'Artikel beschädigt zurückgegeben.\n'
                'Zubehör vollständig und in Ordnung.\n'
                'Delle an Tür.'
            ),
            damaged=True,
            damage_amount=Decimal('12.50'),
        )
        return protocol

    def test_reservation_pdf_is_generated_and_stored_as_document(self):
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        self.assertEqual(document.document_type, Document.DocumentType.RESERVATION)
        self.assertTrue(document.file.name.startswith('documents/reservierungsbestaetigung-'))
        document.file.open('rb')
        self.assertEqual(document.file.read(4), b'%PDF')
        document.file.close()

    def test_handover_pdf_is_generated_with_latest_protocol_and_signatures(self):
        self._create_handover_protocol()

        document = create_or_replace_document(self.case, Document.DocumentType.HANDOVER)

        self.assertEqual(document.document_type, Document.DocumentType.HANDOVER)
        self.assertTrue(document.file.name.startswith('documents/uebergabeprotokoll-'))
        document.file.open('rb')
        self.assertEqual(document.file.read(4), b'%PDF')
        document.file.close()

    def test_return_pdf_is_generated_with_latest_protocol_and_clarification_amount(self):
        self._create_return_protocol()

        document = create_or_replace_document(self.case, Document.DocumentType.RETURN)

        self.assertEqual(document.document_type, Document.DocumentType.RETURN)
        self.assertTrue(document.file.name.startswith('documents/ruecknahmeprotokoll-'))
        document.file.open('rb')
        self.assertEqual(document.file.read(4), b'%PDF')
        document.file.close()

    def test_closing_pdf_is_generated_with_case_summary(self):
        self._create_handover_protocol()
        self._create_return_protocol()
        self.case.status = RentalCase.Status.COMPLETED
        self.case.closed_at = timezone.now()
        self.case.received_donation = Decimal('20.00')
        self.case.save(update_fields=['status', 'closed_at', 'received_donation', 'updated_at'])

        document = create_or_replace_document(self.case, Document.DocumentType.CLOSING)

        self.assertEqual(document.document_type, Document.DocumentType.CLOSING)
        self.assertTrue(document.file.name.startswith('documents/abschlussuebersicht-'))
        document.file.open('rb')
        self.assertEqual(document.file.read(4), b'%PDF')
        document.file.close()

    def test_reservation_document_route_requires_login(self):
        response = self.client.get(reverse('rental:reservation_document', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_reservation_document_route_creates_pdf_and_redirects_to_download(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:reservation_document', args=[self.case.pk]))

        document = self.case.documents.get(document_type=Document.DocumentType.RESERVATION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('rental:document_download', args=[document.pk]))

    def test_handover_document_route_requires_login(self):
        response = self.client.get(reverse('rental:handover_document', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_handover_document_route_creates_pdf_and_redirects_to_download(self):
        self._create_handover_protocol()
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:handover_document', args=[self.case.pk]))

        document = self.case.documents.get(document_type=Document.DocumentType.HANDOVER)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('rental:document_download', args=[document.pk]))

    def test_return_document_route_requires_login(self):
        response = self.client.get(reverse('rental:return_document', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_return_document_route_creates_pdf_and_redirects_to_download(self):
        self._create_return_protocol()
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:return_document', args=[self.case.pk]))

        document = self.case.documents.get(document_type=Document.DocumentType.RETURN)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('rental:document_download', args=[document.pk]))

    def test_closing_document_route_requires_login(self):
        response = self.client.get(reverse('rental:closing_document', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_closing_document_route_creates_pdf_and_redirects_to_download(self):
        self._create_handover_protocol()
        self._create_return_protocol()
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:closing_document', args=[self.case.pk]))

        document = self.case.documents.get(document_type=Document.DocumentType.CLOSING)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('rental:document_download', args=[document.pk]))

    def test_document_download_returns_pdf_inline(self):
        self.client.force_login(self.user)
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        response = self.client.get(reverse('rental:document_download', args=[document.pk]))
        body = b''.join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('inline;'))
        self.assertEqual(body[:4], b'%PDF')

    def test_document_send_requires_login(self):
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        response = self.client.post(reverse('rental:document_send', args=[document.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_document_send_rejects_get(self):
        self.client.force_login(self.user)
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        response = self.client.get(reverse('rental:document_send', args=[document.pk]))

        self.assertEqual(response.status_code, 405)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='verein@example.org')
    def test_document_send_emails_pdf_attachment_and_records_status(self):
        self.client.force_login(self.user)
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        response = self.client.post(reverse('rental:document_send', args=[document.pk]))

        document.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('admin:rental_rentalcase_change', args=[self.case.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.borrower.email])
        self.assertEqual(mail.outbox[0].attachments[0][2], 'application/pdf')
        self.assertEqual(document.sent_to, self.borrower.email)
        self.assertIsNotNone(document.sent_at)
        self.assertEqual(document.send_error, '')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_document_send_records_missing_recipient_error(self):
        self.borrower.email = ''
        self.borrower.save(update_fields=['email', 'updated_at'])
        self.client.force_login(self.user)
        document = create_or_replace_document(self.case, Document.DocumentType.RESERVATION)

        response = self.client.post(reverse('rental:document_send', args=[document.pk]))

        document.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('Keine E-Mail-Adresse', document.send_error)

    def test_generated_document_send_requires_login(self):
        response = self.client.post(reverse('rental:reservation_document_send', args=[self.case.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_generated_document_send_rejects_get(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('rental:reservation_document_send', args=[self.case.pk]))

        self.assertEqual(response.status_code, 405)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='verein@example.org')
    def test_generated_document_send_creates_pdf_and_emails_it(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('rental:reservation_document_send', args=[self.case.pk]))

        document = self.case.documents.get(document_type=Document.DocumentType.RESERVATION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('admin:rental_rentalcase_change', args=[self.case.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.borrower.email])
        self.assertEqual(mail.outbox[0].attachments[0][2], 'application/pdf')
        self.assertEqual(document.sent_to, self.borrower.email)
        self.assertIsNotNone(document.sent_at)
        self.assertEqual(document.send_error, '')
