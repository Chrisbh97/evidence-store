"""Pipeline orchestrator — deterministic sequence of pure stages.

Owns the overall flow:  text -> (filter) -> discovery -> per-source extraction
-> statistical extraction -> compile.  Every stage's output is cached through
`store` keyed on (text_hash, prompt_hash, model), so re-running the pipeline
only re-invokes the LLM for stages whose inputs actually changed.

Data in / data out: `run_pipeline` returns the final output dict and takes all
config as keyword arguments.  It does not read argv, does not exit, and writes
nothing except the cache via `store` and progress messages via `log`.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from src.pdf_extractor import extract as extract_pdf
from src.pipeline import store
from src.pipeline.compiler import Compiler, parse_discovery_json
from src.pipeline.filters import (
    filter_relevant_sections,
    filter_relevant_sections_structural,
    filter_relevant_sections_score,
)
from src.pipeline.llm import run_discovery, run_extraction, run_statistical_extraction
from src.pipeline.parsing import (
    dict_to_er,
    er_to_dict,
    parse_extraction_json_to_er,
    serialize,
)
from src.pipeline.parsing import _extract_json_block


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _extract_and_filter(
    pdf_path: Path,
    engine: str,
    do_filter: bool,
    filter_mode: str,
    log: Callable[[str], None],
) -> str:
    """Extract PDF text (cached), applying the reference filter if requested."""
    cached = store.load_text(pdf_path, engine, do_filter, filter_mode)
    if cached is not None:
        log(f"Text cache hit: {len(cached)} characters")
        return cached

    log(f"Extracting text from {pdf_path} (engine: {engine})...")
    paper_text = extract_pdf(str(pdf_path), engine=engine)
    log(f"Extracted {len(paper_text)} characters")

    if do_filter:
        if filter_mode == "structural":
            paper_text = filter_relevant_sections_structural(str(pdf_path), paper_text)
        elif filter_mode == "score":
            paper_text = filter_relevant_sections_score(str(pdf_path), paper_text)
        else:
            paper_text = filter_relevant_sections(paper_text)
        log(f"After section filter ({filter_mode}): {len(paper_text)} characters")

    store.save_text(pdf_path, engine, do_filter, filter_mode, paper_text)
    return paper_text


def _flatten_experiments(discovery_data: dict) -> tuple:
    """Return ([(exp, source, row_group)], [(exp, stat_source)]) flattened from discovery."""
    all_sources = []
    all_stats = []
    for exp in discovery_data.get("experiments", []):
        for src in exp.get("evidence_sources", []):
            for rg in src.get("row_groups", []):
                all_sources.append((exp, src, rg))
        for sa in exp.get("statistical_analyses", []):
            all_stats.append((exp, sa))
    return all_sources, all_stats


def run_pipeline(
    pdf_path: str,
    *,
    paper_id: Optional[str] = None,
    engine: str = "pypdf",
    do_filter: bool = False,
    filter_mode: str = "regex",
    delay: float = 15.0,
    timeout: float = 300.0,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None,
    log: Callable[[str], None] = _stderr,
) -> dict:
    """Run the full extraction pipeline.  Returns the compiled output dict.

    Keyword-only config mirrors the old CLI flags.  `log` receives progress
    messages (default: stderr).  Returns:
        {records, stat_records, cer: {studies, subjects, experiments, observations}, discovery}
    """
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF not found: {pdf}")

    pid = paper_id or pdf.stem
    model = model or os.getenv("MODEL", "gpt-4o")
    kwargs = {"api_key": api_key, "api_base": api_base, "model": model, "timeout": timeout}
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    # --- Text stage (cached) ---
    paper_text = _extract_and_filter(pdf, engine, do_filter, filter_mode, log)
    thash = store.text_hash(paper_text)

    # --- Discovery stage (cached) ---
    discovery = store.load_discovery(pid, thash, model)
    if discovery is not None:
        raw_discovery, discovery_data = discovery["raw"], discovery["parsed"]
        n_exp = len(discovery_data.get("experiments", []))
        n_src = sum(len(e.get("evidence_sources", [])) for e in discovery_data.get("experiments", []))
        n_stat = sum(len(e.get("statistical_analyses", [])) for e in discovery_data.get("experiments", []))
        log(f"Discovery cache hit: {n_exp} experiments, {n_src} sources, {n_stat} statistical")
    else:
        log("Stage 1: Discovery...")
        raw_discovery = run_discovery(paper_text, **kwargs)
        discovery_data = parse_discovery_json(raw_discovery)
        store.save_discovery(pid, thash, model, raw_discovery, discovery_data)
        n_exp = len(discovery_data.get("experiments", []))
        n_src = sum(len(e.get("evidence_sources", [])) for e in discovery_data.get("experiments", []))
        n_stat = sum(len(e.get("statistical_analyses", [])) for e in discovery_data.get("experiments", []))
        log(f"Discovered {n_exp} experiments, {n_src} evidence sources, {n_stat} statistical analyses")

    all_sources, all_stats = _flatten_experiments(discovery_data)

    # --- Per-row-group extraction stage (cached) ---
    records = []
    calls = 0
    for i, (exp, src, rg) in enumerate(all_sources):
        cached = store.load_source(pid, exp["experiment_id"], src["source_id"], rg["group_id"], thash, model)
        if cached is not None:
            records.append(dict_to_er(cached))
            log(f"  [{i+1}/{len(all_sources)}] {exp['experiment_id']} / {src['source_id']} / {rg['group_id']} — cached")
            continue
        if calls > 0 and delay > 0:
            log(f"  Waiting {delay}s...")
            time.sleep(delay)
        log(f"  [{i+1}/{len(all_sources)}] Extracting {exp['experiment_id']} / {src['source_id']} / {rg['group_id']}...")
        try:
            raw = run_extraction(paper_text, exp, src, rg, **kwargs)
            er = parse_extraction_json_to_er(raw, exp["experiment_id"], src["source_id"], rg["group_id"])
            records.append(er)
            store.save_source(pid, exp["experiment_id"], src["source_id"], rg["group_id"], thash, model, er_to_dict(er))
            calls += 1
        except KeyboardInterrupt:
            log("\nInterrupted. Progress saved to cache.")
            raise
        except Exception as e:
            log(f"  FAILED: {e} — skipping")

    # --- Statistical extraction stage (cached) ---
    stat_records = []
    for i, (exp, sa) in enumerate(all_stats):
        cached = store.load_stat(pid, exp["experiment_id"], sa["source_id"], thash, model)
        if cached is not None:
            stat_records.append(cached)
            log(f"  [{i+1}/{len(all_stats)}] stats {exp['experiment_id']} / {sa['source_id']} — cached")
            continue
        if calls > 0 and delay > 0:
            log(f"  Waiting {delay}s...")
            time.sleep(delay)
        log(f"  [{i+1}/{len(all_stats)}] Extracting stats {exp['experiment_id']} / {sa['source_id']}...")
        try:
            raw = run_statistical_extraction(paper_text, exp, sa, **kwargs)
            parsed = json.loads(_extract_json_block(raw))
            parsed["experiment_id"] = exp["experiment_id"]
            stat_records.append(parsed)
            store.save_stat(pid, exp["experiment_id"], sa["source_id"], thash, model, parsed)
            calls += 1
        except KeyboardInterrupt:
            log("\nInterrupted. Progress saved to cache.")
            raise
        except Exception as e:
            log(f"  FAILED: {e} — skipping")

    # --- Compile CER ---
    log("Compiling CER...")

    compiler = Compiler()
    result = compiler.compile(
        discovery_data,
        records,
        study_id=pid,
        statistical_results=stat_records if stat_records else None,
    )

    return {
        "records": [er_to_dict(r) for r in records],
        "stat_records": stat_records,
        "cer": {
            "studies": [serialize(s) for s in result.studies],
            "subjects": [serialize(s) for s in result.subjects],
            "experiments": [serialize(s) for s in result.experiments],
            "observations": [serialize(s) for s in result.observations],
        },
        "discovery": discovery_data,
    }
