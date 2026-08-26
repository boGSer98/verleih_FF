from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from weasyprint import HTML

from .models import Document


DOCUMENT_TYPE_TO_TEMPLATE = {
    Document.DocumentType.RESERVATION: 'rental/pdfs/reservation.html',
}

DOCUMENT_TYPE_TO_FILENAME = {
    Document.DocumentType.RESERVATION: 'reservierungsbestaetigung',
}


def document_filename(rental_case, document_type):
    prefix = DOCUMENT_TYPE_TO_FILENAME[document_type]
    safe_number = (rental_case.number or f'vorgang-{rental_case.pk}').lower().replace('/', '-').replace(' ', '-')
    return f'{prefix}-{safe_number}.pdf'


def render_document_pdf(rental_case, document_type, *, request=None):
    template_name = DOCUMENT_TYPE_TO_TEMPLATE[document_type]
    context = {
        'rental_case': rental_case,
        'items': rental_case.items.select_related('product').prefetch_related('product__accessories'),
        'generated_at': timezone.localtime(),
        'document_type': document_type,
    }
    html = render_to_string(template_name, context=context, request=request)
    base_url = request.build_absolute_uri('/') if request else None
    return HTML(string=html, base_url=base_url).write_pdf()


def create_or_replace_document(rental_case, document_type, *, request=None):
    pdf_bytes = render_document_pdf(rental_case, document_type, request=request)
    document = rental_case.documents.filter(document_type=document_type).first()
    if document is None:
        document = Document(rental_case=rental_case, document_type=document_type)
    document.file.save(document_filename(rental_case, document_type), ContentFile(pdf_bytes), save=True)
    return document


def document_download_url(document):
    return reverse('rental:document_download', args=[document.pk])
