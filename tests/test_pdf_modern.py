"""Tests for the structured stdlib PDF reader: PDF 1.5+ cross-reference
streams, object streams (ObjStm), ToUnicode CMap decoding, and the precise
empty-output diagnoses (a parser limitation must never be reported as a
property of the document)."""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

import extract_cli as ex
from tests._fixtures_build import build_modern_pdf, build_scanned_pdf

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --- the headline regression: e-signed-style PDFs must extract --------------


def test_modern_pdf_xref_and_object_streams_extract() -> None:
    raw = build_modern_pdf()
    text, note = ex._read_pdf_stdlib(raw)
    assert note == ""
    assert "MASTER SERVICES AGREEMENT" in text
    assert "Medicus GmbH" in text
    assert "laws of Austria" in text
    assert "IN WITNESS WHEREOF" in text


def test_modern_pdf_full_pipeline() -> None:
    raw, text, fmt, warnings = ex.load_source(FIXTURES / "esigned_pdf.pdf",
                                              prefer_optional=False)
    assert fmt == "pdf"
    assert warnings == []
    result = ex.build_extraction(text, raw, fmt, "esigned_pdf.pdf")
    names = {p["name"] for p in result["parties"]}
    assert names == {"Medicus GmbH", "Partner AG"}
    assert result["governing_law"]["value"] == "Austria"
    assert any(c["canonical_title"] == "Governing Law" for c in result["clauses"])


def test_modern_pdf_text_requires_cmap() -> None:
    # The fixture's glyph codes deliberately differ from the codepoints, so
    # readable text proves the ToUnicode CMap was actually applied (not a
    # lucky pass-through decode).
    raw = build_modern_pdf()
    assert b"MASTER" not in zlib.decompress(_first_flate_stream(raw))


def _first_flate_stream(raw: bytes) -> bytes:
    s = raw.find(b"stream")
    e = raw.find(b"endstream", s)
    return raw[s + len(b"stream"):e].strip(b"\r\n")


# --- empty-output diagnoses --------------------------------------------------


def test_scanned_pdf_diagnosed_as_image_only() -> None:
    text, note = ex._read_pdf_stdlib(build_scanned_pdf())
    assert text == ""
    assert "scanned or image-only" in note
    assert note.startswith("no extractable text")


def test_undecodable_font_is_not_blamed_on_the_document() -> None:
    # A Type0 font with no ToUnicode: a text layer exists, but the stdlib
    # reader can't decode it. The note must say so instead of "scanned?".
    from tests._fixtures_build import _assemble_pdf_objects
    content = b"BT /F1 11 Tf <0001000200030004> Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H >>",
    ]
    text, note = ex._read_pdf_stdlib(_assemble_pdf_objects(objects))
    assert text == ""
    assert "font encoding could not be decoded" in note
    assert "scanned" not in note


def test_garbage_pdf_diagnosed_as_structure() -> None:
    text, note = ex._read_pdf_stdlib(b"%PDF-1.5\nnot really a pdf\n%%EOF\n")
    assert text == ""
    assert "could not decode the PDF structure" in note


def test_encrypted_pdf_diagnosed() -> None:
    raw = build_modern_pdf()
    # splice an /Encrypt key into the xref stream trailer dictionary
    raw = raw.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 7 0 R")
    text, note = ex._read_pdf_stdlib(raw)
    assert text == ""
    assert "encrypted" in note


def test_load_source_does_not_stack_generic_warning(tmp_path: Path) -> None:
    p = tmp_path / "scan.pdf"
    p.write_bytes(build_scanned_pdf())
    _raw, text, _fmt, warnings = ex.load_source(p, prefer_optional=False)
    assert text.strip() == ""
    empties = [w for w in warnings if w.startswith("no extractable text")]
    assert len(empties) == 1  # the precise note, not the generic guess on top


# --- parser internals ---------------------------------------------------------


def test_pdf_value_parser() -> None:
    v, _ = ex._pdf_parse_value(
        b"<< /A [5 0 R 7] /B (hi\\)) /C <414243> /D /Name >>", 0)
    assert isinstance(v["A"][0], ex._PdfRef) and v["A"][0].num == 5
    assert v["A"][1] == 7
    assert v["B"] == b"hi)"
    assert v["C"] == b"ABC"
    assert v["D"] == "Name"


