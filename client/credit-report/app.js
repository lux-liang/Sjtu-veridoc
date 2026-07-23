const state = {
  dashboard: null,
  documentsLoaded: false,
  page: 1,
  pageSize: 25,
  total: 0,
  totalPages: 1,
  listController: null,
  detailController: null,
  currentDetailId: "",
  lastFocusedElement: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const conclusionMeta = {
  fail: { label: "规则异常", tone: "red", detail: "存在规则明确不一致" },
  possible: { label: "有造假可能", tone: "amber", detail: "存在需进一步确认的属性偏离" },
  no_automatic_anomaly: { label: "自动项未见异常", tone: "green", detail: "已执行自动项未发现偏离" },
  undetermined: { label: "尚待材料确认", tone: "slate", detail: "当前材料不足以形成稳定自动结论" },
};

const ruleStatusMeta = {
  fail: { label: "异常", longLabel: "规则异常", tone: "red" },
  possible: { label: "可能", longLabel: "有造假可能", tone: "amber" },
  manual: { label: "待确认", longLabel: "待人工确认", tone: "blue" },
  pass: { label: "通过", longLabel: "自动项通过", tone: "green" },
  not_applicable: { label: "不适用", longLabel: "不适用", tone: "slate" },
};

const variantMeta = {
  online_personal: "网银查询版个人征信",
  scanned_online_personal: "网银个人征信扫描件",
  pboc_print_personal: "人行打印版个人征信",
  online_enterprise: "网银查询版法人征信",
  leasing_enterprise: "租赁公司查询版法人征信",
  scanned_enterprise: "法人征信扫描件",
  unknown: "版本待确认",
};

const formatMeta = {
  original_electronic: "原始/电子 PDF",
  scanned_or_image: "扫描或图片版",
  unknown: "形态待确认",
};

const filterLabels = {
  conclusion: "报告结论",
  source_format: "材料形态",
  report_variant: "报告版本",
  review: "人工复核",
};

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function formatDate(value) {
  if (!value) return "结果时间待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "结果时间待确认";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function announce(message) {
  $("#global-announcer").textContent = message;
}

function setServiceStatus(status, text) {
  const container = $("#service-status");
  container.classList.remove("is-loading", "is-online", "is-error");
  container.classList.add(`is-${status}`);
  $("#service-text").textContent = text;
}

function syncThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  const button = $("#theme-toggle");
  button.setAttribute("aria-pressed", String(dark));
  button.setAttribute("aria-label", dark ? "切换到浅色主题" : "切换到深色主题");
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("veridoc-client-theme", next);
  syncThemeButton();
}

