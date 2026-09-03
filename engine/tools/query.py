import duckdb
import json
from engine.tools.ingestion import get_db

def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """Helper to check if a table exists in the DuckDB database."""
    res = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name]
    ).fetchone()
    return res[0] > 0

def _get_bank_table_name(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Helper to find the bank table name (bank_txns or bank)."""
    if _table_exists(conn, "bank_txns"):
        return "bank_txns"
    if _table_exists(conn, "bank"):
        return "bank"
    return None

def _fetch_all_as_dicts(conn: duckdb.DuckDBPyConnection, sql: str, params: list = None) -> list[dict]:
    """Helper to execute SQL and return results as a list of dicts."""
    res = conn.execute(sql, params or [])
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
            else:
                row_dict[col] = val
        results.append(row_dict)
    return results

def query_payments(
    session_id: str,
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    status: str = None,
    method: str = None,
    order_id: str = None,
    type: str = None
) -> list[dict]:
    conn = get_db(session_id)
    try:
        if not _table_exists(conn, "payments"):
            return []

        conditions = []
        params = []

        if date is not None:
            conditions.append("CAST(date AS VARCHAR) = ?")
            params.append(str(date))
        if date_from is not None:
            conditions.append("CAST(date AS VARCHAR) >= ?")
            params.append(str(date_from))
        if date_to is not None:
            conditions.append("CAST(date AS VARCHAR) <= ?")
            params.append(str(date_to))
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if method is not None:
            conditions.append("method = ?")
            params.append(method)
        if order_id is not None:
            conditions.append("order_id = ?")
            params.append(order_id)
        if type is not None:
            conditions.append("type = ?")
            params.append(type)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM payments{where_clause}"
        return _fetch_all_as_dicts(conn, sql, params)
    finally:
        conn.close()

def query_settlements(
    session_id: str,
    settlement_id: str = None,
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    type: str = None,
    order_id: str = None,
    settlement_utr: str = None
) -> list[dict]:
    conn = get_db(session_id)
    try:
        if not _table_exists(conn, "settlements"):
            return []

        conditions = []
        params = []

        if settlement_id is not None:
            conditions.append("settlement_id = ?")
            params.append(settlement_id)
        if date is not None:
            conditions.append("CAST(date AS VARCHAR) = ?")
            params.append(str(date))
        if date_from is not None:
            conditions.append("CAST(date AS VARCHAR) >= ?")
            params.append(str(date_from))
        if date_to is not None:
            conditions.append("CAST(date AS VARCHAR) <= ?")
            params.append(str(date_to))
        if type is not None:
            conditions.append("type = ?")
            params.append(type)
        if order_id is not None:
            conditions.append("order_id = ?")
            params.append(order_id)
        if settlement_utr is not None:
            conditions.append("settlement_utr = ?")
            params.append(settlement_utr)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM settlements{where_clause}"
        return _fetch_all_as_dicts(conn, sql, params)
    finally:
        conn.close()

def query_bank(
    session_id: str,
    txn_id: str = None,
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    classification: str = None,
    utr: str = None,
    credit_min: float = None,
    credit_max: float = None
) -> list[dict]:
    conn = get_db(session_id)
    try:
        table_name = _get_bank_table_name(conn)
        if not table_name:
            return []

        conditions = []
        params = []

        if txn_id is not None:
            conditions.append("txn_id = ?")
            params.append(txn_id)
        if date is not None:
            conditions.append("CAST(date AS VARCHAR) = ?")
            params.append(str(date))
        if date_from is not None:
            conditions.append("CAST(date AS VARCHAR) >= ?")
            params.append(str(date_from))
        if date_to is not None:
            conditions.append("CAST(date AS VARCHAR) <= ?")
            params.append(str(date_to))
        if classification is not None:
            conditions.append("classification = ?")
            params.append(classification)
        if utr is not None:
            conditions.append("utr = ?")
            params.append(utr)
        if credit_min is not None:
            conditions.append("credit >= ?")
            params.append(float(credit_min))
        if credit_max is not None:
            conditions.append("credit <= ?")
            params.append(float(credit_max))

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM {table_name}{where_clause}"
        return _fetch_all_as_dicts(conn, sql, params)
    finally:
        conn.close()

def get_refunds(
    session_id: str,
    parent_order_id: str
) -> list[dict]:
    conn = get_db(session_id)
    try:
        if not _table_exists(conn, "payments"):
            return []

        sql = "SELECT * FROM payments WHERE type = 'refund' AND parent_order_id = ?"
        return _fetch_all_as_dicts(conn, sql, [parent_order_id])
    finally:
        conn.close()

def get_settlement_summary(
    session_id: str,
    settlement_id: str
) -> dict | None:
    conn = get_db(session_id)
    try:
        if not _table_exists(conn, "settlements"):
            return None

        # Check if settlement_id exists
        count_res = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE settlement_id = ?",
            [settlement_id]
        ).fetchone()
        if not count_res or count_res[0] == 0:
            return None

        sql = """
            SELECT 
                FIRST(settlement_id) as settlement_id,
                FIRST(settlement_utr) as settlement_utr,
                CAST(FIRST(date) AS VARCHAR) as date,
                COALESCE(SUM(CASE WHEN type = 'payment' THEN amount ELSE 0 END), 0.0) as total_gross,
                COALESCE(SUM(fee), 0.0) as total_fee,
                COALESCE(SUM(net), 0.0) as total_net,
                COALESCE(SUM(CASE WHEN type = 'refund' THEN ABS(debit) ELSE 0 END), 0.0) as total_refunds,
                COUNT(CASE WHEN type = 'payment' THEN 1 END) as payment_count,
                COUNT(CASE WHEN type = 'refund' THEN 1 END) as refund_count
            FROM settlements
            WHERE settlement_id = ?
            GROUP BY settlement_id
        """
        row = _fetch_all_as_dicts(conn, sql, [settlement_id])[0]

        order_ids_res = conn.execute(
            "SELECT DISTINCT order_id FROM settlements WHERE settlement_id = ? AND order_id IS NOT NULL",
            [settlement_id]
        ).fetchall()
        order_ids = [str(r[0]) for r in order_ids_res]

        return {
            "settlement_id": str(row["settlement_id"]),
            "settlement_utr": str(row["settlement_utr"]) if row["settlement_utr"] is not None else None,
            "date": str(row["date"]),
            "total_gross": float(row["total_gross"]),
            "total_fee": float(row["total_fee"]),
            "total_net": float(row["total_net"]),
            "total_refunds": float(row["total_refunds"]),
            "payment_count": int(row["payment_count"]),
            "refund_count": int(row["refund_count"]),
            "order_ids": order_ids
        }
    finally:
        conn.close()

def get_all_settlement_ids(session_id: str) -> list[str]:
    conn = get_db(session_id)
    try:
        if not _table_exists(conn, "settlements"):
            return []

        sql = """
            SELECT settlement_id 
            FROM settlements 
            GROUP BY settlement_id 
            ORDER BY MIN(date) ASC
        """
        rows = conn.execute(sql).fetchall()
        return [str(r[0]) for r in rows if r[0] is not None]
    finally:
        conn.close()

def get_unmatched_bank_credits(session_id: str) -> list[dict]:
    conn = get_db(session_id)
    try:
        table_name = _get_bank_table_name(conn)
        if not table_name:
            return []

        sql = f"SELECT * FROM {table_name} WHERE classification = 'razorpay_credit'"
        return _fetch_all_as_dicts(conn, sql)
    finally:
        conn.close()

if __name__ == "__main__":
    import uuid
    from engine.tools.ingestion import get_db, init_schema, ingest_payments
    from engine.tools.ingestion import ingest_settlements, ingest_bank

    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()

    base = "data/sample/small"
    ingest_payments(session_id, f"{base}/payments.csv")
    ingest_settlements(session_id, f"{base}/settlements.csv")
    ingest_bank(session_id, f"{base}/bank_statement.csv")

    # Test each query function
    print("=== query_payments (captured only) ===")
    results = query_payments(session_id, status="captured")
    print(f"Count: {len(results)}, first: {results[0]}")

    print("=== query_payments (refunds) ===")
    print(query_payments(session_id, type="refund"))

    print("=== get_refunds (ORD_001) ===")
    print(get_refunds(session_id, "ORD_001"))

    print("=== get_all_settlement_ids ===")
    ids = get_all_settlement_ids(session_id)
    print(f"Total: {len(ids)}, first 3: {ids[:3]}")

    print("=== get_settlement_summary (first settlement) ===")
    print(get_settlement_summary(session_id, ids[0]))

    print("=== get_unmatched_bank_credits ===")
    credits = get_unmatched_bank_credits(session_id)
    print(f"Total razorpay credits: {len(credits)}, first: {credits[0]}")

    print("=== query_bank (bank_charge only) ===")
    charges = query_bank(session_id, classification="bank_charge")
    print(f"Bank charges: {len(charges)}")
