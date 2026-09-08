// SmorX.ai PPAP Control Center frontend.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const AUTH_TOKEN_KEY = "smorx_ppap_token";

const state = {
  caseId: "",
  ppapId: "",
  submission: null,
  rules: null,
  selectedElement: 1,
  standardId: "aiag_ppap",
  reportPrefix: "E",
  submissionFilter: "all",
  severityFilter: "all",
  reviewPayload: null,
  severity: null,
  token: localStorage.getItem(AUTH_TOKEN_KEY) || "",
  user: null
};

const stepOrder = ["setup", "upload", "map", "validate", "resolve", "approval", "report", "submission"];

const dom = {
  views: {
    dashboard: $("#dashboard-view"),
    submissions: $("#submissions-view"),
    create: $("#create-view"),
    workspace: $("#workspace-view"),
    report: $("#report-view"),
    rules: $("#rules-view"),
    customers: $("#customers-view"),
    users: $("#users-view")
  },
  navButtons: $$(".nav-button[data-view]"),
  greeting: $("#dashboard-greeting"),
  kpiGrid: $("#kpi-grid"),
  recentBody: $("#recent-body"),
  healthBars: $("#health-bars"),
  healthStats: $("#health-stats"),
  submissionsBody: $("#submissions-body"),
  submissionsSubhead: $("#submissions-subhead"),
  submissionSearch: $("#submission-search"),
  createForm: $("#create-form"),
  createStatus: $("#create-status"),
  workspaceBreadcrumb: $("#workspace-breadcrumb"),
  workspaceTitle: $("#workspace-title"),
  workspaceMeta: $("#workspace-meta"),
  workspaceStatus: $("#workspace-status"),
  steps: $$(".process-step"),
  tabButtons: $$(".tab-button"),
  panels: {
    details: $("#panel-details"),
    documents: $("#panel-documents"),
    validation: $("#panel-validation"),
    issues: $("#panel-issues"),
    approval: $("#panel-approval")
  },
  form: $("#upload-form"),
  fileInput: $("#files"),
  standard: $("#standard-id"),
  submissionLevel: $("#submission-level"),
  caseIdInput: $("#case-id"),
  submit: $("#submit"),
  status: $("#status"),
  notice: $("#submission-notice"),
  uploadReview: $("#review"),
  reviewBody: $("#review-body"),
  selectedFileCount: $("#selected-file-count"),
  confirm: $("#confirm-processing"),
  changeFiles: $("#change-files"),
  result: $("#result"),
  summary: $("#summary"),
  validate: $("#validate"),
  matrixBody: $("#matrix-body"),
  severityCards: $("#severity-cards"),
  reviewOverview: $("#review-overview"),
  reviewElements: $("#review-elements"),
  generateReport: $("#generate-report"),
  issuesList: $("#issues-list"),
  approvalStatus: $("#approval-status"),
  reportResult: $("#report-result"),
  reportEmpty: $("#report-empty"),
  reportSummary: $("#report-summary"),
  reportKeyFields: $("#report-key-fields"),
  reportNarrative: $("#report-narrative"),
  reportGlobalChart: $("#report-global-chart"),
  reportIssuesBody: $("#report-issues-body"),
  reportElements: $("#report-elements"),
  reportPdf: $("#report-pdf"),
  reportCsv: $("#report-csv"),
  reportKicker: $("#report-kicker"),
  reportTitle: $("#report-title"),
  reportDescription: $("#report-description"),
  reportReadiness: $("#report-readiness"),
  rulesCount: $("#rules-count"),
  ruleTabs: $("#rule-tabs"),
  ruleMeta: $("#rule-meta"),
  ruleTitle: $("#rule-title"),
  ruleApplicability: $("#rule-applicability"),
  ruleList: $("#rule-list"),
  addRule: $("#add-rule"),
  ruleEditor: $("#rule-editor"),
  ruleEditorTitle: $("#rule-editor-title"),
  ruleForm: $("#rule-form"),
  ruleKey: $("#rule-key"),
  ruleDescription: $("#rule-description"),
  ruleReferences: $("#rule-references"),
  ruleEnabled: $("#rule-enabled"),
  ruleFormStatus: $("#rule-form-status"),
  cancelRule: $("#cancel-rule"),
  loginOverlay: $("#login-overlay"),
  loginForm: $("#login-form"),
  loginStatus: $("#login-status"),
  appShell: $("#app-shell"),
  rulesNavGroup: $("#rules-nav-group"),
  reportsNavGroup: $("#reports-nav-group"),
  adminNavGroup: $("#admin-nav-group"),
  sessionUser: $("#session-user"),
  logoutButton: $("#logout-button")
};

bindShell();
bootAuth();

async function bootAuth() {
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    const me = await api("/api/auth/me");
    applySession(me.user, state.token);
    await loadDashboard();
  } catch {
    clearSession();
    showLogin();
  }
}

function showLogin() {
  if (dom.appShell) dom.appShell.hidden = true;
  if (dom.loginOverlay) dom.loginOverlay.hidden = false;
}

function showApp() {
  if (dom.loginOverlay) dom.loginOverlay.hidden = true;
  if (dom.appShell) dom.appShell.hidden = false;
}

function applySession(user, token) {
  state.user = user;
  state.token = token || "";
  if (state.token) localStorage.setItem(AUTH_TOKEN_KEY, state.token);
  showApp();
  applyRoleVisibility();
  if (dom.sessionUser) {
    const label = user.role_label || user.role || "User";
    dom.sessionUser.textContent = `${user.display_name || user.username} · ${label}`;
  }
}

function clearSession() {
  state.user = null;
  state.token = "";
  localStorage.removeItem(AUTH_TOKEN_KEY);
  if (dom.rulesNavGroup) dom.rulesNavGroup.hidden = true;
  if (dom.reportsNavGroup) dom.reportsNavGroup.hidden = true;
  if (dom.adminNavGroup) dom.adminNavGroup.hidden = true;
  $$(".create-only, .quality-only").forEach((node) => {
    node.hidden = false;
  });
}

function permissions() {
  return state.user?.permissions || {
    create: false,
    quality: false,
    rules: false
  };
}

function applyRoleVisibility() {
  const perms = permissions();
  if (dom.rulesNavGroup) dom.rulesNavGroup.hidden = !perms.rules;
  if (dom.reportsNavGroup) dom.reportsNavGroup.hidden = !perms.quality;
  if (dom.adminNavGroup) dom.adminNavGroup.hidden = !isSuperAdmin();

  $$(".create-only").forEach((node) => {
    node.hidden = !perms.create;
  });
  $$(".quality-only").forEach((node) => {
    node.hidden = !perms.quality;
  });

  // Workspace tabs: hide quality tabs for creation-only users.
  dom.tabButtons.forEach((button) => {
    const panel = button.dataset.panel;
    const qualityPanels = ["validation", "issues", "approval"];
    if (qualityPanels.includes(panel)) {
      button.hidden = !perms.quality;
    }
  });

  if (!perms.rules && !dom.views.rules.hidden) {
    showView("dashboard");
  }
  if (!perms.quality && !dom.views.report.hidden) {
    showView("dashboard");
  }
  if (!perms.create && !dom.views.create.hidden) {
    showView("dashboard");
  }
  if (!isSuperAdmin() && ((dom.views.customers && !dom.views.customers.hidden) || (dom.views.users && !dom.views.users.hidden))) {
    showView("dashboard");
  }
}

