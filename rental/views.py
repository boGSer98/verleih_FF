import base64
import binascii
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .emailing import send_document_email
from .models import Borrower, Document, Product, Protocol, ProtocolPhoto, RentalCase, RentalCaseItem
from .pdf import create_or_replace_document, document_filename


def _admin_change_url(rental_case):
    return reverse('admin:rental_rentalcase_change', args=[rental_case.pk])


def _case_card(rental_case):
    item_summary = ', '.join(
        f'{item.quantity} × {item.product.name}'
        for item in rental_case.items.select_related('product')
    )
    return {
        'case': rental_case,
        'url': _admin_change_url(rental_case),
        'handover_url': reverse('rental:handover', args=[rental_case.pk]),
        'return_url': reverse('rental:return', args=[rental_case.pk]),
        'reservation_document_url': reverse('rental:reservation_document', args=[rental_case.pk]),
        'handover_document_url': reverse('rental:handover_document', args=[rental_case.pk]),
        'return_document_url': reverse('rental:return_document', args=[rental_case.pk]),
        'closing_document_url': reverse('rental:closing_document', args=[rental_case.pk]),
        'reservation_document_send_url': reverse('rental:reservation_document_send', args=[rental_case.pk]),
        'handover_document_send_url': reverse('rental:handover_document_send', args=[rental_case.pk]),
        'return_document_send_url': reverse('rental:return_document_send', args=[rental_case.pk]),
        'closing_document_send_url': reverse('rental:closing_document_send', args=[rental_case.pk]),
        'donation_received_url': reverse('rental:donation_received', args=[rental_case.pk]),
        'item_summary': item_summary or 'Noch keine Artikel erfasst',
    }


@login_required
@permission_required('rental.view_rentalcase', raise_exception=True)
def dashboard(request):
    today = timezone.localdate()
    search_query = request.GET.get('q', '').strip()
    cases = RentalCase.objects.select_related('borrower').prefetch_related('items__product')

    search_results = RentalCase.objects.none()
    if search_query:
        search_results = cases.filter(
            Q(number__icontains=search_query)
            | Q(borrower__name__icontains=search_query)
            | Q(borrower__organization__icontains=search_query)
            | Q(items__product__name__icontains=search_query)
            | Q(items__product__inventory_number__icontains=search_query)
        ).distinct().order_by('-updated_at', '-number')

    pickups_today = cases.filter(
        reserved_from__date=today,
        status__in=[RentalCase.Status.RESERVED, RentalCase.Status.PREPARED],
    ).order_by('reserved_from', 'number')
    returns_today = cases.filter(
        reserved_until__date=today,
        status__in=[
            RentalCase.Status.HANDED_OVER,
            RentalCase.Status.DONATION_OPEN,
            RentalCase.Status.DONATION_RECEIVED,
        ],
    ).order_by('reserved_until', 'number')
    donation_open = cases.filter(status=RentalCase.Status.DONATION_OPEN).order_by('reserved_until', 'number')
    clarification = cases.filter(status=RentalCase.Status.CLARIFICATION).order_by('reserved_until', 'number')
    recent_completed = cases.filter(status=RentalCase.Status.COMPLETED).order_by('-closed_at', '-updated_at', '-number')

    active_statuses = [
        RentalCase.Status.REQUEST,
        RentalCase.Status.RESERVED,
        RentalCase.Status.PREPARED,
        RentalCase.Status.HANDED_OVER,
        RentalCase.Status.DONATION_OPEN,
        RentalCase.Status.DONATION_RECEIVED,
        RentalCase.Status.RETURNED,
        RentalCase.Status.CLARIFICATION,
    ]
    status_counts = {
        row['status']: row['total']
        for row in cases.filter(status__in=active_statuses).values('status').annotate(total=Count('id'))
    }
    donation_totals = cases.aggregate(
        expected=Sum('expected_donation'),
        received=Sum('received_donation'),
    )

    context = {
        'today': today,
        'search_query': search_query,
        'search_results': [_case_card(case) for case in search_results[:10]],
        'pickups_today': [_case_card(case) for case in pickups_today],
        'returns_today': [_case_card(case) for case in returns_today],
        'donation_open': [_case_card(case) for case in donation_open[:10]],
        'clarification': [_case_card(case) for case in clarification[:10]],
        'recent_completed': [_case_card(case) for case in recent_completed[:5]],
        'status_counts': status_counts,
        'status_choices': RentalCase.Status.choices,
        'expected_donation_total': donation_totals['expected'] or 0,
        'received_donation_total': donation_totals['received'] or 0,
        'donation_decision_choices': RentalCase.DonationDecision.choices,
        'donation_payment_method_choices': RentalCase.DonationPaymentMethod.choices,
        'case_create_url': reverse('rental:case_create'),
        'calendar_url': reverse('rental:calendar'),
        'admin_case_add_url': reverse('admin:rental_rentalcase_add'),
        'admin_case_list_url': reverse('admin:rental_rentalcase_changelist'),
        'admin_product_list_url': reverse('admin:rental_product_changelist'),
    }
    return render(request, 'rental/dashboard.html', context)


