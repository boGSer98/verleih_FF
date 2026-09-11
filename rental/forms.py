from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Borrower, DonationReceipt, DonationReceiptIssuerProfile, RentalCase
from .pdf import amount_to_german_words


class BorrowerForm(forms.ModelForm):
    class Meta:
        model = Borrower
        fields = ['name', 'organization', 'email', 'phone', 'street', 'postal_code', 'city', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 4}),
        }


class DonationReceiptIssuerProfileForm(forms.ModelForm):
    class Meta:
        model = DonationReceiptIssuerProfile
        fields = '__all__'


class DonationReceiptForm(forms.ModelForm):
    class Meta:
        model = DonationReceipt
        fields = [
            'donor_name',
            'donor_organization',
            'donor_street',
            'donor_postal_code',
            'donor_city',
            'donor_email',
            'donation_amount',
            'donation_date',
            'is_membership_fee',
            'is_expense_reimbursement_waiver',
            'issuer_name',
            'issuer_street',
            'issuer_postal_code',
            'issuer_city',
            'tax_office',
            'tax_number',
            'exemption_notice_date',
            'determination_notice_date',
            'statutory_purposes',
            'membership_fees_deductible',
            'issue_place',
            'signer_name',
            'signer_function',
        ]
        widgets = {
            'donation_date': forms.DateInput(attrs={'type': 'date'}),
            'exemption_notice_date': forms.DateInput(attrs={'type': 'date'}),
            'determination_notice_date': forms.DateInput(attrs={'type': 'date'}),
            'statutory_purposes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, rental_case=None, issuer_profile=None, **kwargs):
        self.rental_case = rental_case
        self.issuer_profile = issuer_profile
        initial = kwargs.pop('initial', {})
        if rental_case and not args:
            borrower = rental_case.borrower
            donation_date = timezone.localdate(rental_case.donation_received_at) if rental_case.donation_received_at else timezone.localdate()
            initial.update({
                'donor_name': borrower.name,
                'donor_organization': borrower.organization,
                'donor_street': borrower.street,
                'donor_postal_code': borrower.postal_code,
                'donor_city': borrower.city,
                'donor_email': borrower.email,
                'donation_amount': rental_case.received_donation or rental_case.expected_donation,
                'donation_date': donation_date,
                'is_membership_fee': False,
                'is_expense_reimbursement_waiver': False,
            })
        if issuer_profile and not args:
            initial.update({
                'issuer_name': issuer_profile.name,
                'issuer_street': issuer_profile.street,
                'issuer_postal_code': issuer_profile.postal_code,
                'issuer_city': issuer_profile.city,
                'tax_office': issuer_profile.tax_office,
                'tax_number': issuer_profile.tax_number,
                'exemption_notice_date': issuer_profile.exemption_notice_date,
                'determination_notice_date': issuer_profile.determination_notice_date,
                'statutory_purposes': issuer_profile.statutory_purposes,
                'membership_fees_deductible': issuer_profile.membership_fees_deductible,
                'issue_place': issuer_profile.default_issue_place or issuer_profile.city,
                'signer_name': issuer_profile.default_signer_name,
                'signer_function': issuer_profile.default_signer_function,
            })
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def clean_donation_amount(self):
        amount = self.cleaned_data['donation_amount']
        if amount <= Decimal('0'):
            raise ValidationError('Der Betrag der Zuwendung muss größer als 0 sein.')
        return amount

    def clean(self):
        cleaned = super().clean()
        if self.rental_case:
            if self.rental_case.received_donation <= Decimal('0'):
                raise ValidationError('Für diesen Vorgang ist keine erhaltene Spende dokumentiert.')
            if self.rental_case.donation_decision not in {RentalCase.DonationDecision.RECEIVED, RentalCase.DonationDecision.PARTIAL}:
                raise ValidationError('Die Spendenentscheidung muss „Erhalten“ oder „Teilweise erhalten“ sein.')
        if not cleaned.get('exemption_notice_date') and not cleaned.get('determination_notice_date'):
            raise ValidationError('Es muss ein Freistellungs-/Körperschaftsteuerbescheid oder ein Feststellungsbescheid § 60a AO eingetragen sein.')
        return cleaned

    def save(self, commit=True, *, user=None):
        receipt = super().save(commit=False)
        if self.rental_case:
            receipt.rental_case = self.rental_case
        receipt.donation_amount_words = amount_to_german_words(receipt.donation_amount)
        receipt.status = DonationReceipt.Status.ISSUED
        if not receipt.issued_at:
            receipt.issued_at = timezone.now()
        if user and not receipt.issued_by_id:
            receipt.issued_by = user
        if commit:
            receipt.save()
        return receipt
