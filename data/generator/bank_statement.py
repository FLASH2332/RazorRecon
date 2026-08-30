import os
import csv

def generate_bank_statement(settlements: list[dict], seed: int = 42) -> list[dict]:
    """
    Generate synthetic bank statement data based on settlements.
    
    Args:
        settlements (list[dict]): List of settlement dicts from generate_settlements().
        seed (int): Seed for random operations if needed.
        
    Returns:
        list[dict]: List of bank statement transaction rows.
    """
    # Group settlements by settlement_id to maintain order of appearance and accumulate net sum
    settlement_groups = {}
    for s in settlements:
        sid = s["settlement_id"]
        if sid not in settlement_groups:
            settlement_groups[sid] = {
                "date": s["date"],
                "settlement_utr": s["settlement_utr"],
                "total_net": 0.0
            }
        settlement_groups[sid]["total_net"] += float(s["net"])

    bank_rows = []
    txn_count = 1
    running_balance = 100000.0
    last_settlement_date = None

    for sid, info in settlement_groups.items():
        txn_id = f"TXN_{txn_count:03d}"
        txn_count += 1
        
        credit_amount = round(info["total_net"], 2)
        running_balance = round(running_balance + credit_amount, 2)
        last_settlement_date = info["date"]
        
        narration = f"NEFT CR: HDFC {info['settlement_utr']} RAZORPAY SETTLEMENT"
        
        row = {
            "txn_id": txn_id,
            "date": info["date"],
            "narration": narration,
            "utr": info["settlement_utr"],
            "credit": credit_amount,
            "debit": 0.0,
            "balance": running_balance,
            "classification": "razorpay_credit"
        }
        bank_rows.append(row)

    # Append noise rows if any settlement was processed (last_settlement_date exists)
    noise_date = last_settlement_date if last_settlement_date else "2026-08-01"

    # Row 1: SMS ALERT CHARGES Q2
    txn_id_n1 = f"TXN_{txn_count:03d}"
    txn_count += 1
    running_balance = round(running_balance - 15.0, 2)
    bank_rows.append({
        "txn_id": txn_id_n1,
        "date": noise_date,
        "narration": "SMS ALERT CHARGES Q2",
        "utr": None,
        "credit": 0.0,
        "debit": 15.0,
        "balance": running_balance,
        "classification": "bank_charge"
    })

    # Row 2: GST ON SMS CHARGES
    txn_id_n2 = f"TXN_{txn_count:03d}"
    txn_count += 1
    running_balance = round(running_balance - 2.7, 2)
    bank_rows.append({
        "txn_id": txn_id_n2,
        "date": noise_date,
        "narration": "GST ON SMS CHARGES",
        "utr": None,
        "credit": 0.0,
        "debit": 2.7,
        "balance": running_balance,
        "classification": "bank_charge"
    })

    # Row 3: NEFT TRANSACTION CHARGES
    txn_id_n3 = f"TXN_{txn_count:03d}"
    txn_count += 1
    running_balance = round(running_balance - 5.0, 2)
    bank_rows.append({
        "txn_id": txn_id_n3,
        "date": noise_date,
        "narration": "NEFT TRANSACTION CHARGES",
        "utr": None,
        "credit": 0.0,
        "debit": 5.0,
        "balance": running_balance,
        "classification": "bank_charge"
    })

    return bank_rows