function goToView(view, updateHash = true, loadDocumentView = true) {
  const target = $(`#view-${view}`);
  if (!target) return;
  $$(".view").forEach((section) => {
    const active = section === target;
    section.hidden = !active;
    section.classList.toggle("active", active);
  });
  $$("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  if (updateHash) history.replaceState(null, "", `#${view}`);
  if (view === "documents" && loadDocumentView && !state.documentsLoaded) loadDocuments();
}

function conclusionBadge(value, large = false) {
  const meta = conclusionMeta[value] || conclusionMeta.undetermined;
  return `<span class="badge conclusion-${escapeHTML(value)}${large ? " badge-large" : ""}">${meta.label}</span>`;
}

function reviewBadge(row) {
  const count = Number(row.rule_status_counts?.manual || 0)
    + Number(row.material_requirement_count || 0);
  if (row.manual_review_required) {
    return `<span class="badge review-required">待确认 ${formatNumber(count)} 项</span>`;
  }
  return '<span class="badge review-complete">当前无需补充确认</span>';
}

function countsText(row) {
  const counts = row.rule_status_counts || {};
  const applicable = ["fail", "possible", "manual", "pass"]
    .reduce((total, key) => total + Number(counts[key] || 0), 0);
  return `适用 ${formatNumber(applicable)} 项 · 异常 ${formatNumber(counts.fail)} · 可能 ${formatNumber(counts.possible)} · 待确认 ${formatNumber(counts.manual)} · 通过 ${formatNumber(counts.pass)} · 不适用 ${formatNumber(counts.not_applicable)}`;
}

function compactCounts(row) {
  const counts = row.rule_status_counts || {};
  return `异常 ${formatNumber(counts.fail)} · 可能 ${formatNumber(counts.possible)} · 待确认 ${formatNumber(counts.manual)} · 通过 ${formatNumber(counts.pass)}`;
}

function primaryFindingHTML(row, compact = false) {
  const item = row.primary_finding;
  if (!item) return '<span class="muted">适用规则项未发现需处理结果</span>';
  const meta = ruleStatusMeta[item.status] || ruleStatusMeta.manual;
  return `<div class="primary-finding ${compact ? "compact" : ""}">
    <div><span class="rule-code">${escapeHTML(item.rule_id)}</span><strong>${escapeHTML(item.title)}</strong><span class="mini-status tone-${meta.tone}">${meta.longLabel}</span></div>
    ${compact ? "" : `<p>${escapeHTML(item.message)}</p>`}
  </div>`;
}

function metricCard(label, value, detail, conclusion, tone) {
  const filter = conclusion
    ? ` data-filter-conclusion="${escapeHTML(conclusion)}"`
    : ' data-jump-view="documents"';
  return `<button class="metric-card tone-${tone}" type="button"${filter}>
    <span>${escapeHTML(label)}</span><strong>${formatNumber(value)}</strong><small>${escapeHTML(detail)}</small><i aria-hidden="true">查看清单 →</i>
  </button>`;
}

function renderDashboard(data) {
  state.dashboard = data;
  const conclusions = data.risk_conclusion_counts || {};
  const attention = Number(conclusions.fail || 0) + Number(conclusions.possible || 0);
  $("#hero-total").textContent = formatNumber(data.total);
  $("#hero-attention").textContent = formatNumber(attention);
  $("#hero-rule-count").textContent = data.rule_count ? `${formatNumber(data.rule_count)} 项` : "按报告适用";
  $("#scope-rule-count").textContent = data.rule_count ? formatNumber(data.rule_count) : "相应";
  $("#generated-at").textContent = formatDate(data.generated_at);

  $("#conclusion-metrics").innerHTML = [
    metricCard("纳入报告", data.total, "本批次征信报告", "", "blue"),
    metricCard("规则异常", conclusions.fail, "规则明确不一致，需复核", "fail", "red"),
    metricCard("有造假可能", conclusions.possible, "属性偏离，不等于最终结论", "possible", "amber"),
    metricCard("自动项未见异常", conclusions.no_automatic_anomaly, "仍需关注该报告的待人工项", "no_automatic_anomaly", "green"),
    metricCard("尚待材料确认", conclusions.undetermined, "当前材料不足以形成稳定自动结论", "undetermined", "slate"),
  ].join("");

  const ruleCounts = data.rule_status_counts || {};
  const manualDocuments = Number(data.manual_review_required_count || 0);
  $("#review-summary").classList.remove("skeleton");
  $("#review-summary").innerHTML = `<div><span class="review-icon" aria-hidden="true">✓</span><div><strong>${formatNumber(manualDocuments)} 份报告存在待人工确认项</strong><p>报告结论与人工复核要求相互独立；当前共有 ${formatNumber(ruleCounts.manual)} 个规则项需结合原件或补充材料确认。</p></div></div><button class="text-button" type="button" data-review-filter="required">查看待人工项报告</button>`;

  renderRuleStats(ruleCounts);
  renderMaterialSummary(data.material_counts || {});
  renderFindings(data.top_findings || []);
  renderAttention(data.attention_rows || []);
  $("#dashboard-error").hidden = true;
}

function renderRuleStats(counts) {
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0) || 1;
  $("#rule-stat-bars").innerHTML = ["fail", "possible", "manual", "pass", "not_applicable"].map((key) => {
    const meta = ruleStatusMeta[key];
    const value = Number(counts[key] || 0);
    const percent = Math.round((value / total) * 1000) / 10;
    return `<div class="stat-row">
      <span>${meta.longLabel}</span>
      <div class="stat-track" role="progressbar" aria-label="${meta.longLabel} ${formatNumber(value)} 项" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${value}"><i class="tone-${meta.tone}" style="--bar-size:${percent}%"></i></div>
      <strong>${formatNumber(value)}</strong><small>${percent}%</small>
    </div>`;
  }).join("");
}

