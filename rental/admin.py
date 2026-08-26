from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import Borrower, Document, Product, ProductAccessory, ProductCategory, Protocol, RentalCase, RentalCaseItem
from .pdf import create_or_replace_document


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']


class ProductAccessoryInline(admin.TabularInline):
    model = ProductAccessory
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'stock_quantity', 'status', 'storage_location', 'active', 'can_be_reserved']
    list_filter = ['active', 'status', 'category']
    search_fields = ['name', 'inventory_number', 'storage_location']
    inlines = [ProductAccessoryInline]
    fieldsets = [
        ('Stammdaten', {'fields': ['name', 'category', 'inventory_number', 'description', 'active', 'status']}),
        ('Bestand und Lager', {'fields': ['stock_quantity', 'storage_location', 'condition_note']}),
        ('Beträge', {'fields': ['suggested_donation', 'deposit_amount', 'replacement_value']}),
    ]


@admin.register(ProductAccessory)
class ProductAccessoryAdmin(admin.ModelAdmin):
    list_display = ['product', 'name', 'quantity', 'required']
    list_filter = ['required', 'product__category']
    search_fields = ['product__name', 'name']


@admin.register(Borrower)
class BorrowerAdmin(admin.ModelAdmin):
    list_display = ['name', 'organization', 'email', 'phone', 'city']
    search_fields = ['name', 'organization', 'email', 'phone', 'city']


class RentalCaseItemInline(admin.TabularInline):
    model = RentalCaseItem
    extra = 1


class ProtocolInline(admin.TabularInline):
    model = Protocol
    extra = 0
    readonly_fields = ['created_at', 'updated_at']


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    readonly_fields = ['created_at', 'updated_at']


@admin.register(RentalCase)
class RentalCaseAdmin(admin.ModelAdmin):
    list_display = ['number', 'borrower', 'reserved_from', 'reserved_until', 'status', 'expected_donation', 'received_donation', 'closed_at']
    list_filter = ['status', 'reserved_from', 'reserved_until']
    search_fields = ['number', 'borrower__name', 'borrower__email']
    readonly_fields = ['number', 'created_at', 'updated_at', 'closed_at']
    date_hierarchy = 'reserved_from'
    inlines = [RentalCaseItemInline, ProtocolInline, DocumentInline]
    actions = [
        'mark_reserved',
        'mark_prepared',
        'mark_handed_over',
        'mark_donation_received',
        'mark_returned',
        'mark_completed',
        'mark_cancelled',
        'generate_reservation_documents',
        'generate_handover_documents',
    ]

    def _transition_selection(self, request, queryset, target_status):
        changed = 0
        errors = []
        for rental_case in queryset:
            try:
                rental_case.transition_to(target_status)
                changed += 1
            except ValidationError as exc:
                errors.append(f'{rental_case}: {exc.message}')
        if changed:
            self.message_user(request, f'{changed} Vorgang/Vorgänge aktualisiert.', messages.SUCCESS)
        for error in errors:
            self.message_user(request, error, messages.ERROR)

    @admin.action(description='Status auf „Reserviert“ setzen')
    def mark_reserved(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.RESERVED)

    @admin.action(description='Status auf „Abholung vorbereitet“ setzen')
    def mark_prepared(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.PREPARED)

    @admin.action(description='Status auf „Übergeben“ setzen')
    def mark_handed_over(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.HANDED_OVER)

    @admin.action(description='Status auf „Spende erhalten“ setzen')
    def mark_donation_received(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.DONATION_RECEIVED)

    @admin.action(description='Status auf „Zurückgenommen“ setzen')
    def mark_returned(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.RETURNED)

    @admin.action(description='Status auf „Abgeschlossen“ setzen')
    def mark_completed(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.COMPLETED)

    @admin.action(description='Status auf „Storniert“ setzen')
    def mark_cancelled(self, request, queryset):
        self._transition_selection(request, queryset, RentalCase.Status.CANCELLED)

    @admin.action(description='Reservierungsbestätigung als PDF erzeugen')
    def generate_reservation_documents(self, request, queryset):
        created = 0
        for rental_case in queryset.prefetch_related('items__product__accessories'):
            create_or_replace_document(rental_case, Document.DocumentType.RESERVATION, request=request)
            created += 1
        self.message_user(request, f'{created} Reservierungsbestätigung(en) erzeugt.', messages.SUCCESS)

    @admin.action(description='Übergabeprotokoll als PDF erzeugen')
    def generate_handover_documents(self, request, queryset):
        created = 0
        for rental_case in queryset.prefetch_related('items__product__accessories'):
            create_or_replace_document(rental_case, Document.DocumentType.HANDOVER, request=request)
            created += 1
        self.message_user(request, f'{created} Übergabeprotokoll(e) erzeugt.', messages.SUCCESS)


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ['rental_case', 'protocol_type', 'performed_at', 'performed_by']
    list_filter = ['protocol_type', 'performed_at']
    search_fields = ['rental_case__number', 'rental_case__borrower__name']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['rental_case', 'document_type', 'sent_to', 'sent_at']
    list_filter = ['document_type', 'sent_at']
    search_fields = ['rental_case__number', 'sent_to']
