from catalog.models import CompanyProfile


def get_company_profile():
    profile, _ = CompanyProfile.objects.get_or_create(pk=1)
    return profile


def company_profile_snapshot(profile):
    return {
        "legal_name": profile.legal_name,
        "trade_name": profile.trade_name,
        "address_line1": profile.address_line1,
        "address_line2": profile.address_line2,
        "city": profile.city,
        "state_code": profile.state_code_id,
        "pincode": profile.pincode,
        "gstin": profile.gstin,
        "pan": profile.pan,
        "phone": profile.phone,
        "email": profile.email,
        "packer_manufacturer_name": profile.packer_manufacturer_name,
        "packer_address": profile.packer_address,
        "country_of_origin": profile.country_of_origin,
        "consumer_care_name": profile.consumer_care_name,
        "consumer_care_address": profile.consumer_care_address,
        "consumer_care_phone": profile.consumer_care_phone,
        "consumer_care_email": profile.consumer_care_email,
        "invoice_prefix": profile.invoice_prefix,
        "credit_note_prefix": profile.credit_note_prefix,
        "logo": profile.logo.name if profile.logo else None,
        "authorised_signatory_name": profile.authorised_signatory_name,
        "signature_image": (
            profile.signature_image.name if profile.signature_image else None
        ),
    }


def company_label_details():
    profile = get_company_profile()
    return {
        "legal_name": profile.legal_name,
        "trade_name": profile.trade_name,
        "gstin": profile.gstin,
        "packer_manufacturer_name": profile.packer_manufacturer_name,
        "packer_address": profile.packer_address,
        "country_of_origin": profile.country_of_origin,
        "consumer_care_name": profile.consumer_care_name,
        "consumer_care_address": profile.consumer_care_address,
        "consumer_care_phone": profile.consumer_care_phone,
        "consumer_care_email": profile.consumer_care_email,
        "logo": profile.logo.url if profile.logo else None,
    }


def company_invoice_details():
    profile = get_company_profile()
    return {
        "legal_name": profile.legal_name,
        "trade_name": profile.trade_name,
        "address_line1": profile.address_line1,
        "address_line2": profile.address_line2,
        "city": profile.city,
        "state_code": profile.state_code_id,
        "pincode": profile.pincode,
        "gstin": profile.gstin,
        "pan": profile.pan,
        "phone": profile.phone,
        "email": profile.email,
        "invoice_prefix": profile.invoice_prefix,
        "credit_note_prefix": profile.credit_note_prefix,
        "logo": profile.logo.url if profile.logo else None,
        "authorised_signatory_name": profile.authorised_signatory_name,
        "signature_image": (
            profile.signature_image.url if profile.signature_image else None
        ),
    }
