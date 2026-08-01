"""
Rotated-table-aware PDF -> Markdown pipeline (v3).

Detection: use PyMuPDF's find_tables() after rotating the page 90°
to verify real 2D-grid tables, rejecting marginalia automatically.

Strategy:
1. pymupdf4llm(page_chunks=True) on the original PDF -> baseline per-page markdown
2. For each page, fast scan for rotated text (line["dir"]). If none, use baseline.
3. If rotated text exists, rotate a temp copy 90° and call find_tables().
   Only proceed if a real table (>= 2 rows, >= 2 cols) is detected.
4. Extract upright table markdown via tab.to_markdown() on the rotated page.
5. Assemble page from fitz normal blocks + upright table markdown.
"""

import fitz
import pymupdf4llm
from collections import Counter
import os
import tempfile

ROTATION_DIR_THRESHOLD = 0.5


def has_vertical_text(page):
    """Fast check: does this page contain any rotated text spans?"""
    tp = page.get_text("dict")
    for block in tp["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            dx, dy = line["dir"]
            if abs(dx) < ROTATION_DIR_THRESHOLD:
                return True
    return False


def find_rotated_tables(src_doc, page_num):
    """Return (table_markdown_str, rotated_bbox_fitz) or (None, None).

    Rotates the page 90/270 degrees and uses PyMuPDF's find_tables()
    to verify real 2D-grid structure.  Returns the markdown and the
    bbox of the rotated content in the original page coordinates.
    """
    page = src_doc[page_num]
    # Collect rotated-line bboxes in original page space (for overlay)
    tp = page.get_text("dict")
    rotated_rects = []
    for block in tp["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            dx, dy = line["dir"]
            if abs(dx) < ROTATION_DIR_THRESHOLD:
                rotated_rects.append(fitz.Rect(line["bbox"]))

    if not rotated_rects:
        return None, None

    for angle in [90, 270]:
        temp_doc = fitz.open()
        temp_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
        temp_page = temp_doc[0]
        target_rotation = (page.rotation + angle) % 360
        temp_page.set_rotation(target_rotation)

        tables = temp_page.find_tables()
        for tab in tables:
            if tab.row_count >= 2 and tab.col_count >= 2:
                md = tab.to_markdown().strip()
                temp_doc.close()
                # Union rotated rects to get the table region in original space
                bbox = rotated_rects[0]
                for r in rotated_rects[1:]:
                    bbox.include_rect(r)
                return md, bbox
        temp_doc.close()

    return None, None


def get_normal_blocks(page, exclude_bbox=None):
    """Return list of {bbox, text} for non-table blocks outside excluded region."""
    tp = page.get_text("dict")
    blocks = []
    for block in tp["blocks"]:
        if "lines" not in block:
            continue
        bbox = fitz.Rect(block["bbox"])
        if exclude_bbox is not None and exclude_bbox.contains(bbox):
            continue
        lines = block.get("lines", [])
        rotated = sum(1 for line in lines if abs(line["dir"][0]) < ROTATION_DIR_THRESHOLD)
        if lines and rotated / len(lines) > 0.5:
            continue
        text = "".join(span["text"] for line in lines for span in line["spans"]).strip()
        if text:
            blocks.append({"bbox": bbox, "text": text})
    return blocks


def process_document(pdf_path):
    """Extract markdown, splicing rotated tables upright."""
    chunks = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)

    doc = fitz.open(pdf_path)
    results = []
    for pnum in range(len(doc)):
        page = doc[pnum]

        if not has_vertical_text(page):
            md = chunks[pnum].get("text", "").strip() if pnum < len(chunks) else ""
            results.append(md)
            continue

        table_md, table_bbox = find_rotated_tables(doc, pnum)
        if table_md is None:
            # detection said "rotated text exists" but no real table found
            # (likely axis labels, marginalia) -> fall back to baseline
            md = chunks[pnum].get("text", "").strip() if pnum < len(chunks) else ""
            results.append(md)
            continue

        normal_blocks = get_normal_blocks(page, exclude_bbox=table_bbox)

        items = [{"y0": b["bbox"].y0, "kind": "text", "content": b["text"]} for b in normal_blocks]
        items.append({"y0": table_bbox.y0, "kind": "table", "content": table_md})
        items.sort(key=lambda i: i["y0"])
        results.append("\n\n".join(i["content"] for i in items))

    doc.close()
    return "\n\n---\n\n".join(results)
