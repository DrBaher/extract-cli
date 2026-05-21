"""Generate the binary fixtures (.docx, .pdf) for the test corpus using only
the stdlib, so the corpus is reproducible without any third-party writer.

Run directly (``python tests/_fixtures_build.py``) or via ``make fixtures``.
conftest.py also calls ensure_binary_fixtures() so the suite self-heals if a
binary fixture is ever missing.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --- DOCX: an employment agreement with bold-numbered headings (Tier 2) -----

_DOCX_PARAS = [
    ("EMPLOYMENT AGREEMENT", False),
    ('This Employment Agreement (the "Agreement") is made and entered into as of '
     'September 9, 2024, by and between Umbrella Corporation (the "Employer") and '
     'Jordan Rivera (the "Employee").', False),
    ("1. Position and Duties", True),
    ("The Employee shall serve as Director of Engineering and perform the duties "
     "customarily associated with that position.", False),
    ("2. Compensation", True),
    ("The Employer shall pay the Employee an annual base salary of $185,000, "
     "payable in accordance with the Employer's standard payroll practices.", False),
    ("3. Term and Termination", True),
    ("The initial term of this Agreement is two (2) years. Either party may "
     "terminate this Agreement upon thirty (30) days' written notice. This "
     "Agreement shall automatically renew for successive one-year terms.", False),
    ("4. Confidentiality", True),
    ("The Employee shall hold all Confidential Information in strict confidence "
     "during and after the term of employment.", False),
    ("5. Governing Law", True),
    ("This Agreement shall be governed by and construed in accordance with the "
     "laws of the Commonwealth of Massachusetts.", False),
]

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_paragraph(text: str, bold: bool) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (f"<w:p><w:r>{rpr}"
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


def build_docx() -> bytes:
    body = "".join(_docx_paragraph(t, b) for t, b in _DOCX_PARAS)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# --- PDF: a software license with ALL-CAPS headings (Tier 3) ----------------

_PDF_TEXT = """SOFTWARE LICENSE AGREEMENT

This Software License Agreement is entered into as of February 2, 2025, by and
between Cyberdyne Systems Corp. ("Licensor") and Tyrell Corporation ("Licensee").

GRANT OF LICENSE

Licensor grants Licensee a non-exclusive, non-transferable license to use the
Software.

LICENSE FEES

Licensee shall pay Licensor a one-time license fee of $75,000.

TERM

This Agreement shall remain in effect for a period of four (4) years. This
Agreement shall not automatically renew.

TERMINATION

Either party may terminate upon forty-five (45) days' written notice.

GOVERNING LAW

This Agreement shall be governed by the laws of the State of Washington.
"""


def _pdf_escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble_pdf(content: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {size} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("latin-1")
    return out


def build_pdf() -> bytes:
    parts = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
    for line in _PDF_TEXT.split("\n"):
        parts.append(f"({_pdf_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return _assemble_pdf("\n".join(parts).encode("latin-1"))


def build_scanned_pdf() -> bytes:
    """A PDF whose only content stream draws a rectangle -- no text operators,
    mimicking a scanned/image-only page. Exercises graceful degradation."""
    content = b"0 0 612 792 re\nf\n"
    return _assemble_pdf(content)


_BINARY_FIXTURES = {
    "employment_docx.docx": build_docx,
    "license_pdf.pdf": build_pdf,
    "scanned.pdf": build_scanned_pdf,
}


def ensure_binary_fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in _BINARY_FIXTURES.items():
        target = FIXTURES / name
        if not target.exists():
            target.write_bytes(builder())


def rebuild_binary_fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in _BINARY_FIXTURES.items():
        (FIXTURES / name).write_bytes(builder())


if __name__ == "__main__":
    rebuild_binary_fixtures()
    for name in _BINARY_FIXTURES:
        print(f"wrote {FIXTURES / name}")
