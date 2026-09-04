from fastapi import APIRouter
from engine.tools.ingestion import get_db, init_schema, check_ingestion_state
import uuid

router = APIRouter()

@router.post("")
def create_session():
    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()
    return {
        "session_id": session_id,
        "status": "created",
        "message": "Upload payments.csv, settlements.csv, and bank_statement.csv to begin"
    }

@router.get("/{session_id}/state")
def get_session_state(session_id: str):
    try:
        state = check_ingestion_state(session_id)
        return {
            "session_id": session_id,
            "sources": state,
            "reconciliation_scope": state["reconciliation_scope"]
        }
    except Exception as e:
        return {"error": str(e), "session_id": session_id}
