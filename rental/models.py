from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)
    updated_at = models.DateTimeField('Geändert am', auto_now=True)

    class Meta:
        abstract = True


class ProductCategory(TimeStampedModel):
    name = models.CharField('Name', max_length=120, unique=True)
    description = models.TextField('Beschreibung', blank=True)

    class Meta:
        verbose_name = 'Kategorie'
        verbose_name_plural = 'Kategorien'
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    name = models.CharField('Name', max_length=160)
    category = models.ForeignKey(ProductCategory, verbose_name='Kategorie', on_delete=models.PROTECT, related_name='products')
    inventory_number = models.CharField('Inventarnummer', max_length=80, blank=True, unique=True, null=True)
    description = models.TextField('Beschreibung', blank=True)
    stock_quantity = models.PositiveIntegerField('Bestand', default=1)
    storage_location = models.CharField('Lagerort', max_length=160, blank=True)
    condition_note = models.TextField('Zustand/Bemerkung', blank=True)
    suggested_donation = models.DecimalField('Spendenempfehlung', max_digits=8, decimal_places=2, default=0)
    deposit_amount = models.DecimalField('Kaution', max_digits=8, decimal_places=2, default=0)
    active = models.BooleanField('Aktiv', default=True)

    class Meta:
        verbose_name = 'Verleihartikel'
        verbose_name_plural = 'Verleihartikel'
        ordering = ['category__name', 'name']

    def __str__(self):
        return self.name


class Borrower(TimeStampedModel):
    name = models.CharField('Name', max_length=180)
    organization = models.CharField('Organisation/Verein', max_length=180, blank=True)
    email = models.EmailField('E-Mail')
    phone = models.CharField('Telefon', max_length=80, blank=True)
    street = models.CharField('Straße/Hausnummer', max_length=180, blank=True)
    postal_code = models.CharField('PLZ', max_length=20, blank=True)
    city = models.CharField('Ort', max_length=120, blank=True)
    notes = models.TextField('Interne Notizen', blank=True)

    class Meta:
        verbose_name = 'Entleiher'
        verbose_name_plural = 'Entleiher'
        ordering = ['name']

    def __str__(self):
        return self.name


class RentalCase(TimeStampedModel):
    class Status(models.TextChoices):
        REQUEST = 'request', 'Anfrage'
        RESERVED = 'reserved', 'Reserviert'
        PREPARED = 'prepared', 'Abholung vorbereitet'
        HANDED_OVER = 'handed_over', 'Übergeben'
        DONATION_OPEN = 'donation_open', 'Spende offen'
        DONATION_RECEIVED = 'donation_received', 'Spende erhalten'
        RETURNED = 'returned', 'Zurückgenommen'
        CLARIFICATION = 'clarification', 'Klärung nötig'
        COMPLETED = 'completed', 'Abgeschlossen'
        CANCELLED = 'cancelled', 'Storniert'

    number = models.CharField('Vorgangsnummer', max_length=40, unique=True, blank=True)
    borrower = models.ForeignKey(Borrower, verbose_name='Entleiher', on_delete=models.PROTECT, related_name='rental_cases')
    reserved_from = models.DateTimeField('Reserviert von')
    reserved_until = models.DateTimeField('Reserviert bis')
    status = models.CharField('Status', max_length=32, choices=Status.choices, default=Status.REQUEST)
    expected_donation = models.DecimalField('Erwartete Spende', max_digits=8, decimal_places=2, default=0)
    received_donation = models.DecimalField('Erhaltene Spende', max_digits=8, decimal_places=2, default=0)
    donation_received_at = models.DateTimeField('Spende erhalten am', null=True, blank=True)
    notes = models.TextField('Bemerkungen', blank=True)
    closed_at = models.DateTimeField('Abgeschlossen am', null=True, blank=True)

    class Meta:
        verbose_name = 'Verleihvorgang'
        verbose_name_plural = 'Verleihvorgänge'
        ordering = ['-reserved_from', '-created_at']

    def __str__(self):
        return self.number or f'Vorgang {self.pk or "neu"}'

    def save(self, *args, **kwargs):
        if not self.number:
            year = timezone.localdate().year
            prefix = f'VF-{year}-'
            latest = RentalCase.objects.filter(number__startswith=prefix).order_by('-number').first()
            next_number = 1
            if latest and latest.number:
                try:
                    next_number = int(latest.number.rsplit('-', 1)[1]) + 1
                except (ValueError, IndexError):
                    next_number = 1
            self.number = f'{prefix}{next_number:04d}'
        super().save(*args, **kwargs)


class RentalCaseItem(TimeStampedModel):
    rental_case = models.ForeignKey(RentalCase, verbose_name='Vorgang', on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, verbose_name='Artikel', on_delete=models.PROTECT, related_name='case_items')
    quantity = models.PositiveIntegerField('Menge', default=1)
    handover_condition = models.TextField('Zustand bei Übergabe', blank=True)
    return_condition = models.TextField('Zustand bei Rücknahme', blank=True)
    missing = models.BooleanField('Fehlt', default=False)
    damaged = models.BooleanField('Beschädigt', default=False)
    damage_amount = models.DecimalField('Schadenbetrag', max_digits=8, decimal_places=2, default=0)
    notes = models.TextField('Bemerkungen', blank=True)

    class Meta:
        verbose_name = 'Vorgangsposition'
        verbose_name_plural = 'Vorgangspositionen'
        unique_together = [('rental_case', 'product')]

    def __str__(self):
        return f'{self.quantity} × {self.product}'


class Protocol(TimeStampedModel):
    class ProtocolType(models.TextChoices):
        HANDOVER = 'handover', 'Übergabeprotokoll'
        RETURN = 'return', 'Rücknahmeprotokoll'

    rental_case = models.ForeignKey(RentalCase, verbose_name='Vorgang', on_delete=models.CASCADE, related_name='protocols')
    protocol_type = models.CharField('Protokolltyp', max_length=20, choices=ProtocolType.choices)
    performed_at = models.DateTimeField('Durchgeführt am', default=timezone.now)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='Helfer', on_delete=models.PROTECT, related_name='performed_protocols')
    borrower_signature = models.ImageField('Unterschrift Entleiher', upload_to='signatures/', blank=True, null=True)
    club_signature = models.ImageField('Unterschrift Verein', upload_to='signatures/', blank=True, null=True)
    notes = models.TextField('Protokollnotizen', blank=True)
    pdf_file = models.FileField('PDF', upload_to='documents/', blank=True, null=True)

    class Meta:
        verbose_name = 'Protokoll'
        verbose_name_plural = 'Protokolle'
        ordering = ['-performed_at']

    def __str__(self):
        return f'{self.get_protocol_type_display()} {self.rental_case}'


class Document(TimeStampedModel):
    class DocumentType(models.TextChoices):
        RESERVATION = 'reservation', 'Reservierungsbestätigung'
        HANDOVER = 'handover', 'Übergabeprotokoll'
        RETURN = 'return', 'Rücknahmeprotokoll'
        CLOSING = 'closing', 'Abschlussübersicht'

    rental_case = models.ForeignKey(RentalCase, verbose_name='Vorgang', on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField('Dokumenttyp', max_length=32, choices=DocumentType.choices)
    file = models.FileField('Datei', upload_to='documents/')
    sent_to = models.EmailField('Gesendet an', blank=True)
    sent_at = models.DateTimeField('Gesendet am', null=True, blank=True)
    send_error = models.TextField('Versandfehler', blank=True)

    class Meta:
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumente'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_document_type_display()} {self.rental_case}'
