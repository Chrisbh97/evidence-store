"""
PDF text extraction with pluggable engines.

Available engines:
  - pypdf       (default, fast, no deps)
  - pdfplumber  (good table extraction, no ML deps)
  - pymupdf4llm (PyMuPDF-based, layout-aware, outputs markdown;
                 auto-detects and re-renders rotated tables)
  - marker      (slow, heavy, but much better table/structure preservation)
  - docling     (IBM, good table extraction, moderate weight, may need GPU)
"""

from pathlib import Path
from typing import Optional


def extract(pdf_path: str, engine: str = "pypdf", **kwargs) -> str:
    if engine == "pypdf":
        return _extract_pypdf(pdf_path)
    elif engine == "pdfplumber":
        return _extract_pdfplumber(pdf_path)
    elif engine == "pymupdf4llm":
        return _extract_pymupdf4llm(pdf_path)
    elif engine == "marker":
        return _extract_marker(pdf_path, **kwargs)
    elif engine == "docling":
        return _extract_docling(pdf_path)
    else:
        raise ValueError(f"Unknown PDF engine: {engine}. Choose 'pypdf', 'pdfplumber', 'pymupdf4llm', 'marker', or 'docling'.")


def _extract_pypdf(pdf_path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_pdfplumber(pdf_path: str) -> str:
    import pdfplumber as _pdfplumber

    with _pdfplumber.open(pdf_path) as pdf:
        parts = []
        for i, page in enumerate(pdf.pages):
            page_parts = []

            text = page.extract_text()
            if text:
                page_parts.append(text)

            tables = page.extract_tables()
            for ti, tbl in enumerate(tables):
                if not tbl:
                    continue
                # Render table as markdown
                lines = []
                for row in tbl:
                    cells = [c.replace("\n", " ") if c else "" for c in row]
                    lines.append("| " + " | ".join(cells) + " |")
                header = lines[0]
                sep = "| " + " | ".join("---" for _ in tbl[0]) + " |"
                table_md = "\n".join([header, sep] + lines[1:])
                page_parts.append(f"\n--- Table on page {i+1} ---\n{table_md}")

            if page_parts:
                parts.append("\n\n".join(page_parts))

        return "\n\n".join(parts)


def _extract_pymupdf4llm(pdf_path: str) -> str:
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError:
        raise ImportError(
            "pymupdf4llm is not installed. Run: pip install pymupdf4llm"
        )

    from src.table_pipeline_v3 import process_document

    return process_document(pdf_path)


def _extract_marker(pdf_path: str, **kwargs) -> str:
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered
    except ImportError:
        raise ImportError(
            "marker-pdf is not installed. Run: pip install marker-pdf"
        )

    model_dict = create_model_dict()
    converter = PdfConverter(artifact_dict=model_dict)
    rendered = converter(pdf_path)
    full_text = text_from_rendered(rendered)

    return (
        f"[Extracted with marker-pdf — tables are rendered as markdown]\n\n"
        f"{full_text}"
    )


def _extract_docling(pdf_path: str) -> str:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise ImportError(
            "docling is not installed. Run: pip install docling"
        )

    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    text = result.document.export_to_markdown()

    return (
        f"[Extracted with docling — tables are rendered as markdown]\n\n"
        f"{text}"
    )