function isSuperAdmin() {
  return Boolean(state.user?.is_super_admin || permissions().rules);
}

function canCreate() {
  return Boolean(permissions().create);
}

function canQuality() {
  return Boolean(permissions().quality);
}

async function handleLogin(event) {
  event.preventDefault();
  dom.loginStatus.textContent = "Signing in...";
  dom.loginStatus.className = "form-status";
  try {
    const payload = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("#login-username").value.trim(),
        password: $("#login-password").value
      })
    });
    applySession(payload.user, payload.token);
    dom.loginForm.reset();
    dom.loginStatus.textContent = "";
    await loadDashboard();
  } catch (error) {
    dom.loginStatus.textContent = error.message;
    dom.loginStatus.className = "form-status status-error";
  }
}

async function handleLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    // ignore logout failures
  }
  clearSession();
  showLogin();
}

function bindShell() {
  if (dom.loginForm) dom.loginForm.addEventListener("submit", handleLogin);
  if (dom.logoutButton) dom.logoutButton.addEventListener("click", handleLogout);

  const bindClick = (id, handler) => {
    const node = typeof id === "string" ? $(id) : id;
    if (node) node.addEventListener("click", handler);
  };

  bindClick("#new-submission", () => {
    if (!canCreate()) return;
    showView("create");
  });
  bindClick("#new-submission-list", () => {
    if (!canCreate()) return;
    showView("create");
  });
  bindClick("#cancel-create", () => showView("dashboard"));
  if (dom.createForm) {
    dom.createForm.addEventListener("submit", async (event) => {
      if (!canCreate()) {
        event.preventDefault();
        return;
      }
      await createSubmission(event);
    });
  }
  if (dom.submissionSearch) {
    dom.submissionSearch.addEventListener("input", debounce(() => loadSubmissions(state.submissionFilter), 250));
  }
  bindClick("#refresh-matrix", loadMatrix);
  bindClick("#mark-approved", () => patchStatus("approved"));
  bindClick("#mark-submitted", () => patchStatus("submitted"));

  $$(".nav-group-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = $(`[data-group-panel="${button.dataset.group}"]`);
      if (panel) panel.classList.toggle("open");
      button.classList.toggle("open");
    });
  });
  $$("[data-group-panel]").forEach((panel) => panel.classList.add("open"));

  dom.navButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const view = button.dataset.view;
      if (view === "submissions") {
        state.submissionFilter = button.dataset.filter || "all";
        showView("submissions");
        await loadSubmissions(state.submissionFilter);
      } else if (view === "workspace") {
        if (!state.caseId) {
          showView("submissions");
          await loadSubmissions("all");
          return;
        }
        showView("workspace");
        showPanel(button.dataset.panel || "details");
      } else if (view === "rules") {
        if (!isSuperAdmin()) {
          showView("dashboard");
          return;
        }
        if (button.dataset.standard) {
          state.standardId = button.dataset.standard;
          if (dom.standard) dom.standard.value = state.standardId;
        }
        showView("rules");
        await loadRules();
      } else if (view === "report") {
        if (!canQuality()) {
          showView("dashboard");
          return;
        }
        showView("report");
      } else if (view === "customers") {
        if (!isSuperAdmin()) {
          showView("dashboard");
          return;
        }
        showView("customers");
        await loadCustomers();
      } else if (view === "users") {
        if (!isSuperAdmin()) {
          showView("dashboard");
          return;
        }
        showView("users");
        await loadPlatformUsers();
      } else if (view === "dashboard") {
        showView("dashboard");
        await loadDashboard();
      } else {
        showView(view);
      }
    });
  });

  dom.tabButtons.forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.panel));
  });

  if (dom.form) {
    dom.form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await previewUpload();
    });
  }
  if (dom.standard) {
    dom.standard.addEventListener("change", () => {
      state.standardId = dom.standard.value;
      state.reportPrefix = state.standardId === "vda_ppf" ? "V" : "E";
    });
  }
  if (dom.changeFiles) dom.changeFiles.addEventListener("click", () => dom.fileInput && dom.fileInput.click());
  if (dom.confirm) dom.confirm.addEventListener("click", processUpload);
  if (dom.validate) dom.validate.addEventListener("click", validateCase);
  if (dom.generateReport) dom.generateReport.addEventListener("click", generateReport);
  if (dom.addRule) {
    dom.addRule.addEventListener("click", async () => {
      if (!isSuperAdmin()) return;
      if (!state.rules) await loadRules();
      openRuleEditor();
    });
  }
  if (dom.cancelRule) dom.cancelRule.addEventListener("click", closeRuleEditor);
  if (dom.ruleForm) dom.ruleForm.addEventListener("submit", saveRule);

  const customerForm = $("#customer-form");
  if (customerForm) customerForm.addEventListener("submit", createCustomer);
  const engineerForm = $("#engineer-form");
  if (engineerForm) engineerForm.addEventListener("submit", addEngineer);
  const platformUserForm = $("#platform-user-form");
  if (platformUserForm) platformUserForm.addEventListener("submit", createPlatformUser);
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    dom.greeting.textContent = localGreeting();
    const counts = data.counts || {};
    dom.kpiGrid.replaceChildren(
      ...[
        ["Submissions", counts.all || 0],
        ["In Review", counts.in_review || 0],
        ["Failed", counts.failed || 0],
        ["Approved", counts.approved || 0]
      ].map(([label, value]) => {
        const card = document.createElement("div");
        card.className = "kpi-card";
        card.innerHTML = `<strong>${esc(value)}</strong><span>${esc(label)}</span>`;
        return card;
      })
    );
    renderSubmissionRows(dom.recentBody, data.recent || [], true);
    const health = data.health || {};
    dom.healthBars.replaceChildren(
      ...[
        ["Documents", health.documents || 0],
        ["Requirements", health.requirements || 0],
        ["Quality", health.quality || 0]
      ].map(([label, value]) => {
        const row = document.createElement("div");
        row.className = "health-row";
        row.innerHTML = `<span>${esc(label)}</span><div class="bar"><i style="width:${Math.max(0, Math.min(100, value))}%"></i></div><strong>${esc(value)}%</strong>`;
        return row;
      })
    );
    dom.healthStats.innerHTML = `
      <span>Critical Issues: <strong>${esc(health.critical || 0)}</strong></span>
      <span>Warnings: <strong>${esc(health.warnings || 0)}</strong></span>
      <span>Missing Documents: <strong>${esc(health.missing_documents || 0)}</strong></span>`;
  } catch (error) {
    dom.greeting.textContent = error.message;
  }
}

