import base64
import binascii
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import Protocol, RentalCase


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
