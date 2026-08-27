import base64
import binascii
import calendar as calendar_module
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
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
        'url': reverse('rental:case_detail', args=[rental_case.pk]),
        'admin_url': _admin_change_url(rental_case),
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
        'complete_url': reverse('rental:case_complete', args=[rental_case.pk]),
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


def _case_form_item_indices(post_data=None):
    if not post_data:
        return [1, 2, 3]
    indices = {
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r'^(?:product|quantity)_(\d+)$', key)]
        if match
    }
    return sorted(indices or {1, 2, 3})


def _borrower_form_data(post_data):
    return {
        'name': post_data.get('borrower_name', '').strip(),
        'organization': post_data.get('borrower_organization', '').strip(),
        'email': post_data.get('borrower_email', '').strip(),
        'phone': post_data.get('borrower_phone', '').strip(),
        'street': post_data.get('borrower_street', '').strip(),
        'postal_code': post_data.get('borrower_postal_code', '').strip(),
        'city': post_data.get('borrower_city', '').strip(),
        'notes': post_data.get('borrower_notes', '').strip(),
    }


def _borrower_values(borrower):
    return {
        'borrower_name': borrower.name,
        'borrower_organization': borrower.organization,
        'borrower_email': borrower.email,
        'borrower_phone': borrower.phone,
        'borrower_street': borrower.street,
        'borrower_postal_code': borrower.postal_code,
        'borrower_city': borrower.city,
        'borrower_notes': borrower.notes,
    }


@login_required
@permission_required(('rental.add_rentalcase', 'rental.add_borrower', 'rental.change_borrower', 'rental.add_rentalcaseitem', 'rental.view_product'), raise_exception=True)
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
            borrower_data = _borrower_form_data(request.POST)
            if not borrower_data['name'] or not borrower_data['email']:
                raise ValueError('Entleiher-Name und E-Mail sind erforderlich.')
            if borrower_id:
                borrower = Borrower.objects.get(pk=borrower_id)
            else:
                borrower = Borrower(**borrower_data)

            reserved_from = _parse_local_datetime(request.POST.get('reserved_from_date'), request.POST.get('reserved_from_time'), 'Beginn')
            reserved_until = _parse_local_datetime(request.POST.get('reserved_until_date'), request.POST.get('reserved_until_time'), 'Ende')
            product_rows = []
            for index in _case_form_item_indices(request.POST):
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
                for field, value in borrower_data.items():
                    setattr(borrower, field, value)
                borrower.full_clean()
                borrower.save()
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
            return redirect('rental:case_detail', pk=rental_case.pk)
        except (Borrower.DoesNotExist, Product.DoesNotExist):
            errors.append('Ausgewählter Entleiher oder Artikel wurde nicht gefunden.')
        except ValueError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))

    item_indices = _case_form_item_indices(request.POST if request.method == 'POST' else None)
    item_rows = [
        {
            'index': index,
            'product': values.get(f'product_{index}', '') if values else '',
            'quantity': values.get(f'quantity_{index}', '1') if values else '1',
        }
        for index in item_indices
    ]
    context = {
        'products': products,
        'borrowers': borrowers,
        'borrower_data': [
            {'id': str(borrower.pk), **_borrower_values(borrower)}
            for borrower in borrowers
        ],
        'values': values,
        'errors': errors,
        'item_rows': item_rows,
        'dashboard_url': reverse('rental:dashboard'),
    }
    return render(request, 'rental/case_form.html', context)


