from __future__ import annotations

import hashlib

from app.models import ModelConsentEvent


STORY_ORGANIZATION_PURPOSE = "story_organization"


def corrected_text_sha256(corrected_text: str) -> str:
    return hashlib.sha256(corrected_text.strip().encode("utf-8")).hexdigest()


def requires_explicit_model_consent(
    provider_name: str, data_classification: str
) -> bool:
    return data_classification != "test" and provider_name not in {"mock", "local"}


def model_consent_error(
    consent: ModelConsentEvent | None,
    *,
    family_id: str,
    session_id: str,
    data_classification: str,
    corrected_text: str,
    require_consumed: bool,
) -> str | None:
    if consent is None:
        return "MODEL_CONSENT_REQUIRED"
    if consent.family_id != family_id or consent.session_id != session_id:
        return "MODEL_CONSENT_SCOPE_MISMATCH"
    if consent.purpose != STORY_ORGANIZATION_PURPOSE:
        return "MODEL_CONSENT_PURPOSE_MISMATCH"
    if consent.data_classification != data_classification:
        return "MODEL_CONSENT_CLASSIFICATION_CHANGED"
    if consent.decision != "granted" or consent.revoked_at is not None:
        return "MODEL_CONSENT_NOT_GRANTED"
    if not consent.one_time:
        return "MODEL_CONSENT_MUST_BE_ONE_TIME"
    if consent.input_sha256 != corrected_text_sha256(corrected_text):
        return "MODEL_CONSENT_TEXT_CHANGED"
    if require_consumed and consent.used_at is None:
        return "MODEL_CONSENT_NOT_RESERVED"
    if not require_consumed and consent.used_at is not None:
        return "MODEL_CONSENT_ALREADY_USED"
    return None