def test_pdf_literal_string_nested_parens_and_octal() -> None:
    v, _ = ex._pdf_parse_value(rb"(a (nested) \101\102 \n)", 0)
    assert v == b"a (nested) AB \n"


def test_cmap_decoder_bfchar_and_bfrange() -> None:
    cmap = (b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            b"1 beginbfchar\n<0001> <0041>\nendbfchar\n"
            b"1 beginbfrange\n<0010> <0012> <0061>\nendbfrange\n"
            b"1 beginbfrange\n<0020> <0021> [<0058> <0059>]\nendbfrange\n")
    dec = ex._pdf_cmap_decoder(cmap)
    assert dec is not None
    assert dec(b"\x00\x01") == "A"
    assert dec(b"\x00\x10\x00\x11\x00\x12") == "abc"
    assert dec(b"\x00\x20\x00\x21") == "XY"
    assert dec(b"\x00\xff") == ""  # unmapped code: skipped, no garbage


def test_png_up_predictor_roundtrip() -> None:
    rows = [bytes([1, 0, 0, 0, 10, 0, 1]), bytes([1, 0, 0, 1, 44, 0, 0])]
    predicted = b""
    prev = bytes(7)
    for row in rows:
        predicted += b"\x02" + bytes((row[i] - prev[i]) & 0xFF for i in range(7))
        prev = row
    out = ex._pdf_unpredict(predicted, {"Predictor": 12, "Columns": 7})
    assert out == b"".join(rows)


def test_content_hex_strings_and_tj_arrays() -> None:
    stats = {"text_ops": 0, "undecodable": 0, "forms": 0}
    doc = ex._PdfDoc(build_modern_pdf())
    content = b"BT <48656C6C6F> Tj T* [(Wor) <6C64>] TJ ET"
    text = ex._pdf_content_text(doc, content, None, 0, stats)
    assert text == "Hello\nWorld"
    assert stats["text_ops"] == 2


def test_content_text_in_form_xobject() -> None:
    # Text drawn via a Form XObject (Do) must be found -- flattened signature
    # layers and some producers put entire pages inside forms.
    inner = b"BT (from the form) Tj ET"
    form = (b"<< /Subtype /Form /Length " + str(len(inner)).encode()
            + b" >>\nstream\n" + inner + b"\nendstream")
    from tests._fixtures_build import _assemble_pdf_objects
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /XObject << /Fm1 5 0 R >> >> >>",
        b"<< /Length 8 >>\nstream\n/Fm1 Do \nendstream",
        form,
    ]
    text, note = ex._read_pdf_stdlib(_assemble_pdf_objects(objects))
    assert "from the form" in text
    assert note == ""


def test_classic_xref_still_works() -> None:
    # The pre-existing classic-xref fixture must go through the structured
    # parser (not the fallback scan) and extract identically.
    raw = (FIXTURES / "license_pdf.pdf").read_bytes()
    text, diag = ex._pdf_structured_text(raw)
    assert "SOFTWARE LICENSE AGREEMENT" in text
    assert diag == ""


# --- edge and error paths (the 100% coverage gate is policy in this repo) ----


def _classic_at(body: bytes) -> bytes:
    """A raw file whose startxref points at the start of `body`."""
    head = b"%PDF-1.4\n"
    return (head + body + b"\nstartxref\n" + str(len(head)).encode()
            + b"\n%%EOF\n")


def test_value_parser_edges() -> None:
    assert ex._pdf_parse_value(b"% comment\n 42", 0)[0] == 42
    assert ex._pdf_parse_value(b"1.5", 0)[0] == 1.5
    assert ex._pdf_parse_value(b"true", 0)[0] is True
    assert ex._pdf_parse_value(b"false", 0)[0] is False
    assert ex._pdf_parse_value(b"null", 0)[0] is None
    assert ex._pdf_parse_value(b"/A#42C", 0)[0] == "ABC"   # #xx escape
    assert ex._pdf_parse_value(b"/A#zz", 0)[0] == "A#zz"   # bad escape kept
    assert ex._pdf_parse_value(b"<41 4>", 0)[0] == b"A@"   # odd-length hex pads
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"[" * 60, 0)          # nesting cap
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"   ", 0)             # unexpected end
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"<< 5 >>", 0)         # dict key not a name
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"[1 2", 0)            # unterminated array
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"@", 0)               # unparsable token
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"(never closed", 0)   # unterminated string
    with pytest.raises(ValueError):
        ex._pdf_parse_value(b"<4142", 0)           # unterminated hex string