async function loadSubmissions(filter = "all") {
  const search = dom.submissionSearch.value.trim();
  const params = new URLSearchParams();
  if (filter && filter !== "all") params.set("status", filter);
  if (search) params.set("search", search);
  const data = await api(`/api/submissions?${params.toString()}`);
  dom.submissionsSubhead.textContent = `${data.count || 0} submissions · filter: ${filter.replaceAll("_", " ")}`;
  renderSubmissionRows(dom.submissionsBody, data.items || [], false);
}

function renderSubmissionRows(body, items, compact) {
  body.replaceChildren();
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="${compact ? 4 : 6}">No submissions found.</td></tr>`;
    return;
  }
  for (const item of items) {
    const row = document.createElement("tr");
    row.className = "clickable-row";
    row.innerHTML = compact
      ? `<td><code>${esc(item.ppap_id)}</code></td><td>${esc(item.customer_name || "-")}</td><td>Level ${esc(item.submission_level)}</td><td>${badge(item.status)}</td>`
      : `<td><code>${esc(item.ppap_id)}</code></td><td>${esc(item.customer_name || "-")}</td><td>${esc(item.part_number || "-")} / ${esc(item.part_name || "-")}</td><td>Level ${esc(item.submission_level)}</td><td>${esc(item.standard_name || formatStandard(item.standard_id))}</td><td>${badge(item.status)}</td>`;
    row.addEventListener("click", () => openSubmission(item.ppap_id || item.case_id));
    body.appendChild(row);
  }
}

async function createSubmission(event) {
  event.preventDefault();
  dom.createStatus.textContent = "Creating submission...";
  try {
    const payload = {
      customer_name: $("#customer-name").value.trim(),
      part_number: $("#part-number").value.trim(),
      part_name: $("#part-name").value.trim(),
      part_revision: $("#part-revision").value.trim(),
      supplier_name: $("#supplier-name").value.trim(),
      program_name: $("#program-name").value.trim(),
      submission_date: $("#submission-date").value,
      due_date: $("#due-date").value,
      standard_id: $("#create-standard").value,
      submission_level: Number($("#create-level").value)
    };
    const created = await api("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    dom.createForm.reset();
    await openSubmission(created.ppap_id || created.case_id);
  } catch (error) {
    dom.createStatus.textContent = error.message;
    dom.createStatus.className = "form-status status-error";
  }
}

async function openSubmission(identifier) {
  const submission = await api(`/api/submissions/${encodeURIComponent(identifier)}`);
  state.submission = submission;
  state.caseId = submission.case_id;
  state.ppapId = submission.ppap_id;
  state.standardId = submission.standard_id || "aiag_ppap";
  state.reportPrefix = state.standardId === "vda_ppf" ? "V" : "E";
  dom.caseIdInput.value = state.caseId;
  dom.standard.value = state.standardId;
  dom.submissionLevel.value = String(submission.submission_level || 3);
  renderWorkspaceHeader(submission);
  setStep("setup", true);
  if ((submission.files || []).length) setStep("upload", true);
  showView("workspace");
  showPanel("details");
  await loadMatrix();
  try {
    const review = await caseApi("/review");
    state.reviewPayload = review;
    state.severity = review.severity || null;
    renderHumanReview(review);
    renderSeverity(review.severity);
    renderIssues(review.severity);
    setStep("validate", true);
    setStep("resolve");
  } catch {
    // no validation yet
  }
  try {
    const report = await caseApi("/report");
    renderReport(report);
    setStep("report", true);
  } catch {
    dom.reportResult.hidden = true;
    if (dom.reportEmpty) dom.reportEmpty.hidden = false;
  }
}

function renderWorkspaceHeader(submission) {
  dom.workspaceBreadcrumb.textContent = `Home > Submissions > ${submission.ppap_id}`;
  dom.workspaceTitle.textContent = submission.ppap_id;
  dom.workspaceMeta.textContent = [
    submission.customer_name,
    `Part: ${submission.part_name || "-"}`,
    `Revision: ${submission.part_revision || "-"}`,
    `Level ${submission.submission_level}`,
    submission.standard_name || formatStandard(submission.standard_id)
  ].filter(Boolean).join(" · ");
  dom.workspaceStatus.textContent = String(submission.status || "draft").replaceAll("_", " ").toUpperCase();
  dom.workspaceStatus.className = `status-badge ${statusClass(submission.status)}`;
  dom.approvalStatus.textContent = submission.status || "draft";
}

async function patchStatus(status) {
  if (!state.ppapId) return;
  const updated = await api(`/api/submissions/${encodeURIComponent(state.ppapId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status })
  });
  state.submission = updated;
  renderWorkspaceHeader(updated);
  if (status === "approved") setStep("approval", true);
  if (status === "submitted") {
    setStep("approval", true);
    setStep("submission", true);
  }
}

async function previewUpload() {
  clearNotice();
  if (!dom.fileInput.files.length) return showError("Select at least one file.");
  dom.submit.disabled = true;
  busy("Reading selected package...");
  try {
    const preview = await api("/api/cases/preview", { method: "POST", body: uploadFormData() });
    renderUploadPreview(preview);
    setStep("upload");
    dom.uploadReview.hidden = false;
    dom.status.textContent = "Review these files, then confirm processing.";
  } catch (error) {
    showError(error.message);
  } finally {
    dom.submit.disabled = false;
  }
}

async function processUpload() {
  dom.submit.disabled = dom.confirm.disabled = dom.changeFiles.disabled = true;
  dom.result.hidden = true;
  dom.validate.hidden = true;
  clearNotice();
  const timer = startProcessingPhases();
  try {
    const payload = await api("/api/cases/upload", { method: "POST", body: uploadFormData() });
    state.caseId = payload.case_id || payload.case?.case_id;
    state.ppapId = payload.ppap_id || payload.case?.ppap_id || state.ppapId;
    state.standardId = payload.standard_id || payload.case?.standard_id || state.standardId;
    state.reportPrefix = state.standardId === "vda_ppf" ? "V" : "E";
    renderMetrics(dom.summary, [
      ["PPAP ID", state.ppapId || state.caseId],
      ["Standard", formatStandard(state.standardId)],
      ["Total", payload.counts?.total ?? 0],
      ["Registered", payload.counts?.registered ?? 0],
      ["Ignored", payload.counts?.ignored ?? 0],
      ["Scope", payload.submission_level || payload.case?.submission_level]
    ]);
    dom.result.hidden = false;
    dom.uploadReview.hidden = true;
    dom.validate.hidden = false;
    setStep("upload", true);
    setStep("map", true);
    setStep("validate");
    dom.status.textContent = `${state.ppapId || state.caseId} registered.`;
    await loadMatrix();
    if (state.ppapId) {
      const refreshed = await api(`/api/submissions/${encodeURIComponent(state.ppapId)}`);
      state.submission = refreshed;
      renderWorkspaceHeader(refreshed);
    }
  } catch (error) {
    showError(error.message);
  } finally {
    clearInterval(timer);
    dom.submit.disabled = dom.confirm.disabled = dom.changeFiles.disabled = false;
  }
}

