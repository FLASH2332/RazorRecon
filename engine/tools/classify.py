import re

CHARGE_KEYWORDS = [
    "SMS",
    "ALERT",
    "CHARGE",
    "GST ON",
    "NEFT CHARGES",
    "IMPS CHARGES",
    "SERVICE FEE",
    "ANNUAL FEE",
]


def normalize_utr(utr: str) -> str:
    """
    Strips dashes, spaces, converts to uppercase.
    Returns normalized string.
    Used to handle malformed UTR scenario.
    """
    if not utr:
        return ""
    return re.sub(r"[-\s]", "", str(utr)).upper()


def utrs_match(utr1: str, utr2: str) -> bool:
    """
    Normalizes both UTRs then compares.
    Returns True if they match after normalization.
    Handles the malformed UTR scenario:
        "235689741234" vs "235-689-741-234" -> True
    """
    if not utr1 or not utr2:
        return False
    return normalize_utr(utr1) == normalize_utr(utr2)


def extract_utr(narration: str) -> str | None:
    """
    Attempts to extract UTR from bank narration.
    Try these patterns in order:
        Pattern 1: NEFT CR format
          r'NEFT\s+CR[:\s]+\S+\s+([A-Z0-9\-]{8,28})'
        Pattern 2: Generic alphanumeric after bank name
          r'(?:HDFC|SBI|ICICI|AXIS|KOTAK)\s+([A-Z0-9\-]{10,28})'
        Pattern 3: Any 12-digit numeric sequence
          r'\b(\d{12})\b'
    Normalize result: re.sub(r'[-\s]', '', utr).upper()
    Return normalized UTR string or None if no pattern matches.
    """
    if not narration:
        return None

    patterns = [
        r"NEFT\s+CR[:\s]+\S+\s+([A-Z0-9\-]{8,28})",
        r"(?:HDFC|SBI|ICICI|AXIS|KOTAK)\s+([A-Z0-9\-]{10,28})",
        r"\b(\d{12})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, narration, re.IGNORECASE)
        if match:
            raw_utr = match.group(1)
            normalized = normalize_utr(raw_utr)
            if normalized:
                return normalized

    return None


def classify_narration(narration: str) -> str:
    """
    Returns one of:
        "razorpay_credit"   if narration contains "RAZORPAY" (case-insensitive)
        "bank_charge"       if narration matches any charge keyword:
                            ["SMS", "ALERT", "CHARGE", "GST ON", "NEFT CHARGES",
                             "IMPS CHARGES", "SERVICE FEE", "ANNUAL FEE"]
        "upi_transfer"      if narration starts with "UPI/"
        "neft_transfer"     if narration starts with "NEFT" but not razorpay
        "unidentified"      if none of the above match
    Check in the order listed above.
    Case-insensitive matching throughout.
    """
    if not narration:
        return "unidentified"

    upper_narr = narration.strip().upper()

    # 1. Razorpay credit
    if "RAZORPAY" in upper_narr:
        return "razorpay_credit"

    # 2. Bank charge
    for kw in CHARGE_KEYWORDS:
        if kw in upper_narr:
            return "bank_charge"

    # 3. UPI transfer
    if upper_narr.startswith("UPI/"):
        return "upi_transfer"

    # 4. NEFT transfer (starts with NEFT but not razorpay)
    if upper_narr.startswith("NEFT"):
        return "neft_transfer"

    # 5. Unidentified
    return "unidentified"


if __name__ == "__main__":
    test_cases = [
        ("NEFT CR: HDFC 235689741234 RAZORPAY SETTLEMENT", "razorpay_credit", "235689741234"),
        ("NEFT CR: HDFC 235-689-741-234 RAZORPAY SETTLEMENT", "razorpay_credit", "235689741234"),
        ("SMS ALERT CHARGES Q2", "bank_charge", None),
        ("GST ON SMS CHARGES", "bank_charge", None),
        ("NEFT TRANSACTION CHARGES", "bank_charge", None),
        ("UPI/CR/235689741234/Someone/HDFC", "upi_transfer", "235689741234"),
        ("NEFT CR: SBI 999999999999 SOME TRANSFER", "neft_transfer", "999999999999"),
        ("UNKNOWN TRANSACTION", "unidentified", None),
    ]

    print(f"{'Narration':<45} {'Expected':<20} {'Got':<20} {'UTR':<15} {'Pass'}")
    print("-" * 110)
    all_pass = True
    for narration, expected_class, expected_utr in test_cases:
        got_class = classify_narration(narration)
        got_utr = extract_utr(narration)
        got_utr_norm = normalize_utr(got_utr) if got_utr else None
        class_pass = got_class == expected_class
        utr_pass = got_utr_norm == expected_utr
        overall = class_pass and utr_pass
        if not overall:
            all_pass = False
        print(f"{narration[:43]:<45} {expected_class:<20} {got_class:<20} {str(got_utr_norm):<15} {'passed' if overall else 'fail'}")

    print()
    print("UTR normalization test:")
    print(f"  235689741234 vs 235-689-741-234: {utrs_match('235689741234', '235-689-741-234')}")
    print(f"  RATN0000001 vs RATN-0000-001:    {utrs_match('RATN0000001', 'RATN-0000-001')}")
    print()
    print("All tests passed" if all_pass else "Some tests failed")
