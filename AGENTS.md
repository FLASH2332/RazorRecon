# RazorRecon — AI Finance Controller

> Razorpay AI Buildathon 2026 · Track 4: AI Finance Controller  

---

## The Problem

Every merchant on Razorpay receives money through a chain of three systems:

```
Customer pays → Razorpay batches & settles → Bank account receives
```

Each system maintains its own record of this money movement. At the end of the month, a merchant has three sources of truth that should all agree — but never do cleanly:

| Source | Format | What it records |
|--------|--------|-----------------|
| `payments.csv` | Structured CSV | Individual order transactions from Razorpay |
| `settlements.csv` | Structured CSV | Batched payouts after MDR and TDS deductions |
| `bank_statement.pdf` | Unstructured PDF | Actual credits that landed in the bank account |

None of these sources speak the same language:

- A single settlement bundles 20–50 individual payments
- Settlement amounts differ from payment totals due to MDR (2%), GST on MDR (18% of MDR), and TDS (1%) deductions
- Bank credits arrive T+2 to T+5 days after settlement
- Bank narrations are free-text UPI strings (`UPI/CR/235689741234/Razorpay Software/RATN0000001`) with no direct settlement reference
- Bank statements contain noise — SMS charges, GST on bank fees, NEFT charges — with no corresponding payment record

Today, a human accountant downloads all three files, opens them side by side in Excel, and manually cross-references every record. For a merchant doing ₹50L/month across 500+ transactions this is a full day's work every month — error-prone, unaudited, and entirely manual.

This is not a niche problem. Every business on Razorpay does this by hand, every month.

---

## Why This Is Hard to Automate

The naive solution — match by amount — fails immediately:

```
payments:    ORD001 ₹10,000 + ORD002 ₹15,000 = ₹25,000 gross
settlement:  SETL_A ₹24,255  ← fees deducted, not ₹25,000
bank credit: ₹24,255 on Aug 3 ← but which settlement is this?
```

The matching problem has five compounding difficulties:

**One-to-many relationships.** One settlement covers N payments. No direct foreign key exists between them — the mapping must be inferred.

**Amount transformation.** Fees make direct equality impossible. The reconciler must reconstruct the settlement equation from scratch for each candidate match.

**No shared identifier.** Payments use Razorpay order IDs. Settlements use settlement IDs. Bank records use UTR numbers buried in free-text narrations. Joining across sources requires extraction and normalization.

**Timing offsets.** Payment on Aug 1 → settlement on Aug 3 → bank credit on Aug 5. Date-exact matching fails; window-based matching introduces false positives.

**Genuine ambiguity.** A ₹24,255 bank credit could correspond to SETL_A alone, or to SETL_X + SETL_Y if they were batched by the bank. The data alone does not always determine the correct answer.

A deterministic rule engine handles the happy path. It cannot handle genuine ambiguity — when two valid explanations exist, a rule engine must pick one. In financial reconciliation, a confident wrong match is worse than an honest "I cannot determine this."

---

## The Core Insight

> **The LLM is not the source of financial truth. It is the investigator.**
>
> It decides what evidence to gather and which investigation path to pursue. Deterministic tools perform every financial calculation and candidate search. A verification layer decides whether the evidence is sufficient to mark a transaction confirmed. If the evidence is insufficient or contradictory, the system abstains and sends it to human review.

This separation is deliberate and load-bearing:

- LLM does **reasoning and strategy** — not arithmetic
- Tools do **computation** — deterministic, auditable, exact
- Verification layer does **conclusion gating** — no guess when evidence is ambiguous

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (HTML/JS)                      │
│   File upload · Reconciliation dashboard · Q&A chat          │
│   Decision trace per record · Exception review table         │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP/REST
┌───────────────────────────▼─────────────────────────────────┐
│                    API Layer (FastAPI/Python)                 │
│                                                              │
│   POST /upload/payments        POST /upload/settlements      │
│   POST /upload/bank-statement  POST /reconcile/{session_id}  │
│   POST /qa/{session_id}        GET  /report/{session_id}     │
│                                                              │
│   Session management · File handling · Async endpoints       │
└───────────────────────────┬─────────────────────────────────┘
                            │ direct Python call
