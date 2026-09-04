from fastapi import APIRouter, HTTPException
import threading
from datetime import datetime
from engine.agent import run_reconciliation
from engine.tools.ingestion import get_db, init_schema

router = APIRouter()


def _run_reconciliation_worker(session_id: str):
    try:
        run_reconciliation(session_id)
    except Exception as e:
        try:
            conn = get_db(session_id)
            init_schema(conn)
            row = conn.execute(
                "SELECT started_at, processed, total FROM reconciliation_progress WHERE session_id = ?",
                [session_id]
            ).fetchone()
            started_at = row[0] if row and row[0] else datetime.utcnow()
            processed = row[1] if row and row[1] is not None else 0
            total = row[2] if row and row[2] is not None else 0
            conn.execute("""
                INSERT OR REPLACE INTO reconciliation_progress
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [session_id, processed, total, str(e)[:100], 'failed', started_at, datetime.utcnow()])
            conn.close()
        except Exception:
            pass


@router.post("/{session_id}/reconcile")
def start_reconciliation(session_id: str):
    conn = get_db(session_id)
    try:
        init_schema(conn)
        row = conn.execute(
            "SELECT status FROM reconciliation_progress WHERE session_id = ?",
            [session_id]
        ).fetchone()
    finally:
        conn.close()

    if row and row[0] == "running":
        raise HTTPException(status_code=409, detail="Reconciliation already in progress")

    thread = threading.Thread(target=_run_reconciliation_worker, args=(session_id,), daemon=True)
    thread.start()

    return {
        "session_id": session_id,
        "status": "started",
        "message": "Reconciliation running. Poll /status for progress."
    }


@router.get("/{session_id}/status")
def get_reconciliation_status(session_id: str):
    conn = get_db(session_id)
    try:
        init_schema(conn)
        row = conn.execute(
            "SELECT processed, total, current_id, status, updated_at FROM reconciliation_progress WHERE session_id = ?",
            [session_id]
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"status": "not_started"}

    processed = row[0] or 0
    total = row[1] or 0
    current_id = row[2]
    status = row[3]
    updated_at = str(row[4]) if row[4] else None
    percent = round((processed / total) * 100, 1) if total > 0 else 0.0

    return {
        "session_id": session_id,
        "status": status,
        "processed": processed,
        "total": total,
        "current_settlement": current_id,
        "percent": percent,
        "updated_at": updated_at
    }