@login_required
@permission_required('rental.view_rentalcase', raise_exception=True)
def case_detail(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related(
            'items__product__category',
            'items__product__accessories',
            'documents',
            'protocols__photos',
        ),
        pk=pk,
    )
    documents_by_type = {document.document_type: document for document in rental_case.documents.all()}
    latest_handover_protocol = next(
        (protocol for protocol in rental_case.protocols.all() if protocol.protocol_type == Protocol.ProtocolType.HANDOVER),
        None,
    )
    latest_return_protocol = next(
        (protocol for protocol in rental_case.protocols.all() if protocol.protocol_type == Protocol.ProtocolType.RETURN),
        None,
    )
    can_complete = rental_case.status in {
        RentalCase.Status.RETURNED,
        RentalCase.Status.DONATION_RECEIVED,
        RentalCase.Status.CLARIFICATION,
    }
    context = {
        'card': _case_card(rental_case),
        'documents_by_type': documents_by_type,
        'latest_handover_protocol': latest_handover_protocol,
        'latest_return_protocol': latest_return_protocol,
        'can_complete': can_complete,
        'needs_donation_decision': rental_case.has_open_donation_decision(),
        'needs_clarification_resolution': rental_case.status == RentalCase.Status.CLARIFICATION,
        'donation_decision_choices': [
            choice for choice in RentalCase.DonationDecision.choices
            if choice[0] != RentalCase.DonationDecision.OPEN
        ],
        'donation_payment_method_choices': RentalCase.DonationPaymentMethod.choices,
        'dashboard_url': reverse('rental:dashboard'),
        'calendar_url': reverse('rental:calendar'),
        'admin_url': _admin_change_url(rental_case),
        'document_types': Document.DocumentType,
    }
    return render(request, 'rental/case_detail.html', context)