┌───────────────────────────▼─────────────────────────────────┐
│                    Agent Core (Python)                       │
│                                                              │
│   ReAct Loop: Reason → Select tool → Execute → Observe       │
│                                                              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │              Investigation Tools                     │   │
│   │                                                      │   │
│   │  Ingestion          Querying         Computation     │   │
│   │  ─────────          ────────         ───────────     │   │
│   │  ingest_payments    query_payments   sum_payments    │   │
│   │  ingest_settle      query_settle     calc_expected   │   │
│   │  parse_bank_pdf     query_bank       find_combos     │   │
│   │  check_state        get_refunds      classify_narr   │   │
│   │                                      extract_utr     │   │
│   │  Resolution                                          │   │
│   │  ──────────                                          │   │
│   │  mark_confirmed(record_id, evidence)                 │   │
│   │  mark_ambiguous(record_id, competing_explanations)   │   │
│   │  mark_unresolved(record_id, strategies_exhausted)    │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │              Verification Layer                      │   │
│   │                                                      │   │
│   │  sufficient + consistent   → CONFIRMED               │   │
│   │  sufficient + contradictory → AMBIGUOUS → human      │   │
│   │  insufficient evidence      → UNRESOLVED → human     │   │
│   └─────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────┘
                            │ DuckDB per session
┌───────────────────────────▼─────────────────────────────────┐
│                     Data Layer (DuckDB)                      │
│                                                              │
│   payments · settlements · bank_txns · reconciliation_log    │
│   ingestion_state                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Financial Model

Every reconciliation calculation in the system uses this equation:

```
settlement_amount =
    Σ(eligible_payment_amounts)
  - Σ(refund_amounts for eligible payments)
  - MDR                    (2.0% of gross)
  - GST_on_MDR             (18% of MDR)
  - TDS                    (1.0% of gross, marketplace merchants only)

# Example: ₹10,000 transaction (standard D2C merchant, no TDS)
# MDR = ₹200, GST on MDR = ₹36, net settlement = ₹9,764

tolerance ε = max(₹10, 0.1% of expected_settlement)
```

This equation is defined once — in the synthetic data generator — and referenced by the agent's `calc_expected_settlement()` tool. The LLM never computes this. It calls the tool and receives the result.

**TDS note:** TCS under Section 52 of CGST Act applies only to merchants operating as e-commerce operators/marketplaces. Direct-to-consumer merchants using Razorpay as a payment gateway are not subject to TDS deduction from settlements. The generator uses a `marketplace_mode` flag to toggle TDS inclusion.

**Refund representation:** Refunds are negative-amount rows in the settlement report against the original `order_id`. They are deducted from future settlement batches, not paid out separately. The `get_refunds(order_id)` tool returns all refund rows linked to a given order.

---

## Data Schema

### payments.csv
```
order_id        TEXT PRIMARY KEY
amount          DECIMAL(12,2)      -- gross payment amount
type            TEXT               -- 'payment' | 'refund'
parent_order_id TEXT               -- populated for refunds only
date            DATE
status          TEXT               -- 'captured' | 'refunded' | 'failed'
utr             TEXT               -- may be null for older records
```

### settlements.csv
```
settlement_id   TEXT               -- batch identifier (setl_XXXXXXXX)
entity_id       TEXT               -- payment_id or refund_id
type            TEXT               -- 'payment' | 'refund'
order_id        TEXT               -- merchant order reference
amount          DECIMAL(12,2)      -- gross transaction amount
credit          DECIMAL(12,2)      -- amount credited (payments)
debit           DECIMAL(12,2)      -- amount debited (refunds)
fee             DECIMAL(12,2)      -- MDR + GST on MDR combined
tax             DECIMAL(12,2)      -- GST on MDR component specifically
net             DECIMAL(12,2)      -- credit - fee (per transaction)
settlement_utr  TEXT               -- correspondent-bank-issued UTR
date            DATE               -- settlement date
method          TEXT               -- 'card' | 'upi' | 'netbanking'
```

