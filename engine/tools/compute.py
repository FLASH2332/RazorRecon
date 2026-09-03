import datetime
import itertools

from engine.tools.ingestion import get_db
from engine.tools.query import (
    get_settlement_summary,
    query_bank,
    get_all_settlement_ids,
    get_unmatched_bank_credits,
)


def add_working_days(date_str: str, days: int) -> str:
    """
    Adds N working days to a date (skips weekends - Saturday, Sunday).
    Returns ISO format date string (YYYY-MM-DD).
    """
    dt = datetime.date.fromisoformat(str(date_str)[:10])
    added = 0
    step = 1 if days >= 0 else -1
    abs_days = abs(days)

    current = dt
    while added < abs_days:
        current += datetime.timedelta(days=step)
        if current.weekday() < 5:
            added += 1

    return current.isoformat()


def calc_expected_settlement(
    session_id: str,
    settlement_id: str
) -> dict | None:
    """
    Gets settlement summary for this settlement_id
    Computes expected bank credit:
        MDR = total_gross * 0.02
        GST_on_MDR = MDR * 0.18
        expected = total_gross - total_refunds - MDR - GST_on_MDR
        expected = round(expected, 2)
        tolerance = round(max(10.0, expected * 0.001), 2)
    Returns summary dict or None if settlement not found.
    """
    summary = get_settlement_summary(session_id, settlement_id)
    if summary is None:
        return None

    total_gross = float(summary.get("total_gross", 0.0))
    total_refunds = float(summary.get("total_refunds", 0.0))
    actual_net = float(summary.get("total_net", 0.0))

    mdr = total_gross * 0.02
    gst_on_mdr = mdr * 0.18
    expected = total_gross - total_refunds - mdr - gst_on_mdr
    expected = round(expected, 2)
    tolerance = round(max(10.0, expected * 0.001), 2)

    return {
        "settlement_id": str(settlement_id),
        "total_gross": round(total_gross, 2),
        "total_refunds": round(total_refunds, 2),
        "MDR": round(mdr, 2),
        "GST_on_MDR": round(gst_on_mdr, 2),
        "expected_bank_credit": expected,
        "tolerance": tolerance,
        "actual_net": round(actual_net, 2)
    }


def find_bank_match(
    session_id: str,
    settlement_id: str,
    date_window_days: int = 5
) -> dict:
    """
    Gets expected settlement from calc_expected_settlement()
    Gets settlement date from get_settlement_summary()
    Queries bank for razorpay_credit rows within date window:
        date_from = settlement_date
        date_to = settlement_date + date_window_days working days
        credit_min = expected - tolerance
        credit_max = expected + tolerance
    Returns match details dict.
    """
    expected_info = calc_expected_settlement(session_id, settlement_id)
    summary = get_settlement_summary(session_id, settlement_id)

    if expected_info is None or summary is None:
        return {
            "settlement_id": str(settlement_id),
            "expected_amount": 0.0,
            "tolerance": 0.0,
            "matches": [],
            "match_count": 0
        }

    expected_amount = expected_info["expected_bank_credit"]
    tolerance = expected_info["tolerance"]
    settlement_date = str(summary["date"])

    date_from = settlement_date[:10]
    date_to = add_working_days(date_from, date_window_days)
    credit_min = round(expected_amount - tolerance, 2)
    credit_max = round(expected_amount + tolerance, 2)

    matches = query_bank(
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
        classification="razorpay_credit",
        credit_min=credit_min,
        credit_max=credit_max
    )

    return {
        "settlement_id": str(settlement_id),
        "expected_amount": expected_amount,
        "tolerance": tolerance,
        "matches": matches,
        "match_count": len(matches)
    }


def find_settlement_combinations(
    session_id: str,
    target_amount: float,
    tolerance: float = 10.0,
    max_combo_size: int = 3,
    date_window_days: int = 5
) -> dict:
    """
    Gets all settlement_ids.
    For each, gets expected bank credit via calc_expected_settlement().
    Tries combinations of size 2 up to max_combo_size (default 3):
        combo_total = sum of expected_bank_credit for each in combo
        if abs(combo_total - target_amount) <= tolerance:
            add to valid_combinations
    Constrains by date: all settlements in combo must be within
    date_window_days of each other.
    """
    all_settlement_ids = get_all_settlement_ids(session_id)
    target_amount = float(target_amount)
    tolerance = float(tolerance)

    settlement_data = {}
    for sid in all_settlement_ids:
        exp_info = calc_expected_settlement(session_id, sid)
        summary = get_settlement_summary(session_id, sid)
        if exp_info is not None and summary is not None and summary.get("date"):
            settlement_data[sid] = {
                "expected": exp_info["expected_bank_credit"],
                "date": datetime.date.fromisoformat(str(summary["date"])[:10])
            }

    valid_combinations = []
    candidate_ids = list(settlement_data.keys())

    for size in range(2, max_combo_size + 1):
        for combo in itertools.combinations(candidate_ids, size):
            dates = [settlement_data[sid]["date"] for sid in combo]
            if (max(dates) - min(dates)).days > date_window_days:
                continue

            combo_total = sum(settlement_data[sid]["expected"] for sid in combo)
            if abs(combo_total - target_amount) <= tolerance:
                valid_combinations.append(list(combo))

    return {
        "target_amount": target_amount,
        "valid_combinations": valid_combinations,
        "combination_count": len(valid_combinations)
    }


if __name__ == "__main__":
    import uuid
    from engine.tools.ingestion import get_db, init_schema
    from engine.tools.ingestion import ingest_payments, ingest_settlements, ingest_bank

    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()

    base = "data/sample/small"
    ingest_payments(session_id, f"{base}/payments.csv")
    ingest_settlements(session_id, f"{base}/settlements.csv")
    ingest_bank(session_id, f"{base}/bank_statement.csv")

    from engine.tools.query import get_all_settlement_ids
    ids = get_all_settlement_ids(session_id)

    print("=== calc_expected_settlement ===")
    result = calc_expected_settlement(session_id, ids[0])
    print(result)

    print("=== find_bank_match ===")
    match = find_bank_match(session_id, ids[0])
    print(match)

    print("=== find_settlement_combinations ===")
    # use first bank credit amount as target
    from engine.tools.query import get_unmatched_bank_credits
    credits = get_unmatched_bank_credits(session_id)
    target = credits[0]['credit']
    combos = find_settlement_combinations(session_id, target)
    print(combos)
