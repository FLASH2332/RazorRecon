import os
import csv
import json
import sys
from data.generator.payments import generate_payments
from data.generator.settlements import generate_settlements
from data.generator.bank_statement import generate_bank_statement
from data.generator.scenarios import inject_scenarios

DATASET_CONFIGS = {
    "small": {"n": 60,  "seed": 42, "date_window_days": 30},
    "medium": {"n": 200, "seed": 43, "date_window_days": 60},
    "large": {"n": 500, "seed": 44, "date_window_days": 90}
}

def generate_dataset(size: str, inject: bool = True) -> dict:
    """
    Generate synthetic dataset for a given size.
    
    Args:
        size (str): One of 'small', 'medium', or 'large'.
        inject (bool): Whether to inject messy edge cases into the dataset.
        
    Returns:
        dict: Summary statistics of the generated dataset.
    """
    if size not in DATASET_CONFIGS:
        raise ValueError(f"Invalid size '{size}'. Expected one of {list(DATASET_CONFIGS.keys())}")

    config = DATASET_CONFIGS[size]
    n = config["n"]
    seed = config["seed"]
    date_window_days = config["date_window_days"]

    output_dir = os.path.join("data", "sample", size)
    os.makedirs(output_dir, exist_ok=True)

    # 1. Generate clean datasets
    payments = generate_payments(n=n, seed=seed, date_window_days=date_window_days)
    settlements = generate_settlements(payments, seed=seed)
    bank = generate_bank_statement(settlements, seed=seed)

    # Write clean CSVs
    p_keys = ["order_id", "amount", "type", "parent_order_id", "date", "status", "method"]
    with open(os.path.join(output_dir, "payments.csv"), mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=p_keys)
        writer.writeheader()
        writer.writerows(payments)

    s_keys = [
        "settlement_id", "entity_id", "type", "order_id", "amount", 
        "credit", "debit", "fee", "tax", "net", "settlement_utr", 
        "date", "method"
    ]
    with open(os.path.join(output_dir, "settlements.csv"), mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=s_keys)
        writer.writeheader()
        writer.writerows(settlements)

    b_keys = ["txn_id", "date", "narration", "utr", "credit", "debit", "balance", "classification"]
    with open(os.path.join(output_dir, "bank_statement.csv"), mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=b_keys)
        writer.writeheader()
        writer.writerows(bank)

    scenarios_injected_count = 0

    # 2. Inject scenarios if requested
    if inject:
        p_messy, s_messy, b_messy, gt = inject_scenarios(payments, settlements, bank, seed=seed)
        scenarios_injected_count = len(gt.get("scenarios", []))

        with open(os.path.join(output_dir, "payments_messy.csv"), mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=p_keys)
            writer.writeheader()
            writer.writerows(p_messy)

        with open(os.path.join(output_dir, "settlements_messy.csv"), mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=s_keys)
            writer.writeheader()
            writer.writerows(s_messy)

        with open(os.path.join(output_dir, "bank_statement_messy.csv"), mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=b_keys)
            writer.writeheader()
            writer.writerows(b_messy)

        with open(os.path.join(output_dir, "ground_truth.json"), mode="w", encoding="utf-8") as f:
            json.dump(gt, f, indent=2)

    captured_count = sum(1 for p in payments if p.get("status") == "captured")
    settlement_batches = len({s["settlement_id"] for s in settlements})

    return {
        "size": size,
        "payments_count": len(payments),
        "captured_count": captured_count,
        "settlement_batches": settlement_batches,
        "bank_rows": len(bank),
        "scenarios_injected": scenarios_injected_count,
        "output_dir": output_dir
    }

def generate_all() -> None:
    """
    Generate all dataset sizes and print a formatted summary table.
    """
    results = []
    for size in DATASET_CONFIGS:
        stats = generate_dataset(size, inject=True)
        results.append(stats)

    print(f"{'Size':<8} | {'Payments':<8} | {'Captured':<8} | {'Batches':<7} | {'Bank rows':<9} | {'Output'}")
    print("-" * 75)
    for r in results:
        print(
            f"{r['size']:<8} | "
            f"{r['payments_count']:<8} | "
            f"{r['captured_count']:<8} | "
            f"{r['settlement_batches']:<7} | "
            f"{r['bank_rows']:<9} | "
            f"{r['output_dir']}/"
        )

if __name__ == "__main__":
    size_arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if size_arg == "all":
        generate_all()
    else:
        stats = generate_dataset(size_arg)
        print(stats)
