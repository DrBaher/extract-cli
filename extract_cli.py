#!/usr/bin/env python3
"""extract-cli -- the open-loop front door of the contract-ops CLI suite.

The suite is a contract lifecycle (store -> draft -> review -> diff -> convert
-> sign) that, until now, only handled documents it authored from its own
templates. `extract-cli` is "passport control": it ingests ANY document --
yours or a counterparty's foreign paper -- in .md/.txt/.html (natively), .docx,
or .pdf, and emits a structured JSON representation that the rest of the suite
(nda-review-cli, compare-cli, contract-vault) consumes.

Two extraction tiers:
  * DETERMINISTIC (default, always on): parties, dates, defined-term inventory,
    the CLAUSE MAP, governing law, best-effort term/notice/value. Pure
    regex/structure -- no network, no LLM.
  * LLM (opt-in via --llm only): the fuzzy fields (renewal mechanics,
    obligation phrasing, ambiguous governing law). Always skippable; the
    deterministic core is fully useful without it.

Every extracted field carries a `confidence` and a `source` in
{deterministic, llm, none} -- downstream tools treat fields as "verify, not
trust".

Stdlib-only. Single file. The clause-detection cascade (H2 -> bold-numbered ->
ALL-CAPS) and the canonical-vocabulary alias normalization are ported from
template-vault-cli so a foreign document's clauses land on the suite's shared
clause vocabulary.

Part of the contract-ops CLI suite. See docs/INTEROP.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html.parser
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__version__ = "0.1.9"

# Bumped independently of the package version when the *extraction logic*
# changes in a way downstream consumers should notice. Embedded in `_meta`.
EXTRACTOR_VERSION = "0.1.9"

# JSON Schema version of the output contract (docs/spec/extract-output.schema.json).
SCHEMA_VERSION = 1

# Resource bounds for untrusted input (extract-cli ingests counterparty files).
# A hard cap on the file we'll read, and a cap on how much a .docx/.pdf is
# allowed to DECOMPRESS to -- so a zip-bomb .docx or zlib-bomb .pdf can't
# exhaust memory. Generous enough that real contracts never hit them.
MAX_INPUT_BYTES = 100 * 1024 * 1024        # 100 MB on-disk file
MAX_DECOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB after decompression

JSON = Dict[str, Any]

CLI_NAME = "extract-cli"

# ---------------------------------------------------------------------------
# Streams / color (convention-shared with the suite; see docs/INTEROP.md)
# ---------------------------------------------------------------------------


def _color_enabled(stream: Any = None) -> bool:
    """Auto-detect color support: opt out via NO_COLOR (https://no-color.org/),
    force on via FORCE_COLOR, otherwise only when the stream is a tty."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    s = stream if stream is not None else sys.stdout
    try:
        return bool(s.isatty())
    except Exception:
        return False


def _c(text: str, code: str) -> str:
    if not _color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(s: str) -> str:
    return _c(s, "32")


def _yellow(s: str) -> str:
    return _c(s, "33")


def _red(s: str) -> str:
    return _c(s, "31")


def _bold(s: str) -> str:
    return _c(s, "1")


def _dim(s: str) -> str:
    return _c(s, "2")


def _eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def _why_print(args_ns: argparse.Namespace, header: str, *lines: str) -> None:
    """Emit a `--why` block to **stderr** so it never pollutes piped stdout.
    No-op unless `--why` was passed. Plain-text envelope (matches this repo's
    siblings template-vault-cli / draft-cli)."""
    if not getattr(args_ns, "why", False):
        return
    _eprint(f"\n[why] {header}")
    for line in lines:
        _eprint(f"  {line}")


def _warn(args_ns: Optional[argparse.Namespace], msg: str) -> None:
    """Diagnostic to stderr, suppressed by -q/--silent."""
    if args_ns is not None and getattr(args_ns, "silent", False):
        return
    _eprint(_yellow("warning:") + f" {msg}")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExtractError(Exception):
    """User-actionable error. main() prints it and exits non-zero."""


# ---------------------------------------------------------------------------
# Clause-detection cascade  (ported from template-vault-cli `template_vault_cli.py`)
#
# Tier 1: H2 headings (`## Title`)            -- Markdown-native templates.
# Tier 2: bold-numbered (`**1. Purpose**`)    -- typical of DOCX -> text.
# Tier 3: ALL-CAPS standalone lines           -- typical of legal PDFs.
# The fallback tiers only run when the prior tier finds nothing, so they can't
# shadow real structure. Foreign clauses are then normalized onto the suite's
# canonical vocabulary via the alias index below.
# ---------------------------------------------------------------------------

# Auto-detect clause headers by H2 only (not H3+). Anchored at line start.
H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Bold-numbered:  **1. Purpose**  /  **Section 4. Term**  /  **(1) Scope**
_BOLD_HEADING_RE = re.compile(
    r"^\*\*\s*"
    r"(?:"
    r"(?:Article|Section|Sec\.?|Art\.?|Clause|Part|§)\s+\S+\.?"  # word-prefixed
    r"|"
    r"\(\d+\)"  # (1)
    r"|"
    r"\d+(?:\.\d+)*"  # 1 / 1.2.3
    r")"
    r"[\.\):\s]+"
    r"([^\*\n]+?)"
    r"\s*\*\*\s*$",
    re.MULTILINE,
)

# ALL-CAPS standalone heading: blank-line framed on both sides (so inline
# shouts in prose don't qualify); doesn't start with `[` (so `[BRACKETED]`
# placeholders never match). Single-token lines need >= 4 ASCII letters
# (enforced in _qualifies_as_all_caps_heading).
_ALL_CAPS_HEADING_RE = re.compile(
    r"(?:^|\n)\n([A-Z][A-Z0-9 \-/&,]{1,}[A-Z0-9])\s*\n\n",
)

# Roman numerals 1-39 -- covers virtually all legal-document section numbering.
# Longer alternatives come first within each group so the regex engine doesn't
# short-circuit on a prefix match (bare V / X must still match).
_ROMAN_RE = (
    r"(?:(?:XXX|XX|X)(?:IX|IV|VIII|VII|VI|V|III|II|I)?"
    r"|IX|IV|VIII|VII|VI|V|III|II|I)"
)

# Leading numbering tokens to strip from a clause title. Order matters: longer
# Article/Section forms come before bare numbers so they're consumed first.
_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"(?:Article|Section|Sec\.?|Art\.?|Clause|Part)\s+"
    r"(?:" + _ROMAN_RE + r"|\d+(?:\.\d+)*)"
    r"|"
    r"§\s*\d+(?:\.\d+)*"
    r"|"
    r"\(\d+\)"
    r"|"
    r"\[\d+\]"
    r"|"
    r"\d+(?:\.\d+)+"
    r"|"
    r"\d+"
    r")"
    r"[\.\)\]:\s]*",
    re.IGNORECASE,
)


def _strip_clause_number(s: str) -> str:
    """Remove a leading numbering token (`1.`, `1)`, `(1)`, `[1]`, `1.2.3`,
    `Article I.`, `Section 4.`, `§ 4.2`). Idempotent."""
    return _NUMBER_PREFIX_RE.sub("", s, count=1).strip()


def _qualifies_as_all_caps_heading(title: str) -> bool:
    """Single-token ALL-CAPS lines need >= 4 ASCII letters (so 'TER' doesn't
    qualify but 'TERM' does). Multi-token lines pass through."""
    tokens = title.split()
    if len(tokens) >= 2:
        return True
    return sum(1 for ch in title if "A" <= ch <= "Z") >= 4


# Tier between bold-numbered and ALL-CAPS: plain numbered headings on their own
# line -- "1. Termination", "5. Wage Compensation", "Section 3. Payment",
# "Article IV. Confidentiality". These are the dominant real-world format in
# foreign paper (and aren't caught by H2, **bold**, or ALL-CAPS). A title-case
# heuristic distinguishes a heading from a numbered *sentence* or list item.
_NUMBERED_HEADING_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?:Article|Section|ARTICLE|SECTION)[ \t]+)?"
    r"(?:" + _ROMAN_RE + r"|\d{1,2})\.?"
    r"[ \t]+"
    r"([A-Z][A-Za-z][^\n]{0,58})"
    r"[ \t]*$",
    re.MULTILINE,
)

# Lowercase words allowed inside an otherwise Title-Cased heading.
_HEADING_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "with",
    "by", "at", "as", "per", "from", "into", "nor", "but",
}


def _qualifies_as_numbered_heading(title: str) -> bool:
    """A numbered line qualifies as a heading only if its title looks like a
    heading: 1-9 words, Title-Cased (every word starts uppercase or is a short
    lowercase connector), no sentence-y lowercase verbs. A single word must be
    >= 4 letters. Rejects 'The parties agree as follows' but accepts 'Wage
    Compensation' and 'Term And Nature Of Employment'."""
    t = title.strip().rstrip(".").strip()
    words = t.split()
    if not (1 <= len(words) <= 9):
        return False
    if len(words) == 1:
        return sum(1 for ch in words[0] if ch.isalpha()) >= 4 and words[0][:1].isupper()
    for w in words:
        if w[:1].isupper() or not w[:1].isalpha():
            continue  # capitalized word, or punctuation/number token
        if w.lower() in _HEADING_STOPWORDS:
            continue  # allowed connector
        return False  # a lowercase content word => this is a sentence, not a heading
    return True


# A bare "ARTICLE N" / "SECTION N" line whose title sits on the FOLLOWING line
# (common in formal agreements). Detected as a pair; reported under the
# "numbered" tier so no new schema value is introduced.
_ARTICLE_LINE_RE = re.compile(
    r"^[ \t]*(?:ARTICLE|Article|SECTION|Section)[ \t]+(?:" + _ROMAN_RE + r"|\d{1,2})"
    r"[ \t]*[.:–—-]?[ \t]*$",
    re.MULTILINE,
)


def _detect_two_line_articles(text: str) -> List[JSON]:
    """Pair each `ARTICLE N` marker line with the heading on the next non-blank
    line. Fires only with >= 2 well-formed pairs, so a one-off `ARTICLE` mention
    can't trigger it."""
    markers = list(_ARTICLE_LINE_RE.finditer(text))
    if len(markers) < 2:
        return []
    out: List[JSON] = []
    for i, m in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        title_line = ""
        for ln in text[m.end():end].splitlines():
            if ln.strip():
                title_line = ln.strip()
                break
        title = _strip_clause_number(title_line)
        # Reject when the next line is itself a numbered section header with body
        # ("Section 1.01. Term. The term ...") or simply not heading-like.
        if not title or not _qualifies_as_numbered_heading(title):
            continue
        out.append({"title": title, "detected": title_line, "anchor": title_line,
                    "start": m.start(), "end": end, "tier": "numbered"})
    return out