function distributionList(values, labels, total) {
  const entries = Object.entries(values || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '<div class="empty-inline">暂无材料构成数据</div>';
  return entries.map(([key, value]) => {
    const percent = total ? Math.round((Number(value) / total) * 1000) / 10 : 0;
    return `<div class="distribution-row"><span>${escapeHTML(labels[key] || key)}</span><div><i style="--bar-size:${percent}%"></i></div><strong>${formatNumber(value)}</strong></div>`;
  }).join("");
}

function renderMaterialSummary(materialCounts) {
  const total = Number(state.dashboard?.total || 0);
  $("#material-summary").innerHTML = `<section><h3>材料形态</h3>${distributionList(materialCounts.source_formats, formatMeta, total)}</section><section><h3>报告版本</h3>${distributionList(materialCounts.report_variants, variantMeta, total)}</section>`;
}

function renderFindings(findings) {
  $("#finding-list").innerHTML = findings.length
    ? findings.map((item) => `<button class="finding-item" type="button" data-rule-filter="${escapeHTML(item.rule_id)}"><span><b>${escapeHTML(item.rule_id)}</b><span>${escapeHTML(item.label)}</span></span><strong>${formatNumber(item.count)} 份</strong></button>`).join("")
    : '<div class="empty-inline">当前没有规则异常或有造假可能的规则项。</div>';
}

function renderAttention(rows) {
  $("#attention-list").innerHTML = rows.length
    ? rows.slice(0, 6).map((row) => `<article class="attention-item"><div><strong>${escapeHTML(row.document_id)}</strong>${conclusionBadge(row.risk_conclusion)}</div>${primaryFindingHTML(row, true)}<button class="text-button" type="button" data-detail="${escapeHTML(row.document_id)}">查看结果</button></article>`).join("")
    : '<div class="empty-inline">当前没有规则异常或有造假可能的报告。</div>';
}

async function loadDashboard() {
  setServiceStatus("loading", "正在连接核验结果");
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error("dashboard unavailable");
    const data = await response.json();
    renderDashboard(data);
    setServiceStatus("online", "本批次结果已连接");
    announce("本批次核验结果已更新");
  } catch (error) {
    setServiceStatus("error", "核验结果暂不可用");
    $("#dashboard-error").hidden = false;
    if (!state.dashboard) {
      $("#conclusion-metrics").innerHTML = "";
      $("#review-summary").classList.remove("skeleton");
      $("#review-summary").textContent = "结果加载失败，请稍后重试。";
      $("#rule-stat-bars").innerHTML = '<div class="empty-inline">规则项统计暂不可用。</div>';
      $("#material-summary").innerHTML = '<div class="empty-inline">材料构成暂不可用。</div>';
      $("#finding-list").innerHTML = '<div class="empty-inline">规则偏离暂不可用。</div>';
      $("#attention-list").innerHTML = '<div class="empty-inline">需关注报告清单暂不可用。</div>';
    }
    announce("本批次核验结果加载失败");
  }
}

function readFilters() {
  return {
    search: $("#search-filter").value.trim(),
    conclusion: $("#conclusion-filter").value,
    source_format: $("#format-filter").value,
    report_variant: $("#variant-filter").value,
    review: $("#review-filter").value,
  };
}

function buildDocumentQuery() {
  const params = new URLSearchParams();
  const filters = readFilters();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return params;
}

function setDocumentLoading() {
  $("#document-state").hidden = false;
  $("#document-state").innerHTML = '<div class="loading-state"><span class="spinner" aria-hidden="true"></span><strong>正在加载报告清单…</strong></div>';
  $("#document-table-wrap").hidden = true;
  $("#document-cards").hidden = true;
  $("#pagination").hidden = true;
}