Note: one settlement_id maps to multiple rows (one per payment/refund). The settlement total = SUM(net) across all rows sharing a settlement_id.

### bank_txns (parsed from PDF)
```
txn_id          TEXT PRIMARY KEY   -- generated during parse
date            DATE
narration       TEXT               -- raw bank narration string
utr             TEXT               -- extracted from narration, may be null
credit          DECIMAL(12,2)
debit           DECIMAL(12,2)
balance         DECIMAL(12,2)
classification  TEXT               -- 'razorpay_credit' | 'bank_charge' | 'upi_transfer' | 'unidentified'
```

### ingestion_state
```
session_id      TEXT
source          TEXT               -- 'payments' | 'settlements' | 'bank'
status          TEXT               -- 'loaded' | 'missing' | 'parse_error' | 'schema_remapped'
record_count    INTEGER
loaded_at       TIMESTAMP
notes           TEXT               -- schema remap details, parse warnings
```

### reconciliation_log
```
decision_id     TEXT PRIMARY KEY
session_id      TEXT
timestamp       TIMESTAMP
record_id       TEXT               -- settlement_id or bank txn_id
strategies      TEXT               -- JSON array of strategies attempted in order
tool_calls      TEXT               -- JSON array of {tool, input, result}
verdict         TEXT               -- 'confirmed' | 'ambiguous' | 'unresolved'
evidence        TEXT               -- JSON: matched records and amounts used
competing       TEXT               -- JSON: competing explanations if ambiguous
reasoning       TEXT               -- LLM's stated reasoning (verbatim)
model           TEXT               -- model name and version used
```

---

## Agent Behavior — Step by Step

### Phase 1: State Check
```
check_ingestion_state()
→ determines available sources
→ scopes reconciliation to what is present
→ records what will be skipped and why
```

### Phase 2: Settlement → Bank Matching (per settlement)
```
For each settlement:
  Strategy 1: settlement_id + amount + date match
    query_bank(amount=settlement.amount, date_range=[T+1 to T+5])
    if single match → verification layer → CONFIRMED
    if UTR available: cross-check narration contains settlement reference
    # Note: UTR in bank statement is correspondent-bank-issued,
    # not Razorpay-issued. settlement_id + net amount is the reliable key.

  Strategy 2: Amount + date window match
    calc_expected_settlement(settlement_id)
    query_bank(amount_range=[expected±ε], date_range=[T+1 to T+5])
    if single match → verification layer → CONFIRMED
    if multiple matches → AMBIGUOUS → human review

  Strategy 3: Combination match (bank batched multiple settlements)
    find_combos(target=bank_credit, max_size=3, date_window=T+5)
    if unique combination → verification layer → CONFIRMED
    if multiple combinations → AMBIGUOUS → human review

  Strategy 4: exhausted
    mark_unresolved(settlement_id, strategies_attempted=[1,2,3])
```

### Phase 3: Payment → Settlement Matching (per payment)
```
For each payment not yet linked to a confirmed settlement:
  query_settlements covering this payment's date range
  calc_expected_settlement() for candidate settlements
  check if payment_amount fits within candidate's coverage
  if fit found → link payment to settlement
  else → mark_unresolved(order_id)
```

### Phase 4: Bank Noise Classification
```
For remaining unmatched bank rows:
  classify_narration(narration)
  → 'bank_charge': exclude from reconciliation, report total separately
  → 'razorpay_credit': orphan credit, flag as unresolved with amount
  → 'unidentified': flag for human review
```

### Phase 5: Report Compilation
```
confirmed_matches     → list with full evidence chain
ambiguous_records     → list with competing explanations
unresolved_records    → list with strategies attempted
bank_charges_excluded → total with breakdown
match_rate            → confirmed / total_attempted
false_match_rate      → measured against synthetic ground truth
coverage              → confirmed / (confirmed + ambiguous + unresolved)
```

---

