"""
Session-bound tool wrappers. Exposes all agent tools without
requiring session_id as a parameter. The LLM never sees session_id.
"""

import os
import sys
from typing import Any, Dict, List, Optional

# Ensure project root and engine paths are available in sys.path
_tools_dir = os.path.dirname(os.path.abspath(__file__))
_engine_dir = os.path.dirname(_tools_dir)
_root_dir = os.path.dirname(_engine_dir)
for p in [_root_dir, _engine_dir, _tools_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
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
    from engine.tools.resolution import mark_confirmed, mark_ambiguous, mark_unresolved
except ImportError:
    from compute import (
        calc_expected_settlement,
        find_bank_match,
        find_settlement_combinations,
    )
    from query import (
        get_settlement_summary,
        get_unmatched_bank_credits,
        get_refunds,
        get_all_settlement_ids,
        query_bank,
    )
    from classify import classify_narration, extract_utr, utrs_match
    from resolution import mark_confirmed, mark_ambiguous, mark_unresolved

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


def tool_mark_confirmed(
    record_id: str,
    evidence: dict,
    strategies_tried: list,
    reasoning: str,
) -> dict:
    """Mark a settlement as confirmed with evidence. Writes to reconciliation log."""
    _check_session()
    return mark_confirmed(
        session_id=_session_id,
        record_id=record_id,
        evidence=evidence,
        strategies_tried=strategies_tried,
        tool_calls=[],
        reasoning=reasoning,
    )


def tool_mark_ambiguous(
    record_id: str,
    competing: list,
    strategies_tried: list,
    reasoning: str,
) -> dict:
    """Mark a settlement as ambiguous — multiple valid explanations exist.
    System abstains rather than guessing."""
    _check_session()
    return mark_ambiguous(
        session_id=_session_id,
        record_id=record_id,
        competing=competing,
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
    "mark_confirmed": tool_mark_confirmed,
    "mark_ambiguous": tool_mark_ambiguous,
    "mark_unresolved": tool_mark_unresolved,
}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_settlement_summary",
            "description": "Get gross, fee, net, refund totals and order list for a settlement batch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "The settlement ID to fetch summary for (e.g. setl_XXXXXXXX).",
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
            "description": "Find bank credit rows matching this settlement by amount and date window. Returns match_count and list of matching bank rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "The settlement ID to match against bank credits.",
                    },
                    "date_window_days": {
                        "type": "integer",
                        "description": "Number of working days after settlement date to search bank records (default 5).",
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
            "description": "Find combinations of settlements that sum to target_amount. Used when bank batched multiple settlements into one credit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_amount": {
                        "type": "number",
                        "description": "Target bank credit amount to find settlement combinations for.",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Allowed difference between combination sum and target amount (default 10.0).",
                    },
                    "max_combo_size": {
                        "type": "integer",
                        "description": "Maximum number of settlements to combine (default 3).",
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
            "description": "Get all refund rows linked to a parent order_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_order_id": {
                        "type": "string",
                        "description": "The parent order ID to find associated refund records for.",
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
            "description": "Get all razorpay_credit bank rows that need matching.",
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
            "description": "Calculate expected bank credit for a settlement after MDR, GST, refunds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "The settlement ID to compute expected payout for.",
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
            "description": "Classify a bank narration string into: razorpay_credit, bank_charge, upi_transfer, neft_transfer, unidentified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "narration": {
                        "type": "string",
                        "description": "Raw bank statement narration text to classify.",
                    }
                },
                "required": ["narration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_confirmed",
            "description": "Mark a settlement as confirmed with evidence. Writes to reconciliation log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "The settlement_id or bank txn_id being reconciled.",
                    },
                    "evidence": {
                        "type": "object",
                        "description": "Dictionary of matched records, amounts, and verification proof.",
                    },
                    "strategies_tried": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of strategy identifiers attempted.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Detailed explanation justifying why this match is confirmed.",
                    },
                },
                "required": ["record_id", "evidence", "strategies_tried", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_ambiguous",
            "description": "Mark a settlement as ambiguous — multiple valid explanations exist. System abstains rather than guessing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "The settlement_id or bank txn_id under review.",
                    },
                    "competing": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of competing valid explanation objects.",
                    },
                    "strategies_tried": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of strategy identifiers attempted.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Explanation of why ambiguity could not be resolved.",
                    },
                },
                "required": ["record_id", "competing", "strategies_tried", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_unresolved",
            "description": "Mark a settlement as unresolved — all strategies exhausted, no match found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "The settlement_id or bank txn_id that could not be reconciled.",
                    },
                    "strategies_tried": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of strategy identifiers attempted.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Explanation of the failure to find matching records.",
                    },
                },
                "required": ["record_id", "strategies_tried", "reasoning"],
            },
        },
    },
]


if __name__ == "__main__":
    import uuid
    import sys
    import os

    # Ensure project root is in sys.path
    _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

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

    print("=== TOOL_SCHEMAS count ===")
    print(f"Schemas defined: {len(TOOL_SCHEMAS)}")
    for s in TOOL_SCHEMAS:
        print(f"  {s['function']['name']}")

    try:
        print("Registry ready ")
    except UnicodeEncodeError:
        print("Registry ready [OK]")
