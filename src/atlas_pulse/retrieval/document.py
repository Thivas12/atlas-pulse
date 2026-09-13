"""Deterministic event-to-document rendering and citation validation."""

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from agent_rag_core import Event

from atlas_pulse.retrieval.base import CitationValidation

_MAX_DOCUMENT_CHARACTERS = 8_000
_SPACE = re.compile(r"\s+")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api-key",
    "api_key",
    "apikey",
    "key",
    "map_key",
    "password",
    "secret",
    "signature",
    "token",
}
_EVIDENCE_FIELDS = ("source_url", "detail_url", "source_report_url", "dataset_url")
_TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("place", "Place"),
    ("alert_type", "Alert type"),
    ("headline", "Headline"),
    ("description", "Description"),
    ("instruction", "Instruction"),
    ("severity", "Severity"),
    ("certainty", "Certainty"),
    ("urgency", "Urgency"),
    ("response", "Response"),
    ("category", "Category"),
    ("priority", "Priority"),
    ("magnitude", "Magnitude"),
    ("magnitude_type", "Magnitude type"),
    ("depth_km", "Depth km"),
    ("tsunami", "Tsunami flag"),
    ("satellite", "Satellite"),
    ("instrument", "Instrument"),
    ("product", "Product"),
    ("confidence", "Confidence"),
    ("fire_radiative_power_mw", "Fire radiative power MW"),
    ("day_night", "Observation period"),
    ("quad_class_label", "GDELT class"),
    ("cameo_event_code", "CAMEO event code"),
    ("actor1", "Actor one"),
    ("actor2", "Actor two"),
    ("goldstein_scale", "Goldstein scale"),
    ("mentions", "Media mentions"),
    ("sources", "Media sources"),
    ("verification_status", "Verification status"),
    ("sender_name", "Issuing authority"),
)


def _render_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        rendered = _SPACE.sub(" ", value).strip()
        return rendered or None
    if isinstance(value, int | float):
        return format(value, "g")
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        rendered_items = [item for item in (_render_value(item) for item in value) if item]
        return ", ".join(rendered_items) or None
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return None


def render_event_document(event: Event) -> str:
    """Render provider-neutral retrieval text without mutating source evidence."""
    lines = [
        f"Source: {event.source}",
        f"Event type: {event.event_type}",
        f"Event ID: {event.event_id}",
        f"Occurred at: {event.occurred_at.isoformat()}",
    ]
    if event.location is not None:
        lines.append(f"Coordinates: {event.location.latitude:.6f}, {event.location.longitude:.6f}")
    for key, label in _TEXT_FIELDS:
        rendered = _render_value(event.payload.get(key))
        if rendered is not None:
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)[:_MAX_DOCUMENT_CHARACTERS]


def document_hash(text: str) -> str:
    """Return a content identity for the exact embedded text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _public_http_url(value: str) -> tuple[bool, tuple[str, ...]]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False, ("malformed_url",)
    if parsed.scheme not in {"http", "https"}:
        return False, ("unsupported_scheme",)
    if parsed.username is not None or parsed.password is not None:
        return False, ("embedded_credentials",)
    hostname = parsed.hostname
    if not hostname:
        return False, ("missing_hostname",)
    if port is not None and not 1 <= port <= 65_535:
        return False, ("invalid_port",)
    normalized_host = hostname.rstrip(".").casefold()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return False, ("non_public_hostname",)
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        if "." not in normalized_host:
            return False, ("non_public_hostname",)
    else:
        if not address.is_global:
            return False, ("non_public_address",)
    sensitive = {
        key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    } & _SENSITIVE_QUERY_KEYS
    if sensitive:
        return False, ("credential_like_query_parameter",)
    return True, ("public_http_url", "source_event_identity_attached")


def validate_event_citation(event: Event) -> CitationValidation:
    """Validate link structure and credential safety without claiming fact verification."""
    rejected_reasons: list[str] = []
    for field in _EVIDENCE_FIELDS:
        value = event.payload.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = value.strip()
        valid, reasons = _public_http_url(candidate)
        if valid:
            return CitationValidation(
                status="traceable",
                url=candidate,
                source_field=field,
                reasons=reasons,
            )
        rejected_reasons.extend(f"{field}:{reason}" for reason in reasons)
    if rejected_reasons:
        return CitationValidation(
            status="rejected",
            url=None,
            source_field=None,
            reasons=tuple(dict.fromkeys(rejected_reasons)),
        )
    return CitationValidation(
        status="missing",
        url=None,
        source_field=None,
        reasons=("no_source_evidence_url",),
    )
