"""Conversion of the SAP 'GI External License number' into the portal's registration format."""
import re

# Portal format for מספר רישום תכשיר, e.g. 146-13-33189-00
PORTAL_LICENSE_RE = re.compile(r"^\d{3}-\d{2}-\d{5}-\d{2}$")


class LicenseFormatError(ValueError):
    """Raised when a license number cannot be converted to the portal format."""


# Drop the leading 'IL-' and turn every '.' into '-': 'IL-146.13.33189.00' -> '146-13-33189-00'
def to_portal_license(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise LicenseFormatError("empty license number")
    if not value.upper().startswith("IL-"):
        raise LicenseFormatError(f"not an Israeli license: {value!r}")
    converted = value[3:].replace(".", "-")
    if not PORTAL_LICENSE_RE.match(converted):
        raise LicenseFormatError(f"unexpected license format after conversion: {value!r} -> {converted!r}")
    return converted
