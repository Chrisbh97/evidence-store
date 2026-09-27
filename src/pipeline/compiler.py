"""
Deterministic compiler — transforms discovery JSON + extraction records into canonical CERs.

No LLM calls. Same input always produces same output.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from src.schemas.extraction_record import ExtractionRecord, RawObservation
from src.schemas.canonical import (
    Study, Subject, Experiment, ExperimentalDimension, DimensionLevel, RowGroup, EvidenceSource,
    ExperimentContext, Observation, MeasurementValue, CompilationResult,
    StatisticalAnalysis, StatisticTable, StatisticRow,
)


_VALUE_SIG_RE = re.compile(r"^(-?\d+\.?\d*)\s*([a-zA-Z]+)$")


def parse_discovery_json(raw_text: str) -> dict:
    """Extract and parse the JSON block from discovery output."""
    text = raw_text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    # Find JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in discovery output")
    return json.loads(text[start:end+1])


def parse_extraction_json(raw_text: str) -> dict:
    """Parse extraction output into a dict."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _try_parse_value(raw: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """Extract (computed_value, significance_letter) from a value string."""
    if not raw:
        return (None, None)
    raw = raw.strip()
    m = _VALUE_SIG_RE.match(raw)
    if m:
        try:
            return (float(m.group(1)), m.group(2).strip() or None)
        except ValueError:
            return (None, None)
    try:
        return (float(raw), None)
    except ValueError:
        return (None, None)


def _infer_subject_type(name: str) -> str:
    raw = name.strip().lower()
    common_crops = {
        "faba bean", "maize", "wheat", "barley", "tef", "sorghum", "rice",
        "soybean", "chickpea", "lentil", "cowpea", "groundnut", "sunflower",
        "cotton", "coffee", "teff", "millet", "oat", "rye", "triticale",
        "bean", "pea", "potato", "cassava", "sweet potato", "sugarcane",
    }
    if raw in ("morocco", "local check", "check"):
        return "check"
    if raw.startswith("etbw"):
        return "advanced_line"
    if raw in common_crops:
        return "crop"
    if "variety" in raw:
        return "released_variety"
    return "unknown"


def _stat_row_from_dict(r: dict) -> StatisticRow:
    """Build a StatisticRow from an LLM stat-record row dict.

    Tolerates both the general shape (statistic_type/value/params) and legacy
    ANOVA-shaped records (f_value/sum_sq/mean_sq) so old cached artifacts
    still compile.
    """
    if "statistic_type" in r or "value" in r:
        return StatisticRow(
            source=r.get("source", ""),
            source_type=r.get("source_type", ""),
            statistic_type=r.get("statistic_type"),
            value=r.get("value"),
            df_numerator=r.get("df_numerator"),
            df_denominator=r.get("df_denominator"),
            p_value=r.get("p_value"),
            significance=r.get("significance"),
            params=dict(r.get("params") or {}),
        )
    # Legacy ANOVA shape: f_value / sum_sq / mean_sq top-level
    params = {}
    for k in ("sum_sq", "mean_sq"):
        if r.get(k) is not None:
            params[k] = r.get(k)
    return StatisticRow(
        source=r.get("source", ""),
        source_type=r.get("source_type", ""),
        statistic_type="F" if r.get("f_value") is not None else None,
        value=r.get("f_value"),
        df_numerator=r.get("df_numerator"),
        df_denominator=r.get("df_denominator"),
        p_value=r.get("p_value"),
        significance=r.get("significance"),
        params=params,
    )


@dataclass
class Compiler:
    """Compiles discovery JSON + ExtractionRecords into CompilationResult."""

    def compile(
        self,
        discovery_json: dict,
        records: list[ExtractionRecord],
        study_id: Optional[str] = None,
        statistical_results: Optional[list[dict]] = None,
    ) -> CompilationResult:
        result = CompilationResult()
        sid = study_id or "UNKNOWN"

        # --- Study ---
        study = Study(study_id=sid, extraction_completeness="full_text")
        study.unaccounted_tables = discovery_json.get("unaccounted_tables", [])
        result.studies.append(study)

        # --- Experiments ---
        experiments: dict[str, Experiment] = {}
        exp_datas = discovery_json.get("experiments", [])

        for ed in exp_datas:
            eid = ed.get("experiment_id", "")
            if not eid:
                continue

            ctx = ExperimentContext(
                purpose=ed.get("purpose"),
                design=ed.get("design"),
                location=ed.get("location"),
                season=ed.get("season"),
                environment=ed.get("environment"),
                replications=ed.get("replications"),
                site_properties=ed.get("site_context", {}),
            )

            dimensions = []
            for f in ed.get("dimensions", ed.get("experimental_factors", ed.get("factors", []))):
                levels = []
                for lvl in f.get("levels", []):
                    if isinstance(lvl, dict):
                        levels.append(DimensionLevel(
                            id=lvl.get("id", ""),
                            label=lvl.get("label", ""),
                            description=lvl.get("description", ""),
                        ))
                    else:
                        # Legacy: string level
                        levels.append(DimensionLevel(
                            id=str(lvl),
                            label=str(lvl),
                            description="",
                        ))
                dimensions.append(ExperimentalDimension(
                    name=f["name"],
                    levels=levels,
                    role=f.get("role", "unknown"),
                ))

            sources = []
            for s in ed.get("evidence_sources", []):
                row_groups = []
                for rg in s.get("row_groups", []):
                    row_groups.append(RowGroup(
                        group_id=rg.get("group_id", ""),
                        varies=rg.get("varies", []),
                        marginal_over=rg.get("marginal_over", []),
                        result_type=rg.get("result_type", "treatment_combination"),
                        row_labels=rg.get("row_labels", []),
                    ))
                sources.append(EvidenceSource(
                    source_id=s.get("source_id", ""),
                    type=s.get("type", "table"),
                    purpose=s.get("purpose", "primary"),
                    row_groups=row_groups,
                    page_number=s.get("page_number"),
                ))

            # --- Statistical analyses ---
            stat_analyses = []
            for sa in ed.get("statistical_analyses", []):
                analysis_id = f"SA-{eid}-{len(stat_analyses) + 1:02d}"
                stat_analyses.append(StatisticalAnalysis(
                    analysis_id=analysis_id,
                    experiment_id=eid,
                    source_id=sa.get("source_id", ""),
                    analysis_type=sa.get("type", "anova"),
                    design=sa.get("design"),
                ))
            # Also attach any extracted statistical results
            if statistical_results:
                for sr in statistical_results:
                    if sr.get("experiment_id") == eid:
                        analysis_id = f"SA-{eid}-{len(stat_analyses) + 1:02d}"
                        tables = []
                        for t in sr.get("tables", []):
                            rows = [_stat_row_from_dict(r) for r in t.get("rows", [])]
                            tables.append(StatisticTable(
                                response_variable=t.get("response_variable", ""),
                                unit=t.get("unit"),
                                rows=rows,
                            ))
                        stat_analyses.append(StatisticalAnalysis(
                            analysis_id=analysis_id,
                            experiment_id=eid,
                            source_id=sr.get("source_id", ""),
                            analysis_type=sr.get("analysis_type", "anova"),
                            design=sr.get("design"),
                            tables=tables,
                            notes=sr.get("notes"),
                        ))

            exp = Experiment(
                experiment_id=eid,
                study_id=sid,
                label=ed.get("label", ""),
                context=ctx,
                dimensions=dimensions,
                evidence_sources=sources,
                statistical_analyses=stat_analyses,
            )
            experiments[eid] = exp

        result.experiments = list(experiments.values())

        # --- Observations ---
        observations: dict[str, Observation] = {}
        subjects: dict[str, Subject] = {}
        subject_by_name: dict[str, str] = {}

        obs_counter = 0
        for er in records:
            exp_id = er.experiment_id
            src_id = er.source_id
            for ro in er.observations:
                obs_counter += 1
                oid = f"O-{obs_counter:04d}"
                # Enforce invariant: treatment_combination must have empty marginal_over
                marginal_over = list(ro.marginal_over)
                if ro.result_type == "treatment_combination":
                    marginal_over = []
                
                obs = Observation(
                    observation_id=oid,
                    experiment_id=exp_id,
                    source_id=src_id,
                    factor_values=dict(ro.factor_values),
                    marginal_over=marginal_over,
                    result_type=ro.result_type,
                    provenance=ro.provenance,
                    confidence=ro.confidence,
                )
                for rm in ro.measurements:
                    cv, sig = _try_parse_value(rm.value_raw)
                    # Prefer model-provided computed_value over our own parse
                    computed = rm.computed_value if rm.computed_value is not None else cv
                    sig_letter = rm.significance_letter or sig
                    obs.measurements.append(MeasurementValue(
                        metric=rm.metric_raw,
                        construct=rm.construct_raw,
                        value_raw=rm.value_raw,
                        computed_value=computed,
                        significance_letter=sig_letter,
                        statistic=rm.statistic,
                        unit=rm.unit_raw,
                    ))
                observations[oid] = obs

                # Derive subjects from factor values
                for fname, fval in ro.factor_values.items():
                    fn = fname.strip().lower()
                    if fn in ("variety", "genotype", "cultivar", "crop", "species", "subject"):
                        raw = fval.strip()
                        if raw:
                            key = raw.lower()
                            if key not in subject_by_name:
                                subj_id = f"SUBJ-{len(subjects) + 1:04d}"
                                subjects[subj_id] = Subject(
                                    subject_id=subj_id,
                                    canonical_name=raw,
                                    subject_type=_infer_subject_type(raw),
                                )
                                subject_by_name[key] = subj_id

        result.observations = list(observations.values())
        result.subjects = list(subjects.values())

        return result
