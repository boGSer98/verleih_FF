import base64

from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from weasyprint import HTML

from .models import Document, Protocol


DOCUMENT_TYPE_TO_TEMPLATE = {
    Document.DocumentType.RESERVATION: 'rental/pdfs/reservation.html',
    Document.DocumentType.HANDOVER: 'rental/pdfs/handover.html',
    Document.DocumentType.RETURN: 'rental/pdfs/return.html',
}

DOCUMENT_TYPE_TO_FILENAME = {
    Document.DocumentType.RESERVATION: 'reservierungsbestaetigung',
    Document.DocumentType.HANDOVER: 'uebergabeprotokoll',
    Document.DocumentType.RETURN: 'ruecknahmeprotokoll',
}

DOCUMENT_TYPE_TO_PROTOCOL_TYPE = {
    Document.DocumentType.HANDOVER: Protocol.ProtocolType.HANDOVER,
    Document.DocumentType.RETURN: Protocol.ProtocolType.RETURN,
}


def document_filename(rental_case, document_type):
    prefix = DOCUMENT_TYPE_TO_FILENAME[document_type]
    safe_number = (rental_case.number or f'vorgang-{rental_case.pk}').lower().replace('/', '-').replace(' ', '-')
    return f'{prefix}-{safe_number}.pdf'


def _signature_data_url(image_field):
    if not image_field:
        return ''
    image_field.open('rb')
    try:
        raw = image_field.read()
    finally:
        image_field.close()
    if not raw:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')


def _latest_protocol(rental_case, document_type):
    protocol_type = DOCUMENT_TYPE_TO_PROTOCOL_TYPE.get(document_type)
    if not protocol_type:
        return None
    return rental_case.protocols.filter(protocol_type=protocol_type).select_related('performed_by').first()


def render_document_pdf(rental_case, document_type, *, request=None):
    template_name = DOCUMENT_TYPE_TO_TEMPLATE[document_type]
    protocol = _latest_protocol(rental_case, document_type)
    context = {
        'rental_case': rental_case,
        'items': rental_case.items.select_related('product').prefetch_related('product__accessories'),
        'generated_at': timezone.localtime(),
        'document_type': document_type,
        'protocol': protocol,
        'borrower_signature_data_url': _signature_data_url(protocol.borrower_signature) if protocol else '',
        'club_signature_data_url': _signature_data_url(protocol.club_signature) if protocol else '',
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
