"""
Session-bound tool wrappers. Exposes all agent tools without
requiring session_id as a parameter. The LLM never sees session_id.
"""

from typing import Any, Dict, List, Optional

from engine.tools.compute import (
    calc_expected_settlement,
    find_bank_match,
    find_settlement_combinations,
)
from engine.tools.query import (
    get_settlement_summary,
    get_unmatched_bank_credits,
    get_refunds,
    get_all_settlement_ids,
    query_bank,
)
from engine.tools.classify import classify_narration, extract_utr, utrs_match
from engine.verification import verify_match, verify_combination
from engine.tools.resolution import mark_confirmed, mark_ambiguous, mark_unresolved

# Module-level variable
_session_id: Optional[str] = None


def init_registry(session_id: str) -> None:
    """
    Sets _session_id = session_id.
    Must be called before any tool is used.
    """
    global _session_id
    _session_id = session_id


def _check_session() -> None:
    if not _session_id:
        raise RuntimeError("Registry not initialized. Call init_registry(session_id) first.")


def tool_get_settlement_summary(settlement_id: str) -> dict:
    """Get gross, fee, net, refund totals and order list for a settlement batch."""
    _check_session()
    res = get_settlement_summary(_session_id, settlement_id)
    return res if res is not None else {}


def tool_find_bank_match(settlement_id: str, date_window_days: int = 5) -> dict:
    """Find bank credit rows matching this settlement by amount and date window.
    Returns match_count and list of matching bank rows."""
    _check_session()
    return find_bank_match(_session_id, settlement_id, date_window_days=date_window_days)


def tool_find_settlement_combinations(
    target_amount: float,
    tolerance: float = 10.0,
    max_combo_size: int = 3,
) -> dict:
    """Find combinations of settlements that sum to target_amount.
    Used when bank batched multiple settlements into one credit."""
    _check_session()
    return find_settlement_combinations(
        session_id=_session_id,
        target_amount=target_amount,
        tolerance=tolerance,
        max_combo_size=max_combo_size,
    )


def tool_get_refunds(parent_order_id: str) -> list:
    """Get all refund rows linked to a parent order_id."""
    _check_session()
    return get_refunds(_session_id, parent_order_id)


def tool_get_unmatched_bank_credits() -> list:
    """Get all razorpay_credit bank rows that need matching."""
    _check_session()
    return get_unmatched_bank_credits(_session_id)


def tool_calc_expected_settlement(settlement_id: str) -> dict:
    """Calculate expected bank credit for a settlement after MDR, GST, refunds."""
    _check_session()
    res = calc_expected_settlement(_session_id, settlement_id)
    return res if res is not None else {}


def tool_classify_narration(narration: str) -> str:
    """Classify a bank narration string into:
    razorpay_credit, bank_charge, upi_transfer, neft_transfer, unidentified."""
    _check_session()
    return classify_narration(narration)


def tool_submit_verdict(
    record_id: str,
    proposed_verdict: str,
    evidence: dict,
    competing: list,
    strategies_tried: list,
    reasoning: str,
) -> dict:
    """
    Submit your investigation verdict for verification.
    proposed_verdict must be one of: confirmed, ambiguous, unresolved.
    evidence must include: expected_amount, actual_amount, tolerance, match_count, bank_txn_id.
    competing is a list of competing explanations (empty if none).
    The system will validate your evidence before recording the verdict.
    You cannot bypass this verification step.
    """
    _check_session()
    if proposed_verdict == "confirmed":
        verification = verify_match(
            expected_amount=float(evidence.get("expected_amount", 0)),
            actual_amount=float(evidence.get("actual_amount", 0)),
            tolerance=float(evidence.get("tolerance", 10)),
            match_count=int(evidence.get("match_count", 0)),
            competing_count=len(competing),
        )
        final_verdict = verification["verdict"]
        reason = verification["reason"]

        if final_verdict == "confirmed":
            return mark_confirmed(
                session_id=_session_id,
                record_id=record_id,
                evidence=evidence,
                strategies_tried=strategies_tried,
                tool_calls=[],
                reasoning=f"{reasoning} | Verification: {reason}",
            )
        elif final_verdict == "ambiguous":
            return mark_ambiguous(
                session_id=_session_id,
                record_id=record_id,
                competing=competing or [{"reason": reason}],
                strategies_tried=strategies_tried,
                tool_calls=[],
                reasoning=f"LLM proposed confirmed but verification overrode to ambiguous: {reason}",
            )
        else:
            return mark_unresolved(
                session_id=_session_id,
                record_id=record_id,
                strategies_tried=strategies_tried,
                tool_calls=[],
                reasoning=f"LLM proposed confirmed but verification overrode to unresolved: {reason}",
            )

    elif proposed_verdict == "ambiguous":
        return mark_ambiguous(
            session_id=_session_id,
            record_id=record_id,
            competing=competing,
            strategies_tried=strategies_tried,
            tool_calls=[],
            reasoning=reasoning,
        )

    else:  # unresolved
        return mark_unresolved(
            session_id=_session_id,
            record_id=record_id,
            strategies_tried=strategies_tried,
            tool_calls=[],
            reasoning=reasoning,
        )


