from django.contrib import admin

from .models import Borrower, Document, Product, ProductCategory, Protocol, RentalCase, RentalCaseItem


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'stock_quantity', 'storage_location', 'active']
    list_filter = ['active', 'category']
    search_fields = ['name', 'inventory_number', 'storage_location']


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
    list_display = ['number', 'borrower', 'reserved_from', 'reserved_until', 'status', 'expected_donation', 'received_donation']
    list_filter = ['status', 'reserved_from', 'reserved_until']
    search_fields = ['number', 'borrower__name', 'borrower__email']
    date_hierarchy = 'reserved_from'
    inlines = [RentalCaseItemInline, ProtocolInline, DocumentInline]


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