def detect_clauses(text: str) -> List[JSON]:
    """Run the clause-detection cascade and return clauses with their tier.

    Returns [{title, detected, anchor, start, end, tier}, ...]. `title` is the
    numbering-stripped heading; `detected` is the raw heading line as it
    appeared. The first tier that fires wins (H2 needs >= 1 hit; the fallbacks
    need >= 2 to avoid false positives)."""
    h2 = list(H2_RE.finditer(text))
    if h2:
        return _matches_to_clauses(text, h2, group=1, tier="h2")
    bold = list(_BOLD_HEADING_RE.finditer(text))
    if len(bold) >= 2:
        return _matches_to_clauses(text, bold, group=1, tier="bold-numbered")
    numbered = [
        m for m in _NUMBERED_HEADING_RE.finditer(text)
        if _qualifies_as_numbered_heading(m.group(1))
    ]
    if len(numbered) >= 2:
        return _matches_to_clauses(text, numbered, group=1, tier="numbered")
    articles = _detect_two_line_articles(text)
    if len(articles) >= 2:
        return articles
    caps = [
        m for m in _ALL_CAPS_HEADING_RE.finditer(text)
        if _qualifies_as_all_caps_heading(m.group(1))
    ]
    if len(caps) >= 2:
        return _matches_to_clauses(text, caps, group=1, tier="all-caps")
    return []


def _matches_to_clauses(text: str, matches: List["re.Match[str]"], group: int,
                        tier: str) -> List[JSON]:
    """Build clause dicts from regex matches whose `group` holds the title.
    The clause body runs from the heading line to the next heading (or EOF)."""
    out: List[JSON] = []
    for i, m in enumerate(matches):
        raw = m.group(group).strip()
        title = _strip_clause_number(raw)
        # Anchor line: for ALL-CAPS, step past the leading newline gap the
        # regex captured so the span starts at the heading line itself.
        anchor_start = text.rfind(m.group(group), m.start(), m.end())
        line_start = text.rfind("\n", 0, anchor_start) + 1
        line_end = text.find("\n", line_start)
        if line_end == -1:
            line_end = len(text)
        anchor = text[line_start:line_end]
        start = line_start
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({
            "title": title,
            "detected": anchor.strip(),
            "anchor": anchor,
            "start": start,
            "end": end,
            "tier": tier,
        })
    return out


def _norm_clause_key(s: str) -> str:
    """Normalize a clause title/alias for matching (number-stripped, trailing
    punctuation removed, lowercased)."""
    return _strip_clause_number(s).strip().lower().rstrip(" .:;,")


# ---------------------------------------------------------------------------
# Canonical clause vocabulary
#
# template-vault-cli stores `clause_aliases` per-template (canonical_title ->
# [alias, ...]). A FOREIGN document carries no such map, so extract-cli ships a
# built-in default vocabulary -- the suite's shared clause names -- and maps a
# document's detected clause titles onto it. This is the differentiator: it
# turns "whatever the counterparty called their sections" into the canonical
# vocabulary nda-review-cli / compare-cli already speak.
# ---------------------------------------------------------------------------

CANONICAL_CLAUSE_ALIASES: Dict[str, List[str]] = {
    "Definitions": ["definitions", "defined terms", "interpretation", "construction"],
    "Confidentiality": [
        "confidentiality", "non-disclosure", "nondisclosure", "confidential information",
        "confidentiality obligations", "secrecy", "protection of confidential information",
    ],
    "Term": ["term", "duration", "agreement term", "term of agreement"],
    "Termination": ["termination", "term and termination", "right to terminate", "termination for cause"],
    "Governing Law": [
        "governing law", "applicable law", "choice of law", "law and jurisdiction",
        "governing law and jurisdiction",
    ],
    "Dispute Resolution": ["dispute resolution", "arbitration", "disputes", "mediation"],
    "Indemnification": ["indemnification", "indemnity", "hold harmless", "indemnities"],
    "Limitation of Liability": [
        "limitation of liability", "liability", "limitation on liability", "liability cap",
        "exclusion of liability",
    ],
    "Intellectual Property": [
        "intellectual property", "ip rights", "ownership of ip", "proprietary rights",
        "intellectual property rights", "ownership",
    ],
    "Payment": ["payment", "fees", "compensation", "fees and payment", "consideration",
                "pricing", "invoicing", "invoices", "invoice"],
    "Warranties": [
        "warranties", "representations and warranties", "warranty", "reps and warranties",
        "representations",
    ],
    "Assignment": ["assignment", "assignability", "assignment and delegation"],
    "Notices": ["notices", "notice"],
    "Force Majeure": ["force majeure", "acts of god"],
    "Entire Agreement": ["entire agreement", "integration", "complete agreement"],
    "Severability": ["severability", "severance"],
    "Waiver": ["waiver", "no waiver"],
    "Non-Compete": [
        "non-compete", "noncompete", "noncompetition", "non-competition",
        "covenant not to compete",
    ],
    "Non-Solicitation": ["non-solicit", "non-solicitation", "nonsolicitation", "no solicitation"],
    "Data Protection": ["data protection", "data privacy", "gdpr", "privacy", "personal data",
                        "customer data", "customer content", "protection by provider",
                        "protection by customer"],
    "Insurance": ["insurance"],
    "Counterparts": ["counterparts"],
    "Survival": ["survival", "survival of obligations"],
    "Amendment": ["amendment", "amendments", "modification", "modifications", "changes"],
    "Relationship of the Parties": [
        "relationship of the parties", "independent contractor", "no partnership", "no agency",
    ],
    "Compliance with Laws": ["compliance with laws", "compliance", "anti-corruption",
                             "anti-bribery", "export controls", "export control"],
    "Publicity": ["publicity", "announcements", "press releases"],
    # Added from a 58-document real-corpus survey of common unmapped titles.
    "Exclusions": ["exclusions", "exceptions", "permitted disclosures", "required disclosures",
                   "exclusions from confidential information"],
    "Remedies": ["remedies", "injunctive relief", "equitable relief", "exclusive remedy",
                 "non-exhaustive remedies", "specific performance"],
    "Restrictions": ["restrictions", "use restrictions", "usage restrictions",
                     "license restrictions", "restrictions and obligations"],
    "Taxes": ["taxes", "tax matters", "withholding"],
    "Reservation of Rights": ["reservation of rights", "reservation of right"],
    "Third-Party Beneficiaries": ["third-party beneficiaries", "third party beneficiaries",
                                  "no third-party beneficiary", "no third party beneficiaries"],
    "Feedback": ["feedback", "feedback and usage data"],
    "Miscellaneous": ["miscellaneous", "general terms", "general provisions"],
    # Round 2 (SaaS / services common clauses from the corpus tail).
    "Suspension": ["suspension", "suspension of service", "suspension of services"],
    "Support": ["support", "support services", "technical support", "customer support"],
    "Service Levels": ["service levels", "service level agreement", "sla", "service level"],
}


def _build_alias_index() -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for canonical, aliases in CANONICAL_CLAUSE_ALIASES.items():
        idx[_norm_clause_key(canonical)] = canonical
        for alias in aliases:
            idx[_norm_clause_key(alias)] = canonical
    return idx


_ALIAS_INDEX = _build_alias_index()


def _canonicalize_clause(detected_title: str) -> Tuple[Optional[str], bool]:
    """Map a detected clause title to a canonical suite title.

    Returns (canonical_title, mapped). On an exact alias/canonical hit, returns
    the canonical name. Otherwise tries a substring containment match against
    the index (so 'Confidentiality and Non-Disclosure' still maps). Falls back
    to a Title-Cased copy of the detected title with mapped=False."""
    key = _norm_clause_key(detected_title)
    if not key:
        return None, False
    canon = _ALIAS_INDEX.get(key)
    if canon is not None:
        return canon, True
    # Containment: longest alias key contained in (or containing) the title.
    best: Optional[str] = None
    best_len = 0
    for alias_key, canonical in _ALIAS_INDEX.items():
        if len(alias_key) >= 5 and (alias_key in key or key in alias_key):
            if len(alias_key) > best_len:
                best, best_len = canonical, len(alias_key)
    if best is not None:
        return best, True
    return _titlecase(detected_title.strip().rstrip(" .:;,")), False


# ---------------------------------------------------------------------------
# Confidence model + field envelope
# ---------------------------------------------------------------------------


def _field(value: Any, confidence: float, source: str = "deterministic") -> JSON:
    """Wrap an extracted value with a confidence and a source. A `None` value
    collapses to the canonical 'not found' envelope."""
    if value is None:
        return {"value": None, "confidence": 0.0, "source": "none"}
    return {"value": value, "confidence": round(float(confidence), 2), "source": source}


def _none_field() -> JSON:
    return {"value": None, "confidence": 0.0, "source": "none"}


