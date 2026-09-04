// Configuration & State
const API_BASE_URL = window.API_BASE_URL || "http://localhost:8000";
let sessionId = null;
let pollInterval = null;
const uploadedState = {
  payments: false,
  settlements: false,
  bank: false,
};

// Format helpers
function formatCurrency(val) {
  if (val === null || val === undefined || isNaN(val)) return "—";
  return "₹" + Number(val).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function showError(msg) {
  const banner = document.getElementById("global-error-banner");
  const text = document.getElementById("global-error-text");
  text.textContent = msg;
  banner.style.display = "flex";
}

function clearError() {
  const banner = document.getElementById("global-error-banner");
  banner.style.display = "none";
}

// 1. Session creation
async function createSession() {
  clearError();
  try {
    const res = await fetch(`${API_BASE_URL}/sessions`, {
      method: "POST"
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to create session`);
    const data = await res.json();
    sessionId = data.session_id;
    document.getElementById("session-id-display").textContent = sessionId;
  } catch (err) {
    console.error("createSession error:", err);
    document.getElementById("session-id-display").textContent = "Error";
    showError("Could not connect to API server at " + API_BASE_URL + ". Is backend running?");
  }
}

// 2. File Uploading
function setupFileInputs() {
  const mapping = [
    { id: "file-payments", type: "payments", card: "card-payments", status: "status-payments" },
    { id: "file-settlements", type: "settlements", card: "card-settlements", status: "status-settlements" },
    { id: "file-bank", type: "bank-statement", card: "card-bank", status: "status-bank", key: "bank" }
  ];

  mapping.forEach(item => {
    const input = document.getElementById(item.id);
    const card = document.getElementById(item.card);
    const statusEl = document.getElementById(item.status);
    const stateKey = item.key || item.type;

    input.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      await uploadFile(item.type, file, card, statusEl, stateKey);
    });

    // Drag and drop support
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      card.classList.add("dragover");
    });
    card.addEventListener("dragleave", () => card.classList.remove("dragover"));
    card.addEventListener("drop", async (e) => {
      e.preventDefault();
      card.classList.remove("dragover");
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        input.files = e.dataTransfer.files;
        await uploadFile(item.type, e.dataTransfer.files[0], card, statusEl, stateKey);
      }
    });
  });
}

async function uploadFile(type, file, cardEl, statusEl, stateKey) {
  if (!sessionId) {
    showError("Session not initialized. Please refresh the page.");
    return;
  }
  clearError();

  // Set loading state
  cardEl.classList.remove("success", "error");
  statusEl.innerHTML = `<span class="spinner"></span> Uploading ${file.name}...`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE_URL}/sessions/${sessionId}/upload/${type}`, {
      method: "POST",
      body: formData
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.message || `Upload failed with HTTP ${res.status}`);
    }

    // Success state
    cardEl.classList.add("success");
    uploadedState[stateKey] = true;
    const count = data.record_count ?? data.captured_count ?? 0;
    statusEl.innerHTML = `✓ ${file.name} (${count} records)`;

    checkAllUploaded();
  } catch (err) {
    console.error(`Upload error for ${type}:`, err);
    cardEl.classList.add("error");
    uploadedState[stateKey] = false;
    statusEl.textContent = `✕ ${err.message}`;
    checkAllUploaded();
  }
}

function checkAllUploaded() {
  const btn = document.getElementById("btn-start-reconcile");
  const ready = uploadedState.payments && uploadedState.settlements && uploadedState.bank;
  btn.disabled = !ready;
}

// 3. Start Reconciliation
async function startReconciliation() {
  if (!sessionId) return;
  clearError();

  const btn = document.getElementById("btn-start-reconcile");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Starting...`;

  try {
    const res = await fetch(`${API_BASE_URL}/sessions/${sessionId}/reconcile`, {
      method: "POST"
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to start reconciliation");
    }

    // Switch to progress section
    document.getElementById("upload-section").classList.remove("active");
    document.getElementById("progress-section").classList.add("active");

    // Start polling
    pollStatus();
    pollInterval = setInterval(pollStatus, 3000);
  } catch (err) {
    console.error("startReconciliation error:", err);
    showError(err.message);
    btn.disabled = false;
    btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Start Reconciliation`;
  }
}

// 4. Polling Progress
async function pollStatus() {
  if (!sessionId) return;

  try {
    const res = await fetch(`${API_BASE_URL}/sessions/${sessionId}/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status} checking status`);

    const data = await res.json();
    if (data.status === "not_started") return;

    const percent = Math.min(100, Math.max(0, data.percent || 0));
    const processed = data.processed || 0;
    const total = data.total || 0;
    const currentSettlement = data.current_settlement;

    // Update UI
    document.getElementById("progress-percent-text").textContent = `${Math.round(percent)}%`;
    document.getElementById("progress-bar-fill").style.width = `${percent}%`;
    document.getElementById("progress-counts-text").textContent = `Processing settlements... ${processed} / ${total}`;

    if (currentSettlement) {
      document.getElementById("current-settlement-text").textContent = `Investigating: ${currentSettlement}`;
    } else if (data.status === "completed") {
      document.getElementById("current-settlement-text").textContent = "Finalizing report...";
    }

    if (data.status === "completed" || data.status === "partial") {
      clearInterval(pollInterval);
      await fetchReportAndShowResults();
      if (data.status === "partial") {
        showError("Reconciliation stopped with partial results due to an error.");
      }
    } else if (data.status === "failed") {
      clearInterval(pollInterval);
      showError("Reconciliation process encountered an error and halted.");
    }
  } catch (err) {
    console.error("pollStatus error:", err);
  }
}

// 5. Results & Report Rendering
async function fetchReportAndShowResults() {
  try {
    const res = await fetch(`${API_BASE_URL}/sessions/${sessionId}/report`);
    if (!res.ok) throw new Error(`Failed to fetch report (HTTP ${res.status})`);
    const report = await res.json();
    showResults(report);
  } catch (err) {
    console.error("fetchReport error:", err);
    showError("Could not retrieve report: " + err.message);
  }
}

function showResults(report) {
  document.getElementById("progress-section").classList.remove("active");
  document.getElementById("results-section").classList.add("active");

  const summary = report.summary || {};
  const confirmedCount = summary.confirmed || 0;
  const ambiguousCount = summary.ambiguous || 0;
  const unresolvedCount = summary.unresolved || 0;
  const matchRate = summary.match_rate ? (summary.match_rate * 100).toFixed(1) + "%" : "0.0%";
  const coverage = summary.coverage ? (summary.coverage * 100).toFixed(1) + "%" : "0.0%";

  // Update Summary Cards
  document.getElementById("summary-confirmed").textContent = confirmedCount;
  document.getElementById("summary-ambiguous").textContent = ambiguousCount;
  document.getElementById("summary-unresolved").textContent = unresolvedCount;
  document.getElementById("summary-match-rate").textContent = matchRate;
  document.getElementById("summary-coverage-desc").textContent = `Coverage: ${coverage}`;

  // Update Tab Pill Counts
  document.getElementById("tab-count-confirmed").textContent = confirmedCount;
  document.getElementById("tab-count-ambiguous").textContent = ambiguousCount;
  document.getElementById("tab-count-unresolved").textContent = unresolvedCount;

  // Populate Confirmed Table
  const confirmedTbody = document.getElementById("tbody-confirmed");
  const confirmedList = report.confirmed_records || [];
  if (confirmedList.length === 0) {
    confirmedTbody.innerHTML = `<tr><td colspan="5" class="empty-state">No confirmed records found.</td></tr>`;
  } else {
    confirmedTbody.innerHTML = confirmedList.map(r => {
      const ev = r.evidence || {};
      const expected = ev.expected_amount !== undefined ? formatCurrency(ev.expected_amount) : "—";
      const actual = ev.actual_amount !== undefined ? formatCurrency(ev.actual_amount) : "—";
      const strategy = (r.strategies_tried && r.strategies_tried[0]) || (ev.strategy) || "direct_match";
      const reasoning = r.reasoning || "Verified match within tolerance.";
      return `
        <tr>
          <td class="mono-cell">${escapeHtml(r.record_id)}</td>
          <td class="amount-cell">${expected}</td>
          <td class="amount-cell">${actual}</td>
          <td><span class="strategy-badge">${escapeHtml(strategy)}</span></td>
          <td class="reasoning-cell" title="${escapeHtml(reasoning)}">${escapeHtml(reasoning)}</td>
        </tr>
      `;
    }).join("");
  }

  // Populate Ambiguous Table
  const ambiguousTbody = document.getElementById("tbody-ambiguous");
  const ambiguousList = report.ambiguous_records || [];
  if (ambiguousList.length === 0) {
    ambiguousTbody.innerHTML = `<tr><td colspan="4" class="empty-state">No ambiguous records detected.</td></tr>`;
  } else {
    ambiguousTbody.innerHTML = ambiguousList.map(r => {
      let competingStr = "Multiple options";
      if (Array.isArray(r.competing)) {
        competingStr = r.competing.map(c => typeof c === "object" ? JSON.stringify(c) : c).join("; ");
      } else if (r.competing) {
        competingStr = typeof r.competing === "object" ? JSON.stringify(r.competing) : String(r.competing);
      }
      const strategies = Array.isArray(r.strategies_tried) ? r.strategies_tried.join(", ") : (r.strategies_tried || "combo_search");
      const reasoning = r.reasoning || "Competing explanations cannot be separated.";
      return `
        <tr>
          <td class="mono-cell">${escapeHtml(r.record_id)}</td>
          <td class="reasoning-cell" title="${escapeHtml(competingStr)}">${escapeHtml(competingStr)}</td>
          <td><span class="strategy-badge">${escapeHtml(strategies)}</span></td>
          <td class="reasoning-cell" title="${escapeHtml(reasoning)}">${escapeHtml(reasoning)}</td>
        </tr>
      `;
    }).join("");
  }

  // Populate Unresolved Table
  const unresolvedTbody = document.getElementById("tbody-unresolved");
  const unresolvedList = report.unresolved_records || [];
  if (unresolvedList.length === 0) {
    unresolvedTbody.innerHTML = `<tr><td colspan="3" class="empty-state">No unresolved records.</td></tr>`;
  } else {
    unresolvedTbody.innerHTML = unresolvedList.map(r => {
      const strategies = Array.isArray(r.strategies_tried) ? r.strategies_tried.join(", ") : (r.strategies_tried || "all_strategies");
      const reasoning = r.reasoning || "No candidate matches located.";
      return `
        <tr>
          <td class="mono-cell">${escapeHtml(r.record_id)}</td>
          <td><span class="strategy-badge">${escapeHtml(strategies)}</span></td>
          <td class="reasoning-cell" title="${escapeHtml(reasoning)}">${escapeHtml(reasoning)}</td>
        </tr>
      `;
    }).join("");
  }

  // Populate Bank Charges Excluded
  const chargesData = report.bank_charges_excluded || {};
  const chargeCount = chargesData.count || (chargesData.records ? chargesData.records.length : 0);
  const chargeTotal = chargesData.total_amount || 0;
  document.getElementById("charges-meta-text").textContent = `(${chargeCount} items · ${formatCurrency(chargeTotal)} total)`;

  const chargesTbody = document.getElementById("tbody-charges");
  const chargeRecords = chargesData.records || [];
  if (chargeRecords.length === 0) {
    chargesTbody.innerHTML = `<tr><td colspan="4" class="empty-state">No bank charges detected.</td></tr>`;
  } else {
    chargesTbody.innerHTML = chargeRecords.map(c => `
      <tr>
        <td class="mono-cell">${escapeHtml(c.txn_id || "—")}</td>
        <td>${escapeHtml(c.date || "—")}</td>
        <td>${escapeHtml(c.narration || "—")}</td>
        <td class="amount-cell">${formatCurrency(c.debit || c.amount || 0)}</td>
      </tr>
    `).join("");
  }
}

// Tab switching
function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));

  const targetPane = document.getElementById(`tab-pane-${tabName}`);
  if (targetPane) targetPane.classList.add("active");

  // Mark the active button
  const buttons = document.querySelectorAll(".tabs-nav button");
  buttons.forEach(btn => {
    if (btn.getAttribute("onclick") && btn.getAttribute("onclick").includes(tabName)) {
      btn.classList.add("active");
    }
  });
}