## Verification Layer — Decision Rules

The verification layer is deterministic. The LLM does not decide verdicts — it only decides which evidence to gather. The verification layer applies these rules to the gathered evidence:

```
CONFIRMED requires:
  - at least one strategy succeeded
  - evidence is internally consistent (amounts balance within ε)
  - no competing explanation exists

AMBIGUOUS requires:
  - at least one strategy produced a candidate match
  - but two or more competing explanations exist that cannot be distinguished
  - system abstains, sends to human with all competing options listed

UNRESOLVED requires:
  - all three strategies exhausted
  - no candidate match found
  - system reports which strategies were tried and what they returned
```

**The system never guesses.** Ambiguity is a first-class output, not a failure mode.

---

## Partial Data Handling

The agent scopes itself to whatever is uploaded. It never fails silently.

| payments | settlements | bank PDF | Agent behavior |
|----------|-------------|----------|----------------|
| ✅ | ✅ | ✅ | Full reconciliation — all three hops |
| ✅ | ✅ | ❌ | Payments → settlements only. Bank verification skipped, noted |
| ✅ | ❌ | ✅ | Payments → bank direct matching. Settlement layer skipped, noted |
| ❌ | ✅ | ✅ | Settlements → bank only. Payment linkage skipped, noted |
| ✅ | ❌ | ❌ | Summary only. Reconciliation requires at least two sources |
| ❌ | ❌ | ✅ | Bank statement parsed and summarized. Upload CSVs to reconcile |
| Any | Any | parse_fail | Graceful fallback. Error reason stated. Continues with CSVs |
| malformed | Any | Any | Schema inference attempted. User confirms column remapping |

Every report includes an explicit section: "What was skipped and why."

---

## PDF Parsing

**Supported:** Text-based bank statement PDFs exported from bank portals (SBI, HDFC, ICICI, Axis, Kotak). These contain a selectable text layer that pdfplumber can extract reliably.

**Not supported:** Scanned/image PDFs, password-protected PDFs. The system returns a clear error with instructions to export as text PDF.

**Parser pipeline:**
```
pdfplumber → extract table rows
  → per row: extract_utr(narration) via regex
  → classify_narration(narration) → razorpay_credit | bank_charge | upi_transfer | unidentified
  → normalize amounts (handle Indian number formatting: 1,00,000)
  → load into bank_txns table

if pdfplumber returns empty:
  → fallback: camelot lattice mode
  → if camelot fails: return parse_error with reason
```

**Bank narration format (Razorpay settlements):**
```
NEFT CR: [BANK_NAME] [UTR] RAZORPAY SETTLEMENT
Example: NEFT CR: HDFC 235689741234AB RAZORPAY SETTLEMENT
```

**Narration parsing:**
```python
# NEFT format — Razorpay settlements always arrive as NEFT credits
utr = re.search(r'NEFT CR[:\s]+\S+\s+([A-Z0-9]{12,22})', narration, re.IGNORECASE)

# Normalization for variant formats across banks
utr_normalized = re.sub(r'[-\s]', '', raw_utr).upper() if raw_utr else None

# Classification: if narration contains RAZORPAY → razorpay_credit
# else if matches charge keywords → bank_charge
# else → unidentified
```

The UTR extracted from narration is a secondary reference only. Primary match is settlement_id + amount + date. UTR cross-check adds confidence but is not required for CONFIRMED verdict.

---

## Security Scope

**Data handling:**
- Each session gets an isolated DuckDB file at `sessions/{session_id}.duckdb`
- Session files are deleted on expiry (configurable, default 1 hour)
- No data is persisted beyond session lifetime

**What reaches the LLM:**
- Tool results (structured JSON) — never raw CSV rows or PDF text
- The agent's system prompt and task description
- Reconciliation state as structured JSON

**What never reaches the LLM:**
- Raw bank statement text
- Raw CSV file contents
- UTR numbers in bulk (only in tool results for specific queries)

