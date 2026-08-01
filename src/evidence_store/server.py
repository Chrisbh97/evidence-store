"""
FastAPI server for the Evidence Discovery System.

Endpoints:
  POST /ask           — send a question, get answer + references
  GET  /api/experiments/{experiment_id}  — full experiment card
  GET  /api/source/{experiment_id}/{source_id}  — table/figure data
  GET  /api/papers    — list of all papers
  GET  /              — serve the single-page frontend
"""

import json
from pathlib import Path
from uuid import uuid4

import traceback

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .engine import ResearchEngine, ResearchSession
from .tools import (
    get_experiment_context, get_dimension_levels,
    get_source_observations, list_papers,
)
from .tools import _db as _tools_db

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Evidence Discovery System")

# Server-side session store
_sessions: dict[str, ResearchSession] = {}


def _get_session(session_id: str | None) -> ResearchSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    sid = session_id or str(uuid4())
    session = ResearchSession(id=sid)
    _sessions[sid] = session
    return session


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None


class AskResponse(BaseModel):
    text: str
    references: list[dict]
    citations: list[dict]
    tool_trace: list[dict]
    session_id: str


@app.post("/ask")
def ask(req: AskRequest):
    try:
        session = _get_session(req.session_id)
        engine = ResearchEngine(session=session)
        answer = engine.answer(req.question)
        return AskResponse(
            text=answer.text,
            references=[r.__dict__ for r in answer.references],
            citations=answer.citations,
            tool_trace=answer.tool_trace,
            session_id=session.id,
        )
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "detail": tb.splitlines()[-3:] if tb else []}
        )


@app.get("/api/papers")
def papers():
    return list_papers()


PDF_MAP = {
    f"P-{i:02d}": Path(__file__).resolve().parent.parent.parent / "Papers" / "Fertiliser" / f"s{i}.pdf"
    for i in range(1, 11)
}
PDF_MAP.update({
    f"P-{i}": Path(__file__).resolve().parent.parent.parent / "Papers" / "Fertiliser" / f"s{i}.pdf"
    for i in range(1, 11)
})


@app.get("/api/papers/{paper_id}/pdf")
def paper_pdf(paper_id: str):
    pdf_path = PDF_MAP.get(paper_id)
    if not pdf_path or not pdf_path.exists():
        return JSONResponse({"error": f"PDF not found for {paper_id}"}, status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(pdf_path), media_type="application/pdf")


@app.get("/api/experiments/{experiment_id}")
def experiment_card(experiment_id: str):
    paper_id = experiment_id.split("_")[0] if "_" in experiment_id else experiment_id
    ctx = get_experiment_context(experiment_id)
    dims = get_dimension_levels(paper_id=paper_id)
    # Pull a sample of measurements for the overview
    con = _tools_db()
    rows = con.execute("""
        SELECT m.measurement_id, m.metric, m.value_raw, m.computed_value, m.significance_letter,
               m.statistic, m.unit, o.source_id, o.provenance
        FROM measurements m
        JOIN observations o ON m.observation_id = o.observation_id
        WHERE o.experiment_id = ?
        ORDER BY o.source_id, m.metric
        LIMIT 20
    """, [experiment_id]).fetchall()
    con.close()
    measurements_sample = []
    for r in rows:
        measurements_sample.append({
            "measurement_id": r[0], "metric": r[1], "value_raw": r[2],
            "computed_value": r[3], "significance_letter": r[4],
            "statistic": r[5], "unit": r[6], "source_id": r[7],
            "provenance": r[8]
        })
    return {
        **ctx,
        "dimensions": dims.get("dimensions", []),
        "measurements_sample": measurements_sample,
    }


@app.get("/api/source/{experiment_id}/{source_id:path}")
def source_data(experiment_id: str, source_id: str):
    return get_source_observations(source_id, experiment_id)


@app.get("/api/measurements/{measurement_id}")
def measurement_card(measurement_id: str):
    con = _tools_db()
    row = con.execute("""
        SELECT m.measurement_id, m.metric, m.value_raw, m.computed_value,
               m.significance_letter, m.statistic, m.unit, m.construct,
               o.observation_id, o.experiment_id, o.source_id,
               o.factor_values_json, o.provenance,
               s.page_number, e.paper_id
        FROM measurements m
        JOIN observations o ON m.observation_id = o.observation_id
        JOIN evidence_sources s ON o.source_id = s.source_id AND o.experiment_id = s.experiment_id
        JOIN experiments e ON o.experiment_id = e.experiment_id
        WHERE m.measurement_id = ?
    """, [measurement_id]).fetchone()
    con.close()
    if not row:
        return {"error": f"Measurement {measurement_id} not found."}
    return {
        "measurement_id": row[0],
        "metric": row[1],
        "value_raw": row[2],
        "computed_value": row[3],
        "significance_letter": row[4],
        "statistic": row[5],
        "unit": row[6],
        "construct": row[7],
        "observation_id": row[8],
        "experiment_id": row[9],
        "source_id": row[10],
        "factor_values": json.loads(row[11]) if isinstance(row[11], str) else row[11],
        "provenance": row[12],
        "page_number": row[13],
        "paper_id": row[14]
    }


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Evidence Discovery System</h1><p>Frontend not built yet.</p>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
