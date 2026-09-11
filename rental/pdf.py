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
    Document.DocumentType.CLOSING: 'rental/pdfs/closing.html',
    Document.DocumentType.DONATION_RECEIPT: 'rental/pdfs/donation_receipt_034122.html',
}

DOCUMENT_TYPE_TO_FILENAME = {
    Document.DocumentType.RESERVATION: 'reservierungsbestaetigung',
    Document.DocumentType.HANDOVER: 'uebergabeprotokoll',
    Document.DocumentType.RETURN: 'ruecknahmeprotokoll',
    Document.DocumentType.CLOSING: 'abschlussuebersicht',
    Document.DocumentType.DONATION_RECEIPT: 'zuwendungsbestaetigung-034122',
}



ONES = {
    0: "null", 1: "ein", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf",
    6: "sechs", 7: "sieben", 8: "acht", 9: "neun", 10: "zehn",
    11: "elf", 12: "zwölf", 13: "dreizehn", 14: "vierzehn", 15: "fünfzehn",
    16: "sechzehn", 17: "siebzehn", 18: "achtzehn", 19: "neunzehn",
}
TENS = {20: "zwanzig", 30: "dreißig", 40: "vierzig", 50: "fünfzig", 60: "sechzig", 70: "siebzig", 80: "achtzig", 90: "neunzig"}


def _number_to_german_words(number):
    number = int(number)
    if number < 20:
        return ONES[number]
    if number < 100:
        ten = number // 10 * 10
        one = number % 10
        if not one:
            return TENS[ten]
        one_word = "ein" if one == 1 else ONES[one]
        return f"{one_word}und{TENS[ten]}"
    if number < 1000:
        hundred = number // 100
        rest = number % 100
        prefix = "einhundert" if hundred == 1 else f"{ONES[hundred]}hundert"
        return prefix + (_number_to_german_words(rest) if rest else "")
    if number < 1000000:
        thousands = number // 1000
        rest = number % 1000
        prefix = "eintausend" if thousands == 1 else f"{_number_to_german_words(thousands)}tausend"
        return prefix + (_number_to_german_words(rest) if rest else "")
    return str(number)


def amount_to_german_words(amount):
    amount = amount.quantize(__import__('decimal').Decimal('0.01'))
    euros = int(amount)
    cents = int((amount - euros) * 100)
    euro_word = "Euro"
    words = f"{_number_to_german_words(euros)} {euro_word}"
    if cents:
        words += f" und {_number_to_german_words(cents)} Cent"
    return words


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


def _split_accessories_for_pdf(item):
    required_accessories = [accessory for accessory in item.product.accessories.all() if accessory.required]
    optional_accessories = [accessory for accessory in item.handover_accessories.all() if not accessory.required]
    return required_accessories, optional_accessories


def _items_for_pdf(rental_case):
    items = list(
        rental_case.items
        .select_related('product')
        .prefetch_related('product__accessories', 'handover_accessories')
    )
    for item in items:
        item.required_accessories_for_pdf, item.optional_accessories_for_pdf = _split_accessories_for_pdf(item)
    return items


def render_document_pdf(rental_case, document_type, *, request=None):
    template_name = DOCUMENT_TYPE_TO_TEMPLATE[document_type]
    protocol = _latest_protocol(rental_case, document_type)
    items = _items_for_pdf(rental_case)
    protocol_photos = []
    for item in items:
        item.return_protocol_photos = []
    if protocol:
        photos_by_item_id = {item.pk: [] for item in items}
        for photo in protocol.photos.select_related('rental_case_item__product').all():
            photo_payload = {
                'caption': photo.caption,
                'data_url': _signature_data_url(photo.image),
                'item': photo.rental_case_item,
            }
            protocol_photos.append(photo_payload)
            if photo.rental_case_item_id in photos_by_item_id:
                photos_by_item_id[photo.rental_case_item_id].append(photo_payload)
        for item in items:
            item.return_protocol_photos = photos_by_item_id.get(item.pk, [])
    context = {
        'rental_case': rental_case,
        'items': items,
        'generated_at': timezone.localtime(),
        'document_type': document_type,
        'protocol': protocol,
        'borrower_signature_data_url': _signature_data_url(protocol.borrower_signature) if protocol else '',
        'club_signature_data_url': _signature_data_url(protocol.club_signature) if protocol else '',
        'protocol_photos': protocol_photos,
        'unassigned_protocol_photos': [photo for photo in protocol_photos if not photo['item']],
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



def donation_receipt_filename(receipt):
    safe_number = (receipt.receipt_number or f'zuwendung-{receipt.pk}').lower().replace('/', '-').replace(' ', '-')
    return f'zuwendungsbestaetigung-034122-{safe_number}.pdf'


def render_donation_receipt_pdf(receipt, *, request=None):
    context = {
        'receipt': receipt,
        'generated_at': timezone.localtime(),
    }
    html = render_to_string('rental/pdfs/donation_receipt_034122.html', context=context, request=request)
    base_url = request.build_absolute_uri('/') if request else None
    return HTML(string=html, base_url=base_url).write_pdf()


def create_donation_receipt_document(receipt, *, request=None):
    pdf_bytes = render_donation_receipt_pdf(receipt, request=request)
    document = receipt.document
    if document is None:
        document = Document(rental_case=receipt.rental_case, document_type=Document.DocumentType.DONATION_RECEIPT)
    document.file.save(donation_receipt_filename(receipt), ContentFile(pdf_bytes), save=True)
    receipt.document = document
    receipt.save(update_fields=['document', 'updated_at'])
    return document

def document_download_url(document):
    return reverse('rental:document_download', args=[document.pk])
