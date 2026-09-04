from fastapi import APIRouter, HTTPException
from engine.report import generate_report
from engine.tools.ingestion import get_db, init_schema

router = APIRouter()


def _ensure_reconciliation_completed(session_id: str):
    conn = get_db(session_id)
    try:
        init_schema(conn)
        row = conn.execute(
            "SELECT status FROM reconciliation_progress WHERE session_id = ?",
            [session_id]
        ).fetchone()
    finally:
        conn.close()

    if not row or row[0] not in ("completed", "partial"):
        raise HTTPException(
            status_code=425,
            detail="Reconciliation not complete yet. Check /status first."
        )


@router.get("/{session_id}/report")
def get_report(session_id: str):
    _ensure_reconciliation_completed(session_id)
    return generate_report(session_id)


@router.get("/{session_id}/report/summary")
def get_report_summary(session_id: str):
    _ensure_reconciliation_completed(session_id)
    report = generate_report(session_id)
    summary = report.get("summary", {})
    return {
        "session_id": session_id,
        "confirmed": summary.get("confirmed", 0),
        "ambiguous": summary.get("ambiguous", 0),
        "unresolved": summary.get("unresolved", 0),
        "total": summary.get("total", 0),
        "match_rate": summary.get("match_rate", 0.0),
        "coverage": summary.get("coverage", 0.0),
    }
