"""Versioned artifact store for the extraction pipeline.

Two responsibilities:

1. Text cache — the extracted (and filtered) paper text is expensive to produce
   (PDF parsing).  It is cached on disk keyed by (pdf_hash, engine, filter,
   filter_version).  Same inputs -> same text, read from disk, never re-parsed.

2. Versioned artifacts — every LLM-stage output (discovery, per-source
   extraction, statistical extraction) is stored as a file whose name carries a
   cache key derived from the inputs that determine it:
       (text_hash, prompt_hash, model)
   An artifact is reused only if it exists AND its inputs are unchanged — i.e.
   the current cache key matches the stored one.  Editing a prompt changes its
   content hash, changes the key, and forces re-extraction of only the stages
   that depended on that prompt.

The final compiled CER (what load_evidence_store.py consumes) is NOT stored
here — it is written by the orchestrator to the --output path as before, so
downstream consumers are unaffected.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

STAGING_ROOT = Path("data/staging")
TEXT_CACHE_ROOT = STAGING_ROOT / "text"

FILTER_VERSION = 1  # bump when the reference-filtering logic changes


def _sha(s: str, n: int = 16) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _pdf_id(pdf_path: Path) -> str:
    """Stable short hash of the PDF file contents."""
    return _sha(pdf_path.read_bytes().hex(), n=16)


# ---------------------------------------------------------------------------
# Text cache
# ---------------------------------------------------------------------------


def text_cache_path(pdf_path: Path, engine: str, do_filter: bool, filter_mode: str) -> Path:
    """Return the cached-text path for the given extraction inputs."""
    fname = f"{_pdf_id(pdf_path)}__{engine}__{'f' if do_filter else 'x'}__{filter_mode}__v{FILTER_VERSION}.txt"
    return TEXT_CACHE_ROOT / fname


def load_text(pdf_path: Path, engine: str, do_filter: bool, filter_mode: str) -> Optional[str]:
    """Return cached text if present, else None."""
    path = text_cache_path(pdf_path, engine, do_filter, filter_mode)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def save_text(pdf_path: Path, engine: str, do_filter: bool, filter_mode: str, text: str) -> Path:
    """Cache extracted text.  Returns the path written."""
    path = text_cache_path(pdf_path, engine, do_filter, filter_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def text_hash(text: str) -> str:
    """sha256 of the extracted text — part of every downstream cache key."""
    return _sha(text, n=16)


# ---------------------------------------------------------------------------
# Versioned artifacts (LLM stages)
# ---------------------------------------------------------------------------


def artifact_key(text_hash: str, prompt_name: str, model: str, group_id: str = "") -> str:
    """Cache identity of an LLM stage: (text, prompt content, model, group_id)."""
    return _sha(f"{text_hash}|{prompt_name}|{prompt_hash(prompt_name)}|{model}|{group_id}", n=12)


def prompt_hash(name: str) -> str:
    from src.prompts import prompt_hash as _ph

    return _ph(name)


def prompt_version(name: str) -> str:
    from src.prompts import prompt_version as _pv

    return _pv(name)


def _metadata(paper_id: str, artifact: str, text_hash: str, prompt_name: str,
              model: str, **extra) -> dict:
    return {
        "artifact": artifact,
        "paper_id": paper_id,
        "text_hash": text_hash,
        "prompt": prompt_name,
        "prompt_version": prompt_version(prompt_name),
        "prompt_hash": prompt_hash(prompt_name),
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **extra,
    }


def discovery_path(paper_id: str, key: str) -> Path:
    return STAGING_ROOT / paper_id / "discovery" / f"{key}.json"


def source_path(paper_id: str, exp_id: str, source_id: str, group_id: str, key: str) -> Path:
    safe_src = source_id.replace("/", "_").replace(" ", "_")
    safe_grp = group_id.replace("/", "_").replace(" ", "_")
    return STAGING_ROOT / paper_id / "sources" / f"{exp_id}__{safe_src}__{safe_grp}__{key}.json"


def stat_path(paper_id: str, exp_id: str, source_id: str, key: str) -> Path:
    safe_src = source_id.replace("/", "_").replace(" ", "_")
    return STAGING_ROOT / paper_id / "stats" / f"{exp_id}__{safe_src}__{key}.json"


def _load(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, metadata: dict, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "data": data}
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def load_discovery(paper_id: str, text_hash: str, model: str) -> Optional[dict]:
    """Return cached discovery {'raw', 'parsed'} if inputs unchanged, else None."""
    key = artifact_key(text_hash, "discovery", model)
    art = _load(discovery_path(paper_id, key))
    if art is None:
        return None
    return {"raw": art["data"].get("raw"), "parsed": art["data"].get("parsed")}


def save_discovery(paper_id: str, text_hash: str, model: str, raw: str, parsed: dict) -> None:
    key = artifact_key(text_hash, "discovery", model)
    meta = _metadata(paper_id, "discovery", text_hash, "discovery", model)
    _save(discovery_path(paper_id, key), meta, {"raw": raw, "parsed": parsed})


def load_source(paper_id: str, exp_id: str, source_id: str, group_id: str, text_hash: str, model: str) -> Optional[dict]:
    """Return cached extraction record if inputs unchanged, else None."""
    key = artifact_key(text_hash, "extraction", model, group_id)
    art = _load(source_path(paper_id, exp_id, source_id, group_id, key))
    return art["data"] if art else None


def save_source(paper_id: str, exp_id: str, source_id: str, group_id: str, text_hash: str, model: str, record: Any) -> None:
    key = artifact_key(text_hash, "extraction", model, group_id)
    meta = _metadata(paper_id, "extraction", text_hash, "extraction", model,
                     experiment_id=exp_id, source_id=source_id, group_id=group_id)
    _save(source_path(paper_id, exp_id, source_id, group_id, key), meta, record)


def load_stat(paper_id: str, exp_id: str, source_id: str, text_hash: str, model: str) -> Optional[dict]:
    key = artifact_key(text_hash, "statistical_extraction", model)
    art = _load(stat_path(paper_id, exp_id, source_id, key))
    return art["data"] if art else None


def save_stat(paper_id: str, exp_id: str, source_id: str, text_hash: str, model: str, record: dict) -> None:
    key = artifact_key(text_hash, "statistical_extraction", model)
    meta = _metadata(paper_id, "statistical_extraction", text_hash, "statistical_extraction", model,
                     experiment_id=exp_id, source_id=source_id)
    _save(stat_path(paper_id, exp_id, source_id, key), meta, record)


def has_source(paper_id: str, exp_id: str, source_id: str, group_id: str, text_hash: str, model: str) -> bool:
    return load_source(paper_id, exp_id, source_id, group_id, text_hash, model) is not None


def has_stat(paper_id: str, exp_id: str, source_id: str, text_hash: str, model: str) -> bool:
    return load_stat(paper_id, exp_id, source_id, text_hash, model) is not None