function renderActiveFilters() {
  const filters = readFilters();
  const values = {
    conclusion: conclusionMeta[filters.conclusion]?.label,
    source_format: formatMeta[filters.source_format],
    report_variant: variantMeta[filters.report_variant],
    review: filters.review === "required" ? "存在待人工项" : filters.review === "not_required" ? "无需人工复核" : "",
  };
  const chips = [];
  if (filters.search) chips.push(`<button type="button" data-clear-filter="search" aria-label="清除搜索条件">搜索：${escapeHTML(filters.search)} ×</button>`);
  Object.entries(values).forEach(([key, value]) => {
    if (value) chips.push(`<button type="button" data-clear-filter="${key}" aria-label="清除${filterLabels[key]}筛选">${filterLabels[key]}：${escapeHTML(value)} ×</button>`);
  });
  $("#active-filters").innerHTML = chips.join("");
}

function documentTableRow(row) {
  return `<tr>
    <td><strong class="document-id">${escapeHTML(row.document_id)}</strong></td>
    <td>${escapeHTML(variantMeta[row.report_variant] || row.report_variant)}<small>${escapeHTML(formatMeta[row.source_format] || row.source_format)}</small></td>
    <td>${conclusionBadge(row.risk_conclusion)}</td>
    <td>${reviewBadge(row)}</td>
    <td><span class="counts-line">${escapeHTML(compactCounts(row))}</span></td>
    <td>${primaryFindingHTML(row)}</td>
    <td><button class="button table-action" type="button" data-detail="${escapeHTML(row.document_id)}">查看结果</button></td>
  </tr>`;
}

function documentCard(row) {
  return `<article class="document-card">
    <div class="document-card-head"><strong>${escapeHTML(row.document_id)}</strong>${conclusionBadge(row.risk_conclusion)}</div>
    <p>${escapeHTML(variantMeta[row.report_variant] || row.report_variant)} · ${escapeHTML(formatMeta[row.source_format] || row.source_format)}</p>
    <div class="card-review">${reviewBadge(row)}</div>
    ${primaryFindingHTML(row)}
    <small class="card-counts">${escapeHTML(compactCounts(row))}</small>
    <button class="button table-action" type="button" data-detail="${escapeHTML(row.document_id)}">查看核验结果</button>
  </article>`;
}

function renderPagination() {
  const container = $("#pagination");
  if (state.totalPages <= 1) {
    container.hidden = true;
    return;
  }
  const pages = [];
  const start = Math.max(1, state.page - 2);
  const end = Math.min(state.totalPages, state.page + 2);
  for (let page = start; page <= end; page += 1) {
    pages.push(`<button type="button" data-page="${page}"${page === state.page ? ' class="active" aria-current="page"' : ""}>${page}</button>`);
  }
  container.innerHTML = `<button type="button" data-page="${state.page - 1}" ${state.page === 1 ? "disabled" : ""}>上一页</button>${pages.join("")}<span>第 ${state.page} / ${state.totalPages} 页</span><button type="button" data-page="${state.page + 1}" ${state.page === state.totalPages ? "disabled" : ""}>下一页</button>`;
  container.hidden = false;
}

function renderDocuments(data) {
  state.documentsLoaded = true;
  state.page = data.page;
  state.pageSize = data.page_size;
  state.total = data.total;
  state.totalPages = data.total_pages;
  $("#page-size").value = String(state.pageSize);
  renderActiveFilters();

  if (!data.rows.length) {
    $("#document-count").textContent = "未找到符合条件的报告";
    $("#document-state").hidden = false;
    $("#document-state").innerHTML = '<div class="empty-state"><strong>没有符合当前条件的报告</strong><p>可调整筛选条件或重置后查看全部报告。</p><button class="button secondary" type="button" data-reset-filters>重置筛选</button></div>';
    $("#document-table-wrap").hidden = true;
    $("#document-cards").hidden = true;
    $("#pagination").hidden = true;
    return;
  }

  const first = (state.page - 1) * state.pageSize + 1;
  const last = Math.min(state.page * state.pageSize, state.total);
  $("#document-count").textContent = `显示 ${first}–${last} 份，共 ${formatNumber(state.total)} 份`;
  $("#document-state").hidden = true;
  $("#document-rows").innerHTML = data.rows.map(documentTableRow).join("");
  $("#document-cards").innerHTML = data.rows.map(documentCard).join("");
  $("#document-table-wrap").hidden = false;
  $("#document-cards").hidden = false;
  renderPagination();
  announce(`已加载第 ${state.page} 页，共 ${state.total} 份报告`);
}