async function validateCase() {
  if (!state.caseId || !canQuality()) return;
  dom.validate.disabled = true;
  setStep("validate");
  busy("Validating enabled rules...");
  try {
    const validation = await caseApi("/validate", { method: "POST" });
    const review = await caseApi("/review");
    state.reviewPayload = review;
    state.severity = review.severity || validation.severity || null;
    renderHumanReview(review);
    renderSeverity(state.severity);
    renderIssues(state.severity);
    setStep("validate", true);
    setStep("resolve");
    showPanel("validation");
    dom.status.textContent = `Validation ${validation.overall_status}. Human verification is ready.`;
    notice("Validation completed. Review severity and issues, then generate the final report.");
    if (state.ppapId) {
      const refreshed = await api(`/api/submissions/${encodeURIComponent(state.ppapId)}`);
      state.submission = refreshed;
      renderWorkspaceHeader(refreshed);
    }
  } catch (error) {
    showError(error.message);
  } finally {
    dom.validate.disabled = false;
  }
}

async function generateReport() {
  if (!state.caseId || !canQuality()) return;
  dom.generateReport.disabled = true;
  clearNotice();
  try {
    if (!(await saveReviewChanges())) return;
    setStep("report");
    busy("Generating the final report...");
    const report = await caseApi("/report?use_llm=true", { method: "POST" });
    renderReport(report);
    setStep("report", true);
    dom.status.textContent = "Final report generated.";
    notice("Final report generated. Open Reports to review or download.");
    showView("report");
  } catch (error) {
    showError(error.message);
  } finally {
    dom.generateReport.disabled = false;
  }
}

async function loadMatrix() {
  if (!state.caseId && !state.ppapId) return;
  try {
    const matrix = await api(`/api/submissions/${encodeURIComponent(state.ppapId || state.caseId)}/matrix`);
    dom.matrixBody.replaceChildren();
    for (const row of matrix.rows || []) {
      const tr = document.createElement("tr");
      const docs = (row.documents || []).join(", ") || "—";
      tr.innerHTML = `
        <td>${esc(row.element_number)}</td>
        <td>${esc(row.element_name)}</td>
        <td>${row.required ? "Yes" : "No"}</td>
        <td>${esc(docs)}</td>
        <td>${badge(row.status)}</td>`;
      dom.matrixBody.appendChild(tr);
    }
  } catch (error) {
    dom.matrixBody.innerHTML = `<tr><td colspan="5">${esc(error.message)}</td></tr>`;
  }
}

function renderSeverity(severity) {
  const counts = severity?.counts || { critical: 0, major: 0, warning: 0, passed: 0 };
  const cards = [
    ["critical", "Critical", counts.critical],
    ["major", "Major", counts.major],
    ["warning", "Warnings", counts.warning],
    ["passed", "Passed", counts.passed]
  ];
  dom.severityCards.replaceChildren(...cards.map(([key, label, value]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `severity-card severity-${key}${state.severityFilter === key ? " active" : ""}`;
    button.innerHTML = `<strong>${esc(value)}</strong><span>${esc(label)}</span>`;
    button.addEventListener("click", () => {
      state.severityFilter = state.severityFilter === key ? "all" : key;
      renderSeverity(state.severity);
      if (state.reviewPayload) renderHumanReview(state.reviewPayload);
      renderIssues(state.severity);
    });
    return button;
  }));
}

function renderIssues(severity) {
  const items = [
    ...(severity?.items?.critical || []),
    ...(severity?.items?.major || [])
  ];
  const filtered = state.severityFilter === "all"
    ? items
    : (severity?.items?.[state.severityFilter] || items.filter((item) => item.severity === state.severityFilter));
  dom.issuesList.replaceChildren();
  if (!filtered.length) {
    dom.issuesList.innerHTML = "<p>No open critical or major issues.</p>";
    return;
  }
  for (const item of filtered.slice(0, 40)) {
    const card = document.createElement("article");
    card.className = `issue-card severity-${item.severity}`;
    card.innerHTML = `
      <header><strong>${esc(item.severity)} · ${artifactCode(item.element_number)} Rule ${esc(item.rule_id)}</strong>${badge(item.status)}</header>
      <p>${esc(item.issue || "No detail provided.")}</p>
      <p><small>Expected: ${esc(item.expected || "-")}</small></p>
      <p><small>Found: ${esc(item.found || "-")}</small></p>
      <p><small>Action: ${esc(item.recommended_action || "-")}</small></p>`;
    dom.issuesList.appendChild(card);
  }
}

