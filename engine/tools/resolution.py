import datetime
import json
import os
import sys
import uuid
import duckdb

# Ensure engine/tools and project root are in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

try:
    from engine.tools.ingestion import get_db, init_schema
except ImportError:
    from ingestion import get_db, init_schema


def mark_confirmed(
    session_id: str,
    record_id: str,
    evidence: dict,
    strategies_tried: list[str],
    tool_calls: list[dict],
    reasoning: str,
    model: str = "gemini-flash"
) -> dict:
    """
    Inserts a row into reconciliation_log with verdict="confirmed".
    evidence dict contains the matched records and amounts used.
    """
    conn = get_db(session_id)
    try:
        init_schema(conn)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now()
        timestamp_str = now.isoformat()

        strategies_json = json.dumps(strategies_tried if strategies_tried is not None else [])
        tool_calls_json = json.dumps(tool_calls if tool_calls is not None else [])
        evidence_json = json.dumps(evidence if evidence is not None else {})

        conn.execute("""
            INSERT INTO reconciliation_log (
                decision_id,
                session_id,
                timestamp,
                record_id,
                strategies,
                tool_calls,
                verdict,
                evidence,
                competing,
                reasoning,
                model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            decision_id,
            session_id,
            now,
            str(record_id),
            strategies_json,
            tool_calls_json,
            "confirmed",
            evidence_json,
            None,
            str(reasoning or ""),
            str(model)
        ])

        return {
            "decision_id": decision_id,
            "record_id": str(record_id),
            "verdict": "confirmed",
            "evidence": evidence,
            "timestamp": timestamp_str
        }
    finally:
        conn.close()


def mark_ambiguous(
    session_id: str,
    record_id: str,
    competing: list[dict],
    strategies_tried: list[str],
    tool_calls: list[dict],
    reasoning: str,
    model: str = "gemini-flash"
) -> dict:
    """
    Inserts a row into reconciliation_log with verdict="ambiguous".
    competing is a list of all valid competing explanations found.
    """
    conn = get_db(session_id)
    try:
        init_schema(conn)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now()
        timestamp_str = now.isoformat()

        strategies_json = json.dumps(strategies_tried if strategies_tried is not None else [])
        tool_calls_json = json.dumps(tool_calls if tool_calls is not None else [])
        competing_json = json.dumps(competing if competing is not None else [])

        conn.execute("""
            INSERT INTO reconciliation_log (
                decision_id,
                session_id,
                timestamp,
                record_id,
                strategies,
                tool_calls,
                verdict,
                evidence,
                competing,
                reasoning,
                model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            decision_id,
            session_id,
            now,
            str(record_id),
            strategies_json,
            tool_calls_json,
            "ambiguous",
            None,
            competing_json,
            str(reasoning or ""),
            str(model)
        ])

        return {
            "decision_id": decision_id,
            "record_id": str(record_id),
            "verdict": "ambiguous",
            "competing_explanations": competing,
            "timestamp": timestamp_str
        }
    finally:
        conn.close()


