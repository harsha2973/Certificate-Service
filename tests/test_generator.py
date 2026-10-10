from datetime import date

import pytest
from pypdf import PdfReader

from app.generator import CertificateData, CertificateGenerationError, generate_certificate_pdf


def _data(**overrides):
    base = dict(
        certificate_id="abc123",
        recipient_name="Jane Doe",
        course_name="Python Bootcamp",
        organization="Acme Academy",
        title="Certificate of Completion",
        issue_date=date(2026, 10, 9),
    )
    base.update(overrides)
    return CertificateData(**base)


def _text(path):
    return " ".join(PdfReader(str(path)).pages[0].extract_text().split())


def test_generates_pdf_containing_recipient_information(tmp_path):
    out = tmp_path / "nested" / "cert.pdf"

    generate_certificate_pdf(_data(), out)

    assert out.read_bytes().startswith(b"%PDF")
    text = _text(out)
    for expected in ("Jane Doe", "Python Bootcamp", "Acme Academy", "09 October 2026", "abc123"):
        assert expected in text
    assert not list(out.parent.glob("*.tmp"))  # no temp files left behind


def test_accented_latin_names_are_supported(tmp_path):
    out = tmp_path / "cert.pdf"
    generate_certificate_pdf(_data(recipient_name="José Müller-Çelik"), out)
    assert out.exists()


def test_non_latin_cyrillic_and_greek_names_are_supported(tmp_path):
    out1 = tmp_path / "cert_cyrillic.pdf"
    generate_certificate_pdf(_data(recipient_name="Алексей Смирнов"), out1)
    assert out1.exists()

    out2 = tmp_path / "cert_greek.pdf"
    generate_certificate_pdf(_data(recipient_name="Νίκος Καζαντζάκης"), out2)
    assert out2.exists()


def test_very_long_name_is_shrunk_to_fit(tmp_path):
    out = tmp_path / "cert.pdf"
    generate_certificate_pdf(_data(recipient_name="W" * 60), out)
    assert out.exists()


def test_unrenderable_characters_raise_and_leave_no_file(tmp_path):
    out = tmp_path / "cert.pdf"
    with pytest.raises(CertificateGenerationError):
        generate_certificate_pdf(_data(recipient_name="张伟"), out)
    assert not out.exists()