function renderUploadPreview(preview) {
  dom.reviewBody.replaceChildren();
  for (const file of preview.files || []) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><code>${esc(file.relative_path || file.filename)}</code></td>
      <td>${esc(file.source_container || "upload")}</td>
      <td>${formatBytes(file.size_bytes || 0)}</td>
      <td>${esc(file.extension || "unknown")}</td>
      <td>${badge(file.ignored ? file.ignore_reason || "ignored" : "Ready")}</td>`;
    dom.reviewBody.appendChild(row);
  }
  const counts = preview.counts || {};
  dom.selectedFileCount.textContent = `${counts.processable || 0} ready / ${counts.total || 0} files`;
}

function renderHumanReview(payload) {
  let elements = payload.elements || [];
  if (state.severityFilter !== "all") {
    elements = elements
      .map((element) => ({
        ...element,
        checkpoint_results: (element.checkpoint_results || []).filter((rule) => {
          const required = Boolean(element.required_for_submission_level);
          const severity = severityFor(rule.status, required);
          return severity === state.severityFilter;
        })
      }))
      .filter((element) => (element.checkpoint_results || []).length);
  }
  const rules = elements.flatMap((element) => element.checkpoint_results || []);
  const prefix = payload.summary?.artifact_prefix || state.reportPrefix || "E";
  state.reportPrefix = prefix;
  renderMetrics(dom.reviewOverview, [
    ["Standard", payload.summary?.standard_name || formatStandard(state.standardId)],
    ["Final Status", payload.summary?.overall_status || "N/A"],
    ["Artifacts", elements.length],
    ["Rules", rules.length],
    ["Human Decisions", payload.summary?.human_override_count || 0]
  ]);
  dom.reviewElements.replaceChildren(...elements.map((element) => reviewElement(element, prefix)));
}

function severityFor(status, required) {
  const value = String(status || "").toUpperCase();
  if (["PASS", "PASSED", "OK"].includes(value)) return "passed";
  if (["NOT_FOUND", "FAIL", "FAILED", "ERROR"].includes(value)) return required ? "critical" : "major";
  if (["FLAG", "WARNING", "WARN", "REVIEW"].includes(value)) return "warning";
  return required ? "major" : "warning";
}

function reviewElement(element, prefix = "E") {
  const details = document.createElement("details");
  details.className = "element-block review-element";
  const counts = element.status_summary || {};
  details.innerHTML = `
    <summary>
      <span><strong>${artifactCode(element.element_number, prefix)} ${esc(element.element_name)}</strong><small>${element.checkpoint_results.length} enabled rules</small></span>
      <span class="element-counts"><em class="count-pass">${counts.pass || 0} Pass</em><em class="count-flag">${counts.flag || 0} Flag</em><em class="count-missing">${counts.not_found || 0} Not found</em></span>
    </summary>
    <div class="element-body"><div class="table-wrap review-table-wrap">
      <table class="human-review-table"><thead><tr><th>Rule no.</th><th>Rule Description</th><th>Reason</th><th>Evidence location</th><th>Decision</th><th>Reviewer remark</th><th>Verified location</th></tr></thead><tbody></tbody></table>
    </div></div>`;
  const body = $("tbody", details);
  (element.checkpoint_results || []).forEach((rule) => body.appendChild(reviewRow(rule, element)));
  return details;
}

function reviewRow(rule, element) {
  const aiStatus = rule.ai_status || rule.status || "NOT_FOUND";
  const row = document.createElement("tr");
  row.className = "review-rule-row";
  Object.assign(row.dataset, {
    ruleKey: rule.rule_key,
    ruleId: rule.rule_id,
    elementNumber: element.element_number,
    aiStatus,
    hasHuman: rule.final_decision_source === "HUMAN" ? "true" : "false"
  });
  row.innerHTML = `
    <td><strong>Rule ${esc(rule.rule_id)}</strong></td>
    <td>${esc(rule.rule_description || "")}</td>
    <td>${esc(rule.ai_reason || rule.reason || "No reason supplied.")}</td>
    <td>${reviewLocations(rule.locations || [])}</td>
    <td class="decision-cell"><select class="review-decision"><option value="PASS">Pass</option><option value="FLAG">Flag</option><option value="NOT_FOUND">Not found</option></select><small>AI: ${esc(aiStatus.replaceAll("_", " "))}</small></td>
    <td><textarea class="review-remark" rows="3" placeholder="Required if decision is changed">${esc(rule.human_remark || "")}</textarea></td>
    <td><input class="review-location" type="text" value="${esc(rule.human_evidence_location || "")}" placeholder="Optional file / page / row"></td>`;
  const select = $(".review-decision", row);
  select.value = rule.human_status || aiStatus;
  select.addEventListener("change", () => row.classList.toggle("is-changed", select.value !== aiStatus));
  select.dispatchEvent(new Event("change"));
  return row;
}

async function saveReviewChanges() {
  const rows = $$(".review-rule-row", dom.reviewElements);
  const missing = rows.filter((row) => changed(row) && !$(".review-remark", row).value.trim());
  if (missing.length) {
    missing.forEach((row) => row.classList.add("needs-remark"));
    showError(`Fill reviewer remark for ${missing.map((row) => `${artifactCode(row.dataset.elementNumber)} Rule ${row.dataset.ruleId}`).join(", ")}.`);
    return false;
  }
  for (const row of rows) {
    const url = casePath(`/review/${encodeURIComponent(row.dataset.ruleKey)}`);
    if (changed(row)) {
      await api(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: $(".review-decision", row).value,
          remark: $(".review-remark", row).value.trim(),
          evidence_location: $(".review-location", row).value.trim()
        })
      });
    } else if (row.dataset.hasHuman === "true") {
      await api(url, { method: "DELETE", expectNoContent: true });
    }
  }
  return true;
}

function renderReport(report) {
  const data = report.summary || {};
  const narrative = report.narrative || {};
  const tables = report.tables || {};
  const prefix = data.artifact_prefix || state.reportPrefix || "E";
  state.reportPrefix = prefix;
  clearReportNodes();
  if (dom.reportEmpty) dom.reportEmpty.hidden = true;
  dom.reportKicker.textContent = data.report_kicker || data.standard_name || "SmorX.ai PPAP";
  dom.reportTitle.textContent = data.report_title || "Validation Report";
  dom.reportDescription.textContent = "Final package status, global findings, and artifact-level evidence.";

  const checkpoint = data.checkpoint_summary || {};
  const total = Number(checkpoint.total_rules || 0) || (
    Number(checkpoint.pass || 0) + Number(checkpoint.flag || 0) + Number(checkpoint.not_found || 0)
  );
  const passed = Number(checkpoint.pass || 0);
  const readiness = total ? Math.round((passed / total) * 100) : 0;
  const overall = String(data.overall_status || "N/A");
  dom.reportReadiness.innerHTML = `
    <div class="readiness-score"><strong>${esc(readiness)}%</strong><span>${esc(overall.replaceAll("_", " "))}</span></div>
    <div class="health-bars compact">
      <div class="health-row"><span>Document Completeness</span><div class="bar"><i style="width:${readiness}%"></i></div><strong>${esc(readiness)}%</strong></div>
      <div class="health-row"><span>Requirement Compliance</span><div class="bar"><i style="width:${Math.max(0, readiness - 5)}%"></i></div><strong>${esc(Math.max(0, readiness - 5))}%</strong></div>
      <div class="health-row"><span>Cross-Document Consistency</span><div class="bar"><i style="width:${Math.max(0, readiness - 8)}%"></i></div><strong>${esc(Math.max(0, readiness - 8))}%</strong></div>
    </div>`;

  renderMetrics(dom.reportSummary, [
    ["Standard", data.standard_name || formatStandard(data.standard_id)],
    ["Overall Status", data.overall_status || "N/A"],
    ["Scope", data.submission_level || "-"],
    ["Open Findings", data.critical_issue_count || 0],
    ["Human Decisions", data.human_override_count || 0]
  ]);
  renderKeyFields(data.key_field_summary || {}, dom.reportKeyFields);
  [narrative.executive_summary, narrative.submission_level_summary, narrative.risk_summary].filter(Boolean).forEach((text) => {
    const p = document.createElement("p");
    p.textContent = text;
    dom.reportNarrative.appendChild(p);
  });
  if (!dom.reportNarrative.childElementCount) dom.reportNarrative.textContent = "No global narrative is available.";
  dom.reportGlobalChart.appendChild(donut(data.checkpoint_summary || {}));
  renderReportIssues(tables.critical_issues || [], prefix);
  (report.element_reports || [])
    .filter((element) => element.presence_status !== "OPTIONAL_NOT_SUBMITTED")
    .forEach((element) => dom.reportElements.appendChild(reportElement(element, prefix)));
  dom.reportPdf.href = casePath("/report.pdf");
  dom.reportCsv.href = casePath("/report.csv");
  dom.reportResult.hidden = false;
}

function renderReportIssues(issues, prefix = "E") {
  dom.reportIssuesBody.replaceChildren();
  if (!issues.length) {
    dom.reportIssuesBody.innerHTML = '<tr><td colspan="6">No priority findings were identified.</td></tr>';
    return;
  }
  for (const issue of issues.slice(0, 30)) {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${badge(issue.priority || "")}</td><td>${artifactCode(issue.element_number, prefix)}</td><td>Rule ${esc(issue.rule_id || "")}</td><td>${badge(issue.status || "")}</td><td>${esc(issue.issue || "")}</td><td>${esc(issue.recommended_action || "")}</td>`;
    dom.reportIssuesBody.appendChild(row);
  }
}