// Collapsible toggle
function toggleChargesCollapsible() {
  const card = document.getElementById("charges-collapsible");
  card.classList.toggle("open");
}

// 6. Load Existing Session (Dev/Debug)
async function loadExistingSession(targetId) {
  const sid = (targetId || (document.getElementById("debug-session-input") && document.getElementById("debug-session-input").value) || "").trim();
  if (!sid) {
    showError("Please enter a valid Session ID to load.");
    return;
  }
  clearError();

  const loadBtn = document.getElementById("btn-load-session");
  const originalText = loadBtn ? loadBtn.textContent : "";
  if (loadBtn) {
    loadBtn.textContent = "Loading...";
    loadBtn.disabled = true;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/sessions/${sid}/report`);
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Failed to load report for session ${sid} (HTTP ${res.status})`);
    }
    const report = await res.json();
    sessionId = sid;
    const sessionDisplay = document.getElementById("session-id-display");
    if (sessionDisplay) sessionDisplay.textContent = sid;

    // Skip upload and progress screens entirely
    document.getElementById("upload-section").classList.remove("active");
    document.getElementById("progress-section").classList.remove("active");
    showResults(report);
  } catch (err) {
    console.error("loadExistingSession error:", err);
    showError(err.message);
  } finally {
    if (loadBtn) {
      loadBtn.textContent = originalText;
      loadBtn.disabled = false;
    }
  }
}

// Reset / Start new
function startNewReconciliation() {
  window.location.reload();
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Initialization
window.addEventListener("DOMContentLoaded", () => {
  createSession();
  setupFileInputs();

  const debugInput = document.getElementById("debug-session-input");
  if (debugInput) {
    debugInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        loadExistingSession();
      }
    });
  }
});
