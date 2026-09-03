"""
Compiles the reconciliation log into a structured report with metrics.
No LLM calls — pure DuckDB queries over reconciliation_log.
"""

import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

from engine.tools.ingestion import get_db
from engine.tools.resolution import get_decisions, get_verdict_summary
from engine.tools.query import get_all_settlement_ids, get_unmatched_bank_credits


def generate_report(session_id: str) -> dict:
    """
    Compiles full reconciliation report for a given session.
    """
    # 1. Summary counts and metrics
    summary_data = get_verdict_summary(session_id)
    summary = {
        "confirmed": int(summary_data.get("confirmed", 0)),
        "ambiguous": int(summary_data.get("ambiguous", 0)),
        "unresolved": int(summary_data.get("unresolved", 0)),
        "total": int(summary_data.get("total", 0)),
        "match_rate": float(summary_data.get("match_rate", 0.0)),
        "coverage": float(summary_data.get("coverage", 0.0)),
        "false_match_rate": 0.0,  # updated by eval harness
    }

    # 2. Decisions breakdown
    decisions = get_decisions(session_id)
    confirmed_records = []
    ambiguous_records = []
    unresolved_records = []

    for d in decisions:
        verdict = d.get("verdict")
        record_id = str(d.get("record_id", ""))
        reasoning = str(d.get("reasoning", ""))
        timestamp = str(d.get("timestamp", ""))

        strategies_tried = d.get("strategies_tried") or d.get("strategies") or []
        if isinstance(strategies_tried, str):
            try:
                strategies_tried = json.loads(strategies_tried)
            except Exception:
                strategies_tried = [strategies_tried]

        evidence = d.get("evidence")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                pass
        if evidence is None:
            evidence = {}

        competing = d.get("competing") or d.get("competing_explanations")
        if isinstance(competing, str):
            try:
                competing = json.loads(competing)
            except Exception:
                pass
        if competing is None:
            competing = []

        if verdict == "confirmed":
            confirmed_records.append({
                "record_id": record_id,
                "verdict": "confirmed",
                "evidence": evidence,
                "reasoning": reasoning,
                "strategies_tried": strategies_tried,
                "timestamp": timestamp,
            })
        elif verdict == "ambiguous":
            ambiguous_records.append({
                "record_id": record_id,
                "verdict": "ambiguous",
                "competing": competing,
                "reasoning": reasoning,
                "strategies_tried": strategies_tried,
                "timestamp": timestamp,
            })
        else:
            unresolved_records.append({
                "record_id": record_id,
                "verdict": "unresolved",
                "strategies_tried": strategies_tried,
                "reasoning": reasoning,
                "timestamp": timestamp,
            })

    # 3. Bank charges excluded and scope notes
    conn = get_db(session_id)
    bank_charge_records = []
    total_charges_amount = 0.0
    scope_notes = []

    try:
        tables_res = conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
        table_names = [t[0] for t in tables_res]
        bank_table = "bank_txns" if "bank_txns" in table_names else ("bank" if "bank" in table_names else None)

        if bank_table:
            rows = conn.execute(f"""
                SELECT txn_id, CAST(date AS VARCHAR) as date, narration, debit
                FROM {bank_table}
                WHERE classification = 'bank_charge'
            """).fetchall()

            for r in rows:
                debit_val = float(r[3] or 0.0)
                bank_charge_records.append({
                    "txn_id": str(r[0]),
                    "date": str(r[1]),
                    "narration": str(r[2]),
                    "debit": debit_val,
                })
                total_charges_amount += debit_val

        if "ingestion_state" in table_names:
            missing_rows = conn.execute("""
                SELECT source, status, notes
                FROM ingestion_state
                WHERE status != 'loaded'
            """).fetchall()

            for m in missing_rows:
                src, st, notes = m[0], m[1], m[2]
                note_str = f"Source '{src}' status: {st}"
                if notes:
                    note_str += f" ({notes})"
                scope_notes.append(note_str)
    finally:
        conn.close()

    total_charges_amount = round(total_charges_amount, 2)

    return {
        "session_id": session_id,
        "summary": summary,
        "confirmed_records": confirmed_records,
        "ambiguous_records": ambiguous_records,
        "unresolved_records": unresolved_records,
        "bank_charges_excluded": {
            "count": len(bank_charge_records),
            "total_amount": total_charges_amount,
            "records": bank_charge_records,
        },
        "scope_notes": scope_notes,
    }


