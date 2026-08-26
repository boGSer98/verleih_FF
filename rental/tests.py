import base64
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Borrower, Document, Product, ProductAccessory, ProductCategory, Protocol, RentalCase, RentalCaseItem
from .pdf import create_or_replace_document


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
        self.assertIn(reverse('rental:reservation_document_send', args=[pickup.pk]), content)
        self.assertIn('Reservierung mailen', content)


class HandoverViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='helfer2', password='testpass123')
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