async function loadDocuments({ resetPage = false } = {}) {
  if (resetPage) state.page = 1;
  state.pageSize = Number($("#page-size").value || 25);
  state.listController?.abort();
  state.listController = new AbortController();
  setDocumentLoading();
  renderActiveFilters();
  try {
    const response = await fetch(`/api/documents?${buildDocumentQuery()}`, {
      cache: "no-store",
      signal: state.listController.signal,
    });
    if (!response.ok) throw new Error("documents unavailable");
    renderDocuments(await response.json());
  } catch (error) {
    if (error.name === "AbortError") return;
    $("#document-count").textContent = "报告清单暂不可用";
    $("#document-state").hidden = false;
    $("#document-state").innerHTML = '<div class="error-state"><strong>报告清单加载失败</strong><p>请检查网络连接后重试，当前筛选条件会保留。</p><button class="button secondary" type="button" data-retry="documents">重新加载</button></div>';
    $("#document-table-wrap").hidden = true;
    $("#document-cards").hidden = true;
    $("#pagination").hidden = true;
    announce("报告清单加载失败");
  }
}

function resetFilters() {
  window.clearTimeout(searchTimer);
  $("#filter-form").reset();
  $("#page-size").value = "25";
  state.pageSize = 25;
  loadDocuments({ resetPage: true });
}

function applyConclusionFilter(value) {
  $("#conclusion-filter").value = value || "";
  goToView("documents", true, false);
  loadDocuments({ resetPage: true });
}

function applyReviewFilter(value) {
  $("#review-filter").value = value;
  goToView("documents", true, false);
  loadDocuments({ resetPage: true });
}

function applyRuleFilter(ruleId) {
  $("#search-filter").value = ruleId;
  goToView("documents", true, false);
  loadDocuments({ resetPage: true });
}

function openDrawer(trigger) {
  const drawer = $("#detail-drawer");
  state.lastFocusedElement = trigger || document.activeElement;
  drawer.hidden = false;
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  $("#main-content").inert = true;
  $(".topbar").inert = true;
  requestAnimationFrame(() => drawer.classList.add("open"));
  $(".close-button", drawer).focus();
}

function closeDrawer() {
  const drawer = $("#detail-drawer");
  state.detailController?.abort();
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
  $("#main-content").inert = false;
  $(".topbar").inert = false;
  window.setTimeout(() => { drawer.hidden = true; }, 180);
  if (state.lastFocusedElement?.isConnected) state.lastFocusedElement.focus();
}

function ruleResultCard(item, label = "规则表述") {
  const meta = ruleStatusMeta[item.status] || ruleStatusMeta.manual;
  const evidence = item.evidence || {};
  const evidenceRows = [
    ["规则要求", evidence.expected],
    ["实际结果", evidence.observed],
    ["原件位置", evidence.source_hint],
    ["复核建议", evidence.review_action],
  ].filter(([, value]) => value);
  return `<article class="rule-result status-${escapeHTML(item.status)}">
    <header>${item.rule_id ? `<span class="rule-code">${escapeHTML(item.rule_id)}</span>` : ""}<strong>${escapeHTML(item.title)}</strong><span class="mini-status tone-${meta.tone}">${meta.longLabel}</span></header>
    <p>${escapeHTML(item.message)}</p>
    <dl><div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(item.rule_level || "按专项核验规则执行")}</dd></div>${evidenceRows.map(([rowLabel, value]) => `<div><dt>${rowLabel}</dt><dd>${escapeHTML(value)}</dd></div>`).join("")}</dl>
  </article>`;
}