def mark_unresolved(
    session_id: str,
    record_id: str,
    strategies_tried: list[str],
    tool_calls: list[dict],
    reasoning: str,
    model: str = "gemini-flash"
) -> dict:
    """
    Inserts a row into reconciliation_log with verdict="unresolved".
    """
    conn = get_db(session_id)
    try:
        init_schema(conn)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now()
        timestamp_str = now.isoformat()

        strategies_json = json.dumps(strategies_tried if strategies_tried is not None else [])
        tool_calls_json = json.dumps(tool_calls if tool_calls is not None else [])

        conn.execute("""
            INSERT INTO reconciliation_log (
                decision_id,
                session_id,
                timestamp,
                record_id,
                strategies,
                tool_calls,
                verdict,
                evidence,
                competing,
                reasoning,
                model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            decision_id,
            session_id,
            now,
            str(record_id),
            strategies_json,
            tool_calls_json,
            "unresolved",
            None,
            None,
            str(reasoning or ""),
            str(model)
        ])

        return {
            "decision_id": decision_id,
            "record_id": str(record_id),
            "verdict": "unresolved",
            "strategies_tried": strategies_tried,
            "timestamp": timestamp_str
        }
    finally:
        conn.close()


def get_decisions(session_id: str) -> list[dict]:
    """
    Returns all rows from reconciliation_log for this session.
    Each row as dict with all fields, ordered by timestamp ascending.
    """
    conn = get_db(session_id)
    try:
        init_schema(conn)
        res = conn.execute(
            "SELECT * FROM reconciliation_log WHERE session_id = ? ORDER BY timestamp ASC",
            [session_id]
        )
        if res.description is None:
            return []

        cols = [desc[0] for desc in res.description]
        rows = res.fetchall()

        results = []
        for row in rows:
            row_dict = {}
            for col, val in zip(cols, row):
                if hasattr(val, "isoformat"):
                    row_dict[col] = val.isoformat()
                elif col in ("strategies", "tool_calls", "evidence", "competing") and isinstance(val, str):
                    try:
                        row_dict[col] = json.loads(val)
                    except Exception:
                        row_dict[col] = val
                else:
                    row_dict[col] = val
            results.append(row_dict)

        return results
    finally:
        conn.close()


def get_verdict_summary(session_id: str) -> dict:
    """
    Queries reconciliation_log for this session and returns summary counts and rates:
        {
          "confirmed": int,
          "ambiguous": int,
          "unresolved": int,
          "total": int,
          "match_rate": float,
          "coverage": float
        }
    """
    conn = get_db(session_id)
    try:
        init_schema(conn)
        sql = """
            SELECT
                COALESCE(SUM(CASE WHEN verdict = 'confirmed' THEN 1 ELSE 0 END), 0) as confirmed,
                COALESCE(SUM(CASE WHEN verdict = 'ambiguous' THEN 1 ELSE 0 END), 0) as ambiguous,
                COALESCE(SUM(CASE WHEN verdict = 'unresolved' THEN 1 ELSE 0 END), 0) as unresolved,
                COUNT(*) as total
            FROM reconciliation_log
            WHERE session_id = ?
        """
        row = conn.execute(sql, [session_id]).fetchone()
        confirmed = int(row[0]) if row else 0
        ambiguous = int(row[1]) if row else 0
        unresolved = int(row[2]) if row else 0
        total = int(row[3]) if row else 0

        match_rate = round(confirmed / total, 4) if total > 0 else 0.0
        denom = confirmed + ambiguous + unresolved
        coverage = round(confirmed / denom, 4) if denom > 0 else 0.0

        return {
            "confirmed": confirmed,
            "ambiguous": ambiguous,
            "unresolved": unresolved,
            "total": total,
            "match_rate": match_rate,
            "coverage": coverage
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import uuid
    from ingestion import get_db, init_schema

    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()

    print("=== mark_confirmed ===")
    r1 = mark_confirmed(
        session_id=session_id,
        record_id="SETL_001",
        evidence={"bank_txn_id": "TXN_001", "settlement_amount": 10000.0, "bank_credit": 9764.0},
        strategies_tried=["strategy_1_exact_match"],
        tool_calls=[{"tool": "find_bank_match", "args": {"settlement_id": "SETL_001"}, "result": "matched"}],
        reasoning="Single bank credit matched within tolerance and date window."
    )
    print(r1)

    print("=== mark_ambiguous ===")
    r2 = mark_ambiguous(
        session_id=session_id,
        record_id="SETL_002",
        competing=[
            {"explanation": "Match with TXN_002", "bank_txn_id": "TXN_002"},
            {"explanation": "Match with TXN_003", "bank_txn_id": "TXN_003"}
        ],
        strategies_tried=["strategy_1_exact_match", "strategy_2_window_match"],
        tool_calls=[],
        reasoning="Multiple bank transactions match the expected settlement amount."
    )
    print(r2)

    print("=== mark_unresolved ===")
    r3 = mark_unresolved(
        session_id=session_id,
        record_id="SETL_003",
        strategies_tried=["strategy_1_exact_match", "strategy_2_window_match", "strategy_3_combinations"],
        tool_calls=[],
        reasoning="All strategies exhausted without finding a matching bank transaction."
    )
    print(r3)

    print("=== get_decisions ===")
    decisions = get_decisions(session_id)
    print(f"Decisions count: {len(decisions)}")
    print("First decision:", decisions[0])

    print("=== get_verdict_summary ===")
    summary = get_verdict_summary(session_id)
    print(summary)
