import os
import csv
import random
from datetime import datetime, timedelta

def generate_settlements(payments: list[dict], seed: int = 42) -> list[dict]:
    """
    Generate synthetic Razorpay settlement data based on captured payments.
    """
    random.seed(seed)
    
    # Only process payments where status == "captured"
    captured_payments = [p for p in payments if p.get("status") == "captured"]
    
    # Group captured payments into settlement batches:
    # Each batch covers payments from a single day
    # One settlement per day that had captured payments
    payments_by_date = {}
    for p in captured_payments:
        date_str = p["date"]
        if date_str not in payments_by_date:
            payments_by_date[date_str] = []
        payments_by_date[date_str].append(p)
        
    # Sort dates to have sequential batch identifiers
    sorted_dates = sorted(payments_by_date.keys())
    
    settlements = []
    
    def add_working_days(date_str: str, days: int) -> str:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        added = 0
        while added < days:
            dt += timedelta(days=1)
            # 5 = Saturday, 6 = Sunday
            if dt.weekday() < 5:
                added += 1
        return dt.isoformat()
        
    for idx, date_str in enumerate(sorted_dates, 1):
        settlement_id = f"SETL_{idx:03d}"
        
        # Generate a random 12-digit numeric string for UTR
        settlement_utr = "".join(random.choice("0123456789") for _ in range(12))
        
        # settlement date is payment date + 2 working days (skip weekends)
        settlement_date = add_working_days(date_str, 2)
        
        batch_payments = payments_by_date[date_str]
        for p in batch_payments:
            amount = p["amount"]
            
            # MDR = amount * 0.02
            # GST_on_MDR = MDR * 0.18
            # fee = MDR + GST_on_MDR (rounded to 2 decimal places)
            mdr = amount * 0.02
            gst_on_mdr = mdr * 0.18
            fee = round(mdr + gst_on_mdr, 2)
            
            credit = round(amount - fee, 2)
            
            row = {
                "settlement_id": settlement_id,
                "entity_id": p["order_id"],
                "type": "payment",
                "order_id": p["order_id"],
                "amount": amount,
                "credit": credit,
                "debit": 0.0,
                "fee": fee,
                "tax": round(gst_on_mdr, 2),
                "net": credit,
                "settlement_utr": settlement_utr,
                "date": settlement_date,
                "method": p["method"]
            }
            settlements.append(row)
            
    return settlements

def validate_settlements(filepath: str, payments_filepath: str) -> bool:
    """
    Validate the generated settlements CSV file.
    """
    if not os.path.exists(filepath):
        print(f"Error: Settlements file {filepath} does not exist.")
        return False
        
    if not os.path.exists(payments_filepath):
        print(f"Error: Payments file {payments_filepath} does not exist.")
        return False
        
    # Read payments.csv to check entity_id exists and is captured
    captured_payments_in_csv = {}
    try:
        with open(payments_filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                captured_payments_in_csv[row["order_id"]] = {
                    "status": row["status"],
                    "date": row["date"]
                }
    except Exception as e:
        print(f"Error reading payments file: {e}")
        return False
        
    required_columns = [
        "settlement_id", "entity_id", "type", "order_id", "amount", 
        "credit", "debit", "fee", "tax", "net", "settlement_utr", 
        "date", "method"
    ]
    
    total_rows = 0
    unique_batches = set()
    total_gross = 0.0
    total_fees = 0.0
    total_net = 0.0
    
    try:
        with open(filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                print("Error: Empty settlements file or no header row.")
                return False
                
            for col in required_columns:
                if col not in reader.fieldnames:
                    print(f"Error: Missing required column '{col}'.")
                    return False
                    
            for row in reader:
                total_rows += 1
                
                settlement_id = row["settlement_id"]
                entity_id = row["entity_id"]
                row_type = row["type"]
                
                try:
                    amount = float(row["amount"])
                    credit = float(row["credit"])
                    debit = float(row["debit"])
                    fee = float(row["fee"])
                    net = float(row["net"])
                except ValueError as e:
                    print(f"Error: Invalid numeric value in row {total_rows}: {e}")
                    return False
                    
                settlement_date_str = row["date"]
                
                unique_batches.add(settlement_id)
                total_gross += amount
                total_fees += fee
                total_net += net
                
                # Check fee calculation: fee = round(amount * 0.02 * 1.18, 2)
                expected_fee = round(amount * 0.02 * 1.18, 2)
                if abs(fee - expected_fee) > 1e-9:
                    print(f"Error: Fee mismatch in row {total_rows}. Expected {expected_fee}, got {fee}")
                    return False
                    
                # Check credit calculation: credit = round(amount - fee, 2)
                expected_credit = round(amount - fee, 2)
                if abs(credit - expected_credit) > 1e-9:
                    print(f"Error: Credit mismatch in row {total_rows}. Expected {expected_credit}, got {credit}")
                    return False
                    
                # Check net == credit
                if abs(net - credit) > 1e-9:
                    print(f"Error: Net and Credit mismatch in row {total_rows}. Net: {net}, Credit: {credit}")
                    return False
                    
                # Check debit == 0.0 for payment rows
                if row_type == "payment" and abs(debit) > 1e-9:
                    print(f"Error: Debit must be 0.0 for payment row. Got {debit} in row {total_rows}")
                    return False
                    
                # Check entity_id exists in payments.csv as a captured payment
                if entity_id not in captured_payments_in_csv:
                    print(f"Error: entity_id '{entity_id}' not found in payments.csv.")
                    return False
                
                p_info = captured_payments_in_csv[entity_id]
                if p_info["status"] != "captured":
                    print(f"Error: entity_id '{entity_id}' is not captured in payments.csv (status is '{p_info['status']}').")
                    return False
                    
                # Check settlement date is always after payment date
                p_date = datetime.strptime(p_info["date"], "%Y-%m-%d").date()
                s_date = datetime.strptime(settlement_date_str, "%Y-%m-%d").date()
                if s_date <= p_date:
                    print(f"Error: Settlement date {settlement_date_str} must be after payment date {p_info['date']}.")
                    return False
                    
        print("Summary:")
        print(f"  Total rows: {total_rows}")
        print(f"  Unique settlement batches: {len(unique_batches)}")
        print(f"  Total gross: {total_gross:.2f}")
        print(f"  Total fees: {total_fees:.2f}")
        print(f"  Total net: {total_net:.2f}")
        return True
        
    except Exception as e:
        print(f"Error validating settlements file: {e}")
        return False

if __name__ == "__main__":
    from payments import generate_payments
    payments = generate_payments(n=60)
    
    # Ensure directory exists
    os.makedirs(os.path.join("data", "sample"), exist_ok=True)
    
    # Write payments to data/sample/payments.csv
    payments_filepath = os.path.join("data", "sample", "payments.csv")
    payments_keys = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    with open(payments_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=payments_keys)
        writer.writeheader()
        writer.writerows(payments)
        
    settlements = generate_settlements(payments)
    
    # Write settlements to data/sample/settlements.csv
    settlements_filepath = os.path.join("data", "sample", "settlements.csv")
    settlements_keys = [
        "settlement_id", "entity_id", "type", "order_id", "amount", 
        "credit", "debit", "fee", "tax", "net", "settlement_utr", 
        "date", "method"
    ]
    with open(settlements_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=settlements_keys)
        writer.writeheader()
        writer.writerows(settlements)
        
    validate_settlements(settlements_filepath, payments_filepath)
