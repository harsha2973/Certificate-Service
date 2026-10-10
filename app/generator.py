"""Certificate rendering. The single predefined template lives here.

The generator is a plain callable ``(CertificateData, Path) -> None`` so it can be
swapped (or made to fail) in tests, and so a different renderer (HTML->PDF, PNG...)
can be dropped in without touching the rest of the app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
MAX_TEXT_WIDTH = PAGE_W - 140
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b08d57")
GREY = colors.HexColor("#555555")

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
REGULAR_TTF = FONTS_DIR / "NotoSans-Regular.ttf"
BOLD_TTF = FONTS_DIR / "NotoSans-Bold.ttf"

_UNICODE_SUPPORTED = False
_CHAR_MAP: set[int] = set()

if REGULAR_TTF.is_file() and BOLD_TTF.is_file():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        pdfmetrics.registerFont(TTFont("NotoSans", str(REGULAR_TTF)))
        pdfmetrics.registerFont(TTFont("NotoSans-Bold", str(BOLD_TTF)))
        FONT_REGULAR = "NotoSans"
        FONT_BOLD = "NotoSans-Bold"
        _font_obj = pdfmetrics.getFont("NotoSans")
        _CHAR_MAP = set(_font_obj.face.charToGlyph.keys())
        _UNICODE_SUPPORTED = True
    except Exception:
        pass


class CertificateGenerationError(Exception):
    pass


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    recipient_name: str
    course_name: str
    organization: str
    title: str
    issue_date: date


Generator = Callable[[CertificateData, Path], None]


def is_renderable(text: str) -> bool:
    """Check if font can render all characters in the string."""
    if _UNICODE_SUPPORTED:
        return all(ord(c) in _CHAR_MAP or c in "\r\n\t " for c in text)
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _fit(text: str, font: str, start: int, minimum: int) -> int:
    size = start
    while size > minimum and stringWidth(text, font, size) > MAX_TEXT_WIDTH:
        size -= 1
    if stringWidth(text, font, size) > MAX_TEXT_WIDTH:
        raise CertificateGenerationError(f"text too long to fit on the certificate: {text[:40]!r}...")
    return size


def _centered(c: canvas.Canvas, y: float, text: str, font: str, size: int, color=NAVY) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(PAGE_W / 2, y, text)


def _draw(data: CertificateData, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{data.title} - {data.recipient_name}")
    c.setAuthor(data.organization)

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(34, 34, PAGE_W - 68, PAGE_H - 68)

    _centered(c, PAGE_H - 125, data.title.upper(), FONT_BOLD, _fit(data.title.upper(), FONT_BOLD, 36, 18))
    _centered(c, PAGE_H - 180, "This is to certify that", FONT_REGULAR, 16, GREY)

    name_size = _fit(data.recipient_name, FONT_BOLD, 38, 12)
    _centered(c, PAGE_H - 240, data.recipient_name, FONT_BOLD, name_size)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(PAGE_W / 2 - 220, PAGE_H - 252, PAGE_W / 2 + 220, PAGE_H - 252)

    _centered(c, PAGE_H - 295, "has successfully completed", FONT_REGULAR, 16, GREY)
    _centered(c, PAGE_H - 335, data.course_name, FONT_BOLD, _fit(data.course_name, FONT_BOLD, 26, 12))
    _centered(c, PAGE_H - 375, f"Issued by {data.organization}", FONT_REGULAR, 15, GREY)

    c.setFont(FONT_REGULAR, 12)
    c.setFillColor(GREY)
    c.drawString(70, 70, f"Date: {data.issue_date.strftime('%d %B %Y')}")
    c.drawRightString(PAGE_W - 70, 70, f"Certificate ID: {data.certificate_id}")

    c.showPage()
    c.save()


def generate_certificate_pdf(data: CertificateData, output_path: Path) -> None:
    for label, value in (
        ("recipient name", data.recipient_name),
        ("course name", data.course_name),
        ("organization", data.organization),
        ("title", data.title),
    ):
        if not is_renderable(value):
            raise CertificateGenerationError(
                f"{label} contains characters the certificate font cannot render"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(output_path.name + ".tmp")
    try:
        _draw(data, tmp)
        os.replace(tmp, output_path)  # atomic: never leave a half-written PDF at the final path
    finally:
        tmp.unlink(missing_ok=True)