def _titlecase(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    # A fully-shouted heading (ALL-CAPS, e.g. from a PDF) is title-cased
    # outright; in a mixed-case title a short all-caps word is treated as a
    # deliberate acronym ("IP Rights") and preserved.
    whole_upper = s.isupper()
    parts = []
    for w in s.split():
        if not whole_upper and w.isupper() and len(w) <= 4:
            parts.append(w)
        else:
            parts.append(w[:1].upper() + w[1:].lower())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Deterministic extractors
# ---------------------------------------------------------------------------

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_PAT = (
    r"(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(?:" + _MONTHS + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?(?:" + _MONTHS + r")\.?,?\s+\d{4}"
    r")"
)
_DATE_RE = re.compile(_DATE_PAT, re.IGNORECASE)

# Highest-confidence: a date explicitly labeled "(the "Effective Date")".
_EFFDATE_LABEL_RE = re.compile(
    r"(" + _DATE_PAT + r")\s*\(\s*(?:the\s+)?[\"“]?\s*Effective\s+Date",
    re.IGNORECASE,
)
_EFFECTIVE_RE = re.compile(
    r"(?:effective(?:\s+date)?(?:\s+(?:as\s+of|date|on))?|"
    r"dated(?:\s+as\s+of)?|"
    r"made(?:\s+and\s+entered\s+into)?(?:\s+as\s+of|\s+on)?|"
    r"entered\s+into(?:\s+as\s+of|\s+on)?|"
    r"as\s+of)"
    r"[\s:,]+(?:the\s+)?(" + _DATE_PAT + r")",
    re.IGNORECASE,
)
_EXPIRE_RE = re.compile(
    r"(?:expir\w*|terminat\w*\s+on|end(?:s|ing)?\s+on|until|through|"
    r"remain\s+in\s+effect\s+until)"
    r"[\s:,]+(?:the\s+)?(" + _DATE_PAT + r")",
    re.IGNORECASE,
)

# Each party must start with a capital letter (optionally "the X"), a quote, or
# a paren. This is case-sensitive on purpose (no global IGNORECASE -- only the
# keywords are): it lets the engine skip an "and" that sits INSIDE a party's own
# description ("...V6E 3S7 and doing business as ...", where the right side
# starts lowercase) and find the real "and" before the second named entity.
_PARTY_START = r"(?:(?:[Tt]he|its)\s+)?[A-Z\"“(]"
_PARTY_BLOCK_RE = re.compile(
    r"(?i:\b(?:by\s+and\s+between|between)\s+)"
    r"(" + _PARTY_START + r"[^\n]{1,200}?)\s+and\s+"
    r"(" + _PARTY_START + r"[^\n]{1,200}?)"
    r"(?=[\.;\n]|(?i:\bwhereas\b|\beffective\b|\bdated\b|\bas\s+of\b|\bwitnesseth\b)|$)",
)
_ROLE_PAREN_RE = re.compile(
    r"\(\s*(?:the\s+)?[\"“]?([^\"”()]+?)[\"”]?\s*\)"
)

# Keyword portion is case-insensitive via an inline (?i:...) group; the
# jurisdiction capture stays case-sensitive so a leading [A-Z] actually
# enforces a capitalized proper noun (a global re.IGNORECASE would defeat that
# and over-capture trailing lowercase clauses like ", without regard to ...").
_GOV_LAW_RE = re.compile(
    r"(?i:governed\s+by(?:\s+and\s+construed\s+in\s+accordance\s+with)?\s+"
    r"(?:the\s+)?laws?\s+of\s+(?:the\s+)?)"
    r"([A-Z][A-Za-z\.\- ]+?(?:,\s*[A-Z][A-Za-z\.\- ]+?)?)"
    r"(?=[\.,;\n)]|\s+and\b|\s+without\b|$)",
)

# Anchor on a term/period/duration keyword, then allow a short same-sentence
# gap before the "<number> <unit>" so phrasings like "the initial term of this
# Agreement is three (3) years" match as well as "for a period of two years".
_TERM_LEN_RE = re.compile(
    r"(?:(?:initial\s+)?term|period|duration|"
    r"in\s+(?:full\s+)?(?:force\s+and\s+)?effect\s+for)"
    r"[^.\n]{0,40}?\b(\d+|[A-Za-z]+)(?:\s*\(\d+\))?\s+(years?|months?|weeks?|days?)\b",
    re.IGNORECASE,
)
_NOTICE_RE = re.compile(
    r"(\d+|[A-Za-z]+)(?:\s*\(\d+\))?\s+days?[’'`]?s?\s+"
    r"(?:prior\s+)?(?:written\s+)?notice",
    re.IGNORECASE,
)
_AUTORENEW_POS_RE = re.compile(
    r"automatic(?:ally)?\s+renew|auto-?renew|renew(?:s|ed)?\s+automatically|"
    r"successive\s+(?:\d+|[A-Za-z]+)[\s-]+(?:year|month)|"
    r"shall\s+(?:automatically\s+)?renew\s+for",
    re.IGNORECASE,
)
# Strong negations only. Deliberately excludes a bare "non-renewal", which in
# practice appears in "...notice of non-renewal" -- the opt-OUT mechanism of a
# contract that DOES auto-renew, not a statement that it doesn't.
_AUTORENEW_NEG_RE = re.compile(
    r"(?:shall|will|does|may)\s+not\s+(?:automatically\s+)?renew|"
    r"no\s+automatic\s+renewal|"
    r"not\s+(?:be\s+)?renewed?\s+automatically|"
    r"shall\s+not\s+(?:be\s+)?(?:automatically\s+)?renewed?",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(?:\$|US\$|USD\s?|EUR\s?|€|£|GBP\s?)"
    r"\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s?(?:million|billion|thousand|bn|m|k))?",
    re.IGNORECASE,
)
_DEFTERM_QUOTED_RE = re.compile(
    r"[\"“]([A-Z][A-Za-z0-9][A-Za-z0-9 \-'/&]{1,60})[\"”]"
)
_DEFTERM_PAREN_RE = re.compile(
    r"\(\s*(?:the\s+)?[\"“]?([A-Z][A-Za-z0-9][A-Za-z0-9 \-'/&]{1,40})[\"”]?\s*\)"
)

_WORD_NUMBERS: Dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def _word_to_int(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


def _parse_date_to_iso(s: str) -> Optional[str]:
    """Best-effort normalization of a matched date string to ISO (YYYY-MM-DD).
    Returns None when no known format parses."""
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s.strip().rstrip("."), flags=re.IGNORECASE)
    cleaned = re.sub(r"\bday\s+of\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    fmts = (
        "%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
        "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y",
    )
    for f in fmts:
        try:
            return _dt.datetime.strptime(cleaned, f).date().isoformat()
        except ValueError:
            continue
    return None


def _date_field_from_str(raw: str, base_conf: float) -> JSON:
    raw = re.sub(r"\s+", " ", raw.strip())
    iso = _parse_date_to_iso(raw)
    if iso is not None:
        return _field(iso, base_conf)
    return _field(raw, max(0.0, base_conf - 0.3))


def _date_field(match: Optional["re.Match[str]"]) -> JSON:
    if match is None:
        return _none_field()
    return _date_field_from_str(match.group(1), 0.85)


# Trailing descriptors that follow a party's actual name and should be dropped
# ("Acme Corp., a Delaware corporation", "... doing business as Foo", "... as of
# March 1", "... having its offices at ..."). Each is matched and everything from
# it onward is cut.
_PARTY_CUT_MARKERS: Tuple[str, ...] = (
    r",\s+an?\s+\w",                                  # ", a Delaware ..." / ", an Ohio ..."
    r"\s+doing\s+business\s+as\b",
    r"\s+d/?b/?a\b",
    r"\s+f/?k/?a\b",
    r"\s+a[n]?\s+\w+\s+(?:corporation|company|partnership|limited)\b",
    r"\s+having\b",
    r"\s+with\s+(?:its\s+)?(?:offices|principal|a\s)\b",
    r"\s+with\s+offices\b",
    r"\s+located\b",
    r"\s+organized\b",
    r"\s+incorporated\b",
    r"\s+whose\b",
    r"\s+together\b",
    r",\s+as\s+\w",                                   # ", as administrative agent"
    r"\s+(?:as\s+of|dated|effective)\b",
)


def _clean_party_name(s: str) -> str:
    """Trim a captured party name down to the entity name, dropping trailing
    descriptors ('a Delaware corporation', 'd/b/a ...', 'together with ...',
    'as of ...') and any dangling unclosed parenthetical ('(each of them ...')."""
    s = re.sub(r"\s+", " ", s).strip().strip(",").strip()
    for pat in _PARTY_CUT_MARKERS:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            s = s[: m.start()].strip().strip(",").strip()
    # Drop a trailing parenthetical that was opened but never closed (the close
    # fell outside the captured span), e.g. "Glenn Rufrano (each of them being".
    if "(" in s and ")" not in s:
        s = s[: s.index("(")].strip().strip(",").strip()
    return s.strip("\"“”").strip()


def _split_name_role(s: str) -> Tuple[str, Optional[str]]:
    s = re.sub(r"\s+", " ", s).strip().strip(",").strip()
    role: Optional[str] = None
    m = _ROLE_PAREN_RE.search(s)
    if m:
        candidate = m.group(1).strip()
        # Only treat short, role-like parentheticals as roles.
        if len(candidate) <= 40 and candidate.lower() not in ("a", "an", "the"):
            role = candidate
        s = (s[: m.start()] + s[m.end():]).strip().rstrip(",").strip()
    return _clean_party_name(s), role


def extract_parties(text: str) -> List[JSON]:
    m = _PARTY_BLOCK_RE.search(text)
    if not m:
        return []
    out: List[JSON] = []
    for raw in (m.group(1), m.group(2)):
        name, role = _split_name_role(raw)
        if not name or len(name) < 2 or len(name) > 120:
            continue
        entry: JSON = {"name": name, "confidence": 0.9, "source": "deterministic"}
        entry["role"] = role
        out.append(entry)
    return out


def extract_dates(text: str) -> JSON:
    label = _EFFDATE_LABEL_RE.search(text)
    if label is not None:
        effective = _date_field_from_str(label.group(1), 0.9)
    else:
        effective = _date_field(_EFFECTIVE_RE.search(text))
    return {"effective": effective, "expiration": _date_field(_EXPIRE_RE.search(text))}


def extract_governing_law(text: str) -> JSON:
    m = _GOV_LAW_RE.search(text)
    if not m:
        return _none_field()
    juris = re.sub(r"\s+", " ", m.group(1).strip().rstrip(".,")).strip()
    if not juris:
        return _none_field()
    return _field(juris, 0.85)


def extract_term(text: str) -> JSON:
    length = _none_field()
    m = _TERM_LEN_RE.search(text)
    if m:
        num = _word_to_int(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        # Only emit when the captured token is a real number; otherwise the
        # match was a coincidence ("...consecutive days") -> leave as not-found.
        if num is not None:
            length = _field(f"{num} {unit}{'s' if num != 1 else ''}", 0.7)

    notice = _none_field()
    nm = _NOTICE_RE.search(text)
    if nm:
        days = _word_to_int(nm.group(1))
        if days is not None:
            notice = _field(days, 0.7)

    auto = _none_field()
    if _AUTORENEW_NEG_RE.search(text):
        auto = _field(False, 0.7)
    elif _AUTORENEW_POS_RE.search(text):
        auto = _field(True, 0.65)

    return {"length": length, "auto_renew": auto, "notice_period_days": notice}


def extract_value(text: str) -> JSON:
    m = _MONEY_RE.search(text)
    if not m:
        return _none_field()
    return _field(re.sub(r"\s+", " ", m.group(0).strip()), 0.6)


def extract_amounts(text: str) -> List[JSON]:
    """All distinct monetary amounts in the document (``value`` is the headline
    first one). Useful downstream for fee schedules, caps, thresholds."""
    seen: Dict[str, None] = {}
    for m in _MONEY_RE.finditer(text):
        amt = re.sub(r"\s+", " ", m.group(0).strip())
        seen.setdefault(amt, None)
        if len(seen) >= 30:
            break
    return [{"value": a, "confidence": 0.6, "source": "deterministic"} for a in seen]


# Signature blocks: "By: <name>", "Name: <name>", "Printed Name: <name>".
_SIGNATORY_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:By|Name|Printed\s+Name|Signed\s+by|Authorized\s+Signatory)"
    r"[ \t]*:[ \t]*([^\n_{}\[\]]{2,60})",
    re.IGNORECASE,
)
_SIG_TITLE_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:Title|Its)[ \t]*:[ \t]*([^\n_{}\[\]]{2,60})",
    re.IGNORECASE,
)


