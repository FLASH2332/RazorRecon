import os
import csv
from pathlib import Path
import duckdb

def get_db(session_id: str) -> duckdb.DuckDBPyConnection:
    """
    Opens (or creates) a DuckDB file at sessions/{session_id}.duckdb
    Creates the sessions/ directory if it doesn't exist.
    """
    sessions_dir = Path("sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    db_path = sessions_dir / f"{session_id}.duckdb"
    conn = duckdb.connect(str(db_path))
    return conn

def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Creates ingestion_state and reconciliation_log tables if they don't exist.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_state (
            session_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            record_count INTEGER,
            loaded_at TIMESTAMP,
            notes VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_log (
            decision_id VARCHAR PRIMARY KEY,
            session_id VARCHAR,
            timestamp TIMESTAMP,
            record_id VARCHAR,
            strategies VARCHAR,
            tool_calls VARCHAR,
            verdict VARCHAR,
            evidence VARCHAR,
            competing VARCHAR,
            reasoning VARCHAR,
            model VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_progress (
            session_id    VARCHAR,
            processed     INTEGER,
            total         INTEGER,
            current_id    VARCHAR,
            status        VARCHAR,   -- 'running' | 'completed' | 'failed'
            started_at    TIMESTAMP,
            updated_at    TIMESTAMP,
            PRIMARY KEY (session_id)
        )
    """)

def _record_ingestion_state(
    conn: duckdb.DuckDBPyConnection,
    session_id: str,
    source: str,
    status: str,
    record_count: int,
    notes: str = None
) -> None:
    """
    Helper to upsert/record state into ingestion_state table for a given session and source.
    """
    # Delete previous record for this session_id + source if exists
    conn.execute(
        "DELETE FROM ingestion_state WHERE session_id = ? AND source = ?",
        [session_id, source]
    )
    conn.execute(
        """
        INSERT INTO ingestion_state (session_id, source, status, record_count, loaded_at, notes)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        """,
        [session_id, source, status, record_count, notes]
    )

def _validate_and_remap_columns(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    required_cols: list,
    column_mappings: dict
) -> tuple[bool, str | None, list]:
    """
    Validates table columns against required_cols.
    If missing, attempts to rename existing columns matching column_mappings.
    Returns (success, notes_str, final_cols).
    """
    current_cols = [col[0] for col in conn.execute(f"DESCRIBE {table_name}").fetchall()]
    missing = [col for col in required_cols if col not in current_cols]

    if not missing:
        return True, None, current_cols

    remapped = []
    for orig_col, target_col in column_mappings.items():
        if target_col in missing and orig_col in current_cols:
            conn.execute(f'ALTER TABLE {table_name} RENAME COLUMN "{orig_col}" TO "{target_col}"')
            remapped.append(f"{orig_col} -> {target_col}")

    # Check remaining missing columns after remapping
    updated_cols = [col[0] for col in conn.execute(f"DESCRIBE {table_name}").fetchall()]
    still_missing = [col for col in required_cols if col not in updated_cols]

    if still_missing:
        return False, f"Missing required columns: {', '.join(still_missing)}", updated_cols

    notes = f"Schema remapped: {', '.join(remapped)}" if remapped else None
    return True, notes, updated_cols

def ingest_payments(session_id: str, filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {
            "source": "payments",
            "status": "parse_error",
            "record_count": 0,
            "captured_count": 0,
            "notes": f"File not found: {filepath}"
        }

    conn = get_db(session_id)
    init_schema(conn)
    try:
        # Load CSV using DuckDB
        conn.execute(f"CREATE OR REPLACE TABLE payments AS SELECT * FROM read_csv_auto('{filepath}')")
        
        required_cols = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
        alt_mappings = {
            "id": "order_id",
            "amt": "amount",
            "transaction_date": "date",
            "payment_type": "type"
        }

        success, notes, _ = _validate_and_remap_columns(conn, "payments", required_cols, alt_mappings)

        if not success:
            _record_ingestion_state(conn, session_id, "payments", "parse_error", 0, notes)
            return {
                "source": "payments",
                "status": "parse_error",
                "record_count": 0,
                "captured_count": 0,
                "notes": notes
            }

        status = "schema_remapped" if notes else "loaded"
        record_count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        captured_count = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'captured'").fetchone()[0]

        _record_ingestion_state(conn, session_id, "payments", status, record_count, notes)
        return {
            "source": "payments",
            "status": status,
            "record_count": record_count,
            "captured_count": captured_count,
            "notes": notes
        }
    except Exception as e:
        notes = f"Error reading file: {str(e)}"
        _record_ingestion_state(conn, session_id, "payments", "parse_error", 0, notes)
        return {
            "source": "payments",
            "status": "parse_error",
            "record_count": 0,
            "captured_count": 0,
            "notes": notes
        }
    finally:
        conn.close()

def ingest_settlements(session_id: str, filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {
            "source": "settlements",
            "status": "parse_error",
            "record_count": 0,
            "unique_batches": 0,
            "notes": f"File not found: {filepath}"
        }

    conn = get_db(session_id)
    init_schema(conn)
    try:
        conn.execute(f"CREATE OR REPLACE TABLE settlements AS SELECT * FROM read_csv_auto('{filepath}')")

        required_cols = [
            "settlement_id", "entity_id", "type", "order_id", "amount",
            "credit", "debit", "fee", "tax", "net", "settlement_utr", "date", "method"
        ]
        alt_mappings = {
            "id": "settlement_id",
            "amt": "amount",
            "transaction_date": "date",
            "utr": "settlement_utr"
        }

        success, notes, _ = _validate_and_remap_columns(conn, "settlements", required_cols, alt_mappings)

        if not success:
            _record_ingestion_state(conn, session_id, "settlements", "parse_error", 0, notes)
            return {
                "source": "settlements",
                "status": "parse_error",
                "record_count": 0,
                "unique_batches": 0,
                "notes": notes
            }

        status = "schema_remapped" if notes else "loaded"
        record_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        unique_batches = conn.execute("SELECT COUNT(DISTINCT settlement_id) FROM settlements").fetchone()[0]

        _record_ingestion_state(conn, session_id, "settlements", status, record_count, notes)
        return {
            "source": "settlements",
            "status": status,
            "record_count": record_count,
            "unique_batches": unique_batches,
            "notes": notes
        }
    except Exception as e:
        notes = f"Error reading file: {str(e)}"
        _record_ingestion_state(conn, session_id, "settlements", "parse_error", 0, notes)
        return {
            "source": "settlements",
            "status": "parse_error",
            "record_count": 0,
            "unique_batches": 0,
            "notes": notes
        }
    finally:
        conn.close()

def ingest_bank(session_id: str, filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {
            "source": "bank",
            "status": "parse_error",
            "record_count": 0,
            "credit_rows": 0,
            "bank_charge_rows": 0,
            "notes": f"File not found: {filepath}"
        }

    conn = get_db(session_id)
    init_schema(conn)
    try:
        conn.execute(f"CREATE OR REPLACE TABLE bank AS SELECT * FROM read_csv_auto('{filepath}')")

        required_cols = ["txn_id", "date", "narration", "utr", "credit", "debit", "balance", "classification"]
        alt_mappings = {
            "id": "txn_id",
            "transaction_id": "txn_id",
            "transaction_date": "date",
            "desc": "narration",
            "description": "narration"
        }

        success, notes, _ = _validate_and_remap_columns(conn, "bank", required_cols, alt_mappings)

        if not success:
            _record_ingestion_state(conn, session_id, "bank", "parse_error", 0, notes)
            return {
                "source": "bank",
                "status": "parse_error",
                "record_count": 0,
                "credit_rows": 0,
                "bank_charge_rows": 0,
                "notes": notes
            }

        status = "schema_remapped" if notes else "loaded"
        record_count = conn.execute("SELECT COUNT(*) FROM bank").fetchone()[0]
        credit_rows = conn.execute("SELECT COUNT(*) FROM bank WHERE credit > 0").fetchone()[0]
        bank_charge_rows = conn.execute("SELECT COUNT(*) FROM bank WHERE classification = 'bank_charge'").fetchone()[0]

        _record_ingestion_state(conn, session_id, "bank", status, record_count, notes)
        return {
            "source": "bank",
            "status": status,
            "record_count": record_count,
            "credit_rows": credit_rows,
            "bank_charge_rows": bank_charge_rows,
            "notes": notes
        }
    except Exception as e:
        notes = f"Error reading file: {str(e)}"
        _record_ingestion_state(conn, session_id, "bank", "parse_error", 0, notes)
        return {
            "source": "bank",
            "status": "parse_error",
            "record_count": 0,
            "credit_rows": 0,
            "bank_charge_rows": 0,
            "notes": notes
        }
    finally:
        conn.close()

def check_ingestion_state(session_id: str) -> dict:
    conn = get_db(session_id)
    init_schema(conn)
    try:
        rows = conn.execute("SELECT source, status, record_count FROM ingestion_state WHERE session_id = ?", [session_id]).fetchall()
        state_map = {row[0]: {"status": row[1], "record_count": row[2]} for row in rows}

        payments_loaded = state_map.get("payments", {}).get("status") in ("loaded", "schema_remapped")
        settlements_loaded = state_map.get("settlements", {}).get("status") in ("loaded", "schema_remapped")
        bank_loaded = state_map.get("bank", {}).get("status") in ("loaded", "schema_remapped")

        if payments_loaded and settlements_loaded and bank_loaded:
            scope = "full"
        elif payments_loaded and settlements_loaded:
            scope = "payments_settlements"
        elif settlements_loaded and bank_loaded:
            scope = "settlements_bank"
        elif payments_loaded and bank_loaded:
            scope = "payments_bank"
        elif payments_loaded:
            scope = "payments_only"
        elif settlements_loaded:
            scope = "settlements_only"
        elif bank_loaded:
            scope = "bank_only"
        else:
            scope = "none"

        return {
            "payments": state_map.get("payments", {"status": "missing"}),
            "settlements": state_map.get("settlements", {"status": "missing"}),
            "bank": state_map.get("bank", {"status": "missing"}),
            "reconciliation_scope": scope
        }
    finally:
        conn.close()

if __name__ == "__main__":
    import uuid
    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()
    
    # test with small dataset
    base = "data/sample/small"
    print(ingest_payments(session_id, f"{base}/payments.csv"))
    print(ingest_settlements(session_id, f"{base}/settlements.csv"))
    print(ingest_bank(session_id, f"{base}/bank_statement.csv"))
    print(check_ingestion_state(session_id))