def validate_bank_statement(filepath: str, settlements_filepath: str) -> bool:
    """
    Validate the bank statement CSV file against requirements and settlements.csv.
    
    Args:
        filepath (str): Path to bank_statement.csv.
        settlements_filepath (str): Path to settlements.csv.
        
    Returns:
        bool: True if all checks pass, False otherwise.
    """
    if not os.path.exists(filepath):
        print(f"Error: Bank statement file {filepath} does not exist.")
        return False

    if not os.path.exists(settlements_filepath):
        print(f"Error: Settlements file {settlements_filepath} does not exist.")
        return False

    required_columns = [
        "txn_id", "date", "narration", "utr",
        "credit", "debit", "balance", "classification"
    ]

    # Read settlements.csv to extract UTR set and settlement_id net sums
    settlement_utrs = set()
    settlement_net_sums = {}

    try:
        with open(settlements_filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                utr = row.get("settlement_utr")
                if utr:
                    settlement_utrs.add(utr)
                
                sid = row["settlement_id"]
                net_val = float(row["net"])
                settlement_net_sums[sid] = settlement_net_sums.get(sid, 0.0) + net_val
    except Exception as e:
        print(f"Error reading settlements file: {e}")
        return False

    # Round expected net sums to 2 decimal places for comparison
    settlement_net_sums = {sid: round(val, 2) for sid, val in settlement_net_sums.items()}

    total_rows = 0
    razorpay_credits_count = 0
    bank_charges_count = 0
    total_credited = 0.0
    total_debited = 0.0
    final_balance = 0.0
    prev_balance = None

    try:
        with open(filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                print("Error: Empty bank statement file or no header row.")
                return False

            for col in required_columns:
                if col not in reader.fieldnames:
                    print(f"Error: Missing required column '{col}'.")
                    return False

            for row in reader:
                total_rows += 1
                
                classification = row["classification"]
                if classification not in ["razorpay_credit", "bank_charge"]:
                    print(f"Error: Invalid classification '{classification}' in row {total_rows}.")
                    return False

                try:
                    credit = float(row["credit"])
                    debit = float(row["debit"])
                    balance = float(row["balance"])
                except ValueError as e:
                    print(f"Error: Invalid numeric value in row {total_rows}: {e}")
                    return False

                total_credited += credit
                total_debited += debit
                final_balance = balance

                if classification == "razorpay_credit":
                    razorpay_credits_count += 1
                    utr = row["utr"]
                    if not utr or utr not in settlement_utrs:
                        print(f"Error: UTR '{utr}' in row {total_rows} not found in settlements.csv.")
                        return False

                    # Check credit balance is strictly increasing for credit rows
                    if prev_balance is not None and balance <= prev_balance:
                        print(f"Error: Balance not strictly increasing for credit row {total_rows}. Prev: {prev_balance}, Current: {balance}")
                        return False

                elif classification == "bank_charge":
                    bank_charges_count += 1
                    if abs(credit) > 1e-9:
                        print(f"Error: bank_charge row {total_rows} must have credit == 0.0. Got {credit}")
                        return False

                prev_balance = balance

        # Also check that credit amount for each razorpay_credit equals sum of net for that settlement_id
        # Re-read file to verify credit matching against settlement_net_sums
        with open(filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            credit_rows = [r for r in reader if r["classification"] == "razorpay_credit"]
            
            # Map UTR to total_net from settlements
            # (Note: each unique settlement has its own settlement_utr)
            utr_to_net_sum = {}
            with open(settlements_filepath, mode="r", encoding="utf-8", newline="") as sf:
                s_reader = csv.DictReader(sf)
                for s_row in s_reader:
                    s_utr = s_row["settlement_utr"]
                    utr_to_net_sum[s_utr] = utr_to_net_sum.get(s_utr, 0.0) + float(s_row["net"])

            for r in credit_rows:
                utr = r["utr"]
                credit_val = float(r["credit"])
                expected_val = round(utr_to_net_sum.get(utr, 0.0), 2)
                if abs(credit_val - expected_val) > 1e-9:
                    print(f"Error: Credit amount mismatch for UTR '{utr}'. Expected {expected_val}, got {credit_val}")
                    return False

        print(f"Summary for {filepath}:")
        print(f"  Total rows: {total_rows}")
        print(f"  Razorpay credits count: {razorpay_credits_count}")
        print(f"  Bank charges count: {bank_charges_count}")
        print(f"  Total credited: {total_credited:.2f}")
        print(f"  Total debited: {total_debited:.2f}")
        print(f"  Final balance: {final_balance:.2f}")
        return True

    except Exception as e:
        print(f"Error validating bank statement file: {e}")
        return False

if __name__ == "__main__":
    from payments import generate_payments
    from settlements import generate_settlements

    payments = generate_payments(n=60)
    settlements = generate_settlements(payments)
    bank = generate_bank_statement(settlements)

    os.makedirs(os.path.join("data", "sample"), exist_ok=True)

    bank_statement_filepath = os.path.join("data", "sample", "bank_statement.csv")
    settlements_filepath = os.path.join("data", "sample", "settlements.csv")
    payments_filepath = os.path.join("data", "sample", "payments.csv")

    # Also ensure payments.csv and settlements.csv are written so validation works
    payments_keys = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    with open(payments_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=payments_keys)
        writer.writeheader()
        writer.writerows(payments)

    settlements_keys = [
        "settlement_id", "entity_id", "type", "order_id", "amount", 
        "credit", "debit", "fee", "tax", "net", "settlement_utr", 
        "date", "method"
    ]
    with open(settlements_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=settlements_keys)
        writer.writeheader()
        writer.writerows(settlements)

    bank_keys = ["txn_id", "date", "narration", "utr", "credit", "debit", "balance", "classification"]
    with open(bank_statement_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=bank_keys)
        writer.writeheader()
        writer.writerows(bank)

    validate_bank_statement(bank_statement_filepath, settlements_filepath)