def extract_signatories(text: str) -> List[JSON]:
    """Best-effort signature-block names (and titles, when adjacent). Skips
    unfilled placeholders. Blank on a template; populated on executed paper."""
    titles = [re.sub(r"\s+", " ", m.group(1)).strip(" .,") for m in _SIG_TITLE_RE.finditer(text)]
    out: List[JSON] = []
    seen: Dict[str, None] = {}
    for i, m in enumerate(_SIGNATORY_RE.finditer(text)):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
        if len(name) < 2 or name.lower() in ("the", "name", "title") or name in seen:
            continue
        seen[name] = None
        entry: JSON = {"name": name, "confidence": 0.55, "source": "deterministic"}
        entry["title"] = titles[i] if i < len(titles) else None
        out.append(entry)
        if len(out) >= 12:
            break
    return out


# Free-text jurisdiction -> a normalized ISO-ish code (best-effort, common only).
_JURISDICTION_CODES: Dict[str, str] = {
    "delaware": "US-DE", "new york": "US-NY", "california": "US-CA",
    "texas": "US-TX", "illinois": "US-IL", "massachusetts": "US-MA",
    "washington": "US-WA", "florida": "US-FL", "nevada": "US-NV",
    "new jersey": "US-NJ", "pennsylvania": "US-PA", "michigan": "US-MI",
    "ontario": "CA-ON", "quebec": "CA-QC", "british columbia": "CA-BC",
    "england and wales": "GB-EAW", "england": "GB-ENG", "scotland": "GB-SCT",
    "united kingdom": "GB", "france": "FR", "germany": "DE", "ireland": "IE",
    "singapore": "SG", "australia": "AU", "india": "IN", "netherlands": "NL",
}


def extract_jurisdiction(governing_law: JSON) -> JSON:
    """Normalize the governing-law jurisdiction to a stable code (e.g. 'State of
    Delaware' -> 'US-DE') for downstream filtering. None when unrecognized."""
    val = governing_law.get("value")
    if not isinstance(val, str) or not val.strip():
        return _none_field()
    key = re.sub(r"^\s*(?:the\s+)?(?:state|commonwealth|province|laws?)\s+of\s+",
                 "", val.strip(), flags=re.IGNORECASE).strip().lower()
    key = re.sub(r"\s+", " ", key)
    code = _JURISDICTION_CODES.get(key)
    if code is None:  # try a contained name ("delaware, usa")
        for name, c in _JURISDICTION_CODES.items():
            if len(name) >= 5 and name in key:
                code = c
                break
    return _field(code, 0.8, "deterministic") if code else _none_field()


def extract_defined_terms(text: str) -> List[JSON]:
    seen: Dict[str, None] = {}
    for rx in (_DEFTERM_QUOTED_RE, _DEFTERM_PAREN_RE):
        for m in rx.finditer(text):
            term = re.sub(r"\s+", " ", m.group(1).strip())
            # Reject sentence-like or lowercase-y captures.
            if len(term) < 2 or len(term.split()) > 6:
                continue
            if not term[0].isupper():
                continue
            seen.setdefault(term, None)
            if len(seen) >= 50:
                break
    return [{"term": t, "confidence": 0.6, "source": "deterministic"} for t in seen]


# Detected-heading titles that are almost never real clauses: front/back-matter,
# page/document codes, exhibit & schedule references, signature blocks (now
# captured as `signatories`), recitals/preamble, and template scaffolding.
_NOISE_TITLE_PREFIX_RE = re.compile(
    r"^(?:table\s+of\s+contents|exhibit|schedule|annex|appendix|attachment|"
    r"signature\s+page|signatures?|page|recitals?|background|preamble|witnesseth|"
    r"defining\s+variables)\b",
    re.IGNORECASE,
)


def _is_noise_clause_title(title: str) -> bool:
    """True for detected 'headings' that are structural noise rather than
    clauses -- document codes/page numbers (4+ consecutive digits, e.g.
    'Ks 112708-2'), front/back-matter ('Table of Contents', 'Exhibit B'),
    signature/recital sections, definition fragments (a title starting with a
    quote, e.g. '"Product" means ...'), and unfilled template placeholders
    ('[ # ]% to [ # ]%'). Safe filters only; kept conservative."""
    t = title.strip()
    if re.search(r"\d{4,}", t):
        return True
    if _NOISE_TITLE_PREFIX_RE.match(t):
        return True
    if t[:1] in "\"“[{":            # definition fragment / bracketed placeholder
        return True
    if re.search(r"\[\s*#|#\s*\]|%\s*\]", t):  # unfilled placeholder ('[ # ]%')
        return True
    return False


def extract_clauses(text: str) -> List[JSON]:
    detected = detect_clauses(text)
    # A heading whose title repeats 3+ times across the document is almost
    # always a running header/footer (e.g. a page code), not that many distinct
    # clauses -- drop every occurrence. (Counted on the normalized title.)
    counts: Dict[str, int] = {}
    for c in detected:
        k = _norm_clause_key(c["title"])
        counts[k] = counts.get(k, 0) + 1

    out: List[JSON] = []
    for c in detected:
        if counts[_norm_clause_key(c["title"])] >= 3:
            continue
        if _is_noise_clause_title(c["title"]):
            continue
        canonical, mapped = _canonicalize_clause(c["title"])
        tier = c["tier"]
        base = {"h2": 0.95, "bold-numbered": 0.85, "numbered": 0.8,
                "all-caps": 0.75, "explicit": 0.95}.get(tier, 0.7)
        conf = round(base * (1.0 if mapped else 0.75), 2)
        out.append({
            "canonical_title": canonical,
            "detected_title": c["detected"],
            "tier": tier,
            "span": {"start": int(c["start"]), "end": int(c["end"])},
            "confidence": conf,
            "source": "deterministic",
            "mapped": mapped,
        })
    return out


def extract_title(text: str, path: Optional[Path], fmt: str) -> Optional[str]:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        ls = line.strip().lstrip("#").strip()
        if not ls:
            continue
        # Skip SGML/XML wrapper lines (e.g. SEC EDGAR "<DOCUMENT>", "<TYPE>...").
        if ls.startswith("<"):
            continue
        if len(ls) <= 90:
            return ls
        break
    if path is not None:
        return _titlecase(path.stem.replace("_", " ").replace("-", " "))
    return None


# ---------------------------------------------------------------------------
# Input readers
# ---------------------------------------------------------------------------


def _looks_like_html(head: str) -> bool:
    """Heuristic: does this text look like HTML? Catches HTML masquerading as
    .txt (e.g. SEC EDGAR full submissions wrap HTML exhibits in a .txt)."""
    low = head.lower()
    if "<!doctype html" in low or "<html" in low or "<body" in low:
        return True
    return len(re.findall(r"</?(?:p|div|table|tr|td|span|br|h[1-6]|font|b|i)\b", low)) >= 6


