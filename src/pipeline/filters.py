"""Reference-section filters — strip the bibliography from extracted text while
preserving appendices/supplementary that follow it.

Three modes share the same goal but use different signals:
  - regex:      line-pattern heuristics only (works with any engine's text)
  - structural: pdfplumber char positions + table bounding boxes (veto)
  - score:      font-size veto + 0-5 heuristic scoring
"""

import re

_REF_START = re.compile(
    r"^(references|bibliography|literature\s*(cited|review)|works?\s*cited)",
    re.IGNORECASE,
)
_REF_ENTRY = re.compile(r"^\[?\d+\]?\s*[A-Z][a-z]+.*\d{4}")
_APPENDIX_START = re.compile(
    r"^(appendix|supplementary\s*(material|table|figure|data)|supporting\s*information)",
    re.IGNORECASE,
)


def filter_relevant_sections(text: str) -> str:
    """Strip references but preserve appendices/supplementary that follow them.

    Regex-based filter for use with any text (no pdfplumber available).
    """
    lines = text.split("\n")
    ref_start = None
    half = len(lines) // 2
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > half and _REF_START.match(stripped):
            ref_start = i
            break
        if i > half and _REF_ENTRY.match(stripped):
            ref_count = sum(1 for j in range(i, min(i + 5, len(lines))) if _REF_ENTRY.match(lines[j].strip()))
            if ref_count >= 2:
                ref_start = i
                break
    if ref_start is None:
        return text

    # Keep everything before references
    kept = lines[:ref_start]

    # Scan after references for appendix/supplementary
    appendix_lines = []
    in_appendix = False
    for line in lines[ref_start:]:
        stripped = line.strip()
        if _APPENDIX_START.match(stripped):
            in_appendix = True
        if in_appendix:
            appendix_lines.append(line)

    if appendix_lines:
        kept.extend(appendix_lines)

    result = "\n".join(kept).strip()
    return result if result else text


def _find_true_references_in_pdf(pdf) -> tuple:
    """Scan pdfplumber pages for the real 'References' heading using structural data.

    Returns (page_number, line_text, line_index_in_page) of the first 'References'
    heading whose chars are NOT inside any detected table bounding box.
    """
    for page in pdf.pages:
        # Get all table bounding boxes on this page
        table_bboxes = [t.bbox for t in (page.find_tables() or [])]
        if not table_bboxes:
            continue

        text = page.extract_text()
        if not text:
            continue

        # Get word-level positions
        words = page.extract_words()
        # Build positions of each word: {lowercase_word: [(x0, top, x1, bottom), ...]}
        word_positions = {}
        for w in words:
            key = w["text"].strip().lower()
            if key:
                word_positions.setdefault(key, []).append((w["x0"], w["top"], w["x1"], w["bottom"]))

        lines = text.split("\n")
        for li, line in enumerate(lines):
            stripped = line.strip()
            if not _REF_START.match(stripped):
                continue
            # Get first word of the match
            first_word = stripped.split()[0].lower()
            positions = word_positions.get(first_word, [])
            if not positions:
                # No positional data — skip this page's check
                continue
            # Check if ANY occurrence of this first word is outside all tables
            for wx0, wtop, wx1, wbottom in positions:
                inside_any_table = any(
                    tb[0] <= (wx0 + wx1) / 2 <= tb[2] and tb[1] <= (wtop + wbottom) / 2 <= tb[3]
                    for tb in table_bboxes
                )
                if not inside_any_table:
                    # This "References" isn't inside any table — it's the real heading
                    return page.page_number, stripped, li
    return None, None, None


def filter_relevant_sections_structural(pdf_path: str, pre_extracted_text: str = None) -> str:
    """Filter references using pdfplumber structural data.

    Uses pdfplumber's per-character position data and table bboxes to
    distinguish the true 'References' section heading (not inside a table)
    from table footer cells like 'References 38 31 31'.
    If pre_extracted_text is given, applies the cut to it; otherwise
    re-extracts with pdfplumber.
    """
    import pdfplumber as _pdfplumber

    with _pdfplumber.open(pdf_path) as pdf:
        ref_page, ref_line_text, ref_line_idx = _find_true_references_in_pdf(pdf)

        if pre_extracted_text:
            lines = pre_extracted_text.split("\n")
        else:
            raw_parts = [_page_text_with_tables(p) for p in pdf.pages]
            lines = "\n\n".join(raw_parts).split("\n")

        ref_start = None
        half = len(lines) // 2
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if i > half and stripped == ref_line_text:
                ref_start = i
                break
            # Fallback: regex-based detection with is_table_ref_footer guard
            if i > half and _REF_START.match(stripped):
                if _is_table_ref_footer(stripped):
                    continue
                ref_start = i
                break
            if i > half and _REF_ENTRY.match(stripped):
                ref_count = sum(1 for j in range(i, min(i + 5, len(lines))) if _REF_ENTRY.match(lines[j].strip()))
                if ref_count >= 2:
                    ref_start = i
                    break

        if ref_start is None:
            return pre_extracted_text or "\n\n".join(raw_parts)

        kept = lines[:ref_start]
        appendix_lines = []
        in_appendix = False
        for line in lines[ref_start:]:
            stripped = line.strip()
            if _APPENDIX_START.match(stripped):
                in_appendix = True
            if in_appendix:
                appendix_lines.append(line)
        if appendix_lines:
            kept.extend(appendix_lines)

        result = "\n".join(kept).strip()
        return result if result else (pre_extracted_text or "\n\n".join(raw_parts))


