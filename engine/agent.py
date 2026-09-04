from datetime import datetime
import json
import os
import time
from dotenv import load_dotenv
from groq import Groq, RateLimitError
import sys

from engine.tools.ingestion import check_ingestion_state, get_db
from engine.tools.query import get_all_settlement_ids
from engine.tools.resolution import get_verdict_summary, get_decisions
from engine.tools.registry import (
    init_registry,
    TOOL_REGISTRY,
    GROQ_TOOL_SCHEMAS,
    tool_mark_unresolved,
    tool_get_unmatched_bank_credits,
    tool_classify_narration,
)

load_dotenv()

MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MAX_ITERATIONS = 8

client = Groq(api_key=GROQ_API_KEY or "your_key_here")


def call_groq_with_retry(messages, tools, max_retries=5):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and attempt < max_retries - 1:
                wait = (2 ** attempt) * 10
                print(f"\n    Rate limited, waiting {wait}s...", end=" ", flush=True)
                time.sleep(wait)
            elif "400" in err_str and "parse" in err_str.lower() and attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                print(f"\n    Parse error, retrying in {wait}s...", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise


SYSTEM_PROMPT = """
You are a financial reconciliation investigator for Razorpay settlements.

Your job: investigate each settlement, gather evidence using tools,
and submit a verdict using submit_verdict.

Investigation steps:
1. Call calc_expected_settlement → get expected bank credit amount and tolerance
2. Call find_bank_match → search for matching bank credits by amount and date
3. Examine match_count in result:
   - match_count == 1 AND amount within tolerance:
       call submit_verdict with proposed_verdict="confirmed"
       include bank_txn_id, expected_amount, actual_amount, tolerance, match_count in evidence
   - match_count > 1:
       call submit_verdict with proposed_verdict="ambiguous"
       list all matching txn_ids in competing
   - match_count == 0:
       proceed to step 4
4. Call find_settlement_combinations with the unmatched bank credit amounts
   - combination_count == 1:
       call submit_verdict with proposed_verdict="confirmed"
       include combined settlement ids in evidence
   - combination_count > 1:
       call submit_verdict with proposed_verdict="ambiguous"
   - combination_count == 0:
       proceed to step 5
5. Call get_refunds for each order in this settlement to check partial refunds
6. If still no match after all steps:
   call submit_verdict with proposed_verdict="unresolved"
   explain in reasoning what you tried and why nothing matched

Critical rules:
- Never guess amounts — always use tool results
- Never skip submit_verdict — every investigation must end with it
- If evidence is contradictory or multiple matches exist: always use ambiguous
- Include specific amounts and txn_ids in reasoning and evidence
- The verification system may override your proposed verdict if evidence is insufficient
"""


def run_settlement_investigation(
    session_id: str,
    settlement_id: str,
) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Investigate settlement {settlement_id}. "
                f"Use tools to gather evidence and submit your verdict."
            ),
        },
    ]

    verdict_reached = False

    for iteration in range(MAX_ITERATIONS):
        if len(messages) > 10:
            messages = messages[:2] + messages[-6:]

        response = call_groq_with_retry(messages, GROQ_TOOL_SCHEMAS)

        message = response.choices[0].message

        if not message.tool_calls:
            break

        # Append assistant message
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ],
        })

        # Execute each tool call
        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, ValueError):
                tool_args = {}

            if tool_name in TOOL_REGISTRY:
                try:
                    tool_result = TOOL_REGISTRY[tool_name](**tool_args)
                except Exception as e:
                    tool_result = {"error": str(e)}
            else:
                tool_result = {"error": f"Unknown tool: {tool_name}"}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result, default=str),
            })

            if tool_name == "submit_verdict":
                verdict_reached = True

        if verdict_reached:
            break

    # Force unresolved if max iterations exceeded
    if not verdict_reached:
        tool_mark_unresolved(
            record_id=settlement_id,
            strategies_tried=["max_iterations_exceeded"],
            reasoning=f"Agent reached {MAX_ITERATIONS} iterations without submitting a verdict.",
        )

    # Return verdict from log
    decisions = get_decisions(session_id)
    for d in reversed(decisions):
        if d["record_id"] == settlement_id:
            return d
    return {
        "record_id": settlement_id,
        "verdict": "unresolved",
        "reason": "no decision logged",
    }


