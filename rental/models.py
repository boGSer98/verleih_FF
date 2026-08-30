from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
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
    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Verfügbar'
        MAINTENANCE = 'maintenance', 'Wartung'
        DEFECTIVE = 'defective', 'Defekt'
        RETIRED = 'retired', 'Ausgemustert'

    name = models.CharField('Name', max_length=160)
    category = models.ForeignKey(ProductCategory, verbose_name='Kategorie', on_delete=models.PROTECT, related_name='products')
    inventory_number = models.CharField('Inventarnummer', max_length=80, blank=True, unique=True, null=True)
    description = models.TextField('Beschreibung', blank=True)
    stock_quantity = models.PositiveIntegerField('Bestand', default=1)
    storage_location = models.CharField('Lagerort', max_length=160, blank=True)
    condition_note = models.TextField('Zustand/Bemerkung', blank=True)
    status = models.CharField('Artikelstatus', max_length=24, choices=Status.choices, default=Status.AVAILABLE)
    suggested_donation = models.DecimalField('Spendenempfehlung', max_digits=8, decimal_places=2, default=0)
    deposit_amount = models.DecimalField('Kaution', max_digits=8, decimal_places=2, default=0)
    replacement_value = models.DecimalField('Ersatzwert', max_digits=8, decimal_places=2, default=0)
    active = models.BooleanField('Aktiv', default=True)

    class Meta:
        verbose_name = 'Verleihartikel'
        verbose_name_plural = 'Verleihartikel'
        ordering = ['category__name', 'name']

    def __str__(self):
        return self.name

    @property
    def can_be_reserved(self):
        return self.active and self.status == self.Status.AVAILABLE and self.stock_quantity > 0

    def reserved_quantity(self, reserved_from, reserved_until, *, exclude_case=None):
        if not reserved_from or not reserved_until:
            return 0
        items = self.case_items.filter(
            rental_case__status__in=RentalCase.blocking_statuses(),
            rental_case__reserved_from__lt=reserved_until,
            rental_case__reserved_until__gt=reserved_from,
        )
        if exclude_case and exclude_case.pk:
            items = items.exclude(rental_case=exclude_case)
        return items.aggregate(total=Sum('quantity'))['total'] or 0

    def available_quantity(self, reserved_from, reserved_until, *, exclude_case=None):
        if not self.can_be_reserved:
            return 0
        return max(self.stock_quantity - self.reserved_quantity(reserved_from, reserved_until, exclude_case=exclude_case), 0)

    def is_available(self, quantity, reserved_from, reserved_until, *, exclude_case=None):
        return self.available_quantity(reserved_from, reserved_until, exclude_case=exclude_case) >= quantity