function reportElement(element, prefix = "E") {
  const details = document.createElement("details");
  details.className = "element-block report-element";
  details.innerHTML = `
    <summary><span><strong>${artifactCode(element.element_number, prefix)} ${esc(element.element_name)}</strong><small>${esc(element.submission_requirement)} / ${element.checkpoint_summary.total_rules} rules</small></span>${badge(element.element_status)}</summary>
    <div class="element-body">
      <div class="important-finding ${element.top_issues?.length ? "has-issue" : ""}"><strong>Important finding</strong><p>${esc(element.top_issues?.[0]?.issue || "No material finding was identified for this artifact.")}</p></div>
      <div class="element-chart-row"></div>
      <div class="table-wrap"><table><thead><tr><th>Rule</th><th>Final</th><th>Source</th><th>Reason / Remark</th><th>Evidence Location</th><th>Action</th></tr></thead><tbody></tbody></table></div>
    </div>`;
  $(".element-chart-row", details).append(donut(element.checkpoint_summary || {}), assessment(element));
  const body = $("tbody", details);
  (element.checkpoint_results || []).forEach((rule) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td><strong>Rule ${esc(rule.rule_id)}</strong><small class="table-rule-description">${esc(rule.rule_description || "")}</small></td><td>${badge(rule.status)}</td><td>${esc(rule.final_decision_source || "AI")}</td><td>${esc(rule.reason || "")}</td><td>${esc(rule.final_evidence_location || "No location available")}</td><td>${esc(rule.recommended_action || "-")}</td>`;
    body.appendChild(row);
  });
  return details;
}

async function loadRules() {
  if (!isSuperAdmin()) {
    dom.rulesCount.textContent = "Rules are available to Super Admin only.";
    return;
  }
  dom.rulesCount.textContent = "Loading rules...";
  try {
    state.rules = await api(`/api/rules?standard_id=${encodeURIComponent(state.standardId)}`);
    state.reportPrefix = state.rules.artifact_prefix || state.reportPrefix || "E";
    renderRules();
  } catch (error) {
    dom.rulesCount.textContent = error.message;
  }
}

function renderRules() {
  const counts = state.rules.counts || {};
  const prefix = state.rules.artifact_prefix || "E";
  dom.rulesCount.textContent = `${state.rules.standard_name || formatStandard(state.standardId)}: ${counts.enabled_rules || 0} enabled / ${counts.rules || 0} total rules across ${counts.elements || 0} artifacts`;
  dom.ruleTabs.replaceChildren();
  (state.rules.elements || []).forEach((element) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rule-chip";
    button.classList.toggle("active", element.element_number === state.selectedElement);
    button.textContent = `${element.short_code} ${element.enabled_rule_count}/${element.rule_count}`;
    button.addEventListener("click", () => {
      state.selectedElement = element.element_number;
      closeRuleEditor();
      renderRules();
    });
    dom.ruleTabs.appendChild(button);
  });
  const selected = (state.rules.elements || []).find((item) => item.element_number === state.selectedElement) || state.rules.elements?.[0];
  if (!selected) return;
  state.selectedElement = selected.element_number;
  dom.ruleTitle.textContent = selected.element_name;
  dom.ruleApplicability.textContent = selected.applicability_rule || "";
  dom.ruleMeta.replaceChildren(...[artifactCode(selected.element_number, prefix), selected.short_code, `${selected.enabled_rule_count} enabled`, `${selected.rule_count - selected.enabled_rule_count} disabled`].map(tag));
  dom.ruleList.replaceChildren(...(selected.rules || []).map(ruleCard));
}

function ruleCard(rule) {
  const card = document.createElement("article");
  card.className = `rule-card ${rule.enabled ? "" : "is-disabled"}`;
  const refs = (rule.referenced_elements || []).map((number) => artifactCode(number, state.rules?.artifact_prefix)).join(", ");
  card.innerHTML = `<header><div><strong>Rule ${esc(rule.rule_id)}</strong>${rule.is_custom ? '<span class="tag">Custom</span>' : ""}</div><label class="switch"><input type="checkbox" ${rule.enabled ? "checked" : ""}><span></span><em>${rule.enabled ? "Enabled" : "Disabled"}</em></label></header><p>${esc(rule.description || "")}</p><footer><span>${refs ? `References ${esc(refs)}` : "Artifact-only rule"}</span><button type="button" class="button-secondary edit-rule">Edit</button></footer>`;
  $(".switch input", card).addEventListener("change", (event) => updateRule(rule, { enabled: event.target.checked }));
  $(".edit-rule", card).addEventListener("click", () => openRuleEditor(rule));
  return card;
}

async function saveRule(event) {
  event.preventDefault();
  const key = dom.ruleKey.value;
  const payload = {
    standard_id: state.rules?.standard_id || state.standardId,
    element_number: state.selectedElement,
    description: dom.ruleDescription.value.trim(),
    referenced_elements: $$('input[type="checkbox"]:checked', dom.ruleReferences).map((input) => Number(input.value)),
    enabled: dom.ruleEnabled.checked
  };
  dom.ruleFormStatus.textContent = "Saving...";
  try {
    await api(key ? `/api/rules/${encodeURIComponent(key)}` : "/api/rules", {
      method: key ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.rules = null;
    await loadRules();
    closeRuleEditor();
  } catch (error) {
    dom.ruleFormStatus.textContent = error.message;
    dom.ruleFormStatus.className = "form-status status-error";
  }
}

async function updateRule(rule, changes) {
  try {
    await api(`/api/rules/${encodeURIComponent(rule.rule_key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        description: changes.description ?? rule.description,
        referenced_elements: changes.referenced_elements ?? rule.referenced_elements,
        enabled: changes.enabled ?? rule.enabled
      })
    });
  } catch (error) {
    dom.rulesCount.textContent = error.message;
  } finally {
    state.rules = null;
    await loadRules();
  }
}

