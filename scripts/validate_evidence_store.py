import duckdb
import sys
import argparse
from pathlib import Path


QUERIES = {
    ".schema": "SELECT * FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name;",

    "1_role_distribution": """
        SELECT d.role, COUNT(DISTINCT d.dimension_id) as dim_count, COUNT(DISTINCT d.experiment_id) as exp_count
        FROM dimensions d
        GROUP BY d.role
        ORDER BY dim_count DESC;
    """,

    "2_stratification_factors": """
        SELECT DISTINCT d.name, d.experiment_id, e.paper_id
        FROM dimensions d
        JOIN experiments e ON d.experiment_id = e.experiment_id
        WHERE d.role = 'stratification'
        ORDER BY d.name, e.paper_id;
    """,

    "3_manipulated_factors": """
        SELECT DISTINCT d.name, d.experiment_id, e.paper_id
        FROM dimensions d
        JOIN experiments e ON d.experiment_id = e.experiment_id
        WHERE d.role = 'manipulated'
        ORDER BY d.name, e.paper_id;
    """,

    "4_cross_paper_phosphorus_source": """
        SELECT DISTINCT e.paper_id, e.experiment_id, e.label
        FROM experiments e
        JOIN dimensions d ON e.experiment_id = d.experiment_id
        WHERE d.name = 'Phosphorus_source' AND d.role = 'manipulated'
        ORDER BY e.paper_id;
    """,

    "4_5_cross_paper_n_queries": """
        SELECT DISTINCT e.paper_id, d.name
        FROM experiments e
        JOIN dimensions d ON e.experiment_id = d.experiment_id
        WHERE d.role = 'manipulated' AND (d.name LIKE '%Nitrogen%' OR d.name LIKE '%nitrogen%' OR d.name = 'N_rate')
        ORDER BY e.paper_id, d.name;
    """,

    "5_anova_availability": """
        SELECT e.paper_id, COUNT(DISTINCT sr.analysis_id) as analyses, COUNT(DISTINCT sr.result_id) as results
        FROM experiments e
        JOIN statistical_analyses sa ON e.experiment_id = sa.experiment_id
        JOIN statistical_results sr ON sa.analysis_id = sr.analysis_id
        GROUP BY e.paper_id
        ORDER BY e.paper_id;
    """,

    "6_significant_effects": """
        SELECT sr.source, sr.source_type, sr.response_variable, sr.value, sr.p_value_raw, sr.significance, e.paper_id
        FROM statistical_results sr
        JOIN statistical_analyses sa ON sr.analysis_id = sa.analysis_id
        JOIN experiments e ON sa.experiment_id = e.experiment_id
        WHERE sr.significance IN ('*', '**', '***', 'significant')
        ORDER BY e.paper_id, sr.response_variable, sr.source;
    """,

    "7_dimensions_per_paper": """
        SELECT e.paper_id, COUNT(DISTINCT d.dimension_id) as dims
        FROM experiments e
        JOIN dimensions d ON e.experiment_id = d.experiment_id
        GROUP BY e.paper_id
        ORDER BY e.paper_id;
    """,

    "8_observations_per_paper": """
        SELECT e.paper_id, COUNT(DISTINCT o.observation_id) as obs
        FROM experiments e
        JOIN observations o ON e.experiment_id = o.experiment_id
        GROUP BY e.paper_id
        ORDER BY e.paper_id;
    """,

    "9_dimensions_by_experiment": """
        SELECT d.experiment_id, e.paper_id, d.name, d.role
        FROM dimensions d
        JOIN experiments e ON d.experiment_id = e.experiment_id
        ORDER BY e.paper_id, d.experiment_id, d.name;
    """,

    "10_n_holding_p_constant": """
        SELECT DISTINCT e.paper_id, e.experiment_id
        FROM experiments e
        WHERE e.experiment_id IN (
            SELECT d1.experiment_id FROM dimensions d1
            WHERE d1.name = 'Nitrogen_rate' AND d1.role = 'manipulated'
        )
        AND e.experiment_id IN (
            SELECT d2.experiment_id FROM dimensions d2
            WHERE d2.name = 'Phosphorus_source' AND d2.role = 'manipulated'
        )
        ORDER BY e.paper_id;
    """,
}


def run_query(db_path: str, query_name: str, raw: bool = False):
    if query_name not in QUERIES and query_name != ".schema":
        print(f"Unknown query: {query_name}")
        print(f"Available: {', '.join(sorted(k for k in QUERIES if k != '.schema'))}")
        sys.exit(1)

    sql = QUERIES[query_name]
    db = duckdb.connect(str(db_path))
    if raw:
        print(sql.strip())
    else:
        result = db.execute(sql).fetchall()
        desc = [d[0] for d in db.description]
        print(f"--- {query_name} ---")
        if not result:
            print("(no results)")
        else:
            print(" | ".join(desc))
            print("-" * (sum(len(h) for h in desc) + 3 * len(desc)))
            for row in result:
                print(" | ".join(str(v) if v is not None else "NULL" for v in row))
        print(f"({len(result)} rows)")
    db.close()


def run_all(db_path: str):
    db = duckdb.connect(str(db_path))
    print(f"=== Evidence Store Validation: {db_path} ===\n")

    # Overview
    tables = db.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name;").fetchall()
    print("Tables:")
    for t, tt in tables:
        print(f"  {t} ({tt})")
    print()

    counts = db.execute("""
        SELECT 'papers' as tbl, COUNT(*) FROM papers
        UNION ALL SELECT 'experiments', COUNT(*) FROM experiments
        UNION ALL SELECT 'dimensions', COUNT(*) FROM dimensions
        UNION ALL SELECT 'observations', COUNT(*) FROM observations
        UNION ALL SELECT 'measurements', COUNT(*) FROM measurements
        UNION ALL SELECT 'statistical_analyses', COUNT(*) FROM statistical_analyses
        UNION ALL SELECT 'statistical_results', COUNT(*) FROM statistical_results
        ORDER BY tbl;
    """).fetchall()
    for tbl, cnt in counts:
        print(f"  {tbl}: {cnt}")
    print()

    for qname in sorted(k for k in QUERIES if k != ".schema"):
        result = db.execute(QUERIES[qname]).fetchall()
        print(f"--- {qname} ({len(result)} rows) ---")
        desc = [d[0] for d in db.description]
        for row in result[:8]:
            print(" | ".join(str(v) if v is not None else "NULL" for v in row))
        if len(result) > 8:
            print(f"  ... ({len(result) - 8} more)")
        print()
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate evidence store contents")
    parser.add_argument("--db", default=None, help="Path to DuckDB evidence store")
    parser.add_argument("--query", "-q", default=None, help="Run a single query by name")
    parser.add_argument("--raw", "-r", action="store_true", help="Print SQL instead of executing")
    parser.add_argument("--list", "-l", action="store_true", help="List available queries")
    args = parser.parse_args()

    if args.list:
        print("Available queries:")
        for k in sorted(QUERIES):
            print(f"  {k}")
        sys.exit(0)

    db_path = args.db
    if db_path is None:
        candidates = list(Path("data").glob("*.duckdb"))
        if not candidates:
            print("No .duckdb file found in data/ and no --db provided. Run the loader first.")
            sys.exit(1)
        db_path = str(candidates[-1])
        print(f"Using: {db_path}")

    if args.query:
        run_query(db_path, args.query, raw=args.raw)
    else:
        run_all(db_path)