class ProductAccessory(TimeStampedModel):
    product = models.ForeignKey(Product, verbose_name='Artikel', on_delete=models.CASCADE, related_name='accessories')
    name = models.CharField('Zubehör/Bestandteil', max_length=160)
    quantity = models.PositiveIntegerField('Menge', default=1)
    required = models.BooleanField('Pflichtbestandteil', default=True)
    notes = models.TextField('Bemerkungen', blank=True)

    class Meta:
        verbose_name = 'Zubehör/Bestandteil'
        verbose_name_plural = 'Zubehör/Bestandteile'
        ordering = ['product__name', 'name']
        unique_together = [('product', 'name')]

    def __str__(self):
        return f'{self.quantity} × {self.name}'


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

    class DonationDecision(models.TextChoices):
        OPEN = 'open', 'Offen'
        RECEIVED = 'received', 'Erhalten'
        PARTIAL = 'partial', 'Teilweise erhalten'
        WAIVED = 'waived', 'Verzichtet'

    class DonationPaymentMethod(models.TextChoices):
        CASH = 'cash', 'Bar'
        BANK_TRANSFER = 'bank_transfer', 'Überweisung'
        PAYPAL = 'paypal', 'PayPal'
        OTHER = 'other', 'Sonstig'

    TRANSITIONS = {
        Status.REQUEST: {Status.RESERVED, Status.CANCELLED},
        Status.RESERVED: {Status.PREPARED, Status.CANCELLED},
        Status.PREPARED: {Status.HANDED_OVER, Status.CANCELLED},
        Status.HANDED_OVER: {Status.DONATION_OPEN, Status.DONATION_RECEIVED, Status.RETURNED, Status.CLARIFICATION},
        Status.DONATION_OPEN: {Status.DONATION_RECEIVED, Status.RETURNED, Status.CLARIFICATION},
        Status.DONATION_RECEIVED: {Status.RETURNED, Status.COMPLETED, Status.CLARIFICATION},
        Status.RETURNED: {Status.COMPLETED, Status.CLARIFICATION},
        Status.CLARIFICATION: {Status.RETURNED, Status.DONATION_RECEIVED, Status.COMPLETED},
        Status.COMPLETED: set(),
        Status.CANCELLED: set(),
    }
    BLOCKING_STATUSES = {
        Status.RESERVED,
        Status.PREPARED,
        Status.HANDED_OVER,
        Status.DONATION_OPEN,
        Status.DONATION_RECEIVED,
    }

    number = models.CharField('Vorgangsnummer', max_length=40, unique=True, blank=True)
    borrower = models.ForeignKey(Borrower, verbose_name='Entleiher', on_delete=models.PROTECT, related_name='rental_cases')
    reserved_from = models.DateTimeField('Reserviert von')
    reserved_until = models.DateTimeField('Reserviert bis')
    status = models.CharField('Status', max_length=32, choices=Status.choices, default=Status.REQUEST)
    expected_donation = models.DecimalField('Erwartete Spende', max_digits=8, decimal_places=2, default=0)
    received_donation = models.DecimalField('Erhaltene Spende', max_digits=8, decimal_places=2, default=0)
    donation_decision = models.CharField(
        'Spendenentscheidung',
        max_length=16,
        choices=DonationDecision.choices,
        default=DonationDecision.OPEN,
    )
    donation_payment_method = models.CharField(
        'Zahlungsart',
        max_length=24,
        choices=DonationPaymentMethod.choices,
        blank=True,
    )
    donation_received_at = models.DateTimeField('Spende erhalten am', null=True, blank=True)
    donation_note = models.TextField('Zahlungsnotiz', blank=True)
    notes = models.TextField('Bemerkungen', blank=True)
    closed_at = models.DateTimeField('Abgeschlossen am', null=True, blank=True)

    class Meta:
        verbose_name = 'Verleihvorgang'
        verbose_name_plural = 'Verleihvorgänge'
        ordering = ['-reserved_from', '-created_at']

    def __str__(self):
        return self.number or f'Vorgang {self.pk or "neu"}'

    def clean(self):
        super().clean()
        if self.reserved_from and self.reserved_until and self.reserved_until <= self.reserved_from:
            raise ValidationError({'reserved_until': 'Das Ende der Reservierung muss nach dem Beginn liegen.'})

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

    def allowed_next_statuses(self):
        return self.TRANSITIONS.get(self.status, set())

    @classmethod
    def blocking_statuses(cls):
        return cls.BLOCKING_STATUSES

    def can_transition_to(self, target_status):
        return target_status in self.allowed_next_statuses()

    def has_open_donation_decision(self):
        return self.expected_donation > 0 and self.donation_decision == self.DonationDecision.OPEN

    def transition_to(self, target_status, *, save=True):
        if not self.can_transition_to(target_status):
            current_label = self.Status(self.status).label
            target_label = self.Status(target_status).label
            raise ValidationError(f'Statuswechsel von „{current_label}“ nach „{target_label}“ ist nicht erlaubt.')
        if target_status == self.Status.COMPLETED and self.has_open_donation_decision():
            raise ValidationError('Der Vorgang kann erst abgeschlossen werden, wenn die Spendenentscheidung dokumentiert ist.')
        self.status = target_status
        if target_status == self.Status.COMPLETED and not self.closed_at:
            self.closed_at = timezone.now()
        if save:
            self.save(update_fields=['status', 'closed_at', 'updated_at'])
        return self


class RentalCaseItem(TimeStampedModel):
    rental_case = models.ForeignKey(RentalCase, verbose_name='Vorgang', on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, verbose_name='Artikel', on_delete=models.PROTECT, related_name='case_items')
    quantity = models.PositiveIntegerField('Menge', default=1)
    handover_condition = models.TextField('Zustand bei Übergabe', blank=True)
    handover_accessories = models.ManyToManyField(
        ProductAccessory,
        verbose_name='Mitgegebenes Zubehör',
        blank=True,
        related_name='handover_items',
    )
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

    def clean(self):
        super().clean()
        if self.quantity < 1:
            raise ValidationError({'quantity': 'Die Menge muss mindestens 1 betragen.'})
        if self.product_id and not self.product.can_be_reserved:
            raise ValidationError({'product': 'Dieser Artikel ist aktuell nicht für Reservierungen verfügbar.'})
        if self.product_id and self.rental_case_id:
            available_quantity = self.product.available_quantity(
                self.rental_case.reserved_from,
                self.rental_case.reserved_until,
                exclude_case=self.rental_case,
            )
            if self.quantity > available_quantity:
                raise ValidationError({
                    'quantity': f'Im gewählten Zeitraum sind nur {available_quantity} Stück verfügbar.'
                })


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


class ProtocolPhoto(TimeStampedModel):
    protocol = models.ForeignKey(Protocol, verbose_name='Protokoll', on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField('Foto', upload_to='protocol-photos/')
    caption = models.CharField('Beschreibung', max_length=240, blank=True)

    class Meta:
        verbose_name = 'Protokollfoto'
        verbose_name_plural = 'Protokollfotos'
        ordering = ['created_at']

    def __str__(self):
        return self.caption or f'Foto zu {self.protocol}'


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