function renderDetail(row) {
  const meta = conclusionMeta[row.risk_conclusion] || conclusionMeta.undetermined;
  const groupOrder = ["fail", "possible", "manual", "pass", "not_applicable"];
  const actionableRisk = ["fail", "possible"].includes(row.risk_conclusion);
  const groups = groupOrder.map((group) => {
    const items = (row.rule_results || []).filter((item) => item.status === group);
    if (!items.length) return "";
    const open = group === "fail" || group === "possible" || (group === "manual" && !actionableRisk);
    const status = ruleStatusMeta[group];
    return `<details class="rule-group group-${group}" ${open ? "open" : ""}><summary><span>${status.longLabel}</span><strong>${items.length} 项</strong></summary><div class="rule-list">${items.map((item) => ruleResultCard(item)).join("")}</div></details>`;
  }).join("");
  const materialRequirements = (row.material_requirements || []).length
    ? `<section class="detail-rules"><div class="detail-section-heading"><h3>材料要求</h3><p>以下内容来自规则文件中的材料前提，不计入 22 项专项规则统计。</p></div><div class="rule-list">${row.material_requirements.map((item) => ruleResultCard(item, "材料前提")).join("")}</div></section>`
    : "";
  const pdfLink = row.pdf_url
    ? `<a class="button primary" target="_blank" rel="noopener noreferrer" href="${escapeHTML(row.pdf_url)}">查看授权原件</a>`
    : "";

  $("#drawer-content").innerHTML = `<article class="detail-report">
    <header class="detail-header"><p class="section-kicker">报告核验结果</p><h2 id="drawer-title">${escapeHTML(row.document_id)}</h2><p>核验编号 · ${escapeHTML(variantMeta[row.report_variant] || row.report_variant)} · ${escapeHTML(formatMeta[row.source_format] || row.source_format)}</p></header>
    <section class="detail-summary" aria-label="报告结论摘要"><div><span>报告结论</span>${conclusionBadge(row.risk_conclusion, true)}<small>${escapeHTML(meta.detail)}</small></div><div><span>补充确认</span>${reviewBadge(row)}<small>${row.manual_review_required ? "报告仍有规则项或材料要求需要结合原件确认" : "当前没有需要补充确认的适用项目"}</small></div></section>
    <section class="detail-counts"><strong>规则项汇总</strong><p>${escapeHTML(countsText(row))}</p></section>
    ${row.primary_finding ? `<section class="primary-panel"><h3>主要规则结果</h3>${primaryFindingHTML(row)}</section>` : ""}
    ${materialRequirements}
    <section class="detail-rules"><div class="detail-section-heading"><h3>逐条规则结果</h3><p>异常、可能与待确认项目默认展开，通过和不适用项目可按需查看。</p></div>${groups}</section>
    <footer class="detail-actions">${pdfLink}<button class="button secondary" type="button" data-close-drawer>关闭</button></footer>
  </article>`;
  $("#print-detail").hidden = false;
}

async function openDetail(documentId, trigger) {
  state.currentDetailId = documentId;
  state.detailController?.abort();
  state.detailController = new AbortController();
  openDrawer(trigger);
  $("#print-detail").hidden = true;
  $("#drawer-content").innerHTML = '<div class="drawer-loading"><span class="spinner" aria-hidden="true"></span><h2 id="drawer-title">正在加载核验结果</h2><p>请稍候…</p></div>';
  try {
    const response = await fetch(`/api/document?id=${encodeURIComponent(documentId)}`, {
      cache: "no-store",
      signal: state.detailController.signal,
    });
    if (!response.ok) throw new Error("detail unavailable");
    renderDetail((await response.json()).document);
    announce(`${documentId} 核验结果已加载`);
  } catch (error) {
    if (error.name === "AbortError") return;
    $("#drawer-content").innerHTML = `<div class="drawer-error"><h2 id="drawer-title">核验结果加载失败</h2><p>请检查网络连接后重试。</p><button class="button secondary" type="button" data-retry-detail="${escapeHTML(documentId)}">重新加载</button></div>`;
    announce(`${documentId} 核验结果加载失败`);
  }
}