**Prompt injection mitigation:**
- PDF and CSV contents are treated as data only, never inserted into the system prompt
- Tool inputs are schema-validated before execution
- The agent cannot modify its own instructions or tool definitions
- Financial state changes (mark_confirmed, mark_ambiguous, mark_unresolved) require a structured evidence object — free-text reasoning alone cannot trigger them

**Explicitly out of scope for hackathon:**
- Authentication and authorization
- Rate limiting
- Encrypted storage
- Multi-tenancy at scale

These are noted as production requirements, not present in the demo.

---

## Evaluation Metrics

Measured against synthetic ground truth (the generator knows the correct answer for every record):

| Metric | Definition | Why it matters |
|--------|-----------|----------------|
| Match rate | confirmed / total attempted | Primary throughput metric |
| Precision | correct confirmed / all confirmed | Are confirmed matches actually correct? |
| False-match rate | wrong confirmed / all confirmed | **Primary safety metric** — a wrong match is worse than no match |
| Coverage | confirmed / (confirmed + ambiguous + unresolved) | How much did the agent resolve? |
| Exception precision | genuinely needed review / flagged for review | Are exceptions real or over-flagged? |
| Runtime | seconds per batch | Throughput |
| LLM calls | count per batch | Cost proxy |

**False-match rate is the headline safety metric.** In financial reconciliation, "I don't know" is always safer than a confident wrong answer.

---

## Synthetic Dataset

The generator produces three dataset sizes:

| Size | Payments | Settlements | Bank txns | Use |
|------|----------|-------------|-----------|-----|
| Small | 50 | 8 | 23 | Development and demo |
| Medium | 200 | 32 | 90 | Benchmarking |
| Large | 500 | 80 | 220 | Scale testing |

**Injected scenarios (randomized, not fixed):**

| Scenario | Description | Expected agent behavior |
|----------|-------------|------------------------|
| Clean match | UTR matches exactly | CONFIRMED via Strategy 1 |
| Amount match | UTR missing, amount+date match | CONFIRMED via Strategy 2 |
| Bank batching | Bank combined two settlements | CONFIRMED via Strategy 3 |
| Malformed UTR | Formatting mismatch, normalization required | CONFIRMED after normalization |
| Partial refund | Payment partially refunded mid-cycle | CONFIRMED after refund subtraction |
| Orphan credit | Bank credit with no settlement | UNRESOLVED — flagged |
| Ambiguous combos | Two settlement combinations = same bank amount | AMBIGUOUS — abstains |
| Bank charges | SMS/GST/NEFT fees | Excluded, reported separately |
| Late settlement | Arrives T+6 instead of T+5 | UNRESOLVED — outside window, flagged |
| Schema mismatch | CSV with non-standard column names | Schema remapped, user confirms |

**Benchmark leakage mitigation:** Generator parameters (amounts, dates, UTRs, scenario distribution) are randomized independently of agent logic. A second dataset is generated after implementation is locked and used as the held-out evaluation set. Agent is not tuned against the held-out set.

---

## Cases Where Rules Fail, Agent Handles

These three cases justify the agent architecture over a pure rule engine:

**Case 1 — Ambiguous bank batching**
```
SETL_A = ₹9,700   SETL_B = ₹14,550   SETL_C = ₹24,250
Bank credit: ₹24,250

Rule engine: matches SETL_C immediately (exact amount) — correct by accident
Alternative: SETL_A + SETL_B = ₹24,250 also valid

Harder version: SETL_C doesn't exist. Only SETL_A + SETL_B = ₹24,250.
Rule engine: no exact match → fails
Agent: find_combos() → finds SETL_A + SETL_B → CONFIRMED

Even harder: SETL_A + SETL_B = SETL_D + SETL_E = ₹24,250
Rule engine: picks first → wrong 50% of the time
Agent: detects competing explanations → AMBIGUOUS → human review
```

**Case 2 — Malformed UTR normalization**
```
Settlement UTR: RATN0000001234
Bank narration: UPI/CR/235689741234/Razorpay/RATN-0000-001234

Rule engine: string match fails → falls through to amount+date
Agent: extract_utr() → normalize → RATN0000001234 = RATN0000001234 → CONFIRMED via Strategy 1
Difference: Strategy 1 succeeds, no ambiguity from multiple amount matches
```