def _parse_local_datetime(date_value, time_value, label):
    try:
        naive = datetime.strptime(f'{date_value} {time_value}', '%Y-%m-%d %H:%M')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} muss im Format Datum und HH:MM angegeben werden.') from exc
    return timezone.make_aware(naive, timezone.get_current_timezone())


@login_required
@permission_required(('rental.add_rentalcase', 'rental.add_borrower', 'rental.add_rentalcaseitem', 'rental.view_product'), raise_exception=True)
def case_create(request):
    products = Product.objects.filter(active=True).select_related('category').order_by('category__name', 'name')
    borrowers = Borrower.objects.order_by('name')
    values = {}
    errors = []

    if request.method == 'POST':
        values = request.POST.copy()
        try:
            borrower_id = request.POST.get('borrower')
            borrower = None
            borrower_data = None
            if borrower_id:
                borrower = Borrower.objects.get(pk=borrower_id)
            else:
                borrower_name = request.POST.get('borrower_name', '').strip()
                borrower_email = request.POST.get('borrower_email', '').strip()
                if not borrower_name or not borrower_email:
                    raise ValueError('Entleiher-Name und E-Mail sind erforderlich, wenn kein vorhandener Entleiher gewählt ist.')
                borrower_data = {
                    'name': borrower_name,
                    'organization': request.POST.get('borrower_organization', '').strip(),
                    'email': borrower_email,
                    'phone': request.POST.get('borrower_phone', '').strip(),
                }

            reserved_from = _parse_local_datetime(request.POST.get('reserved_from_date'), request.POST.get('reserved_from_time'), 'Beginn')
            reserved_until = _parse_local_datetime(request.POST.get('reserved_until_date'), request.POST.get('reserved_until_time'), 'Ende')
            product_rows = []
            for index in range(1, 6):
                product_id = request.POST.get(f'product_{index}')
                quantity_raw = request.POST.get(f'quantity_{index}', '').strip()
                if not product_id:
                    continue
                try:
                    quantity = int(quantity_raw or '1')
                except ValueError as exc:
                    raise ValueError(f'Menge in Position {index} muss eine ganze Zahl sein.') from exc
                product_rows.append((Product.objects.get(pk=product_id), quantity))
            if not product_rows:
                raise ValueError('Mindestens eine Artikelposition ist erforderlich.')

            with transaction.atomic():
                if borrower is None:
                    if borrower_data is None:
                        raise ValueError('Entleiherdaten fehlen.')
                    borrower = Borrower.objects.create(**borrower_data)
                rental_case = RentalCase.objects.create(
                    borrower=borrower,
                    reserved_from=reserved_from,
                    reserved_until=reserved_until,
                    status=RentalCase.Status.RESERVED,
                    notes=request.POST.get('notes', '').strip(),
                )
                rental_case.full_clean()
                for product, quantity in product_rows:
                    item = RentalCaseItem(rental_case=rental_case, product=product, quantity=quantity)
                    item.full_clean()
                    item.save()
            messages.success(request, f'Vorgang {rental_case.number} wurde angelegt.')
            return redirect('rental:dashboard')
        except (Borrower.DoesNotExist, Product.DoesNotExist):
            errors.append('Ausgewählter Entleiher oder Artikel wurde nicht gefunden.')
        except ValueError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))

    context = {
        'products': products,
        'borrowers': borrowers,
        'values': values,
        'errors': errors,
        'dashboard_url': reverse('rental:dashboard'),
    }
    return render(request, 'rental/case_form.html', context)


