from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from .emailing import send_document_email
from .models import Borrower, Document, DonationReceipt, DonationReceiptIssuerProfile, Product, ProductAccessory, ProductCategory, Protocol, ProtocolPhoto, RentalCase, RentalCaseItem
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


class ProtocolPhotoInline(admin.TabularInline):
    model = ProtocolPhoto
    extra = 0
    fields = ['rental_case_item', 'image', 'caption', 'created_at', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']


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
    list_display = [
        'number',
        'borrower',
        'reserved_from',
        'reserved_until',
        'status',
        'donation_decision',
        'donation_payment_method',
        'expected_donation',
        'received_donation',
        'closed_at',
    ]
    list_filter = ['status', 'donation_decision', 'donation_payment_method', 'reserved_from', 'reserved_until']
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
        'generate_return_documents',
        'generate_closing_documents',
    ]

    def _transition_selection(self, request, queryset, target_status):
        changed = 0
        errors = []
        for rental_case in queryset:
            try:
                if target_status == RentalCase.Status.DONATION_RECEIVED:
                    rental_case.transition_to(target_status, save=False)
                    rental_case.received_donation = rental_case.expected_donation
                    rental_case.donation_decision = RentalCase.DonationDecision.RECEIVED
                    rental_case.donation_received_at = timezone.now()
                    rental_case.save(update_fields=[
                        'status',
                        'received_donation',
                        'donation_decision',
                        'donation_received_at',
                        'closed_at',
                        'updated_at',
                    ])
                else:
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

    @admin.action(description='Rücknahmeprotokoll als PDF erzeugen')
    def generate_return_documents(self, request, queryset):
        created = 0
        for rental_case in queryset.prefetch_related('items__product__accessories'):
            create_or_replace_document(rental_case, Document.DocumentType.RETURN, request=request)
            created += 1
        self.message_user(request, f'{created} Rücknahmeprotokoll(e) erzeugt.', messages.SUCCESS)

    @admin.action(description='Abschlussübersicht als PDF erzeugen')
    def generate_closing_documents(self, request, queryset):
        created = 0
        for rental_case in queryset.prefetch_related('items__product__accessories'):
            create_or_replace_document(rental_case, Document.DocumentType.CLOSING, request=request)
            created += 1
        self.message_user(request, f'{created} Abschlussübersicht(en) erzeugt.', messages.SUCCESS)


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ['rental_case', 'protocol_type', 'performed_at', 'performed_by', 'borrower_signature_name', 'club_signature_name']
    list_filter = ['protocol_type', 'performed_at']
    search_fields = ['rental_case__number', 'rental_case__borrower__name', 'borrower_signature_name', 'club_signature_name']
    inlines = [ProtocolPhotoInline]


@admin.register(ProtocolPhoto)
class ProtocolPhotoAdmin(admin.ModelAdmin):
    list_display = ['protocol', 'rental_case_item', 'caption', 'created_at']
    list_filter = ['protocol__protocol_type', 'created_at']
    search_fields = ['protocol__rental_case__number', 'rental_case_item__product__name', 'caption']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['rental_case', 'document_type', 'sent_to', 'sent_at']
    list_filter = ['document_type', 'sent_at']
    search_fields = ['rental_case__number', 'sent_to']
    actions = ['send_documents_by_email']

    @admin.action(description='Dokumente per E-Mail an Entleiher senden')
    def send_documents_by_email(self, request, queryset):
        sent = 0
        failed = 0
        for document in queryset.select_related('rental_case__borrower'):
            if send_document_email(document, request=request):
                sent += 1
            else:
                failed += 1
                self.message_user(
                    request,
                    f'{document}: Versand fehlgeschlagen: {document.send_error}',
                    messages.ERROR,
                )
        if sent:
            self.message_user(request, f'{sent} Dokument(e) per E-Mail versendet.', messages.SUCCESS)
        if failed:
            self.message_user(request, f'{failed} Dokument(e) konnten nicht versendet werden.', messages.WARNING)


@admin.register(DonationReceiptIssuerProfile)
class DonationReceiptIssuerProfileAdmin(admin.ModelAdmin):
    list_display = ['name', 'tax_office', 'tax_number', 'active', 'updated_at']
    list_filter = ['active', 'membership_fees_deductible']
    search_fields = ['name', 'tax_office', 'tax_number', 'city']
    fieldsets = [
        ('Verein / Aussteller', {'fields': ['name', 'street', 'postal_code', 'city', 'active']}),
        ('Finanzamt / Bescheide', {'fields': ['tax_office', 'tax_number', 'exemption_notice_date', 'determination_notice_date']}),
        ('Steuerbegünstigte Zwecke', {'fields': ['statutory_purposes', 'membership_fees_deductible']}),
        ('Ausstellung', {'fields': ['default_issue_place', 'default_signer_name', 'default_signer_function']}),
    ]


@admin.register(DonationReceipt)
class DonationReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'rental_case', 'donor_name', 'donation_amount', 'donation_date', 'status', 'issued_at', 'issued_by']
    list_filter = ['status', 'donation_date', 'issued_at', 'is_membership_fee', 'is_expense_reimbursement_waiver']
    search_fields = ['receipt_number', 'rental_case__number', 'donor_name', 'donor_organization', 'donor_email', 'issuer_name']
    readonly_fields = ['receipt_number', 'document', 'donation_amount_words', 'issued_at', 'issued_by', 'cancelled_at', 'cancelled_by', 'created_at', 'updated_at']
    fieldsets = [
        ('Status', {'fields': ['rental_case', 'document', 'receipt_number', 'status', 'issued_at', 'issued_by', 'cancelled_at', 'cancelled_by', 'cancel_reason']}),
        ('Zuwendender', {'fields': ['donor_name', 'donor_organization', 'donor_street', 'donor_postal_code', 'donor_city', 'donor_email']}),
        ('Zuwendung', {'fields': ['donation_amount', 'donation_amount_words', 'donation_date', 'is_membership_fee', 'is_expense_reimbursement_waiver']}),
        ('Verein / Aussteller', {'fields': ['issuer_name', 'issuer_street', 'issuer_postal_code', 'issuer_city', 'tax_office', 'tax_number', 'exemption_notice_date', 'determination_notice_date', 'statutory_purposes', 'membership_fees_deductible']}),
        ('Ausstellung', {'fields': ['issue_place', 'signer_name', 'signer_function']}),
        ('Technik', {'fields': ['created_at', 'updated_at']}),
    ]

    def has_change_permission(self, request, obj=None):
        if obj and obj.status == DonationReceipt.Status.ISSUED:
            return request.user.has_perm('rental.can_issue_donation_receipt')
        return super().has_change_permission(request, obj=obj)