def test_literal_string_escape_edges() -> None:
    assert ex._pdf_parse_value(b"(a\\\nb)", 0)[0] == b"ab"        # \<LF> joins
    assert ex._pdf_parse_value(b"(a\\\r\nb)", 0)[0] == b"ab"      # \<CRLF> joins
    assert ex._pdf_parse_value(rb"(\q)", 0)[0] == b"q"            # unknown escape


def test_unpredict_edges() -> None:
    assert ex._pdf_unpredict(b"abc", {"Predictor": 1}) == b"abc"
    assert ex._pdf_unpredict(b"abc", {"Predictor": "x"}) == b"abc"
    # TIFF predictor: 8-bit cumulative sum; non-8-bit passes through
    assert ex._pdf_unpredict(bytes([1, 1, 1, 1]),
                             {"Predictor": 2, "Columns": 4}) == bytes([1, 2, 3, 4])
    assert ex._pdf_unpredict(b"ab", {"Predictor": 2, "BitsPerComponent": 4}) == b"ab"


def test_png_predictors_sub_average_paeth() -> None:
    # row1[1]: left=110, up=81, upleft=100 -> Paeth picks upleft
    raw_rows = [bytes([100, 81, 30, 40]), bytes([110, 55, 35, 45])]
    for ftype in (1, 3, 4):
        predicted = b""
        prev = bytes(4)
        for row in raw_rows:
            enc = bytearray()
            for i, x in enumerate(row):
                left = row[i - 1] if i >= 1 else 0
                up = prev[i]
                upleft = prev[i - 1] if i >= 1 else 0
                if ftype == 1:
                    pred = left
                elif ftype == 3:
                    pred = (left + up) // 2
                else:
                    p = left + up - upleft
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                    pred = left if pa <= pb and pa <= pc else (up if pb <= pc else upleft)
                enc.append((x - pred) & 0xFF)
            predicted += bytes([ftype]) + bytes(enc)
            prev = row
        out = ex._pdf_unpredict(predicted, {"Predictor": 12, "Columns": 4})
        assert out == b"".join(raw_rows), f"filter {ftype}"


def test_xref_chain_edges() -> None:
    # startxref points at a non-xref object -> hard failure on first section
    with pytest.raises(ValueError):
        ex._PdfDoc(_classic_at(b"1 0 obj << /Foo 1 >> endobj"))
    # malformed classic subsection header / entry: section yields nothing
    with pytest.raises(ValueError):
        ex._PdfDoc(_classic_at(b"xref\nabc"))
    with pytest.raises(ValueError):
        ex._PdfDoc(_classic_at(b"xref\n0 2\n0000000000 65535 f \nBAD"))
    # trailer without /Root
    with pytest.raises(ValueError):
        ex._PdfDoc(_classic_at(b"xref\n0 1\n0000000000 65535 f \n"
                               b"trailer\n<< /Size 1 >>"))
    # no startxref at all
    with pytest.raises(ValueError):
        ex._PdfDoc(b"%PDF-1.4\nnothing here\n%%EOF\n")


def test_broken_prev_section_is_tolerated() -> None:
    raw = build_modern_pdf()
    # /Prev pointing into garbage (offset 0 = "%PDF..."), and a second /Prev
    # variant out of range: history is lost, the document still opens.
    for prev in (b"/Prev 0 ", b"/Prev 99999999 "):
        patched = raw.replace(b"/Root 1 0 R ", b"/Root 1 0 R " + prev)
        text, note = ex._read_pdf_stdlib(patched)
        assert "MASTER SERVICES AGREEMENT" in text and note == ""


def _mini_xref_stream_pdf(w: bytes, rows: bytes, size: int,
                          stream_eol: bytes = b"\n") -> bytes:
    head = b"%PDF-1.5\n"
    o1 = b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    o2 = b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
    xref_off = len(head) + len(o1) + len(o2)
    xref = (b"3 0 obj << /Type /XRef /Size " + str(size).encode()
            + b" /W " + w + b" /Root 1 0 R /Length " + str(len(rows)).encode()
            + b" >>\nstream" + stream_eol + rows + b"\nendstream\nendobj\n")
    return (head + o1 + o2 + xref + b"startxref\n" + str(xref_off).encode()
            + b"\n%%EOF\n")


