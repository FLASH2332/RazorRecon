# AGENTS.md — RazorRecon Developer & Agent Guide

## Project Overview

RazorRecon is a financial reconciliation system for Razorpay merchants. It ingests three data sources (payments CSV, settlements CSV, bank statement CSV), runs a ReAct agent to match settlements against bank credits, and produces a verified reconciliation report with match rate, false match rate, and an honest exception list.

Stack: Python 3.11+, FastAPI, DuckDB, LiteLLM, Vanilla JS frontend.

---

## Repository Layout

```text
razorrecon/
├── api/
│   ├── main.py              FastAPI app, CORS, router registration
│   ├── routers/
│   │   ├── sessions.py      POST /sessions, GET /sessions/{id}/state
│   │   ├── upload.py        POST /sessions/{id}/upload/{source}
│   │   ├── reconcile.py     POST /sessions/{id}/reconcile, GET /status
│   │   └── report.py        GET /sessions/{id}/report and /summary
│   └── session/
│       └── manager.py       Session lifecycle, DuckDB file management
├── data/
│   └── generator/
│       ├── generate.py      Entry point: generate all dataset sizes
│       ├── payments.py      Generate payment rows
│       ├── settlements.py   Generate settlement rows from payments
│       ├── bank_statement.py Generate bank txn rows from settlements
│       ├── scenarios.py     Inject 4 edge case scenarios + ground truth
│       └── validate_config.py 6-check config validation script
├── engine/
│   ├── agent.py             ReAct loop, context management, orchestration
│   ├── verification.py      Deterministic verdict rules
│   ├── llm_client.py        Simple LiteLLM wrapper for non-agent calls
│   ├── pdf_parser.py        pdfplumber PDF→CSV (scaffolded)
│   ├── report.py            Report generation and metrics
│   └── tools/
│       ├── ingestion.py     DuckDB session init, CSV ingestion, state tracking
│       ├── query.py         Read-only DuckDB query tools
│       ├── compute.py       Fee math, combination search
│       ├── classify.py      Narration parsing, UTR extraction
│       ├── resolution.py    Verdict logging to reconciliation_log
│       └── registry.py      Tool wrappers, schemas, submit_verdict gate
├── frontend/
│   ├── index.html           Single page app
│   ├── dashboard.js         All client logic
│   └── style.css            Dark theme styles
├── tests/
│   ├── eval/
│   │   └── run_eval.py      Evaluation harness against ground truth
│   └── test_context_compression.py  Unit tests for agent helpers
├── sessions/                Runtime DuckDB files (gitignored)
├── data/sample/             Generated datasets (gitignored)
├── assets/                  Architecture diagram, screenshots
├── .env.example             Environment variable template
├── pyproject.toml           Dependency management
└── README.md                Project overview and quickstart
```

---

## Environment Variables

| Variable | Purpose | Valid Values / Format | Read By |
|----------|---------|-----------------------|---------|
| `LLM_MODEL` | Model string for LiteLLM provider routing | `groq/qwen/qwen3.6-27b`, `gemini/gemini-1.5-flash`, `ollama/qwen3:8b`, `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | `engine/agent.py`, `engine/llm_client.py` |
| `GROQ_API_KEY` | API key for Groq cloud inference | `gsk_...` string | LiteLLM |
| `GEMINI_API_KEY` | API key for Google Gemini | `AIza...` string | LiteLLM |
| `OPENROUTER_API_KEY` | API key for OpenRouter inference | `sk-or-...` string | LiteLLM |
| `OLLAMA_BASE_URL` | Ollama local server URL endpoint | URL string (default: `http://localhost:11434`) | LiteLLM |

---

## Data Layer

### DuckDB Session Model

Each user session gets its own isolated DuckDB file:
```text
sessions/{session_id}.duckdb
```

Never share a connection across sessions. Always call `get_db(session_id)` to get the connection for a given session. Close connections after use.

### Tables

- **`payments`**:
  - Columns: `order_id TEXT, amount DECIMAL(12,2), type TEXT ('payment'|'refund'), parent_order_id TEXT (null for payments), date DATE, status TEXT ('captured'|'failed'), method TEXT`
  - Purpose: Source of individual payment and refund records.

