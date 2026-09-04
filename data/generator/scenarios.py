import os
import csv
import json
import random
from datetime import datetime, timedelta

def inject_scenarios(
    payments: list[dict],
    settlements: list[dict],
    bank_statement: list[dict],
    settlement_bank_map: dict = None,
    seed: int = 42
) -> tuple[list, list, list, dict, dict]:
    """
    Inject realistic messiness into clean generated data to create reconciliation edge cases.
    
    Args:
        payments (list[dict]): Clean payments list.
        settlements (list[dict]): Clean settlements list.
        bank_statement (list[dict]): Clean bank statement list.
        settlement_bank_map (dict, optional): Map of settlement_id -> txn_id.
        seed (int): Random seed for reproducibility.
        
    Returns:
        tuple[list, list, list, dict, dict]:
            (modified payments, modified settlements, modified bank_statement, ground_truth, updated_map)
    """
    random.seed(seed)

    # Make deep-ish copies so we don't mutate input parameters unexpectedly
    payments_mod = [dict(p) for p in payments]
    settlements_mod = [dict(s) for s in settlements]
    bank_mod = [dict(b) for b in bank_statement]
    updated_map = dict(settlement_bank_map) if settlement_bank_map is not None else {}
    ground_truth = {"scenarios": []}

    # =========================================================================
    # SCENARIO 1 — Partial refund mid-cycle
    # =========================================================================
    # Pick a random captured payment with amount > 5000
    eligible_payments = [
        p for p in payments_mod 
        if p.get("status") == "captured" and float(p.get("amount", 0)) > 5000.0
    ]
    if eligible_payments:
        target_payment = random.choice(eligible_payments)
        orig_order_id = target_payment["order_id"]
        orig_amount = float(target_payment["amount"])
        orig_date_str = target_payment["date"]
        orig_method = target_payment["method"]

        refund_order_id = f"{orig_order_id}_REFUND"
        refund_amount = round(orig_amount * 0.3, 2)
        refund_date = (datetime.strptime(orig_date_str, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()

        # Add refund row to payments
        refund_payment_row = {
            "order_id": refund_order_id,
            "amount": refund_amount,
            "type": "refund",
            "parent_order_id": orig_order_id,
            "date": refund_date,
            "status": "captured",
            "method": orig_method
        }
        payments_mod.append(refund_payment_row)

        # Find the settlement row for this original payment
        orig_settlement_row = None
        for s in settlements_mod:
            if s.get("order_id") == orig_order_id and s.get("type") == "payment":
                orig_settlement_row = s
                break

        if orig_settlement_row:
            target_sid = orig_settlement_row["settlement_id"]
            target_utr = orig_settlement_row["settlement_utr"]
            target_settlement_date = orig_settlement_row["date"]

            # Update original settlement row credit and net by refund amount & fee proportionally
            # Note: Fee for refund is set to 0.0 in the instructions for the refund row, 
            # and original credit = original_credit - refund_amount per requirement.
            orig_credit = float(orig_settlement_row["credit"])
            orig_settlement_row["credit"] = round(orig_credit - refund_amount, 2)
            orig_settlement_row["net"] = round(orig_credit - refund_amount, 2)

            # Add a refund row to settlements
            refund_settlement_row = {
                "settlement_id": target_sid,
                "entity_id": refund_order_id,
                "type": "refund",
                "order_id": refund_order_id,
                "amount": -refund_amount,
                "credit": 0.0,
                "debit": refund_amount,
                "fee": 0.0,
                "tax": 0.0,
                "net": -refund_amount,
                "settlement_utr": target_utr,
                "date": target_settlement_date,
                "method": orig_method
            }
            settlements_mod.append(refund_settlement_row)

            # Update bank credit for this settlement_id to reflect reduced net
            # Recalculate total net for target_sid across settlements_mod
            new_total_net = sum(
                float(s["net"]) for s in settlements_mod if s["settlement_id"] == target_sid
            )
            new_total_net = round(new_total_net, 2)

            # Find matching bank row for this settlement (by UTR or matching date/narration)
            for b in bank_mod:
                if b.get("utr") == target_utr and b.get("classification") == "razorpay_credit":
                    b["credit"] = new_total_net
                    break

        ground_truth["scenarios"].append({
            "scenario": "partial_refund",
            "order_id": orig_order_id,
            "refund_id": refund_order_id,
            "refund_amount": refund_amount,
            "expected_verdict": "confirmed"
        })

    # Recalculate running balance for bank statement after scenario 1 changes
    def recalculate_bank_balances(rows: list[dict]):
        balance = 100000.0
        for r in rows:
            credit = float(r.get("credit", 0.0))
            debit = float(r.get("debit", 0.0))
            balance = round(balance + credit - debit, 2)
            r["balance"] = balance

    recalculate_bank_balances(bank_mod)

    # =========================================================================
    # SCENARIO 2 — Malformed UTR in bank narration
    # =========================================================================
    razorpay_bank_rows = [b for b in bank_mod if b.get("classification") == "razorpay_credit"]
    if razorpay_bank_rows:
        target_bank_row = random.choice(razorpay_bank_rows)
        clean_utr = target_bank_row["utr"]
        
        # Insert dashes into the UTR for narration (e.g. 235689741234 -> 235-689-741-234)
        # Format: 3 digits - 3 digits - 3 digits - rest
        if clean_utr and len(clean_utr) >= 12:
            malformed_utr_str = f"{clean_utr[:3]}-{clean_utr[3:6]}-{clean_utr[6:9]}-{clean_utr[9:]}"
        elif clean_utr:
            malformed_utr_str = "-".join([clean_utr[i:i+3] for i in range(0, len(clean_utr), 3)])
        else:
            malformed_utr_str = "123-456-789-012"

        new_narration = f"NEFT CR: HDFC {malformed_utr_str} RAZORPAY SETTLEMENT"
        target_bank_row["narration"] = new_narration

        ground_truth["scenarios"].append({
            "scenario": "malformed_utr",
            "txn_id": target_bank_row["txn_id"],
            "original_utr": clean_utr,
            "malformed_narration": new_narration,
            "expected_verdict": "confirmed"
        })

    # =========================================================================
    # SCENARIO 3 — Ambiguous bank batching
    # =========================================================================
    # Pick two consecutive settlements (SETL_X and SETL_Y) whose combined net is unique
    # We aggregate settlements by settlement_id to get net totals and UTRs
    settlement_summary = {}
    for s in settlements_mod:
        sid = s["settlement_id"]
        if sid not in settlement_summary:
            settlement_summary[sid] = {
                "date": s["date"],
                "utr": s["settlement_utr"],
                "net_total": 0.0
            }
        settlement_summary[sid]["net_total"] += float(s["net"])

    for sid in settlement_summary:
        settlement_summary[sid]["net_total"] = round(settlement_summary[sid]["net_total"], 2)

    unique_sids = sorted(settlement_summary.keys())
    batch_pair = None

    all_single_nets = {info["net_total"] for info in settlement_summary.values()}

    for i in range(len(unique_sids) - 1):
        sid1 = unique_sids[i]
        sid2 = unique_sids[i+1]
        combo_net = round(settlement_summary[sid1]["net_total"] + settlement_summary[sid2]["net_total"], 2)
        if combo_net not in all_single_nets:
            batch_pair = (sid1, sid2, combo_net)
            break

    if batch_pair:
        sid1, sid2, combined_net = batch_pair
        utr1 = settlement_summary[sid1]["utr"]
        utr2 = settlement_summary[sid2]["utr"]
        date1 = settlement_summary[sid1]["date"]
        date2 = settlement_summary[sid2]["date"]
        later_date = max(date1, date2)

        # Remove individual bank credit rows corresponding to utr1 and utr2
        bank_mod = [
            b for b in bank_mod 
            if not (b.get("classification") == "razorpay_credit" and b.get("utr") in (utr1, utr2))
        ]

        new_utr = "".join(random.choice("0123456789") for _ in range(12))
        combined_row = {
            "txn_id": "TXN_COMBINED",
            "date": later_date,
            "narration": f"NEFT CR: HDFC {new_utr} RAZORPAY SETTLEMENT",
            "utr": new_utr,
            "credit": combined_net,
            "debit": 0.0,
            "balance": 0.0,  # Will recalculate balance shortly
            "classification": "razorpay_credit"
        }

        # Insert combined_row before bank_charge rows or at an appropriate place
        charge_index = len(bank_mod)
        for idx, b in enumerate(bank_mod):
            if b.get("classification") == "bank_charge":
                charge_index = idx
                break
        bank_mod.insert(charge_index, combined_row)

        ground_truth["scenarios"].append({
            "scenario": "bank_batching",
            "combined_txn_id": "TXN_COMBINED",
            "settlement_ids": [sid1, sid2],
            "combined_amount": combined_net,
            "expected_verdict": "confirmed"
        })

        # Update settlement_bank_map: batched settlements both point to TXN_COMBINED
        updated_map[sid1] = "TXN_COMBINED"
        updated_map[sid2] = "TXN_COMBINED"

    # Recalculate balances after scenario 3
    recalculate_bank_balances(bank_mod)

    # =========================================================================
    # SCENARIO 4 — Orphan bank credit (unresolvable)
    # =========================================================================
    orphan_date = settlements_mod[0]["date"] if settlements_mod else "2026-08-05"
    orphan_utr = "999999999999"
    orphan_amount = 3200.0

    orphan_row = {
        "txn_id": "TXN_ORPHAN",
        "date": orphan_date,
        "narration": f"NEFT CR: HDFC {orphan_utr} RAZORPAY SETTLEMENT",
        "utr": orphan_utr,
        "credit": orphan_amount,
        "debit": 0.0,
        "balance": 0.0,
        "classification": "razorpay_credit"
    }

    # Insert orphan_row before bank_charge rows
    charge_index = len(bank_mod)
    for idx, b in enumerate(bank_mod):
        if b.get("classification") == "bank_charge":
            charge_index = idx
            break
    bank_mod.insert(charge_index, orphan_row)

    ground_truth["scenarios"].append({
        "scenario": "orphan_credit",
        "txn_id": "TXN_ORPHAN",
        "amount": orphan_amount,
        "expected_verdict": "unresolved"
    })

    # Update settlement_bank_map: add TXN_ORPHAN
    updated_map["TXN_ORPHAN"] = None

    # Final recalculation of balances
    recalculate_bank_balances(bank_mod)

    ground_truth["settlement_bank_map"] = updated_map

    return payments_mod, settlements_mod, bank_mod, ground_truth, updated_map

def validate_scenarios(ground_truth_filepath: str) -> bool:
    """
    Validate ground_truth.json content.
    
    Args:
        ground_truth_filepath (str): Path to ground_truth.json.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if not os.path.exists(ground_truth_filepath):
        print(f"Error: File {ground_truth_filepath} does not exist.")
        return False

    try:
        with open(ground_truth_filepath, mode="r", encoding="utf-8") as f:
            data = json.load(f)

        scenarios = data.get("scenarios", [])
        expected_scenario_names = {
            "partial_refund",
            "malformed_utr",
            "bank_batching",
            "orphan_credit"
        }

        found_scenarios = set()
        for sc in scenarios:
            name = sc.get("scenario")
            if name:
                found_scenarios.add(name)
            if "expected_verdict" not in sc:
                print(f"Error: Scenario '{name}' missing 'expected_verdict' field.")
                return False

        missing = expected_scenario_names - found_scenarios
        if missing:
            print(f"Error: Missing scenarios in ground truth: {missing}")
            return False

        print(f"Summary for {ground_truth_filepath}:")
        print(f"  Total scenarios injected: {len(scenarios)}")
        for sc in scenarios:
            print(f"  - [{sc['scenario']}] Verdict: {sc['expected_verdict']}")

        return True

    except Exception as e:
        print(f"Error validating scenarios file: {e}")
        return False

if __name__ == "__main__":
    from data.generator.payments import generate_payments
    from data.generator.settlements import generate_settlements
    from data.generator.bank_statement import generate_bank_statement

    payments = generate_payments(n=60)
    settlements = generate_settlements(payments)
    bank, settlement_bank_map = generate_bank_statement(settlements)

    p_messy, s_messy, b_messy, gt, updated_map = inject_scenarios(
        payments, settlements, bank, settlement_bank_map
    )

    output_dir = os.path.join("data", "sample")
    os.makedirs(output_dir, exist_ok=True)

    # Write payments_messy.csv
    p_filepath = os.path.join(output_dir, "payments_messy.csv")
    p_keys = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    with open(p_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=p_keys)
        writer.writeheader()
        writer.writerows(p_messy)

    # Write settlements_messy.csv
    s_filepath = os.path.join(output_dir, "settlements_messy.csv")
    s_keys = [
        "settlement_id", "entity_id", "type", "order_id", "amount", 
        "credit", "debit", "fee", "tax", "net", "settlement_utr", 
        "date", "method"
    ]
    with open(s_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=s_keys)
        writer.writeheader()
        writer.writerows(s_messy)

    # Write bank_statement_messy.csv
    b_filepath = os.path.join(output_dir, "bank_statement_messy.csv")
    b_keys = ["txn_id", "date", "narration", "utr", "credit", "debit", "balance", "classification"]
    with open(b_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=b_keys)
        writer.writeheader()
        writer.writerows(b_messy)

    # Write ground_truth.json
    gt_filepath = os.path.join(output_dir, "ground_truth.json")
    with open(gt_filepath, mode="w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2)

    validate_scenarios(gt_filepath)