@login_required
@permission_required('rental.view_rentalcase', raise_exception=True)
def calendar(request):
    start = timezone.localdate() - timedelta(days=7)
    end = timezone.localdate() + timedelta(days=45)
    cases = RentalCase.objects.select_related('borrower').prefetch_related('items__product').filter(
        reserved_from__date__lte=end,
        reserved_until__date__gte=start,
    ).exclude(status__in=[RentalCase.Status.CANCELLED, RentalCase.Status.COMPLETED]).order_by('reserved_from', 'number')
    days = []
    current = start
    while current <= end:
        day_cases = [
            case for case in cases
            if timezone.localtime(case.reserved_from).date() <= current <= timezone.localtime(case.reserved_until).date()
        ]
        days.append({'date': current, 'cases': [_case_card(case) for case in day_cases]})
        current += timedelta(days=1)
    return render(request, 'rental/calendar.html', {
        'days': days,
        'dashboard_url': reverse('rental:dashboard'),
        'case_create_url': reverse('rental:case_create'),
    })


def _decode_signature(data_url, label):
    if not data_url:
        raise ValueError(f'{label} fehlt.')
    if not data_url.startswith('data:image/png;base64,'):
        raise ValueError(f'{label} muss als PNG-Signatur übermittelt werden.')
    try:
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f'{label} konnte nicht gelesen werden.') from exc
    if len(raw) < 100:
        raise ValueError(f'{label} ist leer oder unvollständig.')
    return ContentFile(raw, name=f'signature-{uuid.uuid4().hex}.png')


@login_required
@permission_required(('rental.view_rentalcase', 'rental.change_rentalcase', 'rental.change_rentalcaseitem', 'rental.add_protocol'), raise_exception=True)
def handover(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product'),
        pk=pk,
    )
    allowed_statuses = {RentalCase.Status.RESERVED, RentalCase.Status.PREPARED}
    if rental_case.status not in allowed_statuses:
        messages.error(request, 'Die mobile Übergabe ist nur für reservierte oder vorbereitete Vorgänge möglich.')
        return redirect(_admin_change_url(rental_case))

    if request.method == 'POST':
        error = None
        try:
            borrower_signature = _decode_signature(request.POST.get('borrower_signature_data', ''), 'Unterschrift Entleiher')
            club_signature = _decode_signature(request.POST.get('club_signature_data', ''), 'Unterschrift Verein')
        except ValueError as exc:
            error = str(exc)

        if not error:
            with transaction.atomic():
                for item in rental_case.items.all():
                    condition = request.POST.get(f'condition_{item.pk}', '').strip()
                    note = request.POST.get(f'note_{item.pk}', '').strip()
                    item.handover_condition = condition
                    if note:
                        item.notes = note
                    item.save(update_fields=['handover_condition', 'notes', 'updated_at'])

                protocol = Protocol.objects.create(
                    rental_case=rental_case,
                    protocol_type=Protocol.ProtocolType.HANDOVER,
                    performed_by=request.user,
                    notes=request.POST.get('notes', '').strip(),
                )
                protocol.borrower_signature.save(borrower_signature.name, borrower_signature, save=False)
                protocol.club_signature.save(club_signature.name, club_signature, save=False)
                protocol.save(update_fields=['borrower_signature', 'club_signature', 'updated_at'])

                if rental_case.status == RentalCase.Status.RESERVED:
                    rental_case.transition_to(RentalCase.Status.PREPARED)
                rental_case.transition_to(RentalCase.Status.HANDED_OVER)

            messages.success(request, 'Übergabeprotokoll gespeichert und Vorgang auf „Übergeben“ gesetzt.')
            return redirect(_admin_change_url(rental_case))
        messages.error(request, error)

    context = {
        'rental_case': rental_case,
        'admin_case_url': _admin_change_url(rental_case),
        'dashboard_url': reverse('rental:dashboard'),
    }
    return render(request, 'rental/handover.html', context)



