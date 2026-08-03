"""
Load all v3 CERs into DuckDB with the new schema.
"""

import json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

DB_PATH = Path("data/evidence_store_v3.duckdb")
CER_DIR = Path("data/extractions")

# --- Connect / reset ---
if DB_PATH.exists():
    DB_PATH.unlink()
con = duckdb.connect(str(DB_PATH))

def safe_float(v, default=None):
    if v is None: return default
    try: return float(v)
    except (ValueError, TypeError): return default

# --- Create tables ---
con.execute("""
CREATE TABLE papers (
    paper_id VARCHAR,
    citation VARCHAR,
    year VARCHAR,
    source_url VARCHAR,
    extraction_completeness VARCHAR
);

CREATE TABLE experiments (
    experiment_id VARCHAR,
    paper_id VARCHAR,
    label VARCHAR,
    purpose VARCHAR,
    design VARCHAR,
    location VARCHAR,
    season VARCHAR,
    environment VARCHAR,
    replications INTEGER,
    site_context_json VARCHAR
);

CREATE TABLE dimensions (
    dimension_id VARCHAR,
    experiment_id VARCHAR,
    name VARCHAR,
    role VARCHAR,
    levels_json VARCHAR
);

CREATE TABLE evidence_sources (
    source_id VARCHAR,
    experiment_id VARCHAR,
    type VARCHAR,
    purpose VARCHAR,
    page_number INTEGER,
    observation_grain_json VARCHAR
);

CREATE TABLE observations (
    observation_id VARCHAR,
    experiment_id VARCHAR,
    source_id VARCHAR,
    factor_values_json VARCHAR,
    provenance VARCHAR,
    confidence VARCHAR
);

CREATE TABLE measurements (
    measurement_id VARCHAR,
    observation_id VARCHAR,
    metric VARCHAR,
    construct VARCHAR,
    value_raw VARCHAR,
    computed_value DOUBLE,
    significance_letter VARCHAR,
    statistic VARCHAR,
    unit VARCHAR,
    direction VARCHAR
);

CREATE TABLE statistical_analyses (
    analysis_id VARCHAR,
    experiment_id VARCHAR,
    source_id VARCHAR,
    analysis_type VARCHAR,
    design VARCHAR,
    notes VARCHAR
);

CREATE TABLE statistical_results (
    result_id VARCHAR,
    analysis_id VARCHAR,
    response_variable VARCHAR,
    unit VARCHAR,
    source VARCHAR,
    source_type VARCHAR,
    statistic_type VARCHAR,
    value DOUBLE,
    p_value_raw VARCHAR,
    significance VARCHAR,
    params_json VARCHAR
);
""")

# --- Load each CER ---
paper_id = 0
obs_id = 0
measurement_id = 0
analysis_id = 0
result_id = 0
dimension_id = 0

