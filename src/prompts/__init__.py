"""Prompt loading with content-hash + semantic-version tracking.

Each prompt has two identities:
  - prompt_version: human-readable semantic version, declared in versions.json
  - prompt_hash:     sha256 of the prompt file content — the exact cache identity

The hash is the source of truth for caching: editing a prompt changes its hash,
which invalidates any artifact that depended on it.  The semantic version is for
traceability ("this record was made by extraction prompt v1.2").
"""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent

VERSIONS = json.loads((HERE / "versions.json").read_text(encoding="utf-8"))

_PROMPT_FILES = {
    "discovery": "discovery.md",
    "extraction": "extraction.md",
    "statistical_extraction": "statistical_extraction.md",
    "validation": "validation.md",
    "normalization": "normalization.md",
}


def read_prompt(name: str) -> str:
    return (HERE / _PROMPT_FILES[name]).read_text(encoding="utf-8")


def prompt_hash(name: str) -> str:
    """sha256 of the prompt file content — the exact cache identity."""
    return hashlib.sha256((HERE / _PROMPT_FILES[name]).read_bytes()).hexdigest()


def prompt_version(name: str) -> str:
    return VERSIONS.get(name, "0.0.0")


discovery = read_prompt("discovery")
extraction = read_prompt("extraction")
statistical_extraction = read_prompt("statistical_extraction")
validation = read_prompt("validation")
normalization = read_prompt("normalization")