- **`settlements`**:
  - Columns: `settlement_id TEXT, entity_id TEXT, type TEXT, order_id TEXT, amount DECIMAL(12,2), credit DECIMAL(12,2), debit DECIMAL(12,2), fee DECIMAL(12,2), tax DECIMAL(12,2), net DECIMAL(12,2), settlement_utr TEXT, date DATE, method TEXT`
  - Purpose: Razorpay settlement export rows (one row per payment in batch).
  - Note: Multiple rows share the same `settlement_id`. Use `SUM(net)` to get the batch total.

- **`bank_txns`**:
  - Columns: `txn_id TEXT, date DATE, narration TEXT, utr TEXT, credit DECIMAL(12,2), debit DECIMAL(12,2), balance DECIMAL(12,2), classification TEXT ('razorpay_credit'|'bank_charge'|'upi_transfer'|'unidentified')`
  - Purpose: Bank statement rows after parsing and classification.

- **`ingestion_state`**:
  - Columns: `session_id TEXT, source TEXT, status TEXT, record_count INTEGER, loaded_at TIMESTAMP, notes TEXT`
  - Purpose: Tracks which files have been loaded per session.

- **`reconciliation_log`**:
  - Columns: `decision_id TEXT PRIMARY KEY, session_id TEXT, timestamp TIMESTAMP, record_id TEXT, strategies TEXT (JSON), tool_calls TEXT (JSON), verdict TEXT ('confirmed'|'ambiguous'|'unresolved'), evidence TEXT (JSON), competing TEXT (JSON), reasoning TEXT, model TEXT`
  - Purpose: Immutable audit trail of every agent decision.

- **`reconciliation_progress`**:
  - Columns: `session_id TEXT PRIMARY KEY, processed INTEGER, total INTEGER, current_id TEXT, status TEXT ('running'|'completed'|'failed'|'partial'), started_at TIMESTAMP, updated_at TIMESTAMP`
  - Purpose: Progress tracking for frontend polling.

### Invariants

- **Never delete from `reconciliation_log`** — it is an append-only audit trail.
- **Never share DuckDB connections across threads** — open and close per operation.
- **`reconciliation_progress` status only moves forward**: `running` $\rightarrow$ `completed` | `failed` | `partial`.
- **`verdict` field only accepts**: `confirmed` | `ambiguous` | `unresolved`.
- **Every `confirmed` verdict must have non-null evidence with `bank_txn_id`**.

---

## Financial Model

The settlement equation used everywhere in the codebase:

$$\text{settlement\_net} = \sum(\text{eligible\_payments}) - \sum(\text{refunds}) - \text{MDR} - \text{GST\_on\_MDR}$$

Where:
- $\text{MDR} = \text{gross} \times 0.02$ (2.0%)
- $\text{GST\_on\_MDR} = \text{MDR} \times 0.18$ (18% of MDR)
- $\text{TDS} = \text{gross} \times 0.01$ (1.0%, `marketplace_mode` only, default `False`)

Tolerance window:
$$\varepsilon = \max(10.0, \text{expected} \times 0.001)$$

This equation is implemented **ONLY** in `engine/tools/compute.py`: `calc_expected_settlement()`. No other file should compute fees. If fee logic changes, change it in one place only.

### Refunds
Refunds are negative rows in `payments` with `type='refund'` and `parent_order_id` pointing to the original payment. `get_refunds(order_id)` returns all linked refunds. Refund amounts are subtracted before MDR calculation.

---

## Agent Architecture

### Entry Point

`run_reconciliation(session_id)` in `engine/agent.py`:
1. `init_registry(session_id)` — binds all tools to this session.
2. `check_ingestion_state()` — scopes run to available sources.
3. `get_all_settlement_ids()` — ordered by date.
4. For each settlement: `run_settlement_investigation(session_id, settlement_id)`.
5. Sleep 3s between settlements (rate limit protection).
6. Flag orphan bank credits as `unresolved`.
7. Return `get_verdict_summary()`.

### Investigation Loop

`run_settlement_investigation(session_id, settlement_id)`:

State initialization:
```python
state = {
    "settlement_id": settlement_id,
    "tools_called": [],
    "verdict": None
}
last_tool_result = None
verdict_reached = False
```

Each iteration (up to `MAX_ITERATIONS = 8`):
1. Build `user_content` from `build_state_summary(state)` + `last_tool_result` serialized as JSON.
2. Build fresh `messages = [system_prompt, user_message]` (no growing conversation history).
3. Call LLM via `call_llm_with_retry()`.
4. Parse `tool_calls` from response.
5. For each tool call:
   - Parse arguments.
   - Execute via `TOOL_REGISTRY`.
   - `extract_key_result(tool_name, result)` $\rightarrow$ append to `state["tools_called"]`.
   - Set `last_tool_result = full result`.
   - If `tool_name == "submit_verdict"`: set `verdict_reached = True`.
