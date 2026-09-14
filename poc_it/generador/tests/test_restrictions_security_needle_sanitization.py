from __future__ import annotations

from poc_it.generador.guardrails import guardrails_por_spec
from poc_it.generador.restrictions import sanitizar_restrictions


def _security_restriction(words: list[str]) -> dict:
    return {
        "id": "no_credentials_in_code",
        "applies_to": ["*.py"],
        "must_not_contain": words,
        "kind": "SECURITY",
        "enforcement": "code",
        "severity": "BLOCK",
    }


def test_bare_security_words_are_rewritten_to_literal_assignment_regex() -> None:
    sanitized = sanitizar_restrictions(
        [_security_restriction(["password", "secret", "token", "key", "credentials"])]
    )

    must_not = sanitized[0]["must_not_contain"]
    assert len(must_not) == 5
    for needle in must_not:
        assert needle.startswith("re:"), needle


def test_regex_needle_is_left_untouched() -> None:
    sanitized = sanitizar_restrictions([_security_restriction(["re:already_a_pattern"])])
    assert sanitized[0]["must_not_contain"] == ["re:already_a_pattern"]


def test_bare_kwarg_security_words_are_rewritten_to_literal_assignment_regex() -> None:
    """Regresión: el LLM que compila restrictions generó 'credentials=' (no 'credentials' a
    secas), que sigue siendo un needle desnudo peligroso: matchea kwargs legítimos como
    `build(..., credentials=creds)`."""
    sanitized = sanitizar_restrictions([_security_restriction(["credentials=", "token="])])

    must_not = sanitized[0]["must_not_contain"]
    assert len(must_not) == 2
    for needle in must_not:
        assert needle.startswith("re:"), needle


def test_bare_attribute_access_security_words_are_dropped() -> None:
    """Regresión: el LLM generó 'settings.' como needle, que matchea el patrón CORRECTO de
    leer configuración (`settings.google_drive_folder_id`) en vez de hardcodear un secreto.
    Prohibirlo contradice la propia restricción, así que se descarta."""
    sanitized = sanitizar_restrictions([_security_restriction(["settings.", "config."])])

    assert sanitized[0]["must_not_contain"] == []


def test_non_security_bare_words_are_not_rewritten() -> None:
    restriction = {
        "id": "no_eval",
        "applies_to": ["*.py"],
        "must_not_contain": ["eval"],
        "kind": "QUALITY",
        "enforcement": "code",
    }
    sanitized = sanitizar_restrictions([restriction])
    assert sanitized[0]["must_not_contain"] == ["eval"]


# ---------------------------------------------------------------------------
# End-to-end via guardrails_por_spec: el caso real que falló en la E2E
# (google_drive_api.py usando el patrón ADC correcto) ya NO debe bloquear.
# ---------------------------------------------------------------------------


def _spec_with_credentials_restriction() -> dict:
    restriction = _security_restriction(
        ["password", "secret", "token", "key", "credentials"]
    )
    return {
        "endpoints": [],
        "restrictions": sanitizar_restrictions([restriction]),
    }


def test_legitimate_adc_pattern_does_not_trigger_no_credentials_in_code() -> None:
    spec = _spec_with_credentials_restriction()
    files = [
        {
            "path": "app/integrations/google_drive_api.py",
            "content": """
from google.oauth2 import service_account
from app.core.config import get_settings


def build_drive_client():
    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_file(
        settings.google_credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return credentials
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)

    violations = [e for e in res.errors if "no_credentials_in_code" in e]
    assert violations == [], violations


def test_legitimate_kwarg_and_settings_access_pattern_does_not_trigger_restriction() -> None:
    """Caso real de la E2E: el LLM compiló la restricción con needles 'credentials=' y
    'settings.', que bloqueaban código perfectamente correcto (`credentials=creds` como kwarg,
    `settings.google_drive_folder_id` como lectura de config)."""
    restriction = _security_restriction(["credentials=", "settings."])
    spec = {
        "endpoints": [],
        "restrictions": sanitizar_restrictions([restriction]),
    }
    files = [
        {
            "path": "app/integrations/google_drive.py",
            "content": """
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.core.config import get_settings


def build_client():
    settings = get_settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.google_credentials_path
    )
    return build('drive', 'v3', credentials=creds)


def upload_to_google_drive(client, filename):
    settings = get_settings()
    return {'parents': [settings.google_drive_folder_id]}
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)

    violations = [e for e in res.errors if "no_credentials_in_code" in e]
    assert violations == [], violations


def test_hardcoded_secret_literal_still_triggers_no_credentials_in_code() -> None:
    spec = _spec_with_credentials_restriction()
    files = [
        {
            "path": "app/integrations/google_drive_api.py",
            "content": """
api_key = "AIzaSyD-hardcoded-fake-key-1234567890"
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)

    violations = [e for e in res.errors if "no_credentials_in_code" in e]
    assert len(violations) == 1
    assert "app/integrations/google_drive_api.py" in violations[0]
