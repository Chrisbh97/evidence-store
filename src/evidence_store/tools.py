import json, os
from pathlib import Path
from typing import Optional

import duckdb

DB_PATH = Path("data/evidence_store_v4.duckdb")


def _db():
    return duckdb.connect(str(DB_PATH))


SCHEMA = {
    "search_experiments": {
        "name": "search_experiments",
        "citable": True,
        "description": "Call this FIRST to find which experiments studied a given treatment factor or crop. Returns experiment IDs, paper IDs, labels, and design info. Use the returned experiment_ids in other tools. Does NOT return measurements or significance.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Crop or subject keyword (e.g. 'bread wheat', 'faba bean', 'maize'). Filters experiments whose label or paper citation mentions this term."
                },
                "manipulated_factors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Factor names the experiment must manipulate (e.g. ['Nitrogen_rate', 'Phosphorus_rate']). Experiments must have ALL listed factors."
                },
                "stratification_factors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stratification/blocking factors the experiment must have (e.g. ['Soil_type', 'Location'])."
                },
                "paper_id": {
                    "type": "string",
                    "description": "Filter to a specific paper (e.g. 'P-03')."
                }
            }
        }
    },
    "get_significant_findings": {
        "name": "get_significant_findings",
        "citable": True,
        "description": "Call this to get statistical significance (p-values, F-values, significance stars) for treatment factors and interactions. Does NOT return actual measurement values — use get_measurements for those.",
        "parameters": {
            "type": "object",
            "properties": {
                "response_variable": {
                    "type": "string",
                    "description": "Filter by response variable name (e.g. 'Grain yield', 'Biomass'). Case-insensitive substring match."
                },
                "p_max": {
                    "type": "number",
                    "description": "Maximum p-value threshold (default 0.05)."
                },
                "source": {
                    "type": "string",
                    "description": "Filter by source name (e.g. 'Nitrogen_rate', 'Phosphorus_rate x Weeding')."
                },
                "paper_id": {
                    "type": "string",
                    "description": "Filter to a specific paper."
                },
                "significance_min": {
                    "type": "string",
                    "enum": ["*", "**", "***", "significant"],
                    "description": "Minimum significance level. '***' = p<0.001, '**' = p<0.01, '*' = p<0.05."
                }
            }
        }
    },
    "get_measurements": {
        "name": "get_measurements",
        "citable": True,
        "description": "Call this to get actual numeric measurement values (yield, biomass, etc.) grouped by a treatment factor's levels. Returns measurement_ids you can cite. CRITICAL: Set varying_factor to the factor you want to compare across. Use factor_filters to pin other factors to specific levels. Does NOT tell you statistical significance — use get_significant_findings for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "The experiment identifier (e.g. 'P-04_E-1'). Required unless paper_id is given."
                },
                "paper_id": {
                    "type": "string",
                    "description": "Filter to a specific paper (e.g. 'P-04'). Used when experiment_id is unknown."
                },
                "metric": {
                    "type": "string",
                    "description": "Metric name (e.g. 'Grain yield', 'Biomass'). Case-insensitive substring match."
                },
                "varying_factor": {
                    "type": "string",
                    "description": "The factor to compare across (e.g. 'Phosphorus_rate', 'Nitrogen_rate'). Required. The tool returns one result per level of this factor."
                },
                "factor_filters": {
                    "type": "object",
                    "description": "Pin non-varying factors to specific levels, e.g. {'Nitrogen_rate': '207', 'Location': 'Combined'}. Optional — if omitted, all matching rows are returned grouped by the varying factor."
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["value_asc", "value_desc"],
                    "description": "Sort results by computed value ascending or descending."
                },
                "result_type": {
                    "type": "string",
                    "enum": ["treatment_combination", "marginal_value"],
                    "description": "Filter by result type. 'treatment_combination' = all factors specified (cell means). 'marginal_value' = averaged over some factors (main effects)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 100)."
                }
            }
        }
    },
    "list_papers": {
        "name": "list_papers",
        "citable": True,
        "description": "Call this to list all papers in the evidence store with their IDs, citations, years, and experiment counts. Use the paper IDs to filter other tools.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "list_response_variables": {
        "name": "list_response_variables",
        "citable": True,
        "description": "Call this to discover what response variables/metrics were measured in a paper (e.g. 'Grain yield', 'Biomass'). Use the returned names as metric filters in get_measurements or get_significant_findings.",
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Filter to a specific paper."
                }
            }
        }
    },
    "get_experiment_context": {
        "name": "get_experiment_context",
        "citable": True,
        "description": "Call this to get location, season, design, replications, soil properties, and site context for an experiment. Use after search_experiments returns experiment IDs. Does NOT describe treatment factors or their levels — use get_dimension_levels for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "The experiment identifier (e.g. 'P-04_E-1')."
                }
            },
            "required": ["experiment_id"]
        }
    },
    "get_dimension_levels": {
        "name": "get_dimension_levels",
        "citable": True,
        "description": "Call this to find out what treatment levels were tested (e.g. which P rates, N rates, varieties). Returns factor names, roles, and the specific values tested. These are treatment INPUTS — never average or compute statistics on them. Does NOT return outcome data — use get_measurements for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "dimension_name": {
                    "type": "string",
                    "description": "Dimension name (e.g. 'Nitrogen_rate', 'Soil_type'). Case-insensitive."
                },
                "paper_id": {
                    "type": "string",
                    "description": "Filter to a specific paper."
                }
            }
        }
    },
    "trace_observation": {
        "name": "trace_observation",
        "citable": True,
        "description": "Call this to get the full provenance of a single data point — which observation, all its measurements, and the source table/figure including page number. Use when a user asks 'where does this come from?'. Does NOT list all data from a source — use get_source_observations for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "observation_id": {
                    "type": "string",
                    "description": "The observation ID (e.g. 'O-0001') to trace."
                }
            },
            "required": ["observation_id"]
        }
    },
    "get_source_observations": {
        "name": "get_source_observations",
        "citable": True,
        "description": "Call this to get all data from a specific table or figure — all observations and their measurements. Use when a user asks for everything in a source (e.g. 'Table 2'). Does NOT return significance — use get_significant_findings for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "The source identifier (e.g. 'Table 2')."
                },
                "experiment_id": {
                    "type": "string",
                    "description": "The experiment identifier (e.g. 'P-01_E-1')."
                }
            },
            "required": ["source_id", "experiment_id"]
        }
    },
    "get_experiment_schema": {
        "name": "get_experiment_schema",
        "citable": True,
        "description": "Call this BEFORE get_measurements to discover the full factor structure for an experiment — all factor names, their roles, and all tested levels. Use the returned factors to pick your varying_factor and factor_filters for get_measurements. Does NOT return measurement values.",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "The experiment identifier (e.g. 'P-04_E-1')."
                }
            },
            "required": ["experiment_id"]
        }
    }
}