6. If `verdict_reached`: break loop.

If `verdict_reached` is `False` after loop completes:
- Call `tool_mark_unresolved(strategies_tried=["max_iterations_exceeded"])`.

### Context Management

- **Problem:** Growing message history causes HTTP 413 payload errors and context drift.
- **Solution:** Rebuild context from scratch each iteration.

`extract_key_result(tool_name, result)` returns a compact dict:
- `calc_expected_settlement` $\rightarrow$ `{expected_bank_credit, tolerance}`
- `find_bank_match` $\rightarrow$ `{match_count, expected_amount, tolerance}`
- `find_settlement_combinations` $\rightarrow$ `{combination_count, valid_combinations}`
- `get_refunds` $\rightarrow$ `{"refund_count": len(result)}`
- `get_settlement_summary` $\rightarrow$ `{total_gross, total_net, total_refunds, payment_count}`
- `submit_verdict` $\rightarrow$ `{verdict, record_id}`
- *default* $\rightarrow$ `result` unchanged

`build_state_summary(state)` produces compact text:
```text
Steps completed so far:
 - tool_name: {key_result}
Verdict: not yet submitted
```

Token budget stays flat regardless of iteration count. Previous tool results are compressed to key fields only; only `last_tool_result` is passed in full.

### Verification Gate

The LLM calls `submit_verdict()`. It cannot call `mark_confirmed`, `mark_ambiguous`, or `mark_unresolved` directly.

`submit_verdict` flow:
- If `proposed_verdict == "confirmed"`:
  - Runs `verify_match(expected, actual, tolerance, match_count, competing_count)`
  - If verification passes: calls internal `mark_confirmed()`
  - If verification fails: overrides to `ambiguous` or `unresolved` based on failure reason
  - The LLM cannot bypass this check
- If `proposed_verdict == "ambiguous"` or `"unresolved"`:
  - Routes directly to internal `mark_ambiguous()` or `mark_unresolved()`

Verdict invariants (`engine/verification.py`):
- `CONFIRMED`: `match_count == 1` AND $|expected - actual| \le \varepsilon$ AND `competing_count == 0`
- `AMBIGUOUS`: `match_count > 1` OR `competing_count > 0`
- `UNRESOLVED`: `match_count == 0` OR amount outside tolerance

### System Prompt

`SYSTEM_PROMPT` in `engine/agent.py` defines the agent's behavior. If modifying the prompt:
- Never remove the instruction to always call `submit_verdict`.
- Never instruct the agent to skip tools.
- Never instruct the agent to guess amounts.
- Keep the numbered step sequence intact.
- Test changes on the `tiny` dataset before `medium`.

---

## Tool Reference

All tools the LLM can call are registered in `GROQ_TOOL_SCHEMAS` in `engine/tools/registry.py`. All tools the LLM cannot call are internal to `engine/tools/resolution.py`.

### Tools Exposed to LLM

- **`calc_expected_settlement(settlement_id)`**
  - Returns: `expected_bank_credit`, `tolerance`, `MDR`, `GST_on_MDR`, `total_gross`, `total_refunds`, `actual_net`
  - Source: `engine/tools/compute.py`
  - DuckDB: reads `settlements`, `payments`

- **`find_bank_match(settlement_id, date_window_days=5)`**
  - Returns: `match_count`, `matches[]`, `expected_amount`, `tolerance`
  - Source: `engine/tools/compute.py`
  - DuckDB: reads `bank_txns`
  - Note: returns all matches within amount $\pm \varepsilon$ and date window

- **`find_settlement_combinations(target_amount, tolerance=10.0, max_combo_size=3)`**
  - Returns: `combination_count`, `valid_combinations[]`
  - Source: `engine/tools/compute.py`
  - Note: subset-sum bounded by `max_combo_size=3` and date window
  - Warning: $O(n^2 / n^3)$ complexity — keep settlement batches under 100

- **`get_refunds(parent_order_id)`**
  - Returns: list of refund rows
  - Source: `engine/tools/query.py`
  - DuckDB: reads `payments WHERE type='refund' AND parent_order_id=?`

- **`get_settlement_summary(settlement_id)`**
  - Returns: `total_gross`, `total_fee`, `total_net`, `total_refunds`, `payment_count`, `refund_count`, `order_ids[]`
  - Source: `engine/tools/query.py`
  - DuckDB: aggregates settlement rows by `settlement_id`