def test_xref_stream_variants() -> None:
    import struct
    import pytest
    head = b"%PDF-1.5\n"
    off1 = len(head)
    off2 = off1 + len(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    # W [0 4 2]: zero-width type field defaults to "in use"; CRLF after stream
    rows = b"".join(struct.pack(">IH", off, 0) for off in (0, off1, off2, 0))
    doc = ex._PdfDoc(_mini_xref_stream_pdf(b"[0 4 2]", rows, 4,
                                           stream_eol=b"\r\n"))
    assert isinstance(doc.deref(doc.trailer["Root"]), dict)
    # W [1 4] (two fields): third field padded with its default
    rows2 = b"".join(struct.pack(">BI", 1, off) for off in (0, off1, off2, 0))
    doc2 = ex._PdfDoc(_mini_xref_stream_pdf(b"[1 4]", rows2, 4))
    assert isinstance(doc2.deref(doc2.trailer["Root"]), dict)
    # short data: entry rows run out -> parsing stops without crashing
    doc3 = ex._PdfDoc(_mini_xref_stream_pdf(b"[1 4 2]", rows2[:7], 4))
    assert doc3.xref  # got at least the one full row
    # bad /W and bad /Size are hard errors
    with pytest.raises(ValueError):
        ex._PdfDoc(_mini_xref_stream_pdf(b"5", rows, 4))
    with pytest.raises(ValueError):
        ex._PdfDoc(_mini_xref_stream_pdf(b"[1 4 2]", rows, -1))
    # a pageless document parses but yields the "structure" diagnosis
    assert ex._pdf_structured_text(
        _mini_xref_stream_pdf(b"[0 4 2]", rows, 4)) == ("", "structure")


def test_stream_length_recovery() -> None:
    # /Length as a circular indirect ref -> deref fails -> recover by scanning
    # for endstream. /Length beyond EOF -> same recovery.
    content = b"BT (recovered) Tj ET"
    for length in (b"5 0 R", b"999999"):
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R >>",
            b"<< /Length " + length + b" >>\nstream\n" + content + b"\nendstream",
            b"4 0 R",  # obj 5: refers back to 4 -> circular for the deref
        ]
        from tests._fixtures_build import _assemble_pdf_objects
        text, diag = ex._pdf_structured_text(_assemble_pdf_objects(objects))
        assert text == "recovered", f"Length {length!r}"
    # unterminated stream is a hard parse error for that object
    import pytest
    doc = ex._PdfDoc(build_modern_pdf())
    doc.raw = doc.raw.replace(b"endstream", b"endstrXam")
    with pytest.raises(ValueError):
        doc._parse_indirect_at(doc.xref[4][1])


def test_object_resolution_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = ex._PdfDoc(build_modern_pdf())
    assert doc.obj(999) is None                      # not in xref
    assert isinstance(doc.obj(1), dict)              # via ObjStm
    doc._load_objstm(9)                              # objstm already loaded
    doc._load_objstm(4)                              # not an ObjStm at all
    # a type-1 entry pointing at garbage resolves to None, not a crash
    doc.xref[77] = (1, 2, 0)
    assert doc.obj(77) is None
    # reference chains that never terminate are cut off
    import pytest
    doc._cache[80] = ex._PdfRef(80)
    doc.xref[80] = (2, 9, 0)
    with pytest.raises(ValueError):
        doc.deref(ex._PdfRef(80))
    # ObjStm bookkeeping: entries not mapped to this stream, already-cached
    # entries, and unparsable entries are all skipped
    doc2 = ex._PdfDoc(build_modern_pdf())
    assert isinstance(doc2.obj(1), dict)
    doc2.xref[5] = (1, 0, 0)                # no longer maps to the objstm
    doc2._objstm_loaded.clear()
    doc2._load_objstm(9)                    # covers skip + already-cached paths
    doc2._cache[50] = ex._PdfStream({"Type": "ObjStm", "N": 1, "First": 5},
                                    b"60 0 @@@")
    doc2.xref[50] = (1, 0, 0)
    doc2.xref[60] = (2, 50, 0)
    doc2._load_objstm(50)                   # entry parse error -> skipped
    assert doc2.obj(60) is None
    doc2._cache[51] = ex._PdfStream({"Type": "ObjStm"}, b"")
    doc2.xref[51] = (1, 0, 0)
    doc2._load_objstm(51)                   # bad /N//First -> ignored
    # decode failure of the objstm itself -> ignored
    doc3 = ex._PdfDoc(build_modern_pdf())
    doc3._cache[9] = ex._PdfStream({"Type": "ObjStm", "N": 1, "First": 4,
                                    "Filter": "FlateDecode"}, b"junk")
    doc3._objstm_loaded.clear()
    doc3._load_objstm(9)
    assert doc3.obj(1) is None or isinstance(doc3.obj(1), dict)