def search_experiments(subject: Optional[str] = None,
                       manipulated_factors: Optional[list] = None,
                       stratification_factors: Optional[list] = None,
                       paper_id: Optional[str] = None):
    con = _db()
    conditions = []
    params = []

    if subject:
        conditions.append("(e.label ILIKE ? OR e.purpose ILIKE ? OR p.citation ILIKE ?)")
        params.extend([f"%{subject}%", f"%{subject}%", f"%{subject}%"])

    if paper_id:
        conditions.append("e.paper_id = ?")
        params.append(paper_id)

    # Guard: LLMs sometimes pass single-element list as bare string
    if isinstance(manipulated_factors, str):
        manipulated_factors = [manipulated_factors]
    if isinstance(stratification_factors, str):
        stratification_factors = [stratification_factors]

    base = """
        SELECT e.experiment_id, e.paper_id, e.label, e.design, e.location, e.season
        FROM experiments e
        JOIN papers p ON e.paper_id = p.paper_id
    """
    if manipulated_factors:
        base += f"""
            JOIN dimensions dm ON e.experiment_id = dm.experiment_id AND dm.role = 'manipulated'
        """
        placeholders = ",".join("?" for _ in manipulated_factors)
        base += f" AND dm.name IN ({placeholders})"
        params.extend(manipulated_factors)

    if stratification_factors:
        base += f"""
            JOIN dimensions ds ON e.experiment_id = ds.experiment_id AND ds.role = 'stratification'
        """
        placeholders = ",".join("?" for _ in stratification_factors)
        base += f" AND ds.name IN ({placeholders})"
        params.extend(stratification_factors)

    if conditions:
        base += " WHERE " + " AND ".join(conditions)

    base += " GROUP BY e.experiment_id, e.paper_id, e.label, e.design, e.location, e.season"

    having = []
    if manipulated_factors:
        having.append(f"COUNT(DISTINCT CASE WHEN dm.role = 'manipulated' THEN dm.name END) = {len(manipulated_factors)}")
    if stratification_factors:
        having.append(f"COUNT(DISTINCT CASE WHEN ds.role = 'stratification' THEN ds.name END) = {len(stratification_factors)}")
    if having:
        base += " HAVING " + " AND ".join(having)

    rows = con.execute(base, params).fetchall()
    con.close()

    if not rows:
        return {"experiments": [], "message": "No matching experiments found."}

    results = []
    for r in rows:
        results.append({
            "experiment_id": r[0], "paper_id": r[1], "label": r[2],
            "design": r[3], "location": r[4], "season": r[5]
        })
    return {"experiments": results, "count": len(results)}