for i in range(1, 11):
    path = CER_DIR / f"s{i}_v3"
    if not path.exists():
        print(f"SKIP s{i} — not found")
        continue

    with open(str(path)) as f:
        data = json.load(f)

    cer = data["cer"]
    paper_id += 1
    pid = f"P-{paper_id:02d}"

    for study in cer.get("studies", []):
        con.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?, ?)",
            [pid, study.get("citation", ""), study.get("year"), study.get("source_url"), study.get("extraction_completeness")]
        )

    for exp in cer.get("experiments", []):
        eid = f"{pid}_{exp['experiment_id']}"
        ctx = exp.get("context", {})
        con.execute(
            "INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [eid, pid, exp.get("label", ""), ctx.get("purpose"), ctx.get("design"),
             ctx.get("location"), ctx.get("season"), ctx.get("environment"),
             ctx.get("replications"), json.dumps(ctx.get("site_properties", {}))]
        )

        for d in exp.get("dimensions", []):
            dimension_id += 1
            did = f"DIM-{dimension_id:04d}"
            con.execute(
                "INSERT INTO dimensions VALUES (?, ?, ?, ?, ?)",
                [did, eid, d.get("name", ""), d.get("role", "unknown"), json.dumps(d.get("levels", []))]
            )

        for s in exp.get("evidence_sources", []):
            con.execute(
                "INSERT INTO evidence_sources VALUES (?, ?, ?, ?, ?, ?)",
                [s.get("source_id", ""), eid, s.get("type", ""), s.get("purpose", ""),
                 s.get("page_number"), json.dumps(s.get("observation_grain", []))]
            )

        for sa in exp.get("statistical_analyses", []):
            analysis_id += 1
            aid = f"SA-{analysis_id:04d}"
            con.execute(
                "INSERT INTO statistical_analyses VALUES (?, ?, ?, ?, ?, ?)",
                [aid, eid, sa.get("source_id", ""), sa.get("analysis_type"),
                 sa.get("design"), sa.get("notes")]
            )
            for t in sa.get("tables", []):
                for r in t.get("rows", []):
                    result_id += 1
                    # General shape: statistic_type + value + params
                    stat_type = r.get("statistic_type")
                    stat_value = safe_float(r.get("value"))
                    params = dict(r.get("params") or {})
                    # Legacy ANOVA shape: f_value at top level
                    if stat_type is None and r.get("f_value") is not None:
                        stat_type = "F"
                        stat_value = safe_float(r.get("f_value"))
                    for k in ["df_numerator", "df_denominator", "sum_sq", "mean_sq"]:
                        v = r.get(k)
                        if v is not None:
                            try: params[k] = float(v)
                            except: params[k] = v
                    con.execute(
                        "INSERT INTO statistical_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [f"SR-{result_id:04d}", aid,
                         t.get("response_variable", ""), t.get("unit"),
                         r.get("source"), r.get("source_type", ""),
                         stat_type, stat_value,
                         str(r.get("p_value")) if r.get("p_value") is not None else None,
                         r.get("significance"),
                         json.dumps(params) if params else None]
                    )

    for obs in cer.get("observations", []):
        obs_id += 1
        oid = f"O-{obs_id:04d}"
        con.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
            [oid, f"{pid}_{obs.get('experiment_id', '')}", obs.get("source_id", ""),
             json.dumps(obs.get("factor_values", {})), obs.get("provenance"), obs.get("confidence")]
        )
        for m in obs.get("measurements", []):
            measurement_id += 1
            con.execute(
                "INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [f"M-{measurement_id:04d}", oid, m.get("metric", ""), m.get("construct", ""),
                 m.get("value_raw"), m.get("computed_value"), m.get("significance_letter"),
                 m.get("statistic", "mean"), m.get("unit"), m.get("direction", "n/a")]
            )

# --- Create views for convenience ---
con.execute("CREATE VIEW v_observations AS SELECT o1.*, e1.paper_id, e1.design, e1.location, e1.season FROM observations o1 JOIN experiments e1 ON o1.experiment_id = e1.experiment_id;")
con.execute("CREATE VIEW v_dimensions AS SELECT d1.*, e1.paper_id FROM dimensions d1 JOIN experiments e1 ON d1.experiment_id = e1.experiment_id;")
con.execute("CREATE VIEW v_anova AS SELECT sr.result_id, sr.analysis_id, sr.response_variable, sr.unit, sr.source, sr.source_type, sr.statistic_type, sr.value, sr.p_value_raw, sr.significance, sr.params_json, sta.source_id, sta.analysis_type, sta.design, sta.experiment_id, e2.paper_id FROM statistical_results sr JOIN statistical_analyses sta ON sr.analysis_id = sta.analysis_id JOIN experiments e2 ON sta.experiment_id = e2.experiment_id;")
con.execute("CREATE VIEW v_measurements AS SELECT m.measurement_id, m.observation_id, m.metric, m.construct, m.value_raw, m.computed_value, m.significance_letter, m.statistic, m.unit, m.direction, o2.experiment_id, o2.source_id, o2.factor_values_json, o2.provenance FROM measurements m JOIN observations o2 ON m.observation_id = o2.observation_id;")

# --- Summary ---
print(f"Loaded {paper_id} papers")
print(f"  {len(cer.get('experiments', []))} experiments (last paper count)")
res = con.execute("SELECT COUNT(*) FROM experiments").fetchone()
print(f"  {res[0]} total experiments")
res = con.execute("SELECT COUNT(*) FROM dimensions").fetchone()
print(f"  {res[0]} dimensions")
res = con.execute("SELECT COUNT(*) FROM observations").fetchone()
print(f"  {res[0]} observations")
res = con.execute("SELECT COUNT(*) FROM measurements").fetchone()
print(f"  {res[0]} measurements")
res = con.execute("SELECT COUNT(*) FROM statistical_analyses").fetchone()
print(f"  {res[0]} statistical analyses")
res = con.execute("SELECT COUNT(*) FROM statistical_results").fetchone()
print(f"  {res[0]} statistical results")

con.close()
print(f"\nWritten to {DB_PATH}")