- **`get_unmatched_bank_credits()`**
  - Returns: all `bank_txns WHERE classification='razorpay_credit'`
  - Source: `engine/tools/query.py`
  - Note: includes already-matched credits — agent decides relevance

- **`classify_narration(narration)`**
  - Returns: `'razorpay_credit' | 'bank_charge' | 'upi_transfer' | 'neft_transfer' | 'unidentified'`
  - Source: `engine/tools/classify.py`
  - Note: checks `RAZORPAY` keyword first, then charge keywords

- **`submit_verdict(record_id, proposed_verdict, evidence, competing, strategies_tried, reasoning)`**
  - Routes through verification gate
  - `evidence` must include: `expected_amount`, `actual_amount`, `tolerance`, `match_count`, `bank_txn_id` (or `null` if unresolved)
  - `competing`: `[]` if none, list of dicts if ambiguous
  - Returns: `{verdict, record_id, decision_id, timestamp}`
  - Source: `engine/tools/registry.py`

### Internal Tools (Not in GROQ_TOOL_SCHEMAS)

- **`mark_confirmed(session_id, record_id, evidence, strategies_tried, tool_calls, reasoning)`**
- **`mark_ambiguous(session_id, record_id, competing, strategies_tried, tool_calls, reasoning)`**
- **`mark_unresolved(session_id, record_id, strategies_tried, tool_calls, reasoning)`**
  - All write directly to `reconciliation_log`.
  - Source: `engine/tools/resolution.py`.
  - Note: only callable by `submit_verdict` or `engine/agent.py` fallback.

---

## API Reference

All endpoints are prefixed under `/sessions`:

- **`POST /sessions`**
  - Creates session, initializes DuckDB, returns `{session_id}`
- **`GET /sessions/{session_id}/state`**
  - Returns ingestion state for all sources + `reconciliation_scope`
- **`POST /sessions/{session_id}/upload/payments`**
  - Accepts: multipart CSV file
  - Returns: `{source, status, record_count, captured_count, notes}`
- **`POST /sessions/{session_id}/upload/settlements`**
  - Accepts: multipart CSV file
  - Returns: `{source, status, record_count, unique_batches, notes}`
- **`POST /sessions/{session_id}/upload/bank-statement`**
  - Accepts: multipart CSV or PDF file
  - PDF: routes through `engine/pdf_parser.py` (scaffolded — use CSV)
  - Returns: `{source, status, record_count, credit_rows, bank_charge_rows}`
- **`POST /sessions/{session_id}/reconcile`**
  - Starts `run_reconciliation()` in background thread
  - Returns immediately: `{"status": "started"}`
  - Updates `reconciliation_progress` during run
- **`GET /sessions/{session_id}/status`**
  - Returns: `{status, processed, total, current_settlement, percent, updated_at}`
  - Frontend polls this every 3s during reconciliation
- **`GET /sessions/{session_id}/report`**
  - Requires status in `('completed', 'partial')`
  - Returns: full report dict from `engine/report.py`
- **`GET /sessions/{session_id}/report/summary`**
  - Returns: summary section only `{confirmed, ambiguous, unresolved, total, match_rate, coverage}`

---

## Evaluation Harness

Location: `tests/eval/run_eval.py`

Usage:
```bash
python -m tests.eval.run_eval <session_id> <dataset_path>
# Example:
python -m tests.eval.run_eval abc12345 data/sample/medium
```

What it measures:
- `match_rate`: $\frac{\text{confirmed}}{\text{total}}$
- `false_match_rate`: $\frac{\text{wrong confirmed}}{\text{all confirmed}}$ (validates `evidence.bank_txn_id` against `settlement_bank_map`)
- `coverage`: $\frac{\text{confirmed}}{\text{confirmed} + \text{ambiguous} + \text{unresolved}}$
- `scenario_accuracy`: $\frac{\text{correctly handled scenarios}}{\text{total injected scenarios}}$

Output:
- Formats and displays terminal evaluation report.
- Saves report JSON to `sessions/{session_id}_eval.json`.

Ground truth source: `data/sample/{size}/ground_truth.json`
- `scenarios[]`: 4 injected scenarios with `expected_verdict`.
- `settlement_bank_map`: `{settlement_id: expected_bank_txn_id}`.

Regenerate ground truth if generator is changed:
```bash
python -m data.generator.generate all
```

---

## Synthetic Data Generator