def _detect_format(path: Path, raw: bytes) -> str:
    ext = path.suffix.lower()
    if ext in (".htm", ".html", ".xhtml"):
        return "html"
    if ext == ".docx":
        return "docx"
    if ext == ".pdf":
        return "pdf"
    if raw[:4] == b"%PDF":
        return "pdf"
    if raw[:2] == b"PK" and ext not in (".md", ".markdown", ".txt"):
        return "docx"
    base = "markdown" if ext in (".md", ".markdown") else "text"
    # Content sniff: HTML hiding inside a .txt/.md (or extensionless) file.
    if _looks_like_html(raw[:4096].decode("utf-8", "replace")):
        return "html"
    return base


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Stdlib HTML -> text: drops script/style, frames block elements with blank
    lines (so clause-heading detection still works), and unescapes entities."""

    _SKIP = {"script", "style", "head", "title", "meta", "link", "noscript"}
    _BLOCK = {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "table", "ul", "ol", "blockquote", "pre", "hr",
        "thead", "tbody", "header", "footer", "main",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip > 0:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        # Strip each line; collapse runs of blank lines to a single blank line
        # (gives ALL-CAPS / numbered headings their blank-line frame).
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in "".join(self._parts).split("\n")]
        out: List[str] = []
        blank = False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def _read_html(raw_text: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw_text)
        parser.close()
    except Exception:
        # Never crash on malformed markup; fall back to a crude tag strip.
        return re.sub(r"<[^>]+>", " ", raw_text)
    return parser.get_text()


def _read_docx(path: Path, raw: bytes, prefer_optional: bool = True) -> Tuple[str, List[str]]:
    """Extract text from a .docx. Uses python-docx for higher fidelity when the
    optional [docx] extra is installed; otherwise a stdlib zipfile/XML reader
    (always available) handles paragraphs, table cells, and bold runs.

    `prefer_optional=False` forces the stdlib reader regardless of what's
    installed -- used to pin reproducible golden fixtures."""
    warnings: List[str] = []
    if prefer_optional and importlib.util.find_spec("docx") is not None:
        try:
            mod = importlib.import_module("docx")
            document_cls = getattr(mod, "Document")
            doc = document_cls(str(path))
            w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            lines: List[str] = []
            for para in doc.paragraphs:
                line = (para.text or "").strip()
                # Read the style + numbering off the underlying element so the
                # cascade sees clause headings (the same logic the stdlib reader
                # applies); python-docx alone exposes neither as a heading.
                ppr = para._p.find(w + "pPr")
                style = _docx_paragraph_style(ppr, w)
                numbered = bool(ppr is not None and ppr.find(w + "numPr") is not None)
                all_bold = bool(para.runs) and all(
                    getattr(r, "bold", False) for r in para.runs if (r.text or "").strip())
                _emit_docx_paragraph(lines, line, style, numbered, all_bold)
            for table in getattr(doc, "tables", []):
                for row in table.rows:
                    for cell in row.cells:
                        ct = (cell.text or "").strip()
                        if ct:
                            lines.append(ct)
            return "\n\n".join(lines), warnings
        except Exception as e:  # pragma: no cover - fidelity path
            warnings.append(f"python-docx read failed ({e}); falling back to stdlib reader")
    try:
        return _read_docx_stdlib(raw), warnings
    except Exception as e:
        warnings.append(f"could not parse .docx ({e}); treating as empty")
        return "", warnings


def _docx_paragraph_style(ppr: Any, w: str) -> Optional[str]:
    if ppr is None:
        return None
    st = ppr.find(w + "pStyle")
    return st.get(w + "val") if st is not None else None


def _is_heading_style(style: Optional[str]) -> bool:
    """True for Word built-in heading/title styles (Heading1-9, Title, and the
    'H1'/'H2' shorthands). These mark clause headings whose visible numbers are
    auto-generated and absent from the raw text."""
    if not style:
        return False
    s = style.lower()
    return "heading" in s or s == "title" or bool(re.fullmatch(r"h[1-9]", s))


def _docx_heading_title(text: str) -> Optional[str]:
    """Pull the clause title out of a heading paragraph. Many contracts use a
    run-in heading -- 'Performing Services.  Contractor will ...' -- where the
    title is the lead before the first sentence break; a standalone header
    ('Services & Restrictions') has no such break and is used whole.

    Returns None when the paragraph is really a full sentence that merely
    carries a heading style (no run-in title) -- those would otherwise become
    garbage clause titles and mis-map under substring matching."""
    m = re.match(r"\s*(.{2,80}?)[.:]\s+[A-Z(\"“]", text)
    title = m.group(1).strip() if m else text.strip()
    if len(title) > 70 or len(title.split()) > 9:
        return None
    return title


def _emit_docx_paragraph(out: List[str], line: str, style: Optional[str],
                         numbered: bool, all_bold: bool) -> None:
    """Append one .docx paragraph to `out` the way the clause cascade expects.

    Heading-styled (Heading1-9/Title) or auto-numbered (`w:numPr`) paragraphs --
    whose visible number is auto-generated and absent from the text -- become a
    `## <title>` heading (with any run-in body split onto the next line) when the
    lead looks like a heading; a fully-bold paragraph becomes `**...**`; anything
    else stays plain. Shared by BOTH the python-docx and stdlib readers so the
    two paths agree on structure (the python-docx path used to flatten headings,
    losing the clause map on heading-styled Word docs)."""
    if not line:
        out.append("")
        return
    if _is_heading_style(style) or numbered:
        title = _docx_heading_title(line)
        if title is not None:
            out.append(f"## {title}")
            if len(title) < len(line):
                out.append(line[len(title):].lstrip(" .:\t"))
            return
    out.append(f"**{line}**" if all_bold else line)


def _read_docx_stdlib(raw: bytes) -> str:
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        # Zip-bomb guard: the uncompressed size is in the header, so check it
        # before reading (don't decompress GBs into memory).
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_DECOMPRESSED_BYTES:
            raise ValueError(
                f"word/document.xml decompresses to {info.file_size} bytes "
                f"(> {MAX_DECOMPRESSED_BYTES} cap)")
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras: List[str] = []
    # iter over w:p in document order (includes paragraphs inside table cells).
    for p in root.iter(w + "p"):
        ppr = p.find(w + "pPr")
        style = _docx_paragraph_style(ppr, w)
        numbered = ppr is not None and ppr.find(w + "numPr") is not None
        run_texts: List[str] = []
        all_bold = True
        for r in p.iter(w + "r"):
            rpr = r.find(w + "rPr")
            bold = rpr is not None and rpr.find(w + "b") is not None
            txt = "".join(t.text or "" for t in r.iter(w + "t"))
            if txt:
                if not bold:
                    all_bold = False
                run_texts.append(txt)
        line = "".join(run_texts).strip()
        # Clause structure in real Word contracts lives in heading STYLES
        # (Heading1-9/Title) or auto-NUMBERED paragraphs (w:numPr) -- in both the
        # visible number is auto-generated and absent from the text. The shared
        # emitter turns those into `## headings` (run-in body split off), bolds
        # fully-bold lines, and keeps the rest plain. _docx_heading_title rejects
        # full-sentence body items, so this stays conservative.
        _emit_docx_paragraph(paras, line, style, numbered, all_bold)
    return "\n\n".join(paras)


def _read_pdf(path: Path, raw: bytes, prefer_optional: bool = True) -> Tuple[str, List[str]]:
    """Extract text from a .pdf. Uses pypdf when the optional [pdf] extra is
    installed; otherwise a stdlib best-effort reader (zlib FlateDecode + text
    operators). Scanned/image-only PDFs yield no text and are warned about.

    `prefer_optional=False` forces the stdlib reader regardless of what's
    installed -- used to pin reproducible golden fixtures."""
    warnings: List[str] = []
    if prefer_optional and importlib.util.find_spec("pypdf") is not None:
        try:
            mod = importlib.import_module("pypdf")
            reader_cls = getattr(mod, "PdfReader")
            import io
            reader = reader_cls(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages), warnings
        except Exception as e:  # pragma: no cover - fidelity path
            warnings.append(f"pypdf read failed ({e}); falling back to stdlib reader")
    try:
        text = _read_pdf_stdlib(raw)
    except Exception as e:
        warnings.append(f"could not parse .pdf ({e}); treating as empty")
        return "", warnings
    return text, warnings


_PDF_TOKEN_RE = re.compile(
    r"\((?:\\.|[^\\()])*\)|\[(?:\\.|[^\]\\])*\]|Tj|TJ|Td|TD|T\*|BT|ET|'|\""
)


def _pdf_unescape(s: str) -> str:
    out: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in "()\\":
                out.append(nxt)
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt in "rtbf":
                out.append({"r": "\r", "t": "\t", "b": "", "f": ""}[nxt])
                i += 2
                continue
            mo = re.match(r"[0-7]{1,3}", s[i + 1:i + 4])
            if mo:
                out.append(chr(int(mo.group(0), 8) & 0xFF))
                i += 1 + len(mo.group(0))
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _pdf_text_from_content(content: bytes) -> str:
    """Pull text strings from a PDF content stream, but ONLY from inside text
    objects (`BT` ... `ET`). Real text lives there; embedded fonts, images,
    digital-signature blobs and metadata streams have no BT/ET, so gating on it
    keeps their binary bytes (which often contain stray `(...)` sequences) out
    of the output -- essential for real signed/font-embedded PDFs."""
    s = content.decode("latin-1", "replace")
    lines: List[str] = []
    cur: List[str] = []
    in_text = False

    def flush() -> None:
        if cur:
            lines.append("".join(cur))
            cur.clear()

    for m in _PDF_TOKEN_RE.finditer(s):
        tok = m.group(0)
        if tok == "BT":
            flush()
            in_text = True
        elif tok == "ET":
            flush()
            in_text = False
        elif not in_text:
            continue
        elif tok.startswith("("):
            cur.append(_pdf_unescape(tok[1:-1]))
        elif tok.startswith("["):
            for sm in re.finditer(r"\((?:\\.|[^\\()])*\)", tok):
                cur.append(_pdf_unescape(sm.group(0)[1:-1]))
        elif tok in ("Td", "TD", "T*", "'", '"'):
            flush()
    flush()
    return "\n".join(lines)


def _mostly_printable(s: str) -> bool:
    """True if `s` is overwhelmingly printable text (backstop against a
    malformed stream slipping binary through the BT/ET gate)."""
    if not s:
        return False
    printable = sum(1 for ch in s if ch in "\n\t" or 32 <= ord(ch) < 127 or ord(ch) > 160)
    return printable / len(s) >= 0.85


def _read_pdf_stdlib(raw: bytes) -> str:
    import zlib

    chunks: List[str] = []
    idx = 0
    budget = MAX_DECOMPRESSED_BYTES  # total decompressed-output budget (zlib-bomb guard)
    while True:
        s = raw.find(b"stream", idx)
        if s == -1:
            break
        e = raw.find(b"endstream", s)
        if e == -1:
            break
        body = raw[s + len(b"stream"):e].lstrip(b"\r\n")
        try:
            # Bounded decompression: never expand a stream past the remaining
            # budget, so a zlib-bomb stream can't exhaust memory.
            content = zlib.decompressobj().decompress(body, budget + 1)
        except Exception:
            content = body
        if len(content) > budget:
            break
        budget -= len(content)
        piece = _pdf_text_from_content(content)
        if piece.strip() and _mostly_printable(piece):
            chunks.append(piece)
        idx = e + len(b"endstream")
    return "\n".join(chunks)


def load_source(path: Path, prefer_optional: bool = True) -> Tuple[bytes, str, str, List[str]]:
    """Read a document from disk. Returns (raw_bytes, text, format, warnings).
    Never raises on parse trouble -- degrades to empty text with a warning.

    `prefer_optional=False` forces the stdlib readers for .docx/.pdf so output
    is reproducible regardless of which extras are installed (used by the
    golden fixtures). The CLI default (True) uses the best reader available."""
    if not path.exists():
        raise ExtractError(f"no such file: {path}")
    if path.is_dir():
        raise ExtractError(f"path is a directory, not a file: {path}")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > MAX_INPUT_BYTES:
        raise ExtractError(
            f"file is too large ({size // (1024 * 1024)} MB > "
            f"{MAX_INPUT_BYTES // (1024 * 1024)} MB cap); refusing to read")
    raw = path.read_bytes()
    fmt = _detect_format(path, raw)
    warnings: List[str] = []
    if fmt in ("markdown", "text"):
        text = raw.decode("utf-8", "replace")
    elif fmt == "html":
        text = _read_html(raw.decode("utf-8", "replace"))
    elif fmt == "docx":
        text, w = _read_docx(path, raw, prefer_optional)
        warnings += w
    elif fmt == "pdf":
        text, w = _read_pdf(path, raw, prefer_optional)
        warnings += w
    else:  # pragma: no cover - unreachable; _detect_format only returns the above
        text = raw.decode("utf-8", "replace")
    if not text.strip():
        warnings.append(
            f"no extractable text from {fmt} input (scanned or image-only?); "
            "output will be sparse"
        )
    return raw, text, fmt, warnings


# ---------------------------------------------------------------------------
# Extraction orchestration
# ---------------------------------------------------------------------------


def build_extraction(text: str, raw: bytes, fmt: str,
                     source_path: Optional[str]) -> JSON:
    """Run the deterministic tier and assemble the output contract object."""
    sha = hashlib.sha256(raw).hexdigest()
    # Field extractors (parties, dates, governing law, term, value, defined
    # terms) run on a whitespace-flattened copy so values that wrap across a
    # line break in the source -- "...laws of the Province\nof Ontario", a party
    # name split mid-line -- are matched whole. Clause detection and the title
    # keep the original text, which depends on line structure.
    flat = re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", " ", text)
    flat = re.sub(r"[ \t]+", " ", flat)
    governing_law = extract_governing_law(flat)
    return {
        "document": {
            "title": extract_title(text, Path(source_path) if source_path else None, fmt),
            "format": fmt,
            "sha256": sha,
            "source_path": source_path,
        },
        "parties": extract_parties(flat),
        "dates": extract_dates(flat),
        "term": extract_term(flat),
        "governing_law": governing_law,
        "jurisdiction": extract_jurisdiction(governing_law),
        "clauses": extract_clauses(text),
        "defined_terms": extract_defined_terms(flat),
        "value": extract_value(flat),
        "amounts": extract_amounts(flat),
        "signatories": extract_signatories(text),  # signature blocks are line-structured
        "_meta": {
            "extractor_version": EXTRACTOR_VERSION,
            "tiers_used": ["deterministic"],
            "llm_used": False,
        },
    }


def _is_low_signal(result: JSON) -> bool:
    """True when the deterministic tier found essentially nothing extractable
    (e.g. a scanned PDF). Used to set a non-zero exit code as a 'finding'."""
    if result["parties"]:
        return False
    if result["clauses"]:
        return False
    if result["dates"]["effective"]["source"] != "none":
        return False
    if result["governing_law"]["source"] != "none":
        return False
    if result["defined_terms"]:
        return False
    return True


# ---------------------------------------------------------------------------
# LLM tier  (opt-in only, never in a hot path)
# ---------------------------------------------------------------------------

LLM_CONFIG_PATHS = (
    Path.home() / ".config" / "contract-ops" / "llm.json",
    Path("config") / "llm.json",
)


def load_llm_config() -> Optional[JSON]:
    """Suite-shared LLM config lookup: ~/.config/contract-ops/llm.json first,
    then a repo-local ./config/llm.json. Returns the first valid one, else None."""
    for p in LLM_CONFIG_PATHS:
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("api_key"):
                    return data
        except (OSError, json.JSONDecodeError):
            continue
    return None


_LLM_PROMPT_KEYS = (
    "renewal_mechanics (string or null), obligations (array of short strings, "
    "max 5), governing_law (string or null)"
)
# Requested only when the deterministic clause cascade found nothing (e.g. a
# DOCX that auto-numbers with no heading style): ask the model for the section
# headings so we can still produce a clause map.
_LLM_PROMPT_CLAUSES = (
    ", clauses (array, max 40, of objects {\"title\": \"<the section/clause "
    "heading, verbatim if possible>\"} in document order, top-level sections "
    "only)"
)


def _build_llm_prompt(text: str, want_clauses: bool) -> str:
    keys = _LLM_PROMPT_KEYS + (_LLM_PROMPT_CLAUSES if want_clauses else "")
    return (
        "You are a contract-extraction assistant. Given the contract text, "
        "return ONLY a compact JSON object with keys: " + keys + ". Base answers "
        "strictly on the text. No prose, JSON only.\n\nCONTRACT:\n" + text[:16000]
    )


def _llm_request(cfg: JSON, prompt: str, timeout: float = 30.0) -> Optional[str]:
    provider = str(cfg.get("provider", "anthropic")).lower()
    model = cfg.get("model") or ("claude-sonnet-4-6" if provider == "anthropic" else "gpt-4o-mini")
    api_key = cfg["api_key"]
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        base = str(cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - opt-in
        body = json.loads(resp.read().decode("utf-8"))
    if provider == "anthropic":
        parts = body.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    choices = body.get("choices") or []
    if choices:
        return str(choices[0].get("message", {}).get("content", ""))
    return None


def _extract_json_object(s: str) -> Optional[JSON]:
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _llm_clause_map(raw: Any, text: str) -> List[JSON]:
    """Convert LLM-returned clause titles into schema-conformant clause objects.
    Titles are canonicalized through the same suite vocabulary the deterministic
    path uses, located in the document for a best-effort span, and marked
    tier/source = 'llm' with a modest confidence (verify, not trust)."""
    if not isinstance(raw, list):
        return []
    low = text.lower()
    out: List[JSON] = []
    seen: set[str] = set()
    for item in raw[:40]:
        title: Any = item.get("title") if isinstance(item, dict) else item
        if not isinstance(title, str) or not title.strip():
            continue
        title = re.sub(r"\s+", " ", title.strip())
        key = _norm_clause_key(title)
        if not key or key in seen or _is_noise_clause_title(title):
            continue
        seen.add(key)
        canonical, mapped = _canonicalize_clause(title)
        idx = low.find(title.lower())
        span = ({"start": idx, "end": min(idx + len(title), len(text))}
                if idx >= 0 else {"start": 0, "end": 0})
        out.append({
            "canonical_title": canonical,
            "detected_title": title,
            "tier": "llm",
            "span": span,
            "confidence": 0.5,
            "source": "llm",
            "mapped": mapped,
        })
    return out


def llm_enrich(result: JSON, text: str, args_ns: argparse.Namespace) -> None:
    """Opt-in enrichment of fuzzy fields, plus a clause-map fallback when the
    deterministic cascade found no clauses. Mutates `result` in place. Any
    failure (no config, network error, bad JSON) degrades gracefully: a warning
    to stderr and the deterministic output is left untouched."""
    cfg = load_llm_config()
    if cfg is None:
        _warn(args_ns, "no LLM config found (~/.config/contract-ops/llm.json or "
                       "./config/llm.json); skipping --llm enrichment")
        return
    want_clauses = not result["clauses"]
    prompt = _build_llm_prompt(text, want_clauses)
    try:
        raw = _llm_request(cfg, prompt)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        _warn(args_ns, f"LLM request failed ({e}); keeping deterministic output only")
        return
    if not raw:
        _warn(args_ns, "LLM returned no content; keeping deterministic output only")
        return
    obj = _extract_json_object(raw)
    if obj is None:
        _warn(args_ns, "could not parse LLM JSON response; keeping deterministic output only")
        return

    enriched = False
    rm = obj.get("renewal_mechanics")
    if isinstance(rm, str) and rm.strip():
        result["term"]["renewal_mechanics"] = _field(rm.strip(), 0.6, "llm")
        enriched = True
    obligations = obj.get("obligations")
    if isinstance(obligations, list) and obligations:
        result["obligations"] = [
            {"text": str(o).strip(), "confidence": 0.55, "source": "llm"}
            for o in obligations[:5] if str(o).strip()
        ]
        enriched = True
    gl = obj.get("governing_law")
    if isinstance(gl, str) and gl.strip() and result["governing_law"]["source"] == "none":
        result["governing_law"] = _field(gl.strip(), 0.6, "llm")
        enriched = True
    if want_clauses:
        cmap = _llm_clause_map(obj.get("clauses"), text)
        if cmap:
            result["clauses"] = cmap
            enriched = True

    result["_meta"]["llm_used"] = True
    if enriched and "llm" not in result["_meta"]["tiers_used"]:
        result["_meta"]["tiers_used"].append("llm")


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

TOP_LEVEL_FIELDS = (
    "document", "parties", "dates", "term", "governing_law",
    "clauses", "defined_terms", "value",
)


def _apply_field_subset(result: JSON, fields: List[str]) -> JSON:
    wanted = {f.strip() for f in fields if f.strip()}
    out: JSON = {k: v for k, v in result.items() if k in wanted}
    out["_meta"] = result["_meta"]  # provenance always travels with the payload
    return out


def _strip_confidence(obj: Any) -> Any:
    """Recursively drop confidence/source markers for the --no-confidence view.
    Collapses single-remaining-key dicts ({"value": x} -> x, {"term": t} -> t)."""
    if isinstance(obj, dict):
        d = {k: _strip_confidence(v) for k, v in obj.items()
             if k not in ("confidence", "source")}
        if len(d) == 1:
            return next(iter(d.values()))
        return d
    if isinstance(obj, list):
        return [_strip_confidence(v) for v in obj]
    return obj


def render_json(result: JSON, no_confidence: bool) -> str:
    payload = _strip_confidence(result) if no_confidence else result
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=False)


def _fv(field: JSON) -> str:
    v = field.get("value")
    if v is None:
        return _dim("(not found)")
    return str(v)


def render_table(result: JSON, no_confidence: bool) -> str:
    lines: List[str] = []
    doc = result.get("document", {})
    if doc:
        lines.append(_bold("Document"))
        lines.append(f"  title       : {doc.get('title') or _dim('(none)')}")
        lines.append(f"  format      : {doc.get('format')}")
        lines.append(f"  sha256      : {str(doc.get('sha256'))[:16]}...")
    parties = result.get("parties")
    if parties is not None:
        lines.append(_bold("Parties"))
        if parties:
            for p in parties:
                role = f" ({p['role']})" if p.get("role") else ""
                conf = "" if no_confidence else _dim(f"  [{p.get('confidence')}]")
                lines.append(f"  - {p['name']}{role}{conf}")
        else:
            lines.append("  " + _dim("(none detected)"))
    dates = result.get("dates")
    if dates is not None:
        lines.append(_bold("Dates"))
        lines.append(f"  effective   : {_fv(dates['effective'])}")
        lines.append(f"  expiration  : {_fv(dates['expiration'])}")
    term = result.get("term")
    if term is not None:
        lines.append(_bold("Term"))
        lines.append(f"  length      : {_fv(term['length'])}")
        lines.append(f"  auto_renew  : {_fv(term['auto_renew'])}")
        lines.append(f"  notice_days : {_fv(term['notice_period_days'])}")
        if "renewal_mechanics" in term:
            lines.append(f"  renewal     : {_fv(term['renewal_mechanics'])} {_dim('[llm]')}")
    if "governing_law" in result:
        lines.append(_bold("Governing law"))
        lines.append(f"  {_fv(result['governing_law'])}")
    if "value" in result:
        lines.append(_bold("Value"))
        lines.append(f"  {_fv(result['value'])}")
    clauses = result.get("clauses")
    if clauses is not None:
        lines.append(_bold(f"Clause map ({len(clauses)})"))
        if clauses:
            lines.append("  " + _dim("canonical            tier           detected"))
            for c in clauses:
                canon = (c.get("canonical_title") or "")[:20].ljust(20)
                tier = str(c.get("tier"))[:14].ljust(14)
                det = c.get("detected_title", "")
                flag = "" if c.get("mapped") else _yellow(" *")
                conf = "" if no_confidence else _dim(f" [{c.get('confidence')}]")
                lines.append(f"  {canon} {tier} {det}{flag}{conf}")
            if any(not c.get("mapped") for c in clauses):
                lines.append("  " + _dim("* = not mapped to suite vocabulary"))
        else:
            lines.append("  " + _dim("(no clause structure detected)"))
    terms = result.get("defined_terms")
    if terms is not None:
        lines.append(_bold(f"Defined terms ({len(terms)})"))
        if terms:
            lines.append("  " + ", ".join(t["term"] for t in terms[:20]))
        else:
            lines.append("  " + _dim("(none detected)"))
    meta = result.get("_meta", {})
    lines.append(_dim(
        f"tiers={','.join(meta.get('tiers_used', []))} "
        f"llm={meta.get('llm_used')} extractor={meta.get('extractor_version')}"
    ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output JSON Schema  (the cross-CLI contract; source of truth for docs/spec/)
# ---------------------------------------------------------------------------


def output_schema() -> JSON:
    field_ref = {"$ref": "#/$defs/field"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/DrBaher/extract-cli/blob/main/docs/spec/extract-output.schema.json",
        "title": f"extract-cli output schema (v{SCHEMA_VERSION})",
        "description": (
            "Structured payload emitted by `extract <path>` (default JSON output). "
            "The cross-CLI contract that nda-review-cli, compare-cli and "
            "contract-vault consume. Every extracted field carries a confidence "
            "and a source in {deterministic, llm, none}: downstream treats fields "
            "as 'verify, not trust'. Note: the --no-confidence view is a reduced "
            "convenience projection NOT governed by this schema."
        ),
        "type": "object",
        "required": [
            "document", "parties", "dates", "term", "governing_law",
            "jurisdiction", "clauses", "defined_terms", "value", "amounts",
            "signatories", "_meta",
        ],
        "additionalProperties": False,
        "$defs": {
            "source": {"enum": ["deterministic", "llm", "none"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "field": {
                "type": "object",
                "required": ["value", "confidence", "source"],
                "properties": {
                    "value": {},
                    "confidence": {"$ref": "#/$defs/confidence"},
                    "source": {"$ref": "#/$defs/source"},
                },
                "additionalProperties": False,
            },
        },
        "properties": {
            "document": {
                "type": "object",
                "required": ["title", "format", "sha256", "source_path"],
                "properties": {
                    "title": {"type": ["string", "null"]},
                    "format": {"enum": ["markdown", "text", "docx", "pdf", "html"]},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "source_path": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
            "parties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "confidence", "source"],
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": ["string", "null"]},
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                    },
                    "additionalProperties": False,
                },
            },
            "dates": {
                "type": "object",
                "required": ["effective", "expiration"],
                "properties": {"effective": field_ref, "expiration": field_ref},
                "additionalProperties": False,
            },
            "term": {
                "type": "object",
                "required": ["length", "auto_renew", "notice_period_days"],
                "properties": {
                    "length": field_ref,
                    "auto_renew": field_ref,
                    "notice_period_days": field_ref,
                    "renewal_mechanics": field_ref,
                },
                "additionalProperties": False,
            },
            "governing_law": field_ref,
            "jurisdiction": field_ref,
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "canonical_title", "detected_title", "tier",
                        "span", "confidence", "source", "mapped",
                    ],
                    "properties": {
                        "canonical_title": {"type": ["string", "null"]},
                        "detected_title": {"type": "string"},
                        "tier": {"enum": ["h2", "bold-numbered", "numbered", "all-caps", "explicit", "llm"]},
                        "span": {
                            "type": "object",
                            "required": ["start", "end"],
                            "properties": {
                                "start": {"type": "integer", "minimum": 0},
                                "end": {"type": "integer", "minimum": 0},
                            },
                            "additionalProperties": False,
                        },
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                        "mapped": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "defined_terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["term", "confidence", "source"],
                    "properties": {
                        "term": {"type": "string"},
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                    },
                    "additionalProperties": False,
                },
            },
            "value": field_ref,
            "amounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["value", "confidence", "source"],
                    "properties": {
                        "value": {"type": "string"},
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                    },
                    "additionalProperties": False,
                },
            },
            "signatories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "confidence", "source"],
                    "properties": {
                        "name": {"type": "string"},
                        "title": {"type": ["string", "null"]},
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                    },
                    "additionalProperties": False,
                },
            },
            "obligations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "confidence", "source"],
                    "properties": {
                        "text": {"type": "string"},
                        "confidence": {"$ref": "#/$defs/confidence"},
                        "source": {"$ref": "#/$defs/source"},
                    },
                    "additionalProperties": False,
                },
            },
            "_meta": {
                "type": "object",
                "required": ["extractor_version", "tiers_used", "llm_used"],
                "properties": {
                    "extractor_version": {"type": "string"},
                    "tiers_used": {"type": "array", "items": {"enum": ["deterministic", "llm"]}},
                    "llm_used": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    }


# ---------------------------------------------------------------------------
# Field catalog (for `extract fields`)
# ---------------------------------------------------------------------------

FIELD_CATALOG: Tuple[Tuple[str, str, str], ...] = (
    ("document.title", "deterministic", "Document title (first heading or filename)"),
    ("parties", "deterministic", "Contracting parties ('between X and Y')"),
    ("dates.effective", "deterministic", "Effective date (ISO-normalized when parseable)"),
    ("dates.expiration", "deterministic", "Expiration date"),
    ("term.length", "deterministic", "Term length, best-effort"),
    ("term.notice_period_days", "deterministic", "Notice period in days, best-effort"),
    ("term.auto_renew", "deterministic", "Auto-renewal flag, best-effort"),
    ("governing_law", "deterministic", "Governing law / jurisdiction"),
    ("clauses", "deterministic", "Clause map normalized to the suite's canonical vocabulary "
                                 "(LLM fallback under --llm when no headings are detected)"),
    ("defined_terms", "deterministic", "Defined-term inventory (quoted / parenthetical)"),
    ("value", "deterministic", "Headline monetary value"),
    ("term.renewal_mechanics", "llm", "Renewal mechanics (fuzzy; --llm only)"),
    ("obligations", "llm", "Key obligation phrasing (fuzzy; --llm only)"),
)


# ---------------------------------------------------------------------------
# Bundled demo fixture (so `extract demo` works from an installed wheel)
# ---------------------------------------------------------------------------

DEMO_DOCUMENT = """# Mutual Non-Disclosure Agreement

