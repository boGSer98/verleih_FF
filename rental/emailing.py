from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone


DOCUMENT_TYPE_TO_SUBJECT = {
    'reservation': 'Reservierungsbestätigung',
    'handover': 'Übergabeprotokoll',
    'return': 'Rücknahmeprotokoll',
    'closing': 'Abschlussübersicht',
}


def document_email_subject(document):
    label = DOCUMENT_TYPE_TO_SUBJECT.get(document.document_type, document.get_document_type_display())
    return f'{label} {document.rental_case.number}'


def send_document_email(document, *, recipient=None, request=None):
    """Send a stored document PDF to the borrower and persist send status."""
    rental_case = document.rental_case
    recipient = recipient or rental_case.borrower.email
    if not recipient:
        document.sent_to = ''
        document.sent_at = None
        document.send_error = 'Keine E-Mail-Adresse am Entleiher hinterlegt.'
        document.save(update_fields=['sent_to', 'sent_at', 'send_error', 'updated_at'])
        return False
    if not document.file:
        document.sent_to = recipient
        document.sent_at = None
        document.send_error = 'Dokumentdatei fehlt.'
        document.save(update_fields=['sent_to', 'sent_at', 'send_error', 'updated_at'])
        return False

    context = {
        'document': document,
        'rental_case': rental_case,
        'borrower': rental_case.borrower,
        'request': request,
    }
    message = EmailMessage(
        subject=document_email_subject(document),
        body=render_to_string('rental/emails/document.txt', context=context, request=request),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    document.file.open('rb')
    try:
        message.attach(document.file.name.rsplit('/', 1)[-1], document.file.read(), 'application/pdf')
        message.send(fail_silently=False)
    except Exception as exc:  # pragma: no cover - concrete backend exception depends on deployment
        document.sent_to = recipient
        document.sent_at = None
        document.send_error = str(exc)
        document.save(update_fields=['sent_to', 'sent_at', 'send_error', 'updated_at'])
        return False
    finally:
        document.file.close()

    document.sent_to = recipient
    document.sent_at = timezone.now()
    document.send_error = ''
    document.save(update_fields=['sent_to', 'sent_at', 'send_error', 'updated_at'])
    return True