def get_significant_findings(response_variable: Optional[str] = None,
                             p_max: float = 0.05,
                             source: Optional[str] = None,
                             paper_id: Optional[str] = None,
                             significance_min: Optional[str] = None):
    con = _db()
    conditions = ["sr.significance IS NOT NULL"]
    params = []

    if response_variable:
        conditions.append("LOWER(sr.response_variable) LIKE LOWER(?)")
        params.append(f"%{response_variable}%")
    if source:
        conditions.append("LOWER(sr.source) LIKE LOWER(?)")
        params.append(f"%{source}%")
    if paper_id:
        conditions.append("e.paper_id = ?")
        params.append(paper_id)
    if significance_min:
        levels = {"*": 1, "**": 2, "***": 3, "significant": 1}
        min_lvl = levels.get(significance_min, 1)
        if min_lvl >= 3:
            conditions.append("sr.significance = '***'")
        elif min_lvl >= 2:
            conditions.append("sr.significance IN ('**', '***')")
        else:
            conditions.append("sr.significance IN ('*', '**', '***', 'significant')")
    else:
        conditions.append("sr.significance IN ('*', '**', '***', 'significant')")

    sql = f"""
        SELECT sr.source, sr.source_type, sr.response_variable, sr.value, sr.p_value_raw,
               sr.significance, e.paper_id, e.experiment_id
        FROM statistical_results sr
        JOIN statistical_analyses sa ON sr.analysis_id = sa.analysis_id
        JOIN experiments e ON sa.experiment_id = e.experiment_id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.paper_id, sr.response_variable
        LIMIT 100
    """
    rows = con.execute(sql, params).fetchall()
    con.close()

    if not rows:
        return {"results": [], "message": "No significant findings match your criteria."}

    results = []
    for r in rows:
        results.append({
            "source": r[0], "source_type": r[1], "response_variable": r[2],
            "value": r[3], "p_value": r[4], "significance": r[5],
            "paper_id": r[6], "experiment_id": r[7]
        })
    return {"results": results, "count": len(results)}