def _validate_required_choice(post_data, field_name, label, allowed_values):
    value = post_data.get(field_name, '')
    if value not in allowed_values:
        raise ValueError(f'{label} muss bestätigt werden.')
    return value


@login_required
@permission_required(('rental.view_rentalcase', 'rental.change_rentalcase', 'rental.change_rentalcaseitem', 'rental.add_protocol'), raise_exception=True)
def return_case(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    allowed_statuses = {
        RentalCase.Status.HANDED_OVER,
        RentalCase.Status.DONATION_OPEN,
        RentalCase.Status.DONATION_RECEIVED,
    }
    if rental_case.status not in allowed_statuses:
        messages.error(request, 'Die mobile Rücknahme ist nur für übergebene Vorgänge möglich.')
        return redirect(_admin_change_url(rental_case))

    if request.method == 'POST':
        error = None
        has_issue = False
        try:
            _validate_required_choice(
                request.POST,
                'identity_confirmed',
                'Abschnitt „Entleiher und Vorgang geprüft“',
                {'yes'},
            )
            _validate_required_choice(
                request.POST,
                'all_items_checked',
                'Abschnitt „Alle Artikel einzeln geprüft“',
                {'yes'},
            )
            borrower_signature = _decode_signature(request.POST.get('borrower_signature_data', ''), 'Unterschrift Entleiher')
            club_signature = _decode_signature(request.POST.get('club_signature_data', ''), 'Unterschrift Verein')
        except ValueError as exc:
            error = str(exc)

        item_results = []
        if not error:
            try:
                for item in rental_case.items.all():
                    return_status = _validate_required_choice(
                        request.POST,
                        f'return_status_{item.pk}',
                        f'Rückgabestatus für {item.product.name}',
                        {'ok', 'missing', 'damaged', 'cleaning'},
                    )
                    accessory_status = _validate_required_choice(
                        request.POST,
                        f'accessory_status_{item.pk}',
                        f'Zubehörprüfung für {item.product.name}',
                        {'complete', 'missing', 'damaged', 'none'},
                    )
                    damage_amount_raw = request.POST.get(f'damage_amount_{item.pk}', '').strip().replace(',', '.')
                    try:
                        damage_amount = Decimal(damage_amount_raw or '0')
                    except InvalidOperation as exc:
                        raise ValueError(f'Schaden-/Klärbetrag für {item.product.name} ist ungültig.') from exc
                    item_results.append((item, return_status, accessory_status, damage_amount))
                    if return_status != 'ok' or accessory_status in {'missing', 'damaged'}:
                        has_issue = True
            except ValueError as exc:
                error = str(exc)

        if not error:
            with transaction.atomic():
                for item, return_status, accessory_status, damage_amount in item_results:
                    status_labels = {
                        'ok': 'Artikel vollständig und in Ordnung zurückgegeben.',
                        'missing': 'Artikel fehlt bei Rücknahme.',
                        'damaged': 'Artikel beschädigt zurückgegeben.',
                        'cleaning': 'Artikel mit Reinigungsbedarf zurückgegeben.',
                    }
                    accessory_labels = {
                        'complete': 'Zubehör vollständig und in Ordnung.',
                        'missing': 'Zubehör fehlt oder ist unvollständig.',
                        'damaged': 'Zubehör beschädigt.',
                        'none': 'Kein Zubehör zu prüfen.',
                    }
                    detail_note = request.POST.get(f'return_note_{item.pk}', '').strip()
                    condition_parts = [status_labels[return_status], accessory_labels[accessory_status]]
                    if detail_note:
                        condition_parts.append(detail_note)
                    item.return_condition = '\n'.join(condition_parts)
                    item.missing = return_status == 'missing' or accessory_status == 'missing'
                    item.damaged = return_status == 'damaged' or accessory_status == 'damaged'
                    item.damage_amount = damage_amount
                    item.save(update_fields=['return_condition', 'missing', 'damaged', 'damage_amount', 'updated_at'])

                protocol = Protocol.objects.create(
                    rental_case=rental_case,
                    protocol_type=Protocol.ProtocolType.RETURN,
                    performed_by=request.user,
                    notes=request.POST.get('notes', '').strip(),
                )
                protocol.borrower_signature.save(borrower_signature.name, borrower_signature, save=False)
                protocol.club_signature.save(club_signature.name, club_signature, save=False)
                protocol.save(update_fields=['borrower_signature', 'club_signature', 'updated_at'])
                photo_caption = request.POST.get('photo_caption', '').strip()
                for uploaded in request.FILES.getlist('return_photos'):
                    if uploaded.content_type and not uploaded.content_type.startswith('image/'):
                        continue
                    ProtocolPhoto.objects.create(protocol=protocol, image=uploaded, caption=photo_caption)

                target_status = RentalCase.Status.CLARIFICATION if has_issue else RentalCase.Status.RETURNED
                rental_case.transition_to(target_status)

            if has_issue:
                messages.warning(request, 'Rücknahme gespeichert. Wegen Fehlteilen, Schäden oder Reinigungsbedarf ist Klärung nötig.')
            else:
                messages.success(request, 'Rücknahmeprotokoll gespeichert und Vorgang auf „Zurückgenommen“ gesetzt.')
            return redirect(_admin_change_url(rental_case))
        messages.error(request, error)

    context = {
        'rental_case': rental_case,
        'admin_case_url': _admin_change_url(rental_case),
        'dashboard_url': reverse('rental:dashboard'),
    }
    return render(request, 'rental/return.html', context)


@login_required
@permission_required('rental.change_rentalcase', raise_exception=True)
def mark_donation_received(request, pk):
    rental_case = get_object_or_404(RentalCase.objects.select_related('borrower'), pk=pk)
    if request.method != 'POST':
        return HttpResponse('Spendenverbuchung erfordert POST.', status=405)

    if not rental_case.can_transition_to(RentalCase.Status.DONATION_RECEIVED):
        messages.error(request, 'Für diesen Vorgang kann aktuell keine Spende verbucht werden.')
        return redirect(_admin_change_url(rental_case))

    decision = request.POST.get('decision') or RentalCase.DonationDecision.RECEIVED
    if decision not in RentalCase.DonationDecision.values:
        messages.error(request, 'Die Spendenentscheidung ist ungültig.')
        return redirect(_admin_change_url(rental_case))

    payment_method = request.POST.get('payment_method', '').strip()
    if payment_method and payment_method not in RentalCase.DonationPaymentMethod.values:
        messages.error(request, 'Die Zahlungsart ist ungültig.')
        return redirect(_admin_change_url(rental_case))

    amount_raw = request.POST.get('amount', '').strip().replace(',', '.')
    try:
        amount = Decimal(amount_raw) if amount_raw else rental_case.expected_donation
    except InvalidOperation:
        messages.error(request, 'Der Spendenbetrag ist ungültig.')
        return redirect(_admin_change_url(rental_case))

    if decision == RentalCase.DonationDecision.WAIVED:
        amount = Decimal('0')
        payment_method = ''

    if amount < 0:
        messages.error(request, 'Der Spendenbetrag darf nicht negativ sein.')
        return redirect(_admin_change_url(rental_case))

    donation_note = request.POST.get('donation_note', '').strip()

    with transaction.atomic():
        rental_case.received_donation = amount
        rental_case.donation_decision = decision
        rental_case.donation_payment_method = payment_method
        rental_case.donation_note = donation_note
        rental_case.donation_received_at = timezone.now()
        rental_case.transition_to(RentalCase.Status.DONATION_RECEIVED, save=False)
        rental_case.save(update_fields=[
            'status',
            'received_donation',
            'donation_decision',
            'donation_payment_method',
            'donation_note',
            'donation_received_at',
            'closed_at',
            'updated_at',
        ])

    decision_label = RentalCase.DonationDecision(decision).label
    messages.success(request, f'Spendenentscheidung „{decision_label}“ über {amount} € wurde dokumentiert.')
    return redirect(_admin_change_url(rental_case))



@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document'), raise_exception=True)
def generate_reservation_document(request, pk):
    return _generate_document_response(
        request,
        pk,
        Document.DocumentType.RESERVATION,
        'Reservierungsbestätigung als PDF erzeugt.',
    )


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document'), raise_exception=True)
def generate_handover_document(request, pk):
    return _generate_document_response(
        request,
        pk,
        Document.DocumentType.HANDOVER,
        'Übergabeprotokoll als PDF erzeugt.',
    )


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document'), raise_exception=True)
def generate_return_document(request, pk):
    return _generate_document_response(
        request,
        pk,
        Document.DocumentType.RETURN,
        'Rücknahmeprotokoll als PDF erzeugt.',
    )


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document'), raise_exception=True)
def generate_closing_document(request, pk):
    return _generate_document_response(
        request,
        pk,
        Document.DocumentType.CLOSING,
        'Abschlussübersicht als PDF erzeugt.',
    )


def _generate_document_response(request, pk, document_type, success_message):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    document = create_or_replace_document(rental_case, document_type, request=request)
    messages.success(request, success_message)
    return redirect('rental:document_download', pk=document.pk)


def _send_generated_document_response(request, pk, document_type):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    if request.method != 'POST':
        return HttpResponse('Mailversand erfordert POST.', status=405)
    document = create_or_replace_document(rental_case, document_type, request=request)
    if send_document_email(document, request=request):
        messages.success(request, f'{document.get_document_type_display()} wurde an {document.sent_to} gesendet.')
    else:
        messages.error(request, f'{document.get_document_type_display()} konnte nicht gesendet werden: {document.send_error}')
    return redirect(_admin_change_url(rental_case))


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document', 'rental.change_document'), raise_exception=True)
def send_reservation_document(request, pk):
    return _send_generated_document_response(request, pk, Document.DocumentType.RESERVATION)


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document', 'rental.change_document'), raise_exception=True)
def send_handover_document(request, pk):
    return _send_generated_document_response(request, pk, Document.DocumentType.HANDOVER)


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document', 'rental.change_document'), raise_exception=True)
def send_return_document(request, pk):
    return _send_generated_document_response(request, pk, Document.DocumentType.RETURN)


@login_required
@permission_required(('rental.view_rentalcase', 'rental.add_document', 'rental.change_document'), raise_exception=True)
def send_closing_document(request, pk):
    return _send_generated_document_response(request, pk, Document.DocumentType.CLOSING)


@login_required
@permission_required('rental.change_document', raise_exception=True)
def send_document(request, pk):
    document = get_object_or_404(
        Document.objects.select_related('rental_case__borrower'),
        pk=pk,
    )
    if request.method != 'POST':
        return HttpResponse('Mailversand erfordert POST.', status=405)
    if send_document_email(document, request=request):
        messages.success(request, f'Dokument wurde an {document.sent_to} gesendet.')
    else:
        messages.error(request, f'Dokument konnte nicht gesendet werden: {document.send_error}')
    return redirect(_admin_change_url(document.rental_case))


@login_required
@permission_required('rental.view_document', raise_exception=True)
def document_download(request, pk):
    document = get_object_or_404(Document.objects.select_related('rental_case'), pk=pk)
    if not document.file:
        return HttpResponse('Dokumentdatei fehlt.', status=404)
    return FileResponse(
        document.file.open('rb'),
        content_type='application/pdf',
        as_attachment=False,
        filename=document_filename(document.rental_case, document.document_type),
    )