def _calendar_month_from_request(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        current_month = date(year, month, 1)
    except (TypeError, ValueError):
        current_month = date(today.year, today.month, 1)
    return current_month


def _shift_month(month_date, months):
    month_index = month_date.month - 1 + months
    year = month_date.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


@login_required
@permission_required('rental.view_rentalcase', raise_exception=True)
def calendar(request):
    current_month = _calendar_month_from_request(request)
    previous_month = _shift_month(current_month, -1)
    next_month = _shift_month(current_month, 1)
    month_grid = calendar_module.Calendar(firstweekday=0).monthdatescalendar(current_month.year, current_month.month)
    visible_start = month_grid[0][0]
    visible_end = month_grid[-1][-1]
    cases = list(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product').filter(
            reserved_from__date__lte=visible_end,
            reserved_until__date__gte=visible_start,
        ).exclude(status__in=[RentalCase.Status.CANCELLED, RentalCase.Status.COMPLETED]).order_by('reserved_from', 'number')
    )
    weeks = []
    today = timezone.localdate()
    for week in month_grid:
        week_days = []
        for current_day in week:
            day_cases = [
                case for case in cases
                if timezone.localtime(case.reserved_from).date() <= current_day <= timezone.localtime(case.reserved_until).date()
            ]
            week_days.append({
                'date': current_day,
                'is_current_month': current_day.month == current_month.month,
                'is_today': current_day == today,
                'cases': [_case_card(case) for case in day_cases],
            })
        weeks.append(week_days)
    return render(request, 'rental/calendar.html', {
        'current_month': current_month,
        'previous_month': previous_month,
        'next_month': next_month,
        'weekdays': ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'],
        'weeks': weeks,
        'month_cases': [_case_card(case) for case in cases],
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
            return redirect('rental:case_detail', pk=rental_case.pk)
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
            return redirect('rental:case_detail', pk=rental_case.pk)
        messages.error(request, error)

    context = {
        'rental_case': rental_case,
        'admin_case_url': _admin_change_url(rental_case),
        'dashboard_url': reverse('rental:dashboard'),
    }
    return render(request, 'rental/return.html', context)


def _apply_donation_decision_from_post(request, rental_case):
    decision = request.POST.get('decision') or RentalCase.DonationDecision.RECEIVED
    if decision not in RentalCase.DonationDecision.values or decision == RentalCase.DonationDecision.OPEN:
        raise ValueError('Die Spendenentscheidung ist ungültig.')

    payment_method = request.POST.get('payment_method', '').strip()
    if payment_method and payment_method not in RentalCase.DonationPaymentMethod.values:
        raise ValueError('Die Zahlungsart ist ungültig.')

    amount_raw = request.POST.get('amount', '').strip().replace(',', '.')
    try:
        amount = Decimal(amount_raw) if amount_raw else rental_case.expected_donation
    except InvalidOperation as exc:
        raise ValueError('Der Spendenbetrag ist ungültig.') from exc

    if decision == RentalCase.DonationDecision.WAIVED:
        amount = Decimal('0')
        payment_method = ''

    if amount < 0:
        raise ValueError('Der Spendenbetrag darf nicht negativ sein.')

    rental_case.received_donation = amount
    rental_case.donation_decision = decision
    rental_case.donation_payment_method = payment_method
    rental_case.donation_note = request.POST.get('donation_note', '').strip()
    rental_case.donation_received_at = timezone.now()
    return amount, RentalCase.DonationDecision(decision).label


@login_required
@permission_required('rental.change_rentalcase', raise_exception=True)
def mark_donation_received(request, pk):
    rental_case = get_object_or_404(RentalCase.objects.select_related('borrower'), pk=pk)
    if request.method != 'POST':
        return HttpResponse('Spendenverbuchung erfordert POST.', status=405)

    if not rental_case.can_transition_to(RentalCase.Status.DONATION_RECEIVED):
        messages.error(request, 'Für diesen Vorgang kann aktuell keine Spende verbucht werden.')
        return redirect(_admin_change_url(rental_case))

    try:
        amount, decision_label = _apply_donation_decision_from_post(request, rental_case)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('rental:case_detail', pk=rental_case.pk)

    with transaction.atomic():
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

    messages.success(request, f'Spendenentscheidung „{decision_label}“ über {amount} € wurde dokumentiert.')
    return redirect('rental:case_detail', pk=rental_case.pk)


@login_required
@permission_required(('rental.view_rentalcase', 'rental.change_rentalcase', 'rental.add_document'), raise_exception=True)
def complete_case(request, pk):
    rental_case = get_object_or_404(RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'), pk=pk)
    if request.method != 'POST':
        return HttpResponse('Vorgangsabschluss erfordert POST.', status=405)

    allowed_statuses = {
        RentalCase.Status.RETURNED,
        RentalCase.Status.DONATION_RECEIVED,
        RentalCase.Status.CLARIFICATION,
    }
    if rental_case.status not in allowed_statuses:
        messages.error(request, 'Dieser Vorgang kann aktuell nicht abgeschlossen werden.')
        return redirect('rental:case_detail', pk=rental_case.pk)

    if rental_case.status == RentalCase.Status.CLARIFICATION and request.POST.get('issues_resolved') != 'yes':
        messages.error(request, 'Klärfälle können erst abgeschlossen werden, wenn die Klärung bestätigt wurde.')
        return redirect('rental:case_detail', pk=rental_case.pk)

    update_fields = ['status', 'closed_at', 'updated_at']
    try:
        if rental_case.has_open_donation_decision():
            _apply_donation_decision_from_post(request, rental_case)
            update_fields.extend([
                'received_donation',
                'donation_decision',
                'donation_payment_method',
                'donation_note',
                'donation_received_at',
            ])
        closing_note = request.POST.get('closing_note', '').strip()
        if closing_note:
            rental_case.notes = (rental_case.notes + '\n\n' if rental_case.notes else '') + f'Abschluss: {closing_note}'
            update_fields.append('notes')
        with transaction.atomic():
            rental_case.transition_to(RentalCase.Status.COMPLETED, save=False)
            rental_case.save(update_fields=update_fields)
            create_or_replace_document(rental_case, Document.DocumentType.CLOSING, request=request)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect('rental:case_detail', pk=rental_case.pk)

    messages.success(request, f'Vorgang {rental_case.number} wurde abgeschlossen und die Abschlussübersicht erzeugt.')
    return redirect('rental:case_detail', pk=rental_case.pk)


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
