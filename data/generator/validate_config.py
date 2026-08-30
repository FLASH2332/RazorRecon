import sys
import os
from datetime import datetime

# Add current directory to sys.path if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate import DATASET_CONFIGS, generate_dataset
from payments import generate_payments, validate_payments
from settlements import validate_settlements
from bank_statement import validate_bank_statement
from scenarios import validate_scenarios

def run_validation() -> bool:
    all_passed = True

    print("=== RUNNING GENERATOR CONFIG VALIDATION ===")

    # ----------------------------------------------------
    # CHECK 1 — DATASET_CONFIGS structure
    # ----------------------------------------------------
    print("\n--- CHECK 1: DATASET_CONFIGS Structure ---")
    check1_passed = True
    expected_sizes = ["small", "medium", "large"]

    prev_window = 0
    for size in expected_sizes:
        if size not in DATASET_CONFIGS:
            print(f"[{size}] FAIL: Size '{size}' missing from DATASET_CONFIGS.")
            check1_passed = False
            continue

        cfg = DATASET_CONFIGS[size]
        keys_ok = all(k in cfg for k in ["n", "seed", "date_window_days"])
        if not keys_ok:
            print(f"[{size}] FAIL: Missing required keys in config: {cfg}")
            check1_passed = False
            continue

        n_ok = isinstance(cfg["n"], int) and cfg["n"] > 0
        seed_ok = isinstance(cfg["seed"], int)
        window_ok = isinstance(cfg["date_window_days"], int) and cfg["date_window_days"] > 0
        increasing_ok = cfg["date_window_days"] > prev_window
        prev_window = cfg["date_window_days"]

        if n_ok and seed_ok and window_ok and increasing_ok:
            print(f"[{size}] PASS")
        else:
            print(f"[{size}] FAIL: Invalid config parameters (n={cfg.get('n')}, seed={cfg.get('seed')}, date_window_days={cfg.get('date_window_days')})")
            check1_passed = False

    if not check1_passed:
        all_passed = False

    # ----------------------------------------------------
    # CHECK 2 — date_window_days parameter verification
    # ----------------------------------------------------
    print("\n--- CHECK 2: date_window_days Propagation ---")
    try:
        res_5 = generate_payments(n=10, seed=42, date_window_days=5)
        dates_5 = [datetime.strptime(p["date"], "%Y-%m-%d").date() for p in res_5]
        min_5, max_5 = min(dates_5), max(dates_5)
        range_5 = (max_5 - min_5).days + 1

        res_20 = generate_payments(n=10, seed=42, date_window_days=20)
        dates_20 = [datetime.strptime(p["date"], "%Y-%m-%d").date() for p in res_20]
        min_20, max_20 = min(dates_20), max(dates_20)
        range_20 = (max_20 - min_20).days + 1

        if range_5 <= 5 and range_20 > range_5:
            print("PASS: date_window_days correctly controls date range in generate_payments.")
        else:
            print(f"FAIL: Date ranges unexpected. 5-day range={range_5}, 20-day range={range_20}")
            all_passed = False
    except Exception as e:
        print(f"FAIL: Exception in Check 2: {e}")
        all_passed = False

    # ----------------------------------------------------
    # CHECK 3 — Generate and Validate Each Dataset Size
    # ----------------------------------------------------
    print("\n--- CHECK 3: Dataset Generation & File Validation ---")
    for size in expected_sizes:
        print(f"\nTesting dataset size: '{size}'")
        try:
            stats = generate_dataset(size, inject=True)
            out_dir = stats["output_dir"]

            # Validate clean payments
            p_clean = os.path.join(out_dir, "payments.csv")
            v_p_clean = validate_payments(p_clean)

            # Validate clean settlements
            s_clean = os.path.join(out_dir, "settlements.csv")
            v_s_clean = validate_settlements(s_clean, p_clean)

            # Validate clean bank statement
            b_clean = os.path.join(out_dir, "bank_statement.csv")
            v_b_clean = validate_bank_statement(b_clean, s_clean)

            # Validate scenarios / ground truth
            gt_file = os.path.join(out_dir, "ground_truth.json")
            v_gt = validate_scenarios(gt_file)

            if v_p_clean and v_s_clean and v_b_clean and v_gt:
                print(f"[{size}] PASS: All validations passed for '{size}'")
            else:
                print(f"[{size}] FAIL: Validation failed for one or more files in '{size}'")
                all_passed = False
        except Exception as e:
            print(f"[{size}] FAIL: Exception generating/validating '{size}': {e}")
            all_passed = False

    print("\n==========================================")
    if all_passed:
        print("OVERALL RESULT: PASS (All checks passed)")
        return True
    else:
        print("OVERALL RESULT: FAIL (One or more checks failed)")
        return False

if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)