def get_measurements(
    experiment_id: Optional[str] = None,
    paper_id: Optional[str] = None,
    metric: Optional[str] = None,
    varying_factor: Optional[str] = None,
    factor_filters: Optional[dict] = None,
    sort_by: Optional[str] = None,
    result_type: Optional[str] = None,
    limit: int = 100
):
    """Measurement retrieval grouped by varying_factor. Returns all matching rows with full factor context."""
    if not experiment_id and not paper_id:
        return {"error": "Either experiment_id or paper_id is required."}
    if not varying_factor:
        return {"error": "varying_factor is required. Specify which factor to compare across (e.g. Nitrogen_rate, Phosphorus_rate)."}

    con = _db()

    # Determine experiment IDs
    exp_ids = []
    if experiment_id:
        exp_ids = [experiment_id]
    elif paper_id:
        rows = con.execute("SELECT experiment_id FROM experiments WHERE paper_id = ?", [paper_id]).fetchall()
        exp_ids = [r[0] for r in rows]

    if len(exp_ids) != 1:
        con.close()
        return {"error": f"Paper {paper_id} has {len(exp_ids)} experiments. Specify experiment_id to disambiguate."}

    eid = exp_ids[0]

    # Get factor names for this experiment (from dimensions table)
    dim_rows = con.execute("""
        SELECT DISTINCT d.name FROM dimensions d
        WHERE d.experiment_id = ?
    """, [eid]).fetchall()
    experiment_factor_names = [r[0] for r in dim_rows]

    factor_filters = factor_filters or {}

    # Classify filter keys as recognized or unrecognized
    recognized = [k for k in factor_filters if k in experiment_factor_names]
    unrecognized = [k for k in factor_filters if k not in experiment_factor_names]

    # Build query — return all matching rows, grouped by varying_factor level
    conditions = []
    params = []

    conditions.append("o.experiment_id = ?")
    params.append(eid)

    if metric:
        conditions.append("LOWER(m.metric) LIKE LOWER(?)")
        params.append(f"%{metric}%")

    if result_type:
        conditions.append("o.result_type = ?")
        params.append(result_type)

    for k, v in factor_filters.items():
        if k in experiment_factor_names:
            conditions.append("LOWER(o.factor_values_json) LIKE LOWER(?)")
            params.append(f'%"{k}"%{v}%')

    where_clause = " AND ".join(conditions)

    # Extract varying factor level from JSON
    varying_col = f"json_extract_string(o.factor_values_json, '$.{varying_factor}')"

    # Sort: by varying_factor level first (for grouping), then by computed_value
    order_clause = f"ORDER BY {varying_col}"
    if sort_by == "value_desc":
        order_clause += ", computed_value DESC NULLS LAST"
    elif sort_by == "value_asc":
        order_clause += ", computed_value ASC NULLS LAST"
    else:
        order_clause += ", computed_value DESC NULLS LAST"

    sql = f"""
        SELECT {varying_col} as level,
               m.computed_value,
               m.measurement_id,
               m.metric,
               m.unit,
               o.factor_values_json,
               o.provenance
        FROM measurements m
        JOIN observations o ON m.observation_id = o.observation_id
        WHERE {where_clause}
        {order_clause}
        LIMIT ?
    """
    params.append(limit)

    rows = con.execute(sql, params).fetchall()
    con.close()

    results = []
    for r in rows:
        fv = json.loads(r[5]) if isinstance(r[5], str) else r[5]
        results.append({
            "level": r[0],
            "computed_value": r[1],
            "measurement_id": r[2],
            "metric": r[3],
            "unit": r[4],
            "factor_values": fv,
            "provenance": r[6] if len(r) > 6 else None
        })

    return {
        "varying_factor": varying_factor,
        "factor_filters": {k: v for k, v in factor_filters.items() if k in experiment_factor_names},
        "recognized_filters": recognized,
        "unrecognized_filters": unrecognized,
        "results": results,
        "count": len(results)
    }



def get_experiment_context(experiment_id: str):
    con = _db()
    row = con.execute("""
        SELECT experiment_id, paper_id, label, purpose, design, location, season,
               environment, replications, site_context_json
        FROM experiments WHERE experiment_id = ?
    """, [experiment_id]).fetchone()
    con.close()
    if not row:
        return {"error": f"Experiment {experiment_id} not found."}
    
    site_props = json.loads(row[9]) if isinstance(row[9], str) else (row[9] or {})
    return {
        "experiment_id": row[0], "paper_id": row[1], "label": row[2],
        "purpose": row[3], "design": row[4], "location": row[5],
        "season": row[6], "environment": row[7], "replications": row[8],
        "site_properties": site_props
    }


def list_papers():
    con = _db()
    rows = con.execute("""
        SELECT p.paper_id, p.citation, p.year, p.extraction_completeness,
               COUNT(DISTINCT e.experiment_id) as experiments
        FROM papers p
        LEFT JOIN experiments e ON p.paper_id = e.paper_id
        GROUP BY p.paper_id, p.citation, p.year, p.extraction_completeness
        ORDER BY p.paper_id
    """).fetchall()
    con.close()

    return {
        "papers": [
            {"paper_id": r[0], "citation": r[1][:80] + "..." if r[1] and len(r[1]) > 80 else r[1],
             "year": r[2], "completeness": r[3], "experiments": r[4]}
            for r in rows
        ],
        "count": len(rows)
    }