**Case 3 — Partial refund mid-cycle**
```
ORD001: ₹10,000 payment on Aug 1
ORD001-R: ₹2,000 refund on Aug 2
Settlement SETL_A: ₹7,644 on Aug 3

Rule engine: sum(ORD001) * fees = ₹9,702 ≠ ₹7,644 → mismatch → fails
Agent: get_refunds(ORD001) → finds ₹2,000 refund
       calc_expected(ORD001, include_refunds=True) → (10000-2000)*0.98*0.99*... = ₹7,644
       → CONFIRMED
```

---

## Settlement Q&A

After reconciliation completes, the merchant can query the report in natural language.

**Architecture:**
```
User query
  → LLM decides which tools to call
  → tools query reconciliation_log and source tables
  → LLM verbalizes tool results
  → every financial figure cited must reference a tool result
  → if no tool result supports a claim → "Insufficient data to answer"
```

The Q&A agent cannot hallucinate financial figures — it can only report what tools returned. If asked something the data cannot answer, it says so explicitly.

**Example:**
```
Q: "Why did I receive ₹7,644 for ORD001 instead of ₹9,702?"

Agent calls:
  get_refunds("ORD001") → ₹2,000 refund on Aug 2
  calc_expected("ORD001", include_refunds=True) → ₹7,644

Response:
  "ORD001 had a ₹2,000 refund on Aug 2. After deducting the refund,
   MDR (2%), GST on MDR (18% of MDR), and TDS (1%), the expected
   settlement is ₹7,644 — which matches SETL_A exactly."
```

---

## Tech Stack

### Python — Everything
FastAPI (REST layer), agent logic, tools, PDF parsing, LLM orchestration, DuckDB client — all Python. One language, no subprocess bridges, no IPC.

**Why:** LangGraph (ReAct agent framework), pdfplumber, DuckDB Python client, and Gemini SDK are all Python-first. The agent's tool interface is model-agnostic — swapping Gemini for another model requires changing one configuration line.

### FastAPI — REST API
HTTP layer, session management, file upload handling.

**Why:** Everything in this stack is Python — agent, tools, PDF parser, DuckDB client. FastAPI keeps it one language end to end, eliminating the Go→Python subprocess bridge entirely. Async endpoints handle concurrent sessions cleanly. Auto-generated OpenAPI docs are useful for the submission. The architecture simplifies to: `FastAPI → DuckDB` with no IPC hop.

### DuckDB — Data Layer
One `.duckdb` file per session. All source data and reconciliation state stored here.

**Why over SQLite:** DuckDB registers uploaded CSVs as virtual tables with zero ingestion overhead — `SELECT * FROM 'payments.csv'` works directly. Arithmetic and joins happen in DuckDB (deterministic), not in the LLM.

**Why over Vector DB:** All matching is on exact or near-exact values — amounts, dates, UTRs. Semantic similarity search is the wrong tool for financial joins. The one semi-unstructured field (bank narration) is handled by regex extraction and keyword classification, not embeddings.

**Why over Pandas only:** No persistence between API calls. DuckDB gives session isolation at the file level with zero infrastructure overhead.

### Gemini Flash (latest) — LLM
Powers the ReAct agent reasoning loop and Q&A layer.

**Selection criteria met:**
- Native function/tool calling with structured output
- Low latency suitable for iterative ReAct loops over 50+ records
- Sufficient context window for reconciliation state
- Free tier adequate for hackathon development and demo

**Why not local/Ollama:** Multi-step tool-calling reliability degrades significantly on smaller local models. Hosted API wins for correctness under time pressure.

### pdfplumber + camelot — PDF Parser
Extracts bank statement tables from text-based PDFs.

**Why:** pdfplumber handles the majority of bank portal PDFs reliably. Camelot is the fallback for edge cases with complex table borders. Neither requires an LLM — extraction is deterministic. Numbers from the parser are exact, not interpreted.

