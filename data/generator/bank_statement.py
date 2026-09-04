import os
import csv


class SettlementBankMap(dict):
    """
    Dictionary mapping settlement_id -> txn_id.
    Also supports reverse lookup by txn_id.
    """
    def __getitem__(self, key):
        if key in self:
            return super().__getitem__(key)
        # Check reverse lookup if key is a txn_id
        matches = [sid for sid, val in self.items() if val == key]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            return matches
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def generate_bank_statement(settlements: list[dict], seed: int = 42) -> tuple[list[dict], dict]:
    """
    Generate synthetic bank statement data based on settlements.
    
    Args:
        settlements (list[dict]): List of settlement dicts from generate_settlements().
        seed (int): Seed for random operations if needed.
        
    Returns:
        tuple[list[dict], dict]: (bank_rows, settlement_bank_map)
            - bank_rows: List of bank statement transaction rows.
            - settlement_bank_map: Mapping of settlement_id -> txn_id.
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
    settlement_bank_map = SettlementBankMap()
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
        settlement_bank_map[sid] = txn_id

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

    return bank_rows, settlement_bank_map


def validate_bank_statement(
    filepath,
    settlements_filepath = None,
    settlement_bank_map: dict = None
) -> bool:
    """
    Validate the bank statement CSV file or data against requirements and settlements.
    Handles filepaths, in-memory data, and (bank_rows, settlement_bank_map) tuple format.
    
    Args:
        filepath: Path to bank_statement.csv or list[dict] or tuple (bank_rows, settlement_bank_map).
        settlements_filepath: Path to settlements.csv or list[dict].
        settlement_bank_map (dict, optional): Optional settlement to bank mapping dict.
        
    Returns:
        bool: True if all checks pass, False otherwise.
    """
    # Handle tuple return format from generate_bank_statement()
    if isinstance(filepath, tuple) and len(filepath) == 2:
        bank_data, extracted_map = filepath
        if settlement_bank_map is None and isinstance(extracted_map, dict):
            settlement_bank_map = extracted_map
        filepath = bank_data

    is_memory_bank = isinstance(filepath, list)
    is_memory_settlements = isinstance(settlements_filepath, list)

    if not is_memory_bank and not os.path.exists(filepath):
        print(f"Error: Bank statement file {filepath} does not exist.")
        return False

    if settlements_filepath is not None and not is_memory_settlements and not os.path.exists(settlements_filepath):
        print(f"Error: Settlements file {settlements_filepath} does not exist.")
        return False

    required_columns = [
        "txn_id", "date", "narration", "utr",
        "credit", "debit", "balance", "classification"
    ]

    settlement_utrs = set()
    settlement_net_sums = {}

    if is_memory_settlements:
        for row in settlements_filepath:
            utr = row.get("settlement_utr")
            if utr:
                settlement_utrs.add(utr)
            sid = row["settlement_id"]
            net_val = float(row["net"])
            settlement_net_sums[sid] = settlement_net_sums.get(sid, 0.0) + net_val
    elif settlements_filepath and os.path.exists(settlements_filepath):
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

    # Load bank records
    if is_memory_bank:
        bank_records = filepath
        fieldnames = list(bank_records[0].keys()) if bank_records else required_columns
    else:
        try:
            with open(filepath, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                bank_records = list(reader)
        except Exception as e:
            print(f"Error reading bank statement file: {e}")
            return False

    if not bank_records or not fieldnames:
        print("Error: Empty bank statement file or no header row.")
        return False

    for col in required_columns:
        if col not in fieldnames:
            print(f"Error: Missing required column '{col}'.")
            return False

    total_rows = 0
    razorpay_credits_count = 0
    bank_charges_count = 0
    total_credited = 0.0
    total_debited = 0.0
    final_balance = 0.0
    prev_balance = None

    for row in bank_records:
        total_rows += 1
        classification = row["classification"]
        if classification not in ["razorpay_credit", "bank_charge"]:
            print(f"Error: Invalid classification '{classification}' in row {total_rows}.")
            return False

        try:
            credit = float(row["credit"])
            debit = float(row["debit"])
            balance = float(row["balance"])
        except (ValueError, TypeError) as e:
            print(f"Error: Invalid numeric value in row {total_rows}: {e}")
            return False

        total_credited += credit
        total_debited += debit
        final_balance = balance

        if classification == "razorpay_credit":
            razorpay_credits_count += 1
            utr = row.get("utr")
            if settlement_utrs and (not utr or utr not in settlement_utrs):
                print(f"Error: UTR '{utr}' in row {total_rows} not found in settlements.csv.")
                return False

            if prev_balance is not None and balance <= prev_balance:
                print(f"Error: Balance not strictly increasing for credit row {total_rows}. Prev: {prev_balance}, Current: {balance}")
                return False

        elif classification == "bank_charge":
            bank_charges_count += 1
            if abs(credit) > 1e-9:
                print(f"Error: bank_charge row {total_rows} must have credit == 0.0. Got {credit}")
                return False

        prev_balance = balance

    # Check credit amounts against settlement net sums
    if settlement_net_sums:
        utr_to_net_sum = {}
        if is_memory_settlements:
            for s_row in settlements_filepath:
                s_utr = s_row.get("settlement_utr")
                if s_utr:
                    utr_to_net_sum[s_utr] = utr_to_net_sum.get(s_utr, 0.0) + float(s_row["net"])
        elif settlements_filepath and os.path.exists(settlements_filepath):
            with open(settlements_filepath, mode="r", encoding="utf-8", newline="") as sf:
                s_reader = csv.DictReader(sf)
                for s_row in s_reader:
                    s_utr = s_row.get("settlement_utr")
                    if s_utr:
                        utr_to_net_sum[s_utr] = utr_to_net_sum.get(s_utr, 0.0) + float(s_row["net"])

        credit_rows = [r for r in bank_records if r["classification"] == "razorpay_credit"]
        for r in credit_rows:
            utr = r.get("utr")
            if utr in utr_to_net_sum:
                credit_val = float(r["credit"])
                expected_val = round(utr_to_net_sum[utr], 2)
                if abs(credit_val - expected_val) > 1e-9:
                    print(f"Error: Credit amount mismatch for UTR '{utr}'. Expected {expected_val}, got {credit_val}")
                    return False

    # Validate settlement_bank_map if provided
    if settlement_bank_map is not None:
        if not isinstance(settlement_bank_map, dict):
            print("Error: settlement_bank_map must be a dictionary.")
            return False
        bank_txn_ids = {r["txn_id"] for r in bank_records}
        for sid, txn_id in settlement_bank_map.items():
            if sid == "TXN_ORPHAN":
                if txn_id is not None:
                    print("Error: TXN_ORPHAN must map to None in settlement_bank_map.")
                    return False
            else:
                if settlement_net_sums and sid not in settlement_net_sums:
                    print(f"Error: settlement_id '{sid}' in settlement_bank_map not found in settlements.")
                    return False
                if txn_id is not None and txn_id not in bank_txn_ids:
                    print(f"Error: txn_id '{txn_id}' in settlement_bank_map not found in bank statement.")
                    return False

    source_label = "in-memory bank data" if is_memory_bank else str(filepath)
    print(f"Summary for {source_label}:")
    print(f"  Total rows: {total_rows}")
    print(f"  Razorpay credits count: {razorpay_credits_count}")
    print(f"  Bank charges count: {bank_charges_count}")
    print(f"  Total credited: {total_credited:.2f}")
    print(f"  Total debited: {total_debited:.2f}")
    print(f"  Final balance: {final_balance:.2f}")
    if settlement_bank_map is not None:
        print(f"  Settlement-bank mappings: {len(settlement_bank_map)}")
    return True


if __name__ == "__main__":
    from data.generator.payments import generate_payments
    from data.generator.settlements import generate_settlements

    payments = generate_payments(n=60)
    settlements = generate_settlements(payments)
    bank, settlement_bank_map = generate_bank_statement(settlements)

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

    print(f"Mapped {len(settlement_bank_map)} settlements to bank transactions:")
    for sid, txn_id in list(settlement_bank_map.items())[:5]:
        print(f"  {sid} -> {txn_id}")

    # Validate using filepaths and map
    validate_bank_statement(bank_statement_filepath, settlements_filepath, settlement_bank_map=settlement_bank_map)

    # Also validate using (bank, settlement_bank_map) tuple format
    validate_bank_statement((bank, settlement_bank_map), settlements_filepath)