def list_response_variables(paper_id: Optional[str] = None):
    con = _db()
    results = []

    if paper_id:
        for r in con.execute("""
            SELECT sr.response_variable, COUNT(DISTINCT sr.result_id)
            FROM statistical_results sr
            JOIN statistical_analyses sa ON sr.analysis_id = sa.analysis_id
            JOIN experiments e ON sa.experiment_id = e.experiment_id
            WHERE e.paper_id = ?
            GROUP BY sr.response_variable ORDER BY COUNT(*) DESC
        """, [paper_id]).fetchall():
            results.append({"name": r[0], "source": "anova_table", "papers": 1, "result_count": r[1]})

        for r in con.execute("""
            SELECT m.metric, COUNT(DISTINCT m.measurement_id)
            FROM measurements m
            JOIN observations o ON m.observation_id = o.observation_id
            WHERE o.experiment_id LIKE ?
            GROUP BY m.metric ORDER BY COUNT(*) DESC
        """, [f"{paper_id}_%"]).fetchall():
            results.append({"name": r[0], "source": "measurement", "papers": 1, "result_count": r[1]})
    else:
        for r in con.execute("""
            SELECT sr.response_variable, COUNT(DISTINCT e.paper_id), COUNT(DISTINCT sr.result_id)
            FROM statistical_results sr
            JOIN statistical_analyses sa ON sr.analysis_id = sa.analysis_id
            JOIN experiments e ON sa.experiment_id = e.experiment_id
            GROUP BY sr.response_variable
            ORDER BY COUNT(DISTINCT e.paper_id) DESC LIMIT 50
        """).fetchall():
            results.append({"name": r[0], "source": "anova_table", "papers": r[1], "result_count": r[2]})

        for r in con.execute("""
            SELECT m.metric, COUNT(DISTINCT SUBSTR(o.experiment_id, 1, 5)), COUNT(DISTINCT m.measurement_id)
            FROM measurements m
            JOIN observations o ON m.observation_id = o.observation_id
            GROUP BY m.metric
            ORDER BY COUNT(DISTINCT SUBSTR(o.experiment_id, 1, 5)) DESC LIMIT 50
        """).fetchall():
            results.append({"name": r[0], "source": "measurement", "papers": r[1], "result_count": r[2]})

    con.close()
    return {"response_variables": results, "count": len(results)}


def get_experiment_schema(experiment_id: str):
    """Return all factors with roles and levels for one experiment."""
    con = _db()
    rows = con.execute("""
        SELECT d.name, d.role, d.levels_json
        FROM dimensions d
        WHERE d.experiment_id = ?
        ORDER BY d.role, d.name
    """, [experiment_id]).fetchall()
    con.close()

    if not rows:
        return {"experiment_id": experiment_id, "factors": [], "message": "No dimensions found for this experiment."}

    factors = []
    for r in rows:
        factors.append({
            "name": r[0],
            "role": r[1],
            "levels": json.loads(r[2]) if isinstance(r[2], str) else r[2]
        })

    return {"experiment_id": experiment_id, "factors": factors, "count": len(factors)}


def get_dimension_levels(dimension_name: Optional[str] = None,
                          paper_id: Optional[str] = None):
    con = _db()
    conditions = []
    params = []

    if dimension_name:
        conditions.append("LOWER(d.name) LIKE LOWER(?)")
        params.append(f"%{dimension_name}%")
    if paper_id:
        conditions.append("e.paper_id = ?")
        params.append(paper_id)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    rows = con.execute(f"""
        SELECT d.dimension_id, d.name, d.role, d.levels_json, d.experiment_id, e.paper_id
        FROM dimensions d
        JOIN experiments e ON d.experiment_id = e.experiment_id
        {where}
        ORDER BY e.paper_id, d.name
        LIMIT 100
    """, params).fetchall()
    con.close()

    if not rows:
        return {"dimensions": [], "message": "No matching dimensions found."}

    results = []
    for r in rows:
        results.append({
            "dimension_id": r[0], "name": r[1], "role": r[2],
            "levels": json.loads(r[3]) if isinstance(r[3], str) else r[3],
            "experiment_id": r[4], "paper_id": r[5]
        })
    return {"dimensions": results, "count": len(results)}