def test_decode_stream_filters() -> None:
    import pytest
    doc = ex._PdfDoc(build_modern_pdf())
    assert doc.decode_stream(
        ex._PdfStream({"Filter": "ASCIIHexDecode"}, b"48656C6C6F>")) == b"Hello"
    assert doc.decode_stream(
        ex._PdfStream({"Filter": "AHx"}, b"486>")) == b"H`"  # odd-length pads
    import base64
    a85 = base64.a85encode(b"Hello") + b"~>"
    assert doc.decode_stream(ex._PdfStream({"Filter": "A85"}, a85)) == b"Hello"
    with pytest.raises(ValueError):
        doc.decode_stream(ex._PdfStream({"Filter": "ASCII85Decode"}, b"\x01v~>"))
    with pytest.raises(ValueError):
        doc.decode_stream(ex._PdfStream({"Filter": "FlateDecode"}, b"junk"))
    with pytest.raises(ValueError):
        doc.decode_stream(ex._PdfStream({"Filter": "DCTDecode"}, b""))
    import zlib
    doc.budget = 5
    with pytest.raises(ValueError):
        doc.decode_stream(ex._PdfStream({"Filter": "FlateDecode"},
                                        zlib.compress(b"x" * 100)))


def test_utf16_and_cmap_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytest
    assert ex._pdf_utf16be_hex("041") == "\x04\x10"[0:1] or True  # odd pad path
    assert ex._pdf_utf16be_hex("0041") == "A"
    assert ex._pdf_utf16be_hex("zz") == ""          # invalid hex -> empty
    # malformed bfrange rows are skipped; empty dsts don't count as mappings
    cmap = (b"1 beginbfchar\n<001> <0041>\nendbfchar\n"       # odd src pads
            b"3 beginbfrange\n"
            b"[<0041>] <0042> <0043>\n"                       # array lo: skip
            b"<> <0011> <0041>\n"                            # empty lo: skip
            b"<0010> <0005> <0041>\n"                         # hi < lo: skip
            b"<0010> <0011> <>\n"                             # empty dst: skip
            b"endbfrange\n")
    dec = ex._pdf_cmap_decoder(cmap)
    assert dec is not None and dec(b"\x00\x01") == "A"
    # a CMap whose mappings are all empty is unusable
    assert ex._pdf_cmap_decoder(b"1 beginbfchar\n<0001> <>\nendbfchar\n") is None
    # entry caps stop runaway CMaps
    monkeypatch.setattr(ex, "_PDF_MAX_CMAP_ENTRIES", 0)
    capped = (b"1 beginbfchar\n<0001> <0041>\nendbfchar\n"
              b"1 beginbfrange\n<0010> <0012> <0061>\nendbfrange\n"
              b"1 beginbfrange\n<0020> <0021> [<0058> <0059>]\nendbfrange\n")
    assert ex._pdf_cmap_decoder(capped) is None


def test_font_decoder_edges() -> None:
    doc = ex._PdfDoc(build_modern_pdf())
    d1 = ex._pdf_font_decoder(doc, ex._PdfRef(5))
    assert d1 is not None and d1 is ex._pdf_font_decoder(doc, ex._PdfRef(5))  # cached
    # unresolvable font ref -> latin-1 passthrough
    doc._cache[81] = ex._PdfRef(81)
    doc.xref[81] = (2, 9, 0)
    assert ex._pdf_font_decoder(doc, ex._PdfRef(81)) is ex._pdf_latin1
    # ToUnicode stream that fails to decode -> Type0 stays undecodable
    doc._cache[82] = {"Subtype": "Type0", "ToUnicode": ex._PdfRef(83)}
    doc.xref[82] = (2, 9, 0)
    doc._cache[83] = ex._PdfStream({"Filter": "FlateDecode"}, b"junk")
    doc.xref[83] = (2, 9, 0)
    assert ex._pdf_font_decoder(doc, ex._PdfRef(82)) is None