### HTML + Vanilla JS — Frontend
Single-page interface: file upload, reconciliation dashboard, decision trace, Q&A chat.

**Why:** No build step. No framework overhead. Ships in hours. The frontend's job is file upload, table display, and a chat box — React adds complexity with zero benefit at this scope.

---

## What This Does Not Do

Explicitly out of scope — noted here so the system is not evaluated against requirements it never claimed:

- Does not modify Razorpay records
- Does not initiate payments or refunds
- Does not contact banks or Razorpay APIs
- Does not support scanned/image PDFs
- Does not provide authentication or multi-tenancy
- Does not persist data beyond session expiry
- Does not handle currencies other than INR
- Does not support GST filing or tax computation beyond line-item verification
- Does not guarantee zero false matches — it minimizes and measures them

---

## Repository Structure

```
razorrecon/
├── data/
│   └── generator/
│       ├── generate.py          # main entry point, all dataset sizes
│       ├── payments.py          # payment + refund row generation
│       ├── settlements.py       # settlement generation with fee math
│       ├── bank_statement.py    # bank txn generation + narration formatting
│       └── scenarios.py         # scenario injection (ambiguous, malformed, etc.)
│
├── engine/
│   ├── agent.py                 # ReAct loop, tool registration, LLM client
│   ├── tools/
│   │   ├── ingestion.py         # ingest_payments, ingest_settlements, parse_bank_pdf
│   │   ├── query.py             # query_payments, query_settlements, query_bank
│   │   ├── compute.py           # calc_expected, sum_payments, find_combos
│   │   ├── classify.py          # classify_narration, extract_utr
│   │   └── resolution.py        # mark_confirmed, mark_ambiguous, mark_unresolved
│   ├── verification.py          # verification layer — verdict rules
│   ├── pdf_parser.py            # pdfplumber + camelot pipeline
│   └── report.py                # report compilation, metrics calculation
│
├── api/
│   ├── main.py                  # FastAPI app, route registration
│   ├── routers/
│   │   ├── upload.py            # file upload, session creation
│   │   ├── reconcile.py         # trigger agent, return job ID
│   │   ├── report.py            # fetch completed report
│   │   └── qa.py                # natural language query
│   └── session/
│       └── manager.py           # session lifecycle, DuckDB file management
│
├── frontend/
│   ├── index.html
│   ├── dashboard.js             # report table, exception list, decision trace
│   └── style.css
│
├── tests/
│   ├── test_reconciler.py       # unit tests per matching strategy
│   ├── test_verification.py     # verification layer rules
│   ├── test_pdf_parser.py       # parser on sample PDFs
│   └── eval/
│       ├── run_eval.py          # full evaluation against ground truth
│       └── held_out/            # generated after implementation lock
│
└── README.md
```

---

## Build Order

```
Day 1     Synthetic data generator (all scenarios, all sizes)
Day 2-3   DuckDB schema + all tool implementations (no agent yet)
Day 4     PDF parser pipeline
Day 5-6   ReAct agent + verification layer
Day 7     Evaluation harness — measure metrics against ground truth
Day 8     Go REST API
Day 9     Frontend
Day 10    Q&A agent
Day 11    Integration testing — all partial data scenarios
Day 12    Held-out evaluation — lock implementation, generate new data, measure
Day 13    Video script + recording
Day 14    Buffer — edge cases, README final, repo cleanup
```

---

## The Bar We Are Holding Ourselves To

Razorpay's stated bar: *"Throughput plus measured accuracy plus an honest exception list. One cherry-picked match proves nothing."*

Our response:
- Agent runs against a randomized synthetic batch with injected real-world messiness
- Metrics reported against ground truth: match rate, precision, false-match rate, coverage
- Exception list contains only records where the agent exhausted strategies without sufficient evidence
- Ambiguous records are explicitly distinguished from unresolved records
- Every decision has a full audit trace in the reconciliation log
- A held-out evaluation set generated after implementation is locked provides an independent accuracy measurement
