import os
import csv
import random
from datetime import datetime, timedelta

def generate_payments(n: int, seed: int = 42, date_window_days: int = 30) -> list[dict]:
    """
    Generate synthetic Razorpay payments data.
    
    Args:
        n (int): Number of payments to generate.
        seed (int): Random seed for reproducibility.
        date_window_days (int): Number of days to spread payment dates across.
        
    Returns:
        list[dict]: A list of generated payment dictionaries.
    """
    random.seed(seed)
    base_date = datetime(2026, 8, 1).date()
    payments = []
    
    for i in range(1, n + 1):
        order_id = f"ORD_{i:03d}"
        amount = round(random.uniform(500.0, 50000.0), 2)
        # Dates spread across date_window_days
        day_offset = random.randint(0, date_window_days - 1)
        date_str = (base_date + timedelta(days=day_offset)).isoformat()
        
        # 95% captured, 5% failed
        status = "captured" if random.random() < 0.95 else "failed"
        method = random.choice(["upi", "card", "netbanking"])
        
        payments.append({
            "order_id": order_id,
            "amount": amount,
            "type": "payment",
            "parent_order_id": None,
            "date": date_str,
            "status": status,
            "method": method
        })
        
    return payments

def validate_payments(filepath: str) -> bool:
    """
    Validate the payments CSV file structure and values.
    
    Args:
        filepath (str): Path to the payments CSV file.
        
    Returns:
        bool: True if validation passes, False otherwise.
    """
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} does not exist.")
        return False
        
    required_columns = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    
    order_ids = set()
    total_records = 0
    captured_count = 0
    failed_count = 0
    
    try:
        with open(filepath, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            
            # Check all required columns are present
            if not reader.fieldnames:
                print("Error: Empty file or no header row found.")
                return False
                
            for col in required_columns:
                if col not in reader.fieldnames:
                    print(f"Error: Missing required column '{col}'.")
                    return False
            
            for row in reader:
                total_records += 1
                
                # Check order_id is not duplicated
                oid = row["order_id"]
                if oid in order_ids:
                    print(f"Error: Duplicate order_id '{oid}' found.")
                    return False
                order_ids.add(oid)
                
                # Check amount is always > 0
                try:
                    amount = float(row["amount"])
                except ValueError:
                    print(f"Error: Invalid amount value '{row['amount']}' on order '{oid}'.")
                    return False
                if amount <= 0:
                    print(f"Error: Amount must be > 0. Found {amount} on order '{oid}'.")
                    return False
                
                # Check type is always "payment"
                if row["type"] != "payment":
                    print(f"Error: Expected type 'payment', found '{row['type']}' on order '{oid}'.")
                    return False
                
                # Check status is only "captured" or "failed"
                status = row["status"]
                if status not in ["captured", "failed"]:
                    print(f"Error: Invalid status '{status}' on order '{oid}'.")
                    return False
                
                if status == "captured":
                    captured_count += 1
                elif status == "failed":
                    failed_count += 1
                    
        print(f"Summary for {filepath}:")
        print(f"  Total records: {total_records}")
        print(f"  Captured: {captured_count}")
        print(f"  Failed: {failed_count}")
        return True
        
    except Exception as e:
        print(f"Error reading or validating file: {e}")
        return False

if __name__ == "__main__":
    payments = generate_payments(n=60)
    
    # Create the data/sample/ directory if it doesn't exist
    os.makedirs(os.path.join("data", "sample"), exist_ok=True)
    
    filepath = os.path.join("data", "sample", "payments.csv")
    
    # Write to data/sample/payments.csv using csv.DictWriter
    keys = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    with open(filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(payments)
        
    # Validate the generated file
    validate_payments(filepath)