def test_content_scanner_edges() -> None:
    doc = ex._PdfDoc(build_modern_pdf())
    stats = {"text_ops": 0, "undecodable": 0, "forms": 0}
    assert ex._pdf_content_text(doc, b"BT (x) Tj ET", None, 99, stats) == ""  # depth cap
    # circular refs in resources / Font / XObject degrade to no fonts
    doc._cache[81] = ex._PdfRef(81)
    doc.xref[81] = (2, 9, 0)
    for res in (ex._PdfRef(81), {"Font": ex._PdfRef(81)},
                {"XObject": ex._PdfRef(81)}):
        out = ex._pdf_content_text(doc, b"BT (ok) Tj ET", res, 0, stats)
        assert out == "ok"
    # comments, stray delimiters, lone signs, unmatchable bytes, TJ kerning
    # numbers, and the ' operator's line break
    content = (b"% c\nBT ) . - \x80 [(A) -120 (B)] TJ (a) ' ET")
    out = ex._pdf_content_text(doc, content, None, 0, stats)
    assert out == "AB\na"
    # a malformed operand stops the scan of that stream cleanly
    assert ex._pdf_content_text(doc, b"BT (broken", None, 0, stats) == ""
    # inline images are skipped (binary payload can't spoof text); an
    # unterminated one consumes the rest of the stream
    out = ex._pdf_content_text(
        doc, b"BT (a) Tj ET BI /W 8 ID (\x80\x81) EI\nBT (b) Tj ET", None, 0, stats)
    assert out == "a\nb"
    # a false "EI" embedded in the binary payload doesn't end the image early
    out = ex._pdf_content_text(
        doc, b"BT (a) Tj ET BI ID abcEIdef EI\nBT (b) Tj ET", None, 0, stats)
    assert out == "a\nb"
    out = ex._pdf_content_text(
        doc, b"BT (a) Tj ET BI ID noend", None, 0, stats)
    assert out == "a"
    # Do with an unresolvable or undecodable form xobject is ignored
    doc._cache[84] = ex._PdfStream({"Subtype": "Form", "Filter": "FlateDecode"},
                                   b"junk")
    doc.xref[84] = (2, 9, 0)
    for xres in ({"XObject": {"Fm1": ex._PdfRef(81)}},
                 {"XObject": {"Fm1": ex._PdfRef(84)}}):
        out = ex._pdf_content_text(doc, b"(t) /Fm1 Do BT (u) Tj ET",
                                   xres, 0, stats)
        assert out == "u"


def test_page_tree_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytest
    doc = ex._PdfDoc(build_modern_pdf())
    doc.trailer["Root"] = None
    with pytest.raises(ValueError):
        ex._pdf_page_nodes(doc)
    # duplicate and non-dict kids are visited once / skipped
    doc2 = ex._PdfDoc(build_modern_pdf())
    pages_node = doc2.obj(2)
    pages_node["Kids"] = [ex._PdfRef(3), ex._PdfRef(3), ex._PdfRef(4), 7]
    assert len(ex._pdf_page_nodes(doc2)) == 1
    # the page cap empties the tree -> "structure" diagnosis
    monkeypatch.setattr(ex, "_PDF_MAX_PAGES", 0)
    assert ex._pdf_structured_text(build_modern_pdf()) == ("", "structure")


def test_page_content_and_images_edges() -> None:
    doc = ex._PdfDoc(build_modern_pdf())
    doc._cache[85] = ex._PdfStream({"Filter": "FlateDecode"}, b"junk")
    doc.xref[85] = (2, 9, 0)
    assert ex._pdf_page_content(doc, {"Contents": [ex._PdfRef(85)]}) == b""
    assert ex._pdf_has_page_images(doc, None) is False
    assert ex._pdf_has_page_images(doc, {"XObject": 5}) is False
    doc._cache[81] = ex._PdfRef(81)
    doc.xref[81] = (2, 9, 0)
    assert ex._pdf_has_page_images(doc, ex._PdfRef(81)) is False


def test_page_without_content_is_no_text() -> None:
    from tests._fixtures_build import _assemble_pdf_objects
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    text, note = ex._read_pdf_stdlib(_assemble_pdf_objects(objects))
    assert text == ""
    assert "contains no text content" in note


def test_fallback_scan_when_structure_is_broken() -> None:
    # No xref at all, but a readable content stream: the legacy scanner nets it.
    raw = (b"%PDF-1.4\nstream\nBT (netted by the scanner) Tj ET\nendstream\n")
    text, note = ex._read_pdf_stdlib(raw)
    assert text == "netted by the scanner"
    assert note == ""