function printDetail() {
  const groups = $$("#drawer-content details");
  const openStates = groups.map((element) => element.open);
  groups.forEach((element) => { element.open = true; });
  const restore = () => {
    groups.forEach((element, index) => { element.open = openStates[index]; });
    window.removeEventListener("afterprint", restore);
  };
  window.addEventListener("afterprint", restore);
  window.print();
  window.setTimeout(restore, 1000);
}

function trapDrawerFocus(event) {
  const drawer = $("#detail-drawer");
  if (drawer.hidden || event.key !== "Tab") return;
  const focusable = $$("button:not([disabled]), a[href], details > summary, [tabindex]:not([tabindex='-1'])", drawer)
    .filter((element) => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

let searchTimer = 0;

document.addEventListener("click", (event) => {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) goToView(viewButton.dataset.view);

  const jumpButton = event.target.closest("[data-jump-view]");
  if (jumpButton) goToView(jumpButton.dataset.jumpView);

  const conclusionButton = event.target.closest("[data-filter-conclusion]");
  if (conclusionButton) applyConclusionFilter(conclusionButton.dataset.filterConclusion);

  const reviewButton = event.target.closest("[data-review-filter]");
  if (reviewButton) applyReviewFilter(reviewButton.dataset.reviewFilter);

  const ruleButton = event.target.closest("[data-rule-filter]");
  if (ruleButton) applyRuleFilter(ruleButton.dataset.ruleFilter);

  const detailButton = event.target.closest("[data-detail]");
  if (detailButton) openDetail(detailButton.dataset.detail, detailButton);

  const retryButton = event.target.closest("[data-retry]");
  if (retryButton?.dataset.retry === "dashboard") loadDashboard();
  if (retryButton?.dataset.retry === "documents") loadDocuments();

  const retryDetail = event.target.closest("[data-retry-detail]");
  if (retryDetail) openDetail(retryDetail.dataset.retryDetail, retryDetail);

  const pageButton = event.target.closest("[data-page]");
  if (pageButton && !pageButton.disabled) {
    state.page = Number(pageButton.dataset.page);
    loadDocuments();
    $("#result-heading").focus?.();
  }

  const clearButton = event.target.closest("[data-clear-filter]");
  if (clearButton) {
    const key = clearButton.dataset.clearFilter;
    if (key === "search") $("#search-filter").value = "";
    if (key === "conclusion") $("#conclusion-filter").value = "";
    if (key === "source_format") $("#format-filter").value = "";
    if (key === "report_variant") $("#variant-filter").value = "";
    if (key === "review") $("#review-filter").value = "";
    loadDocuments({ resetPage: true });
  }

  if (event.target.closest("[data-reset-filters]")) resetFilters();
  if (event.target.closest("[data-close-drawer]")) closeDrawer();
});

$("#theme-toggle").addEventListener("click", toggleTheme);
$("#reset-filters").addEventListener("click", resetFilters);
$("#print-detail").addEventListener("click", printDetail);
$("#filter-form").addEventListener("submit", (event) => event.preventDefault());
$("#search-filter").addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadDocuments({ resetPage: true }), 300);
});
[$("#conclusion-filter"), $("#format-filter"), $("#variant-filter"), $("#review-filter"), $("#page-size")]
  .forEach((element) => element.addEventListener("change", () => loadDocuments({ resetPage: true })));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#detail-drawer").hidden) closeDrawer();
  trapDrawerFocus(event);
  const currentTab = event.target.closest?.("[role='tab']");
  if (currentTab && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
    event.preventDefault();
    const tabs = $$("[role='tab']");
    const current = tabs.indexOf(currentTab);
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(current + offset + tabs.length) % tabs.length];
    next.focus();
    goToView(next.dataset.view);
  }
});

syncThemeButton();
const initialView = ["overview", "documents", "scope"].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : "overview";
goToView(initialView, false);
loadDashboard();
