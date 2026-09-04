import json
import os
import sys

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from engine.tools.ingestion import get_db
from engine.tools.resolution import get_decisions, get_verdict_summary
from engine.report import generate_report


def load_ground_truth(dataset_path: str) -> dict:
    """
    Loads ground_truth.json from dataset_path.
    Returns the parsed dict.
    """
    if os.path.isfile(dataset_path):
        gt_path = dataset_path
    else:
        gt_path = os.path.join(dataset_path, "ground_truth.json")

    with open(gt_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_agent_decisions(session_id: str) -> dict:
    """
    Loads all decisions from reconciliation_log.
    Returns dict keyed by record_id:
    {
      "SETL_001": {"verdict": "confirmed", "evidence": {...}, ...},
      "SETL_002": {"verdict": "unresolved", ...},
      ...
    }
    """
    decisions = get_decisions(session_id)
    decisions_by_record = {}
    for d in decisions:
        rec_id = d.get("record_id")
        if rec_id:
            evidence = d.get("evidence")
            if isinstance(evidence, str):
                try:
                    d["evidence"] = json.loads(evidence)
                except Exception:
                    pass
            decisions_by_record[rec_id] = d
    return decisions_by_record


def _evaluate_scenario(
    scenario_info: dict,
    agent_decisions: dict,
    settlement_bank_map: dict,
    session_id: str,
    dataset_path: str
) -> dict:
    sc_type = scenario_info.get("scenario")
    expected_verdict = scenario_info.get("expected_verdict")
    actual_verdict = None
    correct = False

    if sc_type == "partial_refund":
        refund_id = scenario_info.get("refund_id")
        orig_order_id = scenario_info.get("order_id")
        target_sid = None

        # Look up in DuckDB settlements table first
        try:
            conn = get_db(session_id)
            row = conn.execute(
                "SELECT settlement_id FROM settlements WHERE entity_id = ? OR order_id = ? LIMIT 1",
                [refund_id, refund_id]
            ).fetchone()
            if row:
                target_sid = row[0]
            elif orig_order_id:
                row = conn.execute(
                    "SELECT settlement_id FROM settlements WHERE order_id = ? LIMIT 1",
                    [orig_order_id]
                ).fetchone()
                if row:
                    target_sid = row[0]
            conn.close()
        except Exception:
            pass

        # Fallback to CSV if not found in db
        if not target_sid:
            for fname in ("settlements_messy.csv", "settlements.csv"):
                fpath = os.path.join(dataset_path, fname)
                if os.path.exists(fpath):
                    import csv
                    with open(fpath, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for r in reader:
                            if r.get("entity_id") == refund_id or r.get("order_id") == refund_id:
                                target_sid = r.get("settlement_id")
                                break
                    if target_sid:
                        break

        if target_sid and target_sid in agent_decisions:
            actual_verdict = agent_decisions[target_sid].get("verdict")
        else:
            actual_verdict = "missing_decision"

        correct = (actual_verdict == expected_verdict)

    elif sc_type == "malformed_utr":
        txn_id = scenario_info.get("txn_id")
        target_sid = None

        # Look in settlement_bank_map (settlement_id -> txn_id)
        for sid, mapped_txn in settlement_bank_map.items():
            if mapped_txn == txn_id:
                target_sid = sid
                break

        # Fallback: check original_utr in settlements table
        if not target_sid and scenario_info.get("original_utr"):
            try:
                conn = get_db(session_id)
                row = conn.execute(
                    "SELECT settlement_id FROM settlements WHERE settlement_utr = ? LIMIT 1",
                    [scenario_info["original_utr"]]
                ).fetchone()
                if row:
                    target_sid = row[0]
                conn.close()
            except Exception:
                pass

        if target_sid and target_sid in agent_decisions:
            actual_verdict = agent_decisions[target_sid].get("verdict")
        else:
            actual_verdict = "missing_decision"

        correct = (actual_verdict == expected_verdict)

    elif sc_type == "bank_batching":
        sids = scenario_info.get("settlement_ids", [])
        verdicts = []
        for sid in sids:
            if sid in agent_decisions:
                verdicts.append(agent_decisions[sid].get("verdict"))
            else:
                verdicts.append("missing_decision")

        if verdicts and all(v == expected_verdict for v in verdicts):
            actual_verdict = expected_verdict
            correct = True
        else:
            actual_verdict = ", ".join(verdicts) if verdicts else "missing_decision"
            correct = False

    elif sc_type == "orphan_credit":
        orphan_id = scenario_info.get("txn_id", "TXN_ORPHAN")
        if orphan_id in agent_decisions:
            actual_verdict = agent_decisions[orphan_id].get("verdict")
        elif "TXN_ORPHAN" in agent_decisions:
            actual_verdict = agent_decisions["TXN_ORPHAN"].get("verdict")
        else:
            actual_verdict = "missing_decision"

        correct = (actual_verdict == expected_verdict)

    else:
        rec_id = (
            scenario_info.get("record_id")
            or scenario_info.get("settlement_id")
            or scenario_info.get("txn_id")
        )
        if rec_id and rec_id in agent_decisions:
            actual_verdict = agent_decisions[rec_id].get("verdict")
        else:
            actual_verdict = "missing_decision"
        correct = (actual_verdict == expected_verdict)

    return {
        "scenario": sc_type,
        "expected_verdict": expected_verdict,
        "actual_verdict": actual_verdict,
        "correct": correct
    }


def evaluate(session_id: str, dataset_path: str) -> dict:
    # Step 1: Load ground truth and agent decisions
    ground_truth = load_ground_truth(dataset_path)
    settlement_bank_map = ground_truth.get("settlement_bank_map", {})
    agent_decisions = load_agent_decisions(session_id)

    # Step 2: Calculate basic metrics from get_verdict_summary()
    summary_data = get_verdict_summary(session_id)

    # Step 3: Calculate false match rate
    false_matches = []
    total_confirmed = summary_data.get("confirmed", 0)

    for record_id, decision in agent_decisions.items():
        if decision.get("verdict") == "confirmed":
            evidence = decision.get("evidence") or {}
            bank_txn_id = evidence.get("bank_txn_id")
            if not bank_txn_id and evidence.get("bank_txn_ids"):
                txn_ids = evidence.get("bank_txn_ids")
                bank_txn_id = txn_ids[0] if isinstance(txn_ids, list) and txn_ids else txn_ids

            expected_txn = settlement_bank_map.get(record_id)
            if expected_txn is not None and bank_txn_id != expected_txn:
                false_matches.append({
                    "record_id": record_id,
                    "expected_txn": expected_txn,
                    "actual_txn": bank_txn_id
                })

    false_match_rate = (
        round(len(false_matches) / total_confirmed, 4)
        if total_confirmed > 0
        else 0.0
    )

    summary = {
        "total": summary_data.get("total", 0),
        "confirmed": summary_data.get("confirmed", 0),
        "ambiguous": summary_data.get("ambiguous", 0),
        "unresolved": summary_data.get("unresolved", 0),
        "match_rate": summary_data.get("match_rate", 0.0),
        "false_match_rate": false_match_rate,
        "coverage": summary_data.get("coverage", 0.0)
    }

    # Step 4: Evaluate scenario coverage
    scenario_results = []
    scenarios = ground_truth.get("scenarios", [])
    for sc in scenarios:
        res = _evaluate_scenario(sc, agent_decisions, settlement_bank_map, session_id, dataset_path)
        scenario_results.append(res)

    correct_count = sum(1 for s in scenario_results if s["correct"])
    scenario_accuracy = (
        round(correct_count / len(scenario_results), 4)
        if scenario_results
        else 1.0
    )

    # Step 5: Return full evaluation report
    return {
        "session_id": session_id,
        "dataset": dataset_path,
        "summary": summary,
        "false_matches": false_matches,
        "scenario_results": scenario_results,
        "scenario_accuracy": scenario_accuracy
    }


def print_eval_report(report: dict) -> None:
    dataset = report.get("dataset", "")
    session_id = report.get("session_id", "")
    summary = report.get("summary", {})
    scenario_results = report.get("scenario_results", [])
    scenario_accuracy = report.get("scenario_accuracy", 0.0)
    false_matches = report.get("false_matches", [])

    total = summary.get("total", 0)
    confirmed = summary.get("confirmed", 0)
    match_rate = summary.get("match_rate", 0.0)
    ambiguous = summary.get("ambiguous", 0)
    unresolved = summary.get("unresolved", 0)
    false_match_rate = summary.get("false_match_rate", 0.0)
    coverage = summary.get("coverage", 0.0)

    print("\n=====================================")
    print("RAZORRECON EVALUATION REPORT")
    print("=====================================")
    print(f"Dataset : {dataset}")
    print(f"Session : {session_id}")
    print()
    print("ACCURACY METRICS")
    print("────────────────")
    print(f"Total settlements     : {total}")
    print(f"Confirmed matches     : {confirmed} ({match_rate:.1%})")
    print(f"Ambiguous             : {ambiguous}")
    print(f"Unresolved            : {unresolved}")
    print(f"False match rate      : {false_match_rate:.1%}  ← headline safety metric")
    print(f"Coverage              : {coverage:.1%}")
    print()
    print(f"SCENARIO COVERAGE ({scenario_accuracy:.0%})")
    print("─────────────────")
    for sc in scenario_results:
        mark = "✓" if sc.get("correct") else "✗"
        sc_name = sc.get("scenario", "")
        actual = sc.get("actual_verdict", "none")
        expected = sc.get("expected_verdict", "")
        print(f"  {mark}  {sc_name} → agent: {actual} (expected: {expected})")
    if not scenario_results:
        print("  No scenarios found.")
    print()
    print("FALSE MATCHES")
    print("─────────────")
    if not false_matches:
        print("None detected")
    else:
        for fm in false_matches:
            print(f"  - Record: {fm.get('record_id')}, Expected: {fm.get('expected_txn')}, Actual: {fm.get('actual_txn')}")
    print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tests.eval.run_eval <session_id> <dataset_path>")
        print("Example: python -m tests.eval.run_eval abc12345 data/sample/medium")
        sys.exit(1)

    session_id = sys.argv[1]
    dataset_path = sys.argv[2]

    report = evaluate(session_id, dataset_path)
    print_eval_report(report)

    output_path = f"sessions/{session_id}_eval.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nEval report saved to {output_path}")
