# quick test
from engine.agent import extract_key_result, build_state_summary

r = extract_key_result("find_bank_match", {
    "match_count": 1,
    "expected_amount": 22851.34,
    "tolerance": 22.85,
    "matches": [{"txn_id": "TXN_001", "credit": 22851.34}]
})
print(r)
# Expected: {"match_count": 1, "expected_amount": 22851.34, "tolerance": 22.85}

state = {
    "settlement_id": "SETL_001",
    "tools_called": [
        {"tool": "calc_expected_settlement", "key_result": {"expected_bank_credit": 22851.34}},
        {"tool": "find_bank_match", "key_result": {"match_count": 0}}
    ],
    "verdict": None
}
print(build_state_summary(state))