def print_report(report: dict) -> None:
    """
    Pretty prints the reconciliation report to the console.
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    session_id = report.get("session_id", "")
    summary = report.get("summary", {})
    total = summary.get("total", 0)
    confirmed = summary.get("confirmed", 0)
    match_rate = summary.get("match_rate", 0.0)
    ambiguous = summary.get("ambiguous", 0)
    unresolved = summary.get("unresolved", 0)
    coverage = summary.get("coverage", 0.0)

    lines = [
        "================================",
        "RAZORRECON RECONCILIATION REPORT",
        "================================",
        f"Session: {session_id}",
        "",
        "SUMMARY",
        "-------",
        f"Total records processed : {total}",
        f"Confirmed matches       : {confirmed} ({match_rate:.1%})",
        f"Ambiguous               : {ambiguous}",
        f"Unresolved              : {unresolved}",
        f"Coverage                : {coverage:.1%}",
        "",
        f"CONFIRMED ({confirmed})",
        "-----------------------",
    ]

    for r in report.get("confirmed_records", []):
        reason_snip = (r.get("reasoning") or "")[:80]
        lines.append(f"  ✓ {r.get('record_id')} | {reason_snip}")

    lines.extend([
        "",
        f"AMBIGUOUS ({ambiguous})",
        "-----------------------",
    ])
    for r in report.get("ambiguous_records", []):
        reason_snip = (r.get("reasoning") or "")[:80]
        competing = r.get("competing", [])
        lines.append(f"  ⚠ {r.get('record_id')} | {reason_snip}")
        lines.append(f"    Competing: {competing}")

    lines.extend([
        "",
        f"UNRESOLVED ({unresolved})",
        "-------------------------",
    ])
    for r in report.get("unresolved_records", []):
        reason_snip = (r.get("reasoning") or "")[:80]
        lines.append(f"  ✗ {r.get('record_id')} | {reason_snip}")

    charges = report.get("bank_charges_excluded", {})
    count = charges.get("count", 0)
    total_amount = charges.get("total_amount", 0.0)

    lines.extend([
        "",
        "BANK CHARGES EXCLUDED",
        "---------------------",
        f"Count : {count}",
        f"Total : ₹{total_amount:.2f}",
        "",
        "SCOPE NOTES",
        "-----------",
    ])

    scope_notes = report.get("scope_notes", [])
    if scope_notes:
        for sn in scope_notes:
            lines.append(f"  - {sn}")
    else:
        lines.append("None — all sources loaded")

    output_str = "\n".join(lines)
    try:
        print(output_str)
    except UnicodeEncodeError:
        # Fallback for terminals lacking UTF-8 support
        safe_str = (
            output_str.replace("✓", "[OK]")
            .replace("⚠", "[!]")
            .replace("✗", "[X]")
            .replace("₹", "Rs. ")
        )
        print(safe_str)


def save_report(report: dict, filepath: str) -> None:
    """
    Saves report as JSON to filepath.
    Creates parent directory if needed.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)


if __name__ == "__main__":
    import uuid
    from engine.tools.ingestion import get_db, init_schema
    from engine.tools.ingestion import ingest_payments, ingest_settlements, ingest_bank
    from engine.tools.resolution import mark_confirmed, mark_ambiguous, mark_unresolved

    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()

    base = "data/sample/small"
    ingest_payments(session_id, f"{base}/payments.csv")
    ingest_settlements(session_id, f"{base}/settlements.csv")
    ingest_bank(session_id, f"{base}/bank_statement.csv")

    # Inject some fake decisions to test report
    mark_confirmed(
        session_id=session_id,
        record_id="SETL_001",
        evidence={"bank_txn_id": "TXN_001", "expected_amount": 11558.51, "actual_amount": 11558.51, "match_count": 1},
        strategies_tried=["amount_date_match"],
        tool_calls=[],
        reasoning="Single bank credit matched within tolerance",
    )
    mark_ambiguous(
        session_id=session_id,
        record_id="TXN_COMBINED",
        competing=[{"settlements": ["SETL_010", "SETL_011"]}, {"settlements": ["SETL_012"]}],
        strategies_tried=["amount_date_match", "combination_match"],
        tool_calls=[],
        reasoning="Two valid combinations found, cannot determine correct match",
    )
    mark_unresolved(
        session_id=session_id,
        record_id="TXN_ORPHAN",
        strategies_tried=["orphan_check"],
        tool_calls=[],
        reasoning="Bank credit with no corresponding settlement found",
    )

    report = generate_report(session_id)
    print_report(report)
    save_report(report, f"sessions/{session_id}_report.json")
    print(f"\nReport saved to sessions/{session_id}_report.json")