function openRuleEditor(rule = null) {
  dom.ruleReferences.replaceChildren(...(state.rules?.elements || [])
    .filter((element) => element.element_number !== state.selectedElement)
    .map((element) => {
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" value="${element.element_number}" ${(rule?.referenced_elements || []).map(Number).includes(element.element_number) ? "checked" : ""}><span>${artifactCode(element.element_number, state.rules?.artifact_prefix)} ${esc(element.short_code)}</span>`;
      return label;
    }));
  dom.ruleEditorTitle.textContent = rule ? `Edit Rule ${rule.rule_id}` : `Add Rule to ${artifactCode(state.selectedElement, state.rules?.artifact_prefix)}`;
  dom.ruleKey.value = rule?.rule_key || "";
  dom.ruleDescription.value = rule?.description || "";
  dom.ruleEnabled.checked = rule?.enabled ?? true;
  dom.ruleFormStatus.textContent = "";
  dom.ruleEditor.hidden = false;
  dom.ruleDescription.focus();
}

function closeRuleEditor() {
  dom.ruleEditor.hidden = true;
  dom.ruleForm.reset();
  dom.ruleReferences.replaceChildren();
}

async function api(url, options = {}) {
  const { expectNoContent = false, ...fetchOptions } = options;
  const headers = { ...(fetchOptions.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(url, { ...fetchOptions, headers });
  if (expectNoContent && response.status === 204) return null;
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || `Request failed with status ${response.status}.`);
  }
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Request failed.");
  return payload;
}

function caseApi(path, options) {
  return api(casePath(path), options);
}

function casePath(path = "") {
  return `/api/cases/${encodeURIComponent(state.caseId)}${path}`;
}

function uploadFormData() {
  const data = new FormData();
  data.append("standard_id", state.standardId);
  data.append("submission_level", dom.submissionLevel.value);
  if (state.caseId) data.append("case_id", state.caseId);
  for (const file of dom.fileInput.files) data.append("files", file);
  return data;
}

function changed(row) {
  return $(".review-decision", row).value !== row.dataset.aiStatus;
}

function renderMetrics(container, metrics) {
  container.replaceChildren(...metrics.map(([label, value]) => {
    const box = document.createElement("div");
    box.className = "metric";
    box.innerHTML = `<strong>${esc(value)}</strong><span>${esc(label)}</span>`;
    return box;
  }));
}

function renderKeyFields(fields, container) {
  const preferred = [["customer_part_number", "Customer Part No."], ["supplier_part_number", "Supplier Part No."], ["part_number", "Part No."], ["part_name", "Part Name"], ["customer_name", "Customer"], ["supplier_name", "Supplier"], ["drawing_number", "Drawing"], ["revision", "Revision"], ["reason_for_submission", "Reason for Submission"], ["cpk", "Cpk"], ["ppk", "Ppk / Pk"]];
  const nodes = preferred.filter(([key]) => fields[key]).map(([key, label]) => {
    const box = document.createElement("div");
    box.className = "key-field";
    box.innerHTML = `<span>${esc(label)}</span><strong>${esc(fields[key])}</strong>`;
    return box;
  });
  container.replaceChildren(...(nodes.length ? nodes : [emptyKeyField()]));
}

function donut(counts) {
  const values = [["pass", "Pass", "#1f7a5b"], ["flag", "Flag", "#b86b12"], ["not_found", "Not found", "#b42318"]].map(([key, label, color]) => ({ key, label, color, value: Number(counts[key] || 0) }));
  const total = values.reduce((sum, item) => sum + item.value, 0);
  let offset = 0;
  const circles = values.map((item) => {
    const pct = total ? (item.value / total) * 100 : 0;
    const circle = `<circle cx="21" cy="21" r="15.9" fill="none" stroke="${item.color}" stroke-width="5" pathLength="100" stroke-dasharray="${pct} ${100 - pct}" stroke-dashoffset="-${offset}" transform="rotate(-90 21 21)"><title>${item.label}: ${item.value}</title></circle>`;
    offset += pct;
    return circle;
  }).join("");
  const panel = document.createElement("div");
  panel.className = "donut-panel";
  panel.innerHTML = `<div class="donut-chart"><svg viewBox="0 0 42 42"><circle cx="21" cy="21" r="15.9" fill="none" stroke="#e4eaf0" stroke-width="5"></circle>${circles}</svg><div class="donut-center"><strong>${total}</strong><span>Rules</span></div></div><div class="chart-legend">${values.map((item) => `<span><i style="background:${item.color}"></i>${item.label}<strong>${item.value}</strong></span>`).join("")}</div>`;
  return panel;
}

function assessment(element) {
  const box = document.createElement("div");
  box.className = "chart-explanation";
  box.innerHTML = `<h4>Artifact Assessment</h4><p>${esc(element.narrative?.summary || element.element_summary || "No summary available.")}</p><p>${esc(element.narrative?.risk_statement || "")}</p>`;
  return box;
}

function reviewLocations(locations) {
  if (!locations.length) return '<span class="muted-cell">No evidence location returned.</span>';
  return locations.slice(0, 2).map((item) => `<div class="review-location-text"><strong>${esc([item.file_name || "Document", item.unit_id || ""].filter(Boolean).join(" / "))}</strong>${item.quote ? `<q>${esc(item.quote)}</q>` : ""}</div>`).join("");
}

function badge(value) {
  const text = String(value || "N/A");
  return `<span class="status-badge ${statusClass(text)}">${esc(text.replaceAll("_", " "))}</span>`;
}

function statusClass(value) {
  const text = String(value || "").toLowerCase();
  if (["pass", "registered", "complete", "success", "required", "ready", "high", "approved", "submitted", "mapped"].some((item) => text.includes(item))) return "success";
  if (["flag", "warning", "review", "duplicate", "optional", "medium", "in_review", "draft"].some((item) => text.includes(item))) return "warn";
  if (["error", "failed", "fail", "missing", "not_found", "critical"].some((item) => text.includes(item))) return "danger";
  return "info";
}

function startProcessingPhases() {
  const phases = [["upload", "Uploading and extracting documents..."], ["map", "Mapping / tagging standard artifacts..."]];
  let index = 0;
  setStep(phases[0][0]);
  busy(phases[0][1]);
  return setInterval(() => {
    index = Math.min(index + 1, phases.length - 1);
    setStep(phases[index][0]);
    busy(phases[index][1]);
  }, 2500);
}

function setStep(name, complete = false) {
  const active = stepOrder.indexOf(name);
  dom.steps.forEach((step) => {
    const index = stepOrder.indexOf(step.dataset.step);
    const already = step.classList.contains("is-complete");
    step.classList.toggle("is-complete", already || (active >= 0 && (index < active || (complete && index === active))));
    step.classList.toggle("is-active", active >= 0 && index === active && !complete);
  });
}

function showView(name) {
  Object.entries(dom.views).forEach(([view, node]) => {
    if (node) node.hidden = view !== name;
  });
  dom.navButtons.forEach((button) => {
    const matchView = button.dataset.view === name;
    const matchFilter = name !== "submissions" || (button.dataset.filter || "all") === state.submissionFilter;
    button.classList.toggle("active", matchView && matchFilter);
  });
}

