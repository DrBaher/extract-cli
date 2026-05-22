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


def _docx_paragraph(text: str, bold: bool = False, style: str = "",
                    numbered: bool = False, ilvl: int = 0) -> str:
    inner = ""
    if style:
        inner += f'<w:pStyle w:val="{style}"/>'
    if numbered:
        inner += f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="1"/></w:numPr>'
    ppr = f"<w:pPr>{inner}</w:pPr>" if inner else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (f"<w:p>{ppr}<w:r>{rpr}"
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


# An auto-numbered agreement: clauses are w:numPr list paragraphs with NO heading
# style and NO visible number (Word generates "1.", "2." from numbering.xml).
# Run-in titles at ilvl 0/1 are clause headings; ilvl-2 full sentences are body
# and must be rejected. Mirrors real DOCX like the Common Paper DPA.
_NUMBERED_DOCX_PARAS = [
    ('Data Processing Agreement', False, "", False, 0),
    ('This Data Processing Agreement is made as of July 7, 2024, by and between '
     'Globex Cloud, Inc. ("Provider") and Initech Ltd. ("Customer").', False, "", False, 0),
    ('Definitions', False, "", True, 0),
    ('Processing.  Provider will process Customer Data only on documented '
     'instructions from the Customer.', False, "", True, 0),
    ('Confidentiality.  Provider will keep Customer Data confidential.', False, "", True, 1),
    ('Subprocessors.  Provider may engage subprocessors as permitted.', False, "", True, 1),
    ('Provider will ensure each subprocessor is bound by equivalent obligations '
     'and remains fully liable for their performance under this Agreement.', False, "", True, 2),
    ('Governing Law.  This Agreement is governed by the laws of the State of '
     'New York.', False, "", True, 0),
]


def build_numbered_docx() -> bytes:
    return _docx_package(
        "".join(_docx_paragraph(t, b, style=s, numbered=n, ilvl=l)
                for t, b, s, n, l in _NUMBERED_DOCX_PARAS)
    )


# A Word-styled agreement: clause structure carried by Heading1 styles (their
# numbers are auto-generated, absent from text), including a run-in heading and
# a full sentence that merely carries the heading style (must be rejected).
_HEADING_DOCX_PARAS = [
    ('Cloud Service Agreement', False, "Title"),
    ('This Cloud Service Agreement is entered into as of April 4, 2024, by and '
     'between Initech Software, Inc. (the "Provider") and Globex Corporation '
     '(the "Customer").', False, ""),
    ('Confidentiality', False, "Heading1"),
    ('Each party will protect the other party’s Confidential Information.', False, ""),
    ('Payment.  Customer will pay the fees set out in the Order Form within '
     'thirty (30) days.', False, "Heading1"),
    ('Term & Termination', False, "Heading1"),
    ('The term of this Agreement is two (2) years and will automatically renew '
     'for successive one-year terms.', False, ""),
    ('Either party may terminate this Agreement upon material breach that '
     'remains uncured for thirty days after written notice.', False, "Heading1"),
    ('Governing Law', False, "Heading1"),
    ('This Agreement is governed by the laws of the State of New York.', False, ""),
]


def _docx_package(body: str) -> bytes:
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
    # Deterministic: a fixed timestamp on every entry so regenerating the
    # fixture produces byte-identical output (stable sha256 -> stable goldens).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", content_types),
                           ("_rels/.rels", rels),
                           ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def build_docx() -> bytes:
    return _docx_package("".join(_docx_paragraph(t, b) for t, b in _DOCX_PARAS))


def build_heading_docx() -> bytes:
    return _docx_package(
        "".join(_docx_paragraph(t, b, style=s) for t, b, s in _HEADING_DOCX_PARAS)
    )


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
    "heading_docx.docx": build_heading_docx,
    "numbered_docx.docx": build_numbered_docx,
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
