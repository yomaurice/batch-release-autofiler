import pytest

from batch_release.license import LicenseFormatError, to_portal_license


def test_converts_il_license() -> None:
    assert to_portal_license("IL-146.13.33189.00") == "146-13-33189-00"
    assert to_portal_license("  IL-043.08.23618.00 ") == "043-08-23618-00"


@pytest.mark.parametrize("raw", ["", None, "US-214326", "DE-40921.00.00", "IL-065394;US-065394",
                                 "IL-29c product (not licensed)"])
def test_rejects_non_portal_licenses(raw: str | None) -> None:
    with pytest.raises(LicenseFormatError):
        to_portal_license(raw)