async function loadCustomers() {
  const list = $("#customers-list");
  if (!list) return;
  list.innerHTML = "<p>Loading...</p>";
  try {
    const data = await api("/api/admin/customers");
    if (!data.items?.length) {
      list.innerHTML = "<p>No licensed customers yet.</p>";
      return;
    }
    list.innerHTML = data.items.map((item) => `
      <article class="admin-card">
        <header>
          <strong>${esc(item.company_name)}</strong>
          <code>${esc(item.license_key)}</code>
        </header>
        <p>Device: ${esc(item.device_id || "—")} · Host: ${esc(item.host_name || "—")} · Require device: ${item.require_device ? "ON" : "OFF"}</p>
        <p>Engineers: ${(item.engineers || []).map((e) => esc(e.email)).join(", ") || "—"}</p>
        <button type="button" class="button-secondary" data-add-eng="${esc(item.customer_id)}">Add engineer</button>
      </article>
    `).join("");
    list.querySelectorAll("[data-add-eng]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = $("#engineer-panel");
        const idInput = $("#eng-customer-id");
        if (panel) panel.hidden = false;
        if (idInput) idInput.value = btn.dataset.addEng;
      });
    });
  } catch (error) {
    list.innerHTML = `<p class="status-error">${esc(error.message)}</p>`;
  }
}

async function createCustomer(event) {
  event.preventDefault();
  const status = $("#customer-form-status");
  const creds = $("#customer-credentials");
  status.textContent = "Saving...";
  status.className = "form-status";
  try {
    const payload = await api("/api/admin/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_name: $("#cust-company").value.trim(),
        license_key: $("#cust-license").value.trim(),
        install_password: $("#cust-install-password").value,
        device_id: $("#cust-device").value.trim(),
        host_name: $("#cust-host").value.trim(),
        engineer_full_name: $("#cust-eng-name").value.trim(),
        engineer_email: $("#cust-eng-email").value.trim(),
        temporary_password: $("#cust-temp-password").value,
        require_device: $("#cust-require-device").checked,
        grant_full_access: $("#cust-grant-full").checked
      })
    });
    status.textContent = "Customer saved.";
    status.className = "form-status status-ok";
    if (creds && payload.credentials) {
      const c = payload.credentials;
      creds.hidden = false;
      creds.innerHTML = `
        <h3>Send to customer</h3>
        <p><strong>License key:</strong> <code>${esc(c.license_key)}</code></p>
        <p><strong>Install password:</strong> <code>${esc(c.install_password)}</code></p>
        <p><strong>Email:</strong> <code>${esc(c.email)}</code></p>
        <p><strong>Temporary password:</strong> <code>${esc(c.temporary_password)}</code></p>
      `;
    }
    event.target.reset();
    $("#cust-require-device").checked = true;
    $("#cust-grant-full").checked = true;
    await loadCustomers();
  } catch (error) {
    status.textContent = error.message;
    status.className = "form-status status-error";
  }
}

async function addEngineer(event) {
  event.preventDefault();
  const status = $("#engineer-form-status");
  const customerId = $("#eng-customer-id").value;
  status.textContent = "Saving...";
  try {
    await api(`/api/admin/customers/${encodeURIComponent(customerId)}/engineers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_name: $("#eng-name").value.trim(),
        email: $("#eng-email").value.trim(),
        temporary_password: $("#eng-temp").value,
        device_id: $("#eng-device").value.trim(),
        host_name: $("#eng-host").value.trim(),
        grant_full_access: true
      })
    });
    status.textContent = "Engineer added.";
    event.target.reset();
    $("#eng-customer-id").value = customerId;
    await loadCustomers();
  } catch (error) {
    status.textContent = error.message;
    status.className = "form-status status-error";
  }
}

async function loadPlatformUsers() {
  const list = $("#platform-users-list");
  if (!list) return;
  list.innerHTML = "<p>Loading...</p>";
  try {
    const data = await api("/api/admin/users");
    list.innerHTML = (data.items || []).map((user) => `
      <article class="admin-card">
        <header><strong>${esc(user.display_name || user.username)}</strong><span>${esc(user.role_label || user.role)}</span></header>
        <p>@${esc(user.username)}</p>
      </article>
    `).join("") || "<p>No users.</p>";
  } catch (error) {
    list.innerHTML = `<p class="status-error">${esc(error.message)}</p>`;
  }
}

async function createPlatformUser(event) {
  event.preventDefault();
  const status = $("#platform-user-status");
  status.textContent = "Saving...";
  try {
    await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("#pu-username").value.trim(),
        display_name: $("#pu-display").value.trim(),
        role: $("#pu-role").value,
        password: $("#pu-password").value
      })
    });
    status.textContent = "User saved.";
    event.target.reset();
    await loadPlatformUsers();
  } catch (error) {
    status.textContent = error.message;
    status.className = "form-status status-error";
  }
}

function showPanel(name) {
  Object.entries(dom.panels).forEach(([key, node]) => {
    if (node) node.hidden = key !== name;
  });
  dom.tabButtons.forEach((button) => button.classList.toggle("active", button.dataset.panel === name));
  if (name === "documents") loadMatrix();
  if (name === "issues") renderIssues(state.severity);
}

function busy(message) {
  dom.status.className = "";
  dom.status.innerHTML = `<span class="status-line"><span class="spinner"></span>${esc(message)}</span>`;
}

function showError(message) {
  dom.status.textContent = message;
  dom.status.className = "status-error";
}

function notice(message) {
  dom.notice.textContent = message;
  dom.notice.hidden = false;
}

function clearNotice() {
  dom.notice.textContent = "";
  dom.notice.hidden = true;
}

function clearReportNodes() {
  [dom.reportSummary, dom.reportKeyFields, dom.reportNarrative, dom.reportGlobalChart, dom.reportIssuesBody, dom.reportElements, dom.reportReadiness].forEach((node) => node && node.replaceChildren());
}

function tag(text) {
  const span = document.createElement("span");
  span.textContent = text;
  return span;
}

function artifactCode(value, prefix = state.reportPrefix || "E") {
  return `${prefix || "E"}${pad(value)}`;
}

function formatStandard(value) {
  return { aiag_ppap: "AIAG PPAP", vda_ppf: "VDA 2 PPF/PPA" }[value] || value || "-";
}

function emptyKeyField() {
  const box = document.createElement("div");
  box.className = "key-field";
  box.innerHTML = "<span>Package fields</span><strong>Not extracted</strong>";
  return box;
}

function localGreeting() {
  const hour = new Date().getHours();
  let prefix = "Good evening";
  if (hour < 12) prefix = "Good morning";
  else if (hour < 18) prefix = "Good afternoon";
  return `${prefix}, Team`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function pad(value) {
  return String(value || "").padStart(2, "0");
}

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
