from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import RentalCase


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