def tool_mark_unresolved(
    record_id: str,
    strategies_tried: list,
    reasoning: str,
) -> dict:
    """Mark a settlement as unresolved — all strategies exhausted, no match found."""
    _check_session()
    return mark_unresolved(
        session_id=_session_id,
        record_id=record_id,
        strategies_tried=strategies_tried,
        tool_calls=[],
        reasoning=reasoning,
    )


TOOL_REGISTRY = {
    "get_settlement_summary": tool_get_settlement_summary,
    "find_bank_match": tool_find_bank_match,
    "find_settlement_combinations": tool_find_settlement_combinations,
    "get_refunds": tool_get_refunds,
    "get_unmatched_bank_credits": tool_get_unmatched_bank_credits,
    "calc_expected_settlement": tool_calc_expected_settlement,
    "classify_narration": tool_classify_narration,
    "submit_verdict": tool_submit_verdict,
}


GROQ_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_settlement_summary",
            "description": "Get financial breakdown and order list for a settlement batch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "Settlement batch identifier.",
                    }
                },
                "required": ["settlement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_bank_match",
            "description": "Find bank credits matching settlement amount and date window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "Settlement ID to match.",
                    },
                    "date_window_days": {
                        "type": "integer",
                        "description": "Working days window to search (default 5).",
                    },
                },
                "required": ["settlement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_settlement_combinations",
            "description": "Find settlement combinations summing to a target bank credit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_amount": {
                        "type": "number",
                        "description": "Target bank credit amount.",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Allowed amount tolerance (default 10.0).",
                    },
                    "max_combo_size": {
                        "type": "integer",
                        "description": "Maximum settlements to combine (default 3).",
                    },
                },
                "required": ["target_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_refunds",
            "description": "Get refund records linked to an order ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_order_id": {
                        "type": "string",
                        "description": "Parent order ID.",
                    }
                },
                "required": ["parent_order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_unmatched_bank_credits",
            "description": "Get all unmatched Razorpay bank credit transactions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc_expected_settlement",
            "description": "Calculate expected bank credit after MDR fees and refunds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "Settlement ID to calculate payout for.",
                    }
                },
                "required": ["settlement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_narration",
            "description": "Classify bank narration into transaction type category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "narration": {
                        "type": "string",
                        "description": "Bank statement narration string.",
                    }
                },
                "required": ["narration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_verdict",
            "description": "Submit investigation verdict and evidence for deterministic verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "Settlement or bank transaction ID.",
                    },
                    "proposed_verdict": {
                        "type": "string",
                        "enum": ["confirmed", "ambiguous", "unresolved"],
                        "description": "Proposed verdict: confirmed, ambiguous, or unresolved.",
                    },
                    "evidence": {
                        "type": "object",
                        "description": "Evidence dictionary validating the proposed match.",
                        "properties": {
                            "expected_amount": {"type": "number"},
                            "actual_amount": {"type": "number"},
                            "tolerance": {"type": "number"},
                            "match_count": {"type": "integer"},
                            "bank_txn_id": {"type": "string"},
                            "bank_txn_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                    "competing": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of competing explanations if any.",
                    },
                    "strategies_tried": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attempted strategy names.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Explanation justifying the proposed verdict.",
                    },
                },
                "required": [
                    "record_id",
                    "proposed_verdict",
                    "evidence",
                    "competing",
                    "strategies_tried",
                    "reasoning",
                ],
            },
        },
    },
]

# Alias for backward compatibility
TOOL_SCHEMAS = GROQ_TOOL_SCHEMAS


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

    init_registry(session_id)

    print("=== tool_calc_expected_settlement ===")
    from engine.tools.query import get_all_settlement_ids

    ids = get_all_settlement_ids(session_id)
    print(tool_calc_expected_settlement(ids[0]))

    print("=== tool_find_bank_match ===")
    print(tool_find_bank_match(ids[0]))

    print("=== tool_get_unmatched_bank_credits ===")
    credits = tool_get_unmatched_bank_credits()
    print(f"Credits count: {len(credits)}")

    print("=== tool_submit_verdict (confirmed) ===")
    match = tool_find_bank_match(ids[0])
    expected = tool_calc_expected_settlement(ids[0])
    result = tool_submit_verdict(
        record_id=ids[0],
        proposed_verdict="confirmed",
        evidence={
            "expected_amount": expected["expected_bank_credit"],
            "actual_amount": match["matches"][0]["credit"] if match["match_count"] > 0 else 0,
            "tolerance": expected["tolerance"],
            "match_count": match["match_count"],
            "bank_txn_id": match["matches"][0]["txn_id"] if match["match_count"] > 0 else None,
        },
        competing=[],
        strategies_tried=["amount_date_match"],
        reasoning="Single bank credit found matching expected amount within tolerance",
    )
    print(result)
    print(f"Final verdict: {result.get('verdict')}")

    print("=== GROQ_TOOL_SCHEMAS count ===")
    print(f"Schemas defined: {len(GROQ_TOOL_SCHEMAS)}")
    for s in GROQ_TOOL_SCHEMAS:
        print(f"  {s['function']['name']}")

    try:
        print("Registry ready ")
    except UnicodeEncodeError:
        print("Registry ready [OK]")
