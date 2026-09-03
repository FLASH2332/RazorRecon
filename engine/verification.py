"""
The verification layer. Deterministic rules that decide verdicts
based on evidence gathered by the agent's tools.
The LLM never decides verdicts — this module does.

Dependencies: none (pure Python logic)
"""

from typing import Any, Dict, List, Optional


def verify_match(
    expected_amount: float,
    actual_amount: float,
    tolerance: float,
    match_count: int,
    competing_count: int = 0,
) -> Dict[str, Any]:
    """
    Applies deterministic verdict rules for matching a transaction or settlement
    against candidate records.

    Verdict Rules:
      CONFIRMED if:
        match_count == 1
        AND abs(expected_amount - actual_amount) <= tolerance
        AND competing_count == 0

      AMBIGUOUS if:
        match_count > 1
        OR competing_count > 0 (or competing_count > 1)

      UNRESOLVED if:
        match_count == 0
        OR (match_count == 1 and abs(expected_amount - actual_amount) > tolerance)

    Returns:
      {
        "verdict": "confirmed" | "ambiguous" | "unresolved",
        "reason": str,
        "amount_delta": float,
        "within_tolerance": bool
      }
    """
    amount_delta = round(abs(expected_amount - actual_amount), 4)
    within_tolerance = amount_delta <= tolerance

    if match_count == 0:
        verdict = "unresolved"
        reason = "No candidate match found."
    elif match_count > 1 or competing_count > 0:
        verdict = "ambiguous"
        if match_count > 1 and competing_count > 0:
            reason = (
                f"Ambiguous: {match_count} candidate matches and "
                f"{competing_count} competing explanation(s) found."
            )
        elif match_count > 1:
            reason = f"Ambiguous: multiple ({match_count}) candidate matches found."
        else:
            reason = (
                f"Ambiguous: single candidate found but {competing_count} "
                "competing explanation(s) exist."
            )
    elif match_count == 1 and within_tolerance and competing_count == 0:
        verdict = "confirmed"
        reason = (
            f"Confirmed single match within tolerance (delta: {amount_delta:.2f} "
            f"<= {tolerance:.2f})."
        )
    else:
        # Single match found but delta exceeds tolerance
        verdict = "unresolved"
        reason = (
            f"Candidate match amount delta ({amount_delta:.2f}) "
            f"exceeds tolerance ({tolerance:.2f})."
        )

    return {
        "verdict": verdict,
        "reason": reason,
        "amount_delta": amount_delta,
        "within_tolerance": within_tolerance,
    }


def verify_combination(
    combination_count: int,
    combinations: List[List[str]],
) -> Dict[str, Any]:
    """
    Evaluates combinations of records (e.g. settlements batched by bank).

    Verdict Rules:
      CONFIRMED if combination_count == 1
      AMBIGUOUS if combination_count > 1
      UNRESOLVED if combination_count == 0

    Returns:
      {
        "verdict": "confirmed" | "ambiguous" | "unresolved",
        "reason": str,
        "combination_count": int,
        "selected_combination": list[str] | None
      }
    """
    if combination_count == 1:
        verdict = "confirmed"
        reason = "Unique combination found matching target amount."
        selected_combination = combinations[0] if combinations and len(combinations) > 0 else None
    elif combination_count > 1:
        verdict = "ambiguous"
        reason = f"Multiple valid combinations found ({combination_count}). Human review required."
        selected_combination = None
    else:
        verdict = "unresolved"
        reason = "No combination of records matches target amount."
        selected_combination = None

    return {
        "verdict": verdict,
        "reason": reason,
        "combination_count": combination_count,
        "selected_combination": selected_combination,
    }


def verify_evidence_sufficient(
    strategies_tried: List[str],
    all_strategies: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Checks if all available reconciliation strategies were attempted.

    Returns:
      {
        "all_strategies_exhausted": bool,
        "strategies_tried": list[str],
        "strategies_remaining": list[str]
      }
    """
    if all_strategies is None:
        all_strategies = ["amount_date", "combinations"]

    strategies_tried_list = list(strategies_tried) if strategies_tried is not None else []
    strategies_remaining = [s for s in all_strategies if s not in strategies_tried_list]
    all_exhausted = len(strategies_remaining) == 0

    return {
        "all_strategies_exhausted": all_exhausted,
        "strategies_tried": strategies_tried_list,
        "strategies_remaining": strategies_remaining,
    }


if __name__ == "__main__":
    print("=== verify_match tests ===")

    # Should be CONFIRMED
    r1 = verify_match(expected_amount=11558.51, actual_amount=11558.51,
                      tolerance=11.56, match_count=1, competing_count=0)
    print(f"Clean match: {r1['verdict']} — {r1['reason']}")
    assert r1['verdict'] == 'confirmed'

    # Should be CONFIRMED (within tolerance)
    r2 = verify_match(expected_amount=11558.51, actual_amount=11560.00,
                      tolerance=11.56, match_count=1, competing_count=0)
    print(f"Within tolerance: {r2['verdict']} — {r2['reason']}")
    assert r2['verdict'] == 'confirmed'

    # Should be AMBIGUOUS (multiple matches)
    r3 = verify_match(expected_amount=24250.0, actual_amount=24250.0,
                      tolerance=10.0, match_count=2, competing_count=0)
    print(f"Multiple matches: {r3['verdict']} — {r3['reason']}")
    assert r3['verdict'] == 'ambiguous'

    # Should be UNRESOLVED (no match)
    r4 = verify_match(expected_amount=3200.0, actual_amount=0.0,
                      tolerance=10.0, match_count=0, competing_count=0)
    print(f"No match: {r4['verdict']} — {r4['reason']}")
    assert r4['verdict'] == 'unresolved'

    # Should be AMBIGUOUS (outside tolerance)
    r5 = verify_match(expected_amount=11558.51, actual_amount=11900.00,
                      tolerance=11.56, match_count=1, competing_count=0)
    print(f"Outside tolerance: {r5['verdict']} — {r5['reason']}")
    assert r5['verdict'] == 'unresolved'

    print()
    print("=== verify_combination tests ===")

    c1 = verify_combination(1, [["SETL_001", "SETL_002"]])
    print(f"Single combo: {c1['verdict']} — selected: {c1['selected_combination']}")
    assert c1['verdict'] == 'confirmed'

    c2 = verify_combination(2, [["SETL_001", "SETL_002"], ["SETL_003", "SETL_004"]])
    print(f"Two combos: {c2['verdict']} — selected: {c2['selected_combination']}")
    assert c2['verdict'] == 'ambiguous'

    c3 = verify_combination(0, [])
    print(f"No combo: {c3['verdict']}")
    assert c3['verdict'] == 'unresolved'

    print()
    print("All verification tests passed ")