def _score_ref_candidate(lines: list, idx: int) -> int:
    """Score a 'References' candidate line (idx) from 0-5.

    Higher = more likely to be the real section heading.
    """
    line = lines[idx].strip()
    score = 0

    # 1. Standalone short line
    if len(line) < 20:
        score += 1

    # 2. Next 3+ lines look like bibliography entries
    entry_count = 0
    for j in range(idx + 1, min(idx + 8, len(lines))):
        if _REF_ENTRY.match(lines[j].strip()):
            entry_count += 1
    if entry_count >= 3:
        score += 1

    # 3. Preceded by common pre-reference markers
    pre = "\n".join(lines[max(0, idx - 10):idx]).lower()
    if any(m in pre for m in ("data availability", "acknowledgement", "funding", "conflict", "code availability")):
        score += 1

    # 4. NOT followed by inline digits (rejects table footnotes like "References 38 31 31")
    rest = line[len("references"):].strip() if line.lower().startswith("references") else line
    if rest and not rest[0].isdigit():
        score += 1

    # 5. In last 25% of document
    if idx >= len(lines) * 0.75:
        score += 1

    return score


def _load_page_font_info(pdf_path: str) -> tuple:
    """Return (body_font_size, set of table-first-words) from pdfplumber.

    body_font_size is estimated as the most common char size across all pages.
    table_first_words are first words from detected table cells — used to
    flag candidates that likely originate inside a table, not as a heading.
    """
    import pdfplumber
    from collections import Counter

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_sizes = []
            table_words = set()
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    for row in tbl:
                        for cell in row:
                            if cell and cell.strip():
                                table_words.add(cell.strip().split()[0].lower())
                for ch in page.chars:
                    s = ch.get("size", 0)
                    if 6 < s < 30:
                        all_sizes.append(round(s, 1))
            body_size = Counter(all_sizes).most_common(1)[0][0] if all_sizes else 10
            return body_size, table_words
    except Exception:
        return 10, set()


def filter_relevant_sections_score(pdf_path: str, pre_extracted_text: str = None) -> str:
    """Filter references using font size (if available) + text heuristics.

    Font check is a veto only — if the candidate's first word appears only
    in table cells (small font), it's rejected.  Text heuristics score 0-5
    make the final decision.
    """
    import pdfplumber as _pdfplumber

    if pre_extracted_text:
        lines = pre_extracted_text.split("\n")
    else:
        with _pdfplumber.open(pdf_path) as pdf:
            raw_parts = [_page_text_with_tables(p) for p in pdf.pages]
        lines = "\n\n".join(raw_parts).split("\n")

    body_size, table_first_words = _load_page_font_info(pdf_path)

    half = len(lines) // 2
    best = (0, None)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not _REF_START.match(stripped):
            continue

        # Font veto: reject if first word is ONLY known from small-font table cells
        first_word = stripped.split()[0].lower() if stripped.split() else ""
        if first_word in table_first_words and body_size:
            score = _score_ref_candidate(lines, i)
            if score < 3:
                continue  # looks like table footer

        score = _score_ref_candidate(lines, i)
        if i > half and score >= 3:
            return _cut_at_reference(lines, i)
        if i > half and score > best[0]:
            best = (score, i)

    if best[1] is not None and best[0] >= 2:
        return _cut_at_reference(lines, best[1])

    return pre_extracted_text if pre_extracted_text else "\n\n".join(lines)


def _cut_at_reference(lines: list, ref_idx: int) -> str:
    """Cut lines at ref_idx, keeping appendix/supplementary after."""
    kept = lines[:ref_idx]
    appendix = []
    in_app = False
    for line in lines[ref_idx:]:
        s = line.strip()
        if _APPENDIX_START.match(s):
            in_app = True
        if in_app:
            appendix.append(line)
    if appendix:
        kept.extend(appendix)
    result = "\n".join(kept).strip()
    return result if result else "\n".join(lines)


def _is_table_ref_footer(stripped: str) -> bool:
    """Detect if a 'References' line is a table footer (e.g. 'References 38 31 31')."""
    rest = stripped[len("references"):].strip() if stripped.lower().startswith("references") else ""
    return bool(rest) and not rest[0].isalpha()


def _page_text_with_tables(page) -> str:
    """Rendered page text with tables as markdown (same format as pdfplumber engine)."""
    page_text = page.extract_text() or ""
    page_parts = []
    if page_text:
        page_parts.append(page_text)
    for tbl in (page.extract_tables() or []):
        if not tbl:
            continue
        lines = []
        for row in tbl:
            cells = [c.replace("\n", " ") if c else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        header = lines[0]
        sep = "| " + " | ".join("---" for _ in tbl[0]) + " |"
        page_parts.append(f"\n--- Table on page {page.page_number} ---\n" + "\n".join([header, sep] + lines[1:]))
    return "\n\n".join(page_parts)