def run_reconciliation(session_id: str) -> dict:
    init_registry(session_id)

    # Step 1: Check state
    state = check_ingestion_state(session_id)
    if state["reconciliation_scope"] == "none":
        return {"error": "No data uploaded", "status": "failed"}

    scope = state["reconciliation_scope"]
    print(f"Scope: {scope}")

    # Step 2: Get settlements
    settlement_ids = get_all_settlement_ids(session_id)
    if not settlement_ids:
        return {"error": "No settlements found", "status": "failed"}

    total = len(settlement_ids)
    print(f"Processing {total} settlements...")

    conn = get_db(session_id)
    conn.execute("""
        INSERT OR REPLACE INTO reconciliation_progress
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [session_id, 0, total, None, 'running',
          datetime.utcnow(), datetime.utcnow()])
    conn.close()

    # Step 3: Investigate each settlement
    for i, settlement_id in enumerate(settlement_ids):
        print(f"  [{i+1}/{total}] {settlement_id}...", end=" ", flush=True)
        verdict = run_settlement_investigation(session_id, settlement_id)
        print(verdict.get("verdict", "unknown"), flush=True)
        conn = get_db(session_id)
        conn.execute("""
            INSERT OR REPLACE INTO reconciliation_progress
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [session_id, i+1, total, settlement_id, 'running',
              conn.execute("SELECT started_at FROM reconciliation_progress WHERE session_id=?",
              [session_id]).fetchone()[0], datetime.utcnow()])
        conn.close()
        time.sleep(3)

    # Step 4: Flag orphan bank credits
    all_decisions = get_decisions(session_id)
    confirmed_bank_txns = set()
    for d in all_decisions:
        if d["verdict"] == "confirmed" and d["evidence"]:
            try:
                evidence = (
                    d["evidence"]
                    if isinstance(d["evidence"], dict)
                    else json.loads(d["evidence"])
                )
                if "bank_txn_id" in evidence:
                    confirmed_bank_txns.add(evidence["bank_txn_id"])
                if "bank_txn_ids" in evidence:
                    confirmed_bank_txns.update(evidence["bank_txn_ids"])
            except Exception:
                pass

    bank_credits = tool_get_unmatched_bank_credits()
    orphan_count = 0
    for credit in bank_credits:
        if credit["txn_id"] not in confirmed_bank_txns:
            classification = tool_classify_narration(credit["narration"])
            if classification == "razorpay_credit":
                tool_mark_unresolved(
                    record_id=credit["txn_id"],
                    strategies_tried=["orphan_check"],
                    reasoning=(
                        f"Bank credit of {credit['credit']} on {credit['date']} "
                        f"has no corresponding settlement. Possible delayed payout, "
                        f"duplicate, or bank error. Requires human review."
                    ),
                )
                orphan_count += 1

    if orphan_count:
        print(f"  {orphan_count} orphan bank credit(s) flagged")

    conn = get_db(session_id)
    conn.execute("""
        INSERT OR REPLACE INTO reconciliation_progress
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [session_id, total, total, None, 'completed',
          conn.execute("SELECT started_at FROM reconciliation_progress WHERE session_id=?",
          [session_id]).fetchone()[0], datetime.utcnow()])
    conn.close()

    # Step 5: Report
    summary = get_verdict_summary(session_id)
    return {
        "session_id": session_id,
        "scope": scope,
        "confirmed": summary["confirmed"],
        "ambiguous": summary["ambiguous"],
        "unresolved": summary["unresolved"],
        "total": summary["total"],
        "match_rate": summary["match_rate"],
        "coverage": summary["coverage"],
        "status": "completed",
    }


if __name__ == "__main__":
    import uuid
    from engine.tools.ingestion import get_db, init_schema
    from engine.tools.ingestion import ingest_payments, ingest_settlements, ingest_bank

    session_id = str(uuid.uuid4())[:8]
    conn = get_db(session_id)
    init_schema(conn)
    conn.close()

    base = sys.argv[1] if len(sys.argv) > 1 else "data/sample/small"
    ingest_payments(session_id, f"{base}/payments_messy.csv")
    ingest_settlements(session_id, f"{base}/settlements_messy.csv")
    ingest_bank(session_id, f"{base}/bank_statement_messy.csv")

    print(f"Session: {session_id}")
    print("Running reconciliation agent...")
    result = run_reconciliation(session_id)
    print()
    print("=== FINAL REPORT ===")
    print(json.dumps(result, indent=2))