Entry point: `python -m data.generator.generate {size|all}`  
Sizes: `tiny` (15 payments), `small` (60), `medium` (200), `large` (500).

Pipeline:
1. `generate_payments(n, seed, date_window_days)` $\rightarrow$ payments list with 5% failure rate.
2. `generate_settlements(payments)` $\rightarrow$ one settlement batch per day with captured payments. Fee calculation: $\text{MDR}=2\%$, $\text{GST}=18\%\text{ of MDR}$. Settlement date = payment date + 2 business days.
3. `generate_bank_statement(settlements)` $\rightarrow$ one bank credit per settlement batch. Narration format: `"NEFT CR: HDFC {utr} RAZORPAY SETTLEMENT"`. Appends 3 noise rows: SMS charge, GST on SMS, NEFT charge. Returns `(bank_rows, settlement_bank_map)`.
4. `inject_scenarios(payments, settlements, bank, settlement_bank_map)` $\rightarrow$
   - Scenario 1: `partial_refund` — 30% refund on random captured payment.
   - Scenario 2: `malformed_utr` — dashes injected into narration UTR.
   - Scenario 3: `bank_batching` — two settlements merged into `TXN_COMBINED`.
   - Scenario 4: `orphan_credit` — `TXN_ORPHAN` added with no settlement.
   - Updates `settlement_bank_map` accordingly.
   - Returns `(payments_messy, settlements_messy, bank_messy, ground_truth, updated_map)`.

`ground_truth.json` contains:
- `scenarios[]`: `expected_verdict` per scenario.
- `settlement_bank_map`: `{settlement_id: expected_bank_txn_id}`.

---

## Frontend

Single-page application with no external build step or framework dependencies.

Three primary UI states:
1. **Upload State:**
   - On load: `POST /sessions` $\rightarrow$ stores `session_id`.
   - Per file: `POST /upload/{source}` $\rightarrow$ upload cards turn green on success.
   - Start button: enabled once all 3 sources are loaded.
   - On click: `POST /reconcile` $\rightarrow$ transitions to Progress state.
2. **Progress State:**
   - Polls `GET /status` every 3s.
   - Shows live progress bar, completion percentage, and current settlement ID.
   - When status reaches `'completed'` or `'partial'`: calls `GET /report` $\rightarrow$ transitions to Results state.
3. **Results State:**
   - 4 summary KPI cards: confirmed, ambiguous, unresolved, match rate.
   - 3 decision tabs: Confirmed | Ambiguous | Unresolved.
   - Tables display matched amounts, candidate counts, and full reasoning traces.
   - Excluded bank charge rows displayed in collapsible accordion.
   - "Start New Reconciliation" reloads page to restart.

Debug Bar: DEV session loader input allows loading existing historical sessions directly.

---

## Development Commands

```bash
# Generate test datasets
python -m data.generator.generate all

# Validate generator configuration
python -m data.generator.validate_config

# Run FastAPI backend
uvicorn api.main:app --reload --port 8000

# Test agent tools
python -m engine.tools.registry
python -m engine.tools.compute
python -m engine.verification

# Run agent investigation on tiny dataset
python -m engine.agent data/sample/tiny

# Run evaluation harness
python -m tests.eval.run_eval <session_id> data/sample/medium

# Run context compression tests
python tests/test_context_compression.py
```

---

## Coding Conventions

1. **Session Binding:** All tools take `session_id` as their first argument in raw form. Session-bound closures in `engine/tools/registry.py` hide `session_id` from the LLM.
2. **No Arithmetic in LLM:** The LLM never computes arithmetic. If a financial calculation is required, it must call a tool. Tools execute exact math in DuckDB or Python.
3. **Connection Lifecycle:** All DuckDB connections are opened and closed per function call. Never keep a database connection open across tool calls.
4. **Append-Only Log:** `reconciliation_log` is immutable and append-only. Never update or delete rows.
5. **Tool Schema Alignment:** `GROQ_TOOL_SCHEMAS` governs what tools the LLM can call. When adding a tool: add wrapper in `registry.py` + add schema definition + write test. When removing a tool: remove from `GROQ_TOOL_SCHEMAS` first, then remove wrapper.
6. **Single Verification Authority:** The verification gate (`engine/verification.py`) is the single source of verdict truth. Never embed verdict decision logic elsewhere.
7. **Single Source of Fee Logic:** The financial settlement equation lives in `engine/tools/compute.py` only. Never duplicate fee math across other files.