This Mutual Non-Disclosure Agreement (the "Agreement") is made and entered into
as of March 1, 2024, by and between Acme Robotics, Inc. (the "Disclosing Party")
and Beta Logistics LLC (the "Receiving Party").

## Definitions

For purposes of this Agreement, "Confidential Information" means any non-public
information disclosed by one party to the other.

## Confidentiality Obligations

The Receiving Party shall protect the Confidential Information using no less than
reasonable care and shall not disclose it to any third party.

## Term

This Agreement shall remain in effect for a period of three (3) years from the
Effective Date and shall automatically renew for successive one-year terms unless
either party gives sixty (60) days' written notice of non-renewal.

## Limitation of Liability

In no event shall either party's aggregate liability exceed $50,000.

## Governing Law

This Agreement shall be governed by and construed in accordance with the laws of
the State of Delaware, without regard to its conflict-of-laws principles.
"""


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    path = Path(args.path)
    raw, text, fmt, warnings = load_source(path)
    for w in warnings:
        _warn(args, w)

    result = build_extraction(text, raw, fmt, str(args.path))

    if args.llm:
        llm_enrich(result, text, args)

    fmt_out = "json" if args.json else args.format
    if args.fields:
        result = _apply_field_subset(result, args.fields.split(","))

    _why_print(
        args, f"extracted {path.name}",
        f"format={fmt} parties={len(result.get('parties', []))} "
        f"clauses={len(result.get('clauses', []))}",
        f"tiers={','.join(result['_meta']['tiers_used'])} "
        f"llm_used={result['_meta']['llm_used']}",
        f"low_signal={_is_low_signal(result)}" if not args.fields else "fields_subset=on",
    )

    if args.silent and fmt_out != "json":
        pass  # silent suppresses the human table; JSON is the machine payload
    elif fmt_out == "table":
        print(render_table(result, args.no_confidence))
    else:
        print(render_json(result, args.no_confidence))

    if not args.fields and _is_low_signal(result):
        _warn(args, "document produced no high-signal fields (parties/clauses/dates); "
                    "it may be scanned, image-only, or unstructured")
        return 1
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    print(json.dumps(output_schema(), indent=2, ensure_ascii=True))
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    if args.json:
        payload = [
            {"field": f, "tier": tier, "description": desc}
            for f, tier, desc in FIELD_CATALOG
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0
    print(_bold("Extractable fields") + _dim("  (tier = which extraction tier produces it)"))
    for f, tier, desc in FIELD_CATALOG:
        tag = _green(tier) if tier == "deterministic" else _yellow(tier)
        print(f"  {f.ljust(26)} {tag.ljust(22)} {_dim(desc)}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    raw = DEMO_DOCUMENT.encode("utf-8")
    result = build_extraction(DEMO_DOCUMENT, raw, "markdown", "(bundled demo fixture)")
    if not args.silent:
        _eprint(_bold("extract-cli demo") + " -- the suite's passport control")
        _eprint(_dim(
            "  A foreign document comes in (here: a bundled NDA). The deterministic\n"
            "  tier maps its clauses onto the suite's canonical vocabulary and pulls\n"
            "  parties/dates/term/governing-law -- no LLM, no network. The JSON below\n"
            "  is what nda-review-cli / compare-cli / contract-vault consume.\n"
        ))
    fmt_out = "json" if args.json else args.format
    if fmt_out == "table":
        print(render_table(result, args.no_confidence))
    else:
        print(render_json(result, args.no_confidence))
    if not args.silent:
        _eprint(_dim("\n  Try:  extract demo --format json | jq '.clauses[].canonical_title'"))
    return 0


# ---------------------------------------------------------------------------
# Shell completion
# ---------------------------------------------------------------------------

_SUBCOMMANDS = ("schema", "fields", "demo", "completion")
_GLOBAL_FLAGS = (
    "--json", "--why", "-q", "--silent", "--no-color", "--llm",
    "--format", "--fields", "--no-confidence", "--catalog",
    "-V", "--version", "-h", "--help",
)

_BASH_COMPLETION = r"""# extract-cli bash completion
#   eval "$(extract completion bash)"
_extract_completions() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    local cmds="schema fields demo completion"
    local flags="--json --why -q --silent --no-color --llm --format --fields --no-confidence --catalog -V --version -h --help"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "${cmds}" -- "${cur}") $(compgen -f -- "${cur}") )
        return 0
    fi
    if [[ "${cur}" == -* ]]; then
        COMPREPLY=( $(compgen -W "${flags}" -- "${cur}") )
        return 0
    fi
    COMPREPLY=( $(compgen -f -- "${cur}") )
}
complete -F _extract_completions extract
"""

_ZSH_COMPLETION = r"""# extract-cli zsh completion
#   eval "$(extract completion zsh)"
_extract() {
    local -a cmds flags
    cmds=(
        'schema:Print the output JSON Schema (the cross-CLI contract)'
        'fields:List extractable fields and their tier'
        'demo:Run extraction on a bundled fixture'
        'completion:Emit a shell completion script'
    )
    flags=(
        '--json' '--why' '-q' '--silent' '--no-color' '--llm'
        '--format' '--fields' '--no-confidence' '--catalog' '-V' '--version'
    )
    if (( CURRENT == 2 )); then
        _describe 'command' cmds
        _files
        return
    fi
    _files
    compadd -- ${flags}
}
compdef _extract extract
"""


def cmd_completion(args: argparse.Namespace) -> int:
    shell = (args.shell or "").lower()
    if shell == "bash":
        sys.stdout.write(_BASH_COMPLETION)
        return 0
    if shell == "zsh":
        sys.stdout.write(_ZSH_COMPLETION)
        return 0
    raise ExtractError(f"unsupported shell: {args.shell!r}. Supported: bash, zsh.")


def _completion_handler(argv: List[str]) -> int:
    """Hidden `__complete` handler invoked by the shell-completion scripts."""
    if not argv:
        return 0
    what = argv[0]
    if what == "commands":
        for c in _SUBCOMMANDS:
            print(c)
    elif what == "flags":
        for f in _GLOBAL_FLAGS:
            print(f)
    return 0


# ---------------------------------------------------------------------------
# Machine-readable catalog (`extract --catalog json`)
# ---------------------------------------------------------------------------
# The suite's shared discovery contract: agents call `extract --catalog json`
# at startup to learn every command and flag instead of hardcoding them
# (parallel to `nda-review-cli --catalog json`, `docx2pdf --catalog json`,
# `sign --catalog json`). It is a STABLE contract — keep it complete and
# accurate; `tests/test_cli.py` asserts it never drifts from the real parser.


def _flag(name: str, *, aliases: Optional[List[str]] = None, help: str = "",
          default: Any = None, choices: Optional[List[str]] = None,
          required: bool = False) -> JSON:
    return {
        "name": name,
        "aliases": aliases if aliases is not None else [],
        "help": help,
        "required": required,
        "default": default,
        "choices": choices,
    }


# Output flags shared by `extract` and `demo` (mirror _add_common_output_flags).
_CATALOG_OUTPUT_FLAGS: Tuple[JSON, ...] = (
    _flag("--json", help="Force JSON output to stdout (the default)."),
    _flag("--format", default="json", choices=["json", "table"],
          help="Output format (default: json)."),
    _flag("--no-confidence",
          help="Omit confidence/source markers (reduced convenience view)."),
    _flag("--why", help="Print a rationale block to stderr."),
    _flag("--silent", aliases=["-q", "--quiet"],
          help="Suppress non-error diagnostics (and the human table)."),
)


def build_catalog() -> JSON:
    """The machine-readable catalog emitted by `extract --catalog json`."""
    extract_flags: List[JSON] = [
        _flag("--llm",
              help="Opt-in LLM enrichment of fuzzy fields (renewal mechanics, "
                   "obligations, and a clause-map fallback). Off by default; the "
                   "deterministic core is fully useful without it."),
        _flag("--fields", default="",
              help="Comma-separated subset of top-level fields to emit "
                   "(e.g. parties,clauses,governing_law)."),
        *_CATALOG_OUTPUT_FLAGS,
    ]
    return {
        "name": CLI_NAME,
        "bin": "extract",
        "version": __version__,
        "description": (
            "Open-loop front door of the contract-ops CLI suite: ingest any contract "
            "(.md/.txt/.html/.docx/.pdf) and emit structured JSON."
        ),
        "commands": [
            {
                "name": "extract",
                "help": "Parse a document into structured JSON. The default action: "
                        "`extract <path>` works without naming the subcommand. "
                        "Positional: path to a .md/.txt/.html/.docx/.pdf file.",
                "flags": extract_flags,
            },
            {
                "name": "schema",
                "help": "Print the output JSON Schema — the cross-CLI output contract.",
                "flags": [],
            },
            {
                "name": "fields",
                "help": "List extractable fields and the tier that produces each.",
                "flags": [_flag("--json", help="Emit the field list as JSON.")],
            },
            {
                "name": "demo",
                "help": "Run extraction on a bundled fixture (zero-config first run).",
                "flags": list(_CATALOG_OUTPUT_FLAGS),
            },
            {
                "name": "completion",
                "help": "Emit a shell-completion script. Positional: bash | zsh.",
                "flags": [],
            },
        ],
        "exitCodes": {
            "0": "success",
            "1": "low-signal document — no high-signal fields (parties/clauses/dates) "
                 "could be extracted; e.g. a scanned/image-only or empty file. "
                 "A finding, not a crash.",
            "2": "bad usage / user-actionable error (unreadable path, bad flag value, "
                 "unsupported completion shell).",
        },
    }


# ---------------------------------------------------------------------------
# Argument parsing + main
# ---------------------------------------------------------------------------


def _add_common_output_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="Force JSON output to stdout (the default).")
    p.add_argument("--format", choices=("json", "table"), default="json",
                   help="Output format (default: json).")
    p.add_argument("--no-confidence", action="store_true",
                   help="Omit confidence/source markers (reduced convenience view).")
    p.add_argument("--why", action="store_true",
                   help="Print a rationale block to stderr.")
    p.add_argument("-q", "--silent", "--quiet", dest="silent", action="store_true",
                   help="Suppress non-error diagnostics (and the human table).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract",
        description="Ingest any contract (.md/.txt/.html/.docx/.pdf) and emit structured "
                    "JSON for the contract-ops CLI suite. See docs/INTEROP.md.",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"{CLI_NAME} {__version__}")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color (also honors NO_COLOR / FORCE_COLOR).")

    sub = parser.add_subparsers(dest="command")

    p_schema = sub.add_parser("schema", help="Print the output JSON Schema (the contract).")
    p_schema.set_defaults(func=cmd_schema)

    p_fields = sub.add_parser("fields", help="List extractable fields and their tier.")
    p_fields.add_argument("--json", action="store_true", help="Emit JSON.")
    p_fields.set_defaults(func=cmd_fields)

    p_demo = sub.add_parser("demo", help="Run extraction on a bundled fixture.")
    _add_common_output_flags(p_demo)
    p_demo.add_argument("--llm", action="store_true", help=argparse.SUPPRESS)
    p_demo.add_argument("--fields", default="", help=argparse.SUPPRESS)
    p_demo.set_defaults(func=cmd_demo)

    p_comp = sub.add_parser("completion", help="Emit a shell completion script (bash or zsh).")
    p_comp.add_argument("shell", choices=("bash", "zsh"))
    p_comp.set_defaults(func=cmd_completion)

    p_ex = sub.add_parser("extract", help="Extract a document (explicit form of the default).")
    _build_extract_args(p_ex)

    return parser


def _build_extract_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="Path to the document (.md/.txt/.html/.docx/.pdf).")
    p.add_argument("--llm", action="store_true",
                   help="Opt-in LLM enrichment of fuzzy fields (renewal, obligations). "
                        "Off by default; the deterministic core is fully useful without it.")
    p.add_argument("--fields", default="",
                   help="Comma-separated subset of top-level fields to emit "
                        "(e.g. parties,clauses,governing_law).")
    _add_common_output_flags(p)
    p.set_defaults(func=cmd_extract)


def _build_default_extract_parser() -> argparse.ArgumentParser:
    """Parser for the bare `extract <path>` default action (no subcommand)."""
    p = argparse.ArgumentParser(
        prog="extract",
        description="Extract a document into structured JSON (default action).",
    )
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI color (also honors NO_COLOR / FORCE_COLOR).")
    _build_extract_args(p)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    # Locale-safe stdout/stderr: POSIX/C locale (common on macOS CI runners)
    # leaves the streams in ASCII mode, so any non-ASCII char would raise
    # UnicodeEncodeError. Force UTF-8 regardless of LANG/LC_ALL.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    argv = sys.argv[1:] if argv is None else argv

    # Global --no-color before argparse so it works on every form.
    if "--no-color" in argv:
        os.environ["NO_COLOR"] = "1"
        argv = [a for a in argv if a != "--no-color"]

    # Hidden completion handler (kept out of argparse / --help).
    if argv and argv[0] == "__complete":
        return _completion_handler(argv[1:])

    # `extract --catalog json` (or `--catalog=json`): the suite discovery
    # contract. Intercepted before routing so it works as a bare global flag.
    catalog_fmt: Optional[str] = None
    for i, a in enumerate(argv):
        if a == "--catalog":
            catalog_fmt = argv[i + 1] if i + 1 < len(argv) else "json"
            break
        if a.startswith("--catalog="):
            catalog_fmt = a.split("=", 1)[1] or "json"
            break
    if catalog_fmt is not None:
        if catalog_fmt != "json":
            _eprint(_red("error:") + f" unknown --catalog format {catalog_fmt!r}; supported: json")
            return 2
        print(json.dumps(build_catalog(), indent=2, ensure_ascii=True))
        return 0

    if not argv:
        build_parser().print_help()
        return 0

    # Route: a known subcommand or -V/-h go through the full parser; anything
    # else is treated as the default `extract <path>` action.
    known = set(_SUBCOMMANDS) | {"extract", "-V", "--version", "-h", "--help"}
    first = argv[0]
    try:
        if first in known:
            parser = build_parser()
            args = parser.parse_args(argv)
            if not getattr(args, "func", None):
                parser.print_help()
                return 0
        else:
            args = _build_default_extract_parser().parse_args(argv)
        return args.func(args) or 0
    except ExtractError as e:
        _eprint(_red("error:") + f" {e}")
        return 2
    except BrokenPipeError:  # e.g. `extract foo.md | head`
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        _eprint("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
