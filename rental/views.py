import base64
import binascii
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import Document, Protocol, RentalCase
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
        'item_summary': item_summary or 'Noch keine Artikel erfasst',
    }


@login_required
def dashboard(request):
    today = timezone.localdate()
    cases = RentalCase.objects.select_related('borrower').prefetch_related('items__product')

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
        'pickups_today': [_case_card(case) for case in pickups_today],
        'returns_today': [_case_card(case) for case in returns_today],
        'donation_open': [_case_card(case) for case in donation_open[:10]],
        'clarification': [_case_card(case) for case in clarification[:10]],
        'status_counts': status_counts,
        'status_choices': RentalCase.Status.choices,
        'expected_donation_total': donation_totals['expected'] or 0,
        'received_donation_total': donation_totals['received'] or 0,
        'admin_case_add_url': reverse('admin:rental_rentalcase_add'),
        'admin_case_list_url': reverse('admin:rental_rentalcase_changelist'),
        'admin_product_list_url': reverse('admin:rental_product_changelist'),
    }
    return render(request, 'rental/dashboard.html', context)


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
def generate_reservation_document(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    document = create_or_replace_document(
        rental_case,
        Document.DocumentType.RESERVATION,
        request=request,
    )
    messages.success(request, 'Reservierungsbestätigung als PDF erzeugt.')
    return redirect('rental:document_download', pk=document.pk)


@login_required
def generate_handover_document(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    document = create_or_replace_document(
        rental_case,
        Document.DocumentType.HANDOVER,
        request=request,
    )
    messages.success(request, 'Übergabeprotokoll als PDF erzeugt.')
    return redirect('rental:document_download', pk=document.pk)


@login_required
def generate_return_document(request, pk):
    rental_case = get_object_or_404(
        RentalCase.objects.select_related('borrower').prefetch_related('items__product__accessories'),
        pk=pk,
    )
    document = create_or_replace_document(
        rental_case,
        Document.DocumentType.RETURN,
        request=request,
    )
    messages.success(request, 'Rücknahmeprotokoll als PDF erzeugt.')
    return redirect('rental:document_download', pk=document.pk)


@login_required
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