def trace_observation(observation_id: str):
    """Return full provenance chain for one observation: measurements + evidence source + page."""
    con = _db()
    obs = con.execute("""
        SELECT o.observation_id, o.experiment_id, o.source_id, o.factor_values_json,
               o.provenance, o.confidence,
               es.type, es.purpose, es.page_number,
               es.observation_grain_json
        FROM observations o
        LEFT JOIN evidence_sources es ON o.source_id = es.source_id AND o.experiment_id = es.experiment_id
        WHERE o.observation_id = ?
    """, [observation_id]).fetchone()
    if not obs:
        con.close()
        return {"error": f"Observation {observation_id} not found."}

    measurements = con.execute("""
        SELECT metric, construct, value_raw, computed_value, significance_letter, statistic, unit
        FROM measurements WHERE observation_id = ?
    """, [observation_id]).fetchall()
    con.close()

    return {
        "observation_id": obs[0],
        "experiment_id": obs[1],
        "source_id": obs[2],
        "factor_values": json.loads(obs[3]) if isinstance(obs[3], str) else obs[3],
        "provenance": obs[4],
        "confidence": obs[5],
        "source_type": obs[6],
        "source_purpose": obs[7],
        "page_number": obs[8],
        "observation_grain": json.loads(obs[9]) if isinstance(obs[9], str) else obs[9],
        "measurements": [
            {"metric": m[0], "construct": m[1], "value_raw": m[2],
             "computed_value": m[3], "significance_letter": m[4],
             "statistic": m[5], "unit": m[6]}
            for m in measurements
        ],
        "measurement_count": len(measurements)
    }


def get_source_observations(source_id: str, experiment_id: str):
    """Return all observations from one source (table) with their measurements."""
    con = _db()
    source = con.execute("""
        SELECT type, purpose, page_number, observation_grain_json
        FROM evidence_sources WHERE source_id = ? AND experiment_id = ?
    """, [source_id, experiment_id]).fetchone()

    obs_rows = con.execute("""
        SELECT o.observation_id, o.factor_values_json, o.provenance,
               o.marginal_over_json, o.result_type,
               m.metric, m.value_raw, m.computed_value, m.significance_letter, m.statistic, m.unit
        FROM observations o
        LEFT JOIN measurements m ON o.observation_id = m.observation_id
        WHERE o.source_id = ? AND o.experiment_id = ?
        ORDER BY o.observation_id
    """, [source_id, experiment_id]).fetchall()
    con.close()

    # Group measurements by observation
    obs_map = {}
    for r in obs_rows:
        oid = r[0]
        if oid not in obs_map:
            obs_map[oid] = {
                "observation_id": oid,
                "factor_values": json.loads(r[1]) if isinstance(r[1], str) else r[1],
                "marginal_over": json.loads(r[3]) if isinstance(r[3], str) else (r[3] or []),
                "result_type": r[4] or "treatment_combination",
                "provenance": r[2],
                "measurements": []
            }
        if r[5]:  # metric is not null
            obs_map[oid]["measurements"].append({
                "metric": r[5], "value_raw": r[6], "computed_value": r[7],
                "significance_letter": r[8], "statistic": r[9], "unit": r[10]
            })

    return {
        "source_id": source_id,
        "experiment_id": experiment_id,
        "source_type": source[0] if source else None,
        "source_purpose": source[1] if source else None,
        "page_number": source[2] if source else None,
        "observation_grain": json.loads(source[3]) if source and isinstance(source[3], str) else (source[3] if source else None),
        "observations": list(obs_map.values()),
        "observation_count": len(obs_map)
    }


TOOL_IMPLS = {
    "search_experiments": search_experiments,
    "get_significant_findings": get_significant_findings,
    "get_measurements": get_measurements,
    "list_papers": list_papers,
    "list_response_variables": list_response_variables,
    "get_dimension_levels": get_dimension_levels,
    "get_experiment_context": get_experiment_context,
    "get_experiment_schema": get_experiment_schema,
    "trace_observation": trace_observation,
    "get_source_observations": get_source_observations,
}

TOOL_SCHEMAS = [v for v in SCHEMA.values()]
