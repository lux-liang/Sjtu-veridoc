#!/usr/bin/env python3
"""Patch the deployed VeriDoc dashboard with evaluation and business views.

The web application currently lives only on the deployment host and is not
tracked in this repository.  This script applies an idempotent, source-level
patch to its index.html, app.js and styles.css files.
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path


EVAL_MARKER = 'data-veridoc-enhancement="labeled-evaluation-20260717"'
BUSINESS_MARKER = 'data-veridoc-enhancement="business-perspective-20260717"'
JS_MARKER = "VERIDOC_LABELED_EVALUATION_20260717"
DUAL_EVAL_JS_MARKER = "VERIDOC_DUAL_SCOPE_EVALUATION_20260718"
SEAL_REASON_JS_MARKER = "VERIDOC_COLOR_AGNOSTIC_SEAL_REASONS_20260717"
SEAL_CLASS_REASON_JS_MARKER = "VERIDOC_SEAL_CLASS_REASONS_20260718"
SEAL_DETAIL_JS_MARKER = "VERIDOC_SEAL_DETAIL_FIELDS_20260717"
SEAL_CLASS_DETAIL_JS_MARKER = "VERIDOC_SEAL_CLASS_DETAIL_FIELDS_20260718"
OCR_REASON_JS_MARKER = "VERIDOC_OCR_REASON_LABELS_20260717"
CSS_MARKER = "VERIDOC_HOMEPAGE_ENHANCEMENT_20260717"
DUAL_EVAL_CSS_MARKER = "VERIDOC_DUAL_SCOPE_CSS_20260718"
VISUAL_REFRESH_MARKER = 'data-veridoc-enhancement="visual-refresh-20260718-v2"'
VISUAL_REFRESH_JS_MARKER = "VERIDOC_VISUAL_REFRESH_20260718_V2"
VISUAL_REFRESH_CSS_MARKER = "VERIDOC_VISUAL_REFRESH_CSS_20260718_V2"
BUSINESS_ACCEPTANCE_MARKER = 'data-veridoc-enhancement="business-acceptance-20260719"'
BUSINESS_ACCEPTANCE_JS_MARKER = "VERIDOC_BUSINESS_ACCEPTANCE_20260719"
BUSINESS_ACCEPTANCE_CSS_MARKER = "VERIDOC_BUSINESS_ACCEPTANCE_CSS_20260719"
TECHNICAL_PERSPECTIVE_MARKER = 'data-veridoc-enhancement="technical-perspective-20260719"'
TECHNICAL_PERSPECTIVE_CSS_MARKER = "VERIDOC_TECHNICAL_PERSPECTIVE_CSS_20260719"


COMMAND_HERO_SECTION = r'''
        <section class="command-hero" data-veridoc-enhancement="visual-refresh-20260718-v2">
          <div class="command-hero-copy">
            <div class="hero-kicker"><span class="live-pulse"></span> REAL-TIME DOCUMENT FORENSICS</div>
            <h2>让材料核验从“一个分数”，升级为<br><em>可解释、可复核的证据链。</em></h2>
            <p>融合 PDF 对象结构、图像取证、印章语义、OCR 与业务勾稽，快速定位可疑材料，并保留每一条判定依据。</p>
            <div class="hero-actions">
              <button class="hero-button primary" type="button" data-jump-view="documents">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16M4 12h16M4 19h10"/><path d="m17 16 3 3-3 3"/></svg>
                进入样本核查
              </button>
              <button class="hero-button" type="button" data-jump-view="upload">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 16V4M8 8l4-4 4 4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>
                上传新材料
              </button>
              <button class="hero-link" type="button" data-jump-view="principles">查看检测原理 <span>↗</span></button>
            </div>
            <div class="hero-capabilities" aria-label="检测能力">
              <span>PDF 篡改</span><span>PS 痕迹</span><span>印章核验</span><span>征信报告</span><span>相似图片</span>
            </div>
          </div>

          <aside class="hero-console" aria-label="当前检测状态">
            <div class="console-head">
              <div><span class="console-label">SYSTEM SNAPSHOT</span><strong>核验引擎状态</strong></div>
              <span class="online-badge"><i></i> 服务在线</span>
            </div>
            <div class="console-focus">
              <div>
                <span>真实挑战口径 F1</span>
                <strong id="hero-audit-f1">—</strong>
                <small>去除显式 marker 证据后的同集审计</small>
              </div>
              <div class="console-ring" aria-hidden="true"><span id="hero-audit-recall">—</span><small>Recall</small></div>
            </div>
            <div class="console-stats">
              <div><span>已核验材料</span><strong id="hero-sample-count">—</strong></div>
              <div><span>中高风险</span><strong id="hero-risk-count">—</strong></div>
            </div>
            <div class="pipeline-status">
              <span><i class="done"></i>结构解析</span><b></b>
              <span><i class="done"></i>多模态取证</span><b></b>
              <span><i class="active"></i>证据融合</span>
            </div>
            <div class="console-foot"><span>数据与接口已连接</span><time id="hero-last-refresh">等待刷新</time></div>
          </aside>
        </section>

        <div class="overview-heading">
          <div><span>OVERVIEW</span><h2>核验数据概览</h2></div>
          <p>点击指标卡可直接筛选对应样本</p>
        </div>
'''


EVALUATION_SECTION = r'''
        <section class="panel eval-overview" id="labeled-evaluation" data-veridoc-enhancement="labeled-evaluation-20260717">
          <div class="panel-head">
            <div>
              <h2>带标签样本检测表现 · 双口径</h2>
              <span>全标签口径与去显式 marker 审计口径并列展示 · 风险分 ≥ 25 判定“疑似虚假”</span>
            </div>
            <span class="eval-scope">同一标签集 · 两种评分口径</span>
          </div>
          <div id="labeled-evaluation-body" class="eval-loading">
            正在核算 Accuracy、Precision、Recall 与 F1…
          </div>
        </section>
'''


BUSINESS_ACCEPTANCE_SECTION = r'''
        <section class="panel business-acceptance" id="business-acceptance" data-veridoc-enhancement="business-acceptance-20260719">
          <div class="panel-head">
            <div>
              <p class="eyebrow">BUSINESS ACCEPTANCE</p>
              <h2>五大业务分项指标 · 内部评测</h2>
              <span>去显式 marker 口径 · 当前结果尚未完成独立盲测验收</span>
            </div>
            <span class="acceptance-scope">未正式验收</span>
          </div>
          <div id="business-acceptance-body" class="acceptance-loading">正在核算五大业务分项指标…</div>
        </section>
'''


TECHNICAL_PERSPECTIVE_OPEN = r'''
        <section class="panel technical-perspective" data-veridoc-enhancement="technical-perspective-20260719">
          <div class="panel-head">
            <div>
              <p class="eyebrow">TECHNICAL PERSPECTIVE</p>
              <h2>技术视角：五类检测证据</h2>
            </div>
            <span>特征提取 → 触发判据 → 融合评分</span>
          </div>
          <p class="pr-lead">从实现侧统一说明系统如何读取 PDF 对象、页面像素、印章候选、OCR 坐标与业务字段，并将多类证据融合为可解释的风险结论。</p>
          <div class="technical-stack">
'''


TECHNICAL_PERSPECTIVE_CLOSE = r'''          </div>
        </section>
'''


BUSINESS_SECTION = r'''
        <section class="panel business-perspective" data-veridoc-enhancement="business-perspective-20260717">
          <div class="panel-head">
            <div>
              <p class="eyebrow">BUSINESS PERSPECTIVE</p>
              <h2>业务视角：五大算法产品线</h2>
            </div>
            <span>虚假类型 → 检测原理 → 技术路线</span>
          </div>
          <p class="pr-lead">技术五类回答“系统看什么证据”，业务五类回答“客户要解决什么问题”。每条产品线都先细分常见虚假类型，再把 PDF 结构、图像取证、OCR、版式与业务勾稽组合成可交付路线。</p>

          <div class="business-grid">
            <article class="business-card biz-pdf">
              <div class="business-card-head"><span class="biz-no">01</span><div><h3>PDF 篡改检测算法开发</h3><p>识别 PDF 被贴图、转换、拼页或二次编辑留下的结构与内容痕迹。</p></div><span class="biz-stage">已上线 · 迭代中</span></div>
              <div class="biz-type-grid">
                <div><strong>局部贴图 / 覆盖</strong><span>枚举图像对象、SMask、整页底图与小覆盖层组合，联动局部渲染差异。</span></div>
                <div><strong>Word / 图片转 PDF</strong><span>分析 Producer、字体对象、文本层、页面图像化比例与版式一致性，判断重导出来源。</span></div>
                <div><strong>增量更新 / 二次保存</strong><span>检查 EOF、startxref、xref 链、注释和嵌入对象，识别追加编辑痕迹。</span></div>
                <div><strong>页面拼接 / 替换</strong><span>比较多页尺寸、字体、元数据、背景纹理与页码连续性，定位异源页面。</span></div>
              </div>
              <div class="biz-route"><b>路线</b><span>对象层解析</span><i>→</i><span>多页渲染</span><i>→</i><span>文本层/OCR 对齐</span><i>→</i><span>跨页一致性</span><i>→</i><span>证据融合</span></div>
            </article>

            <article class="business-card biz-seal">
              <div class="business-card-head"><span class="biz-no">02</span><div><h3>虚假印章算法开发</h3><p>从红章贴图扩展到重绘章、复制章和黑白复印章。</p></div><span class="biz-stage next">重点攻关</span></div>
              <div class="biz-type-grid">
                <div><strong>PS 贴章</strong><span>检测透明蒙版、硬边、颜色过平、噪声割裂与印章区域的复制粘贴关系。</span></div>
                <div><strong>复制 / 重绘章</strong><span>利用圆椭圆几何、环形文字分布、局部相似检索和印章图像嵌入识别模板复用。</span></div>
                <div class="highlight"><strong>黑白扫描 / 复印章</strong><span>改用颜色无关的候选定位，扣出印章后分析半色调网点、纸张融合、边缘与灰度纹理，再做印章 OCR。</span></div>
                <div><strong>主体 / 位置错配</strong><span>把印章 OCR 的机构名称与合同甲乙方、发票销售方等主体勾稽，并校验盖章区域是否合理。</span></div>
              </div>
              <div class="biz-route"><b>路线</b><span>印章候选定位</span><i>→</i><span>抠图分割</span><i>→</i><span>极坐标展平/OCR</span><i>→</i><span>真假特征</span><i>→</i><span>主体与位置勾稽</span></div>
              <p class="biz-note">多模态大模型适合做候选定位兜底和弱标注；生产主链建议使用本地检测/分割模型，坐标更稳定、成本更低，也避免金融材料外发。</p>
            </article>

            <article class="business-card biz-invoice">
              <div class="business-card-head"><span class="biz-no">03</span><div><h3>PS 发票检测算法开发</h3><p>面向金额、税额、购销方、二维码、印章与签名等常规 PS 痕迹。</p></div><span class="biz-stage">部分可用</span></div>
              <div class="biz-type-grid">
                <div><strong>金额 / 税额篡改</strong><span>OCR 定位字段并校验“金额 + 税额 = 价税合计”，同时检查字体、基线与背景恢复痕迹。</span></div>
                <div><strong>购销方 / 税号替换</strong><span>模板区域约束 + 字体字号/字距一致性 + 税号格式与主体关联校验。</span></div>
                <div><strong>二维码 / 号码替换</strong><span>二维码解码内容与票面 OCR 交叉校验，检查局部图层、边界和清晰度突变。</span></div>
                <div><strong>印章 / 签名贴图</strong><span>联动印章检测、SMask、局部噪声和位置规则，形成跨域强证据。</span></div>
              </div>
              <div class="biz-route"><b>路线</b><span>发票版式识别</span><i>→</i><span>字段 OCR/坐标</span><i>→</i><span>勾稽校验</span><i>→</i><span>局部取证</span><i>→</i><span>票面一致性</span></div>
            </article>

            <article class="business-card biz-credit">
              <div class="business-card-head"><span class="biz-no">04</span><div><h3>征信报告篡改检测算法开发</h3><p>围绕字体、背景、行距、章节结构和跨页业务语义做基础检测。</p></div><span class="biz-stage next">需模板化</span></div>
              <div class="biz-type-grid">
                <div><strong>身份信息篡改</strong><span>OCR 抽取姓名、证件号、报告日期，校验字符形态、坐标与同页字体风格。</span></div>
                <div><strong>账户 / 逾期记录修改</strong><span>按章节识别表格行，检查列对齐、行距、余额/状态语义和上下文连续性。</span></div>
                <div><strong>遮盖 / 删除不良记录</strong><span>检测异常空白、背景纹理断裂、表格线修补、行号跳变和文本层缺口。</span></div>
                <div><strong>页面删插 / 拼接</strong><span>校验页码、报告编号、页眉页脚、页面尺寸、扫描噪声与章节顺序。</span></div>
              </div>
              <div class="biz-route"><b>路线</b><span>章节/版式解析</span><i>→</i><span>OCR 坐标</span><i>→</i><span>字体背景行距</span><i>→</i><span>跨页规则</span><i>→</i><span>语义复核</span></div>
            </article>

            <article class="business-card biz-similar">
              <div class="business-card-head"><span class="biz-no">05</span><div><h3>相似图片检测算法开发</h3><p>基于对比学习与局部匹配，发现重复提交、变体复用和跨材料贴图。</p></div><span class="biz-stage next">待建设</span></div>
              <div class="biz-type-grid">
                <div><strong>完全重复 / 近重复</strong><span>感知哈希快速召回，覆盖压缩、缩放、轻微亮度与色彩变化。</span></div>
                <div><strong>裁剪 / 旋转 / 加水印</strong><span>对比学习图像向量做全局相似检索，再用几何验证排除偶然相似。</span></div>
                <div><strong>局部复制 / 局部覆盖</strong><span>使用局部特征或密集向量匹配，输出复用区域而不只给整图相似分。</span></div>
                <div><strong>跨文档素材复用</strong><span>对印章、签名、票面截图等候选区域建向量库，发现一图多用和模板化造假。</span></div>
              </div>
              <div class="biz-route"><b>路线</b><span>pHash 粗召回</span><i>→</i><span>对比学习向量</span><i>→</i><span>向量库检索</span><i>→</i><span>局部几何验证</span><i>→</i><span>阈值校准</span></div>
            </article>
          </div>
        </section>
'''


EVALUATION_JS = r'''
// VERIDOC_LABELED_EVALUATION_20260717
// VERIDOC_DUAL_SCOPE_EVALUATION_20260718
const LABELED_RISK_THRESHOLD = 25;

function calculateLabeledMetrics(rows, scoreField, threshold = LABELED_RISK_THRESHOLD) {
  const labeled = rows.filter((row) => row.label === "fake" || row.label === "normal");
  const positive = (row) => Number(row[scoreField] ?? 0) >= threshold;
  const tp = labeled.filter((row) => row.label === "fake" && positive(row)).length;
  const fp = labeled.filter((row) => row.label === "normal" && positive(row)).length;
  const tn = labeled.filter((row) => row.label === "normal" && !positive(row)).length;
  const fn = labeled.filter((row) => row.label === "fake" && !positive(row)).length;
  const div = (a, b) => b ? a / b : 0;
  const precision = div(tp, tp + fp);
  const recall = div(tp, tp + fn);
  return {
    tp, fp, tn, fn,
    total: labeled.length,
    accuracy: div(tp + tn, labeled.length),
    precision,
    recall,
    f1: div(2 * precision * recall, precision + recall),
  };
}

function evalMetricCell(label, value, subtitle) {
  const display = value == null ? "—" : fmtPct(value);
  return `<div class="eval-compare-cell"><span>${label}</span><strong>${display}</strong><small>${subtitle}</small></div>`;
}

function evalScopeRow(name, note, metrics, tone) {
  return `<section class="eval-compare-row tone-${tone}">
    <div class="eval-compare-scope"><strong>${name}</strong><span>${note}</span></div>
    ${evalMetricCell("Accuracy", metrics?.accuracy, "整体正确率")}
    ${evalMetricCell("Precision", metrics?.precision, "疑似虚假命中率")}
    ${evalMetricCell("Recall", metrics?.recall, "虚假样本召回率")}
    ${evalMetricCell("F1", metrics?.f1, "P/R 调和均值")}
  </section>`;
}

function evalMatrix(metrics, title, coverage) {
  if (!metrics) return `<div class="eval-matrix unavailable"><strong>${title}</strong><p>当前产物未包含去 marker 风险分，请先重算检测结果。</p></div>`;
  return `<div class="eval-matrix" aria-label="${title}混淆矩阵">
    <div class="eval-matrix-title"><strong>${title}</strong><span>${coverage}</span></div>
    <div class="matrix-cell good"><span>TP 真阳性</span><b>${fmtNum(metrics.tp)}</b></div>
    <div class="matrix-cell bad"><span>FP 误报</span><b>${fmtNum(metrics.fp)}</b></div>
    <div class="matrix-cell bad"><span>FN 漏报</span><b>${fmtNum(metrics.fn)}</b></div>
    <div class="matrix-cell good"><span>TN 真阴性</span><b>${fmtNum(metrics.tn)}</b></div>
  </div>`;
}

function summaryMetrics(summary) {
  if (!summary?.sample_count) return null;
  const matrix = summary.confusion_matrix || {};
  return {
    tp: Number(matrix.tp || 0), fp: Number(matrix.fp || 0),
    tn: Number(matrix.tn || 0), fn: Number(matrix.fn || 0),
    total: Number(summary.sample_count || 0),
    accuracy: Number(summary.accuracy || 0), precision: Number(summary.precision || 0),
    recall: Number(summary.recall || 0), f1: Number(summary.f1 || 0),
  };
}

function paintLabeledEvaluation(target, fullMetrics, auditMetrics, meta) {
  target.className = "eval-body";
  target.innerHTML = `
    <div class="eval-compare" role="table" aria-label="双口径检测指标对比">
      ${evalScopeRow("全标签口径", "包含 synthetic / edited / training 等显式标记通道", fullMetrics, "full")}
      ${evalScopeRow("去 marker 审计口径", "同一批样本重新评分，不使用显式 marker 证据通道", auditMetrics, "audit")}
    </div>
    <div class="eval-detail-grid">
      ${evalMatrix(fullMetrics, "全标签混淆矩阵", meta.coverage)}
      ${evalMatrix(auditMetrics, "去 marker 混淆矩阵", meta.auditCoverage)}
    </div>
    <div class="eval-disclaimer">
      <strong>如何理解这两组数字</strong>
      <p>两组都在当前 ${fmtNum(meta.fakeCount)} 份虚假、${fmtNum(meta.normalCount)} 份正常标签上，以风险分 <b>≥ ${meta.threshold}</b> 判定“疑似虚假”。区别是审计口径重新计算了不使用显式 marker 通道的风险分，而不是简单删掉难样本。</p>
      <p class="warn">当前有 ${fmtNum(meta.markerDriven)} 份虚假样本命中显式标记，只有 ${fmtNum(meta.unmarkedFake)} 份虚假样本不依赖该标记来源。审计口径更接近真实任务，但仍不是独立来源、盲测、未见生产集上的泛化准确率。</p>
    </div>`;
}

async function renderLabeledEvaluation() {
  const target = document.querySelector("#labeled-evaluation-body");
  if (!target) return;
  target.className = "eval-loading";
  target.textContent = "正在核算 Accuracy、Precision、Recall 与 F1…";
  try {
    const summary = state.dashboard?.labeled_evaluation;
    if (summary?.sample_count) {
      const fullSummary = summary.full_set || summary;
      const auditSummary = summary.marker_free_audit;
      const fullMetrics = summaryMetrics(fullSummary);
      const auditMetrics = summaryMetrics(auditSummary);
      paintLabeledEvaluation(target, fullMetrics, auditMetrics, {
        threshold: Number(summary.threshold ?? LABELED_RISK_THRESHOLD),
        fakeCount: Number(summary.class_counts?.fake || 0),
        normalCount: Number(summary.class_counts?.normal || 0),
        markerDriven: Number(summary.marker_driven_fake_count || 0),
        unmarkedFake: Number(summary.unmarked_fake_count || 0),
        coverage: `后端评估产物 · ${fmtNum(summary.sample_count)} 份带标签样本`,
        auditCoverage: auditMetrics ? `同集重评分 · ${fmtNum(auditMetrics.total)} 份` : "待重算",
      });
      return;
    }
    const [fakeResponse, normalResponse] = await Promise.all([
      fetch("/api/documents?label=fake"),
      fetch("/api/documents?label=normal"),
    ]);
    if (!fakeResponse.ok || !normalResponse.ok) throw new Error("labeled sample API unavailable");
    const [fakeData, normalData] = await Promise.all([fakeResponse.json(), normalResponse.json()]);
    const fakeRows = fakeData.rows || [];
    const normalRows = normalData.rows || [];
    const rows = [...fakeRows, ...normalRows];
    const fullMetrics = calculateLabeledMetrics(rows, "combined_risk_score");
    const auditAvailable = rows.some((row) => row.marker_free_risk_score !== undefined && row.marker_free_risk_score !== "");
    const auditMetrics = auditAvailable ? calculateLabeledMetrics(rows, "marker_free_risk_score") : null;
    const markerDriven = fakeRows.filter((row) => String(row.combined_risk_reasons || "").includes("marker:")).length;
    const unmarkedFake = fakeRows.length - markerDriven;
    const complete = Number(fakeData.count) === fakeRows.length && Number(normalData.count) === normalRows.length;
    const coverage = complete
      ? `完整覆盖 ${fmtNum(fullMetrics.total)} 份带标签样本`
      : `接口返回 ${fmtNum(fullMetrics.total)} / ${fmtNum(Number(fakeData.count || 0) + Number(normalData.count || 0))} 份`;
    paintLabeledEvaluation(target, fullMetrics, auditMetrics, {
      threshold: LABELED_RISK_THRESHOLD,
      fakeCount: fakeRows.length,
      normalCount: normalRows.length,
      markerDriven,
      unmarkedFake,
      coverage,
      auditCoverage: auditMetrics ? `同集重评分 · ${fmtNum(auditMetrics.total)} 份` : "待重算",
    });
  } catch (error) {
    target.className = "eval-error";
    target.textContent = "带标签指标暂时无法加载，请检查 /api/documents 标签筛选接口。";
  }
}
'''


BUSINESS_ACCEPTANCE_JS = r'''
// VERIDOC_BUSINESS_ACCEPTANCE_20260719
const BUSINESS_ACCEPTANCE_META = {
  pdf_tamper: { no: "01", tone: "blue", short: "PDF" },
  fake_seal: { no: "02", tone: "red", short: "SEAL" },
  ps_invoice: { no: "03", tone: "amber", short: "INVOICE" },
  credit_report: { no: "04", tone: "green", short: "CREDIT" },
  similar_image: { no: "05", tone: "violet", short: "SIMILAR" },
};

function acceptanceMetric(label, value) {
  return `<div><span>${label}</span><strong>${value == null ? "—" : fmtPct(Number(value))}</strong></div>`;
}

function acceptanceCard(item) {
  const meta = BUSINESS_ACCEPTANCE_META[item.key] || { no: "—", tone: "blue", short: "BUSINESS" };
  const available = item.available !== false && item.accuracy != null;
  const accuracy = available ? fmtPct(Number(item.accuracy)) : "—";
  const fakeCount = Number(item.class_counts?.fake || 0);
  const normalCount = Number(item.class_counts?.normal || 0);
  const status = item.status || "unavailable";
  const meter = available ? Math.max(0, Math.min(100, Number(item.accuracy) * 100)) : 0;
  return `<article class="acceptance-card tone-${meta.tone} status-${status}" style="--acceptance-value:${meter}%">
    <div class="acceptance-card-head">
      <span class="acceptance-no">${meta.no}</span>
      <div><small>${meta.short}</small><h3>${esc(item.name || "业务分项")}</h3></div>
      <span class="acceptance-status">${esc(item.status_label || "内部评测 · 未验收")}</span>
    </div>
    <div class="acceptance-score">
      <div><span>Accuracy</span><strong>${accuracy}</strong><small>当前准确率</small></div>
      <div class="acceptance-ring" aria-label="准确率 ${accuracy}"><span>${available ? Math.round(meter) : "—"}</span><small>${available ? "%" : "N/A"}</small></div>
    </div>
    <div class="acceptance-submetrics">
      ${acceptanceMetric("Recall", item.recall)}
      ${acceptanceMetric("F1", item.f1)}
    </div>
    <div class="acceptance-samples"><span>虚假样本 <b>${fmtNum(fakeCount)}</b></span><span>正常样本 <b>${fmtNum(normalCount)}</b></span></div>
    <p>${esc(item.cohort || "暂无分项说明")}</p>
  </article>`;
}

function renderBusinessAcceptance(data) {
  const target = document.querySelector("#business-acceptance-body");
  if (!target) return;
  const summary = data?.business_acceptance;
  const items = summary?.items || [];
  if (!items.length) {
    target.className = "acceptance-error";
    target.textContent = "五大业务分项指标暂时不可用，请检查后端验收数据。";
    return;
  }
  target.className = "acceptance-body";
  target.innerHTML = `<div class="acceptance-grid">${items.map(acceptanceCard).join("")}</div>
    <div class="acceptance-note"><strong>验收说明</strong><span>${esc(summary.warning || "分项指标需结合 Recall、F1 与独立盲测共同验收。")}</span><b>判定阈值：风险分 ≥ ${Number(summary.threshold ?? LABELED_RISK_THRESHOLD)}</b></div>`;
}
'''


VISUAL_REFRESH_JS = r'''
// VERIDOC_VISUAL_REFRESH_20260718_V2
function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
}

function renderCommandHero(data) {
  const totals = data?.totals || {};
  const evaluation = data?.labeled_evaluation || {};
  const audit = evaluation.marker_free_audit || {};
  setText("#hero-audit-f1", audit.f1 == null ? "待重算" : fmtPct(audit.f1));
  setText("#hero-audit-recall", audit.recall == null ? "—" : fmtPct(audit.recall));
  setText("#hero-sample-count", fmtNum(totals.pdf_documents || evaluation.sample_count || 0));
  setText("#hero-risk-count", fmtNum(totals.high_or_medium_risk || 0));
  setText("#hero-last-refresh", `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date())}`);
}

function decorateMetricCards() {
  const details = [
    ["已进入统一核验底盘", "数据底盘"],
    ["当前标签为正常", "基准样本"],
    ["当前标签为疑似虚假", "重点复核"],
    ["由线上入口提交", "实时接入"],
    ["等待人工确认标签", "标注队列"],
    ["综合风险分 ≥ 25", "风险队列"],
  ];
  document.querySelectorAll("#metrics .metric").forEach((card, index) => {
    if (card.querySelector(".metric-context")) return;
    const [hint, badge] = details[index] || ["核验指标", "实时数据"];
    card.insertAdjacentHTML("beforeend", `<div class="metric-context"><small>${hint}</small><span>${badge}</span></div>`);
  });
}

document.addEventListener("click", (event) => {
  const control = event.target.closest("[data-jump-view]");
  if (!control) return;
  const view = control.dataset.jumpView;
  switchView(view);
  if (view === "documents") renderDocuments();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
'''


SEAL_REASON_JS = r'''
  // VERIDOC_COLOR_AGNOSTIC_SEAL_REASONS_20260717
  "visual:seal_color_agnostic_candidate": ["seal", "颜色无关印章候选（已定位）"],
  "visual:seal_monochrome_candidate": ["seal", "黑白扫描 / 复印章候选"],
  "visual:seal_candidate_hard_edge_review": ["seal", "印章候选硬边复核提示"],
  "visual:seal_ocr_entity_match_context": ["seal", "印章文字与材料主体匹配"],
  "visual:seal_ocr_entity_mismatch_review": ["seal", "印章文字与材料主体疑似不匹配"],
  "visual:seal_ocr_low_confidence_review": ["seal", "印章 OCR 低置信复核"],
'''


SEAL_CLASS_REASON_JS = r'''
  // VERIDOC_SEAL_CLASS_REASONS_20260718
  "visual:seal_candidate_likely_seal": ["seal", "候选分类：较可能为印章"],
  "visual:seal_candidate_likely_logo": ["seal", "候选分类：较可能为 Logo / 徽标"],
  "visual:seal_candidate_dense_square_nonseal": ["seal", "高密度方形图案（二维码等，非印章优先）"],
  "visual:seal_candidate_unknown_review": ["seal", "候选分类：未知，需人工复核"],
  "visual:seal_position_expected_context": ["seal", "盖章位置位于常见签署区域"],
  "visual:seal_position_unusual_review": ["seal", "盖章位置处于非常见页眉区域"],
'''


SEAL_DETAIL_JS = r'''
    // VERIDOC_SEAL_DETAIL_FIELDS_20260717
    ["印章候选页", row.seal_candidate_page ?? "-"],
    ["印章候选分", row.seal_candidate_best_score == null ? "-" : Number(row.seal_candidate_best_score).toFixed(3)],
    ["黑白/复印章候选", Number(row.seal_candidate_is_monochrome || 0) ? "是" : "否"],
    ["印章候选坐标", row.seal_candidate_bbox_norm || "-"],
    ["印章 OCR", row.seal_ocr_text || "-"],
    ["印章主体匹配", row.seal_entity_best_match || "-"],
    ["主体相似度", row.seal_entity_similarity === "" || row.seal_entity_similarity == null ? "-" : Number(row.seal_entity_similarity).toFixed(3)],
    ["AI 印章候选数", row.qwen_seal_count ?? "-"],
'''


SEAL_CLASS_DETAIL_JS = r'''
    // VERIDOC_SEAL_CLASS_DETAIL_FIELDS_20260718
    ["候选分类", ({seal: "印章", logo: "Logo / 徽标", unknown: "未知", none: "无"})[row.seal_candidate_class] || row.seal_candidate_class || "-"],
    ["分类置信度", row.seal_candidate_class_confidence === "" || row.seal_candidate_class_confidence == null ? "-" : fmtPct(Number(row.seal_candidate_class_confidence))],
    ["候选语义分", row.seal_candidate_semantic_score === "" || row.seal_candidate_semantic_score == null ? "-" : Number(row.seal_candidate_semantic_score).toFixed(3)],
    ["版面区域", ({top: "顶部", middle: "中部", bottom: "底部", none: "-"})[row.seal_candidate_zone] || row.seal_candidate_zone || "-"],
    ["位置语义", ({expected_signature_zone: "常见签署区", unusual_header_zone_review: "非常见页眉区", context_unknown: "待结合业务复核", not_applicable: "不适用"})[row.seal_position_assessment] || row.seal_position_assessment || "-"],
    ["重复图标数", row.seal_candidate_duplicate_count ?? "-"],
    ["OCR 已触发", Number(row.seal_ocr_triggered || 0) ? "是" : "否"],
'''


OCR_REASON_JS = r'''
  // VERIDOC_OCR_REASON_LABELS_20260717
  "ocr:training_synthetic_marker": ["marker", "OCR 识别到训练 / 合成标记"],
  "ocr:edited_marker": ["marker", "OCR 识别到编辑标记"],
  "ocr:void_marker": ["marker", "OCR 识别到作废标记"],
  "ocr:fake_marker": ["marker", "OCR 识别到伪造标记"],
  "ocr:ocr_credit_id_missing": ["ocr", "OCR 未稳定识别征信证件号"],
  "ocr:ocr_many_low_confidence_words": ["ocr", "OCR 低置信文字较多"],
  "ocr:ocr_low_mean_confidence": ["ocr", "OCR 平均置信度偏低"],
  "ocr:ocr_invoice_amount_logic_suspicious": ["biz", "OCR 发票金额关系可疑"],
  "ocr:ocr_contract_date_missing": ["ocr", "OCR 未稳定识别合同日期"],
'''


ENHANCEMENT_CSS = r'''

/* VERIDOC_HOMEPAGE_ENHANCEMENT_20260717 */
.eval-overview { overflow: hidden; position: relative; }
.eval-overview::before { content: ""; position: absolute; width: 260px; height: 260px; right: -100px; top: -150px; border-radius: 999px; background: radial-gradient(circle, rgba(99,102,241,.14), transparent 68%); pointer-events: none; }
.eval-scope { display: inline-flex; align-items: center; padding: 5px 11px; border-radius: 999px; color: var(--blue); background: #eef2ff; font-size: 12px; font-weight: 750; }
.eval-loading, .eval-error { padding: 22px; border: 1px dashed var(--line); border-radius: var(--r); color: var(--muted); background: #fbfcfe; text-align: center; }
.eval-error { color: var(--red); background: #fff5f6; border-color: #fecdd5; }
.eval-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px; }
.eval-stat { min-height: 126px; display: flex; flex-direction: column; justify-content: center; padding: 17px 18px; border: 1px solid var(--line-2); border-radius: var(--r-lg); background: linear-gradient(145deg, #fff, #f8faff); box-shadow: var(--shadow-sm); }
.eval-stat > span { color: var(--muted); font-size: 12px; font-weight: 750; letter-spacing: .05em; text-transform: uppercase; }
.eval-stat strong { margin: 7px 0 4px; font-size: 34px; line-height: 1; font-weight: 850; letter-spacing: -.035em; font-variant-numeric: tabular-nums; }
.eval-stat small { color: var(--faint); font-size: 11.5px; line-height: 1.45; }
.eval-stat.tone-sky strong { color: var(--sky); } .eval-stat.tone-violet strong { color: var(--violet); }
.eval-stat.tone-green strong { color: var(--green); } .eval-stat.tone-amber strong { color: var(--amber); }
.eval-detail-grid { display: grid; grid-template-columns: minmax(320px, .9fr) minmax(360px, 1.1fr); gap: 14px; margin-top: 14px; }
.eval-matrix, .eval-disclaimer { border: 1px solid var(--line-2); border-radius: var(--r); background: #fbfcfe; padding: 14px; }
.eval-matrix { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.eval-matrix-title { grid-column: 1 / -1; display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 3px; }
.eval-matrix-title strong { font-size: 13.5px; } .eval-matrix-title span { color: var(--faint); font-size: 11.5px; }
.matrix-cell { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 9px; font-size: 12px; }
.matrix-cell b { font-size: 19px; font-variant-numeric: tabular-nums; }
.matrix-cell.good { background: #ecfdf5; color: #047857; } .matrix-cell.bad { background: #fff1f2; color: #be123c; }
.eval-disclaimer strong { font-size: 13.5px; } .eval-disclaimer p { color: var(--muted); font-size: 12.5px; line-height: 1.65; margin: 8px 0 0; }
.eval-disclaimer p.warn { padding: 9px 11px; border-radius: 9px; color: #92400e; background: #fff7ed; border: 1px solid #fed7aa; }

.business-perspective { border-top: 3px solid var(--violet); }
.business-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 18px; }
.business-card { --biz-color: var(--blue); border: 1px solid var(--line); border-top: 3px solid var(--biz-color); border-radius: var(--r-lg); padding: 18px; background: linear-gradient(160deg, #fff 58%, #f8faff); box-shadow: var(--shadow-sm); }
.business-card.biz-pdf { --biz-color: var(--blue); } .business-card.biz-seal { --biz-color: var(--red); }
.business-card.biz-invoice { --biz-color: var(--amber); } .business-card.biz-credit { --biz-color: var(--green); }
.business-card.biz-similar { --biz-color: var(--violet); grid-column: 1 / -1; }
.business-card-head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: 12px; }
.biz-no { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px; color: #fff; background: var(--biz-color); font-size: 12px; font-weight: 850; box-shadow: 0 7px 16px -8px var(--biz-color); }
.business-card h3 { font-size: 16px; margin: 0 0 4px; } .business-card-head p { color: var(--muted); font-size: 12.5px; line-height: 1.55; margin: 0; }
.biz-stage { padding: 4px 9px; border-radius: 999px; background: #ecfdf5; color: #047857; font-size: 11px; font-weight: 750; white-space: nowrap; }
.biz-stage.next { color: #b45309; background: #fff7ed; }
.biz-type-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 15px; }
.biz-type-grid > div { min-height: 92px; padding: 11px 12px; border: 1px solid var(--line-2); border-radius: 10px; background: #fbfcfe; }
.biz-type-grid > div.highlight { border-color: #fecdd5; background: #fff5f6; }
.biz-type-grid strong { display: block; color: var(--text); font-size: 12.5px; margin-bottom: 5px; }
.biz-type-grid span { display: block; color: var(--muted); font-size: 11.8px; line-height: 1.55; }
.biz-route { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 13px; padding: 10px 11px; border-radius: 10px; background: color-mix(in srgb, var(--biz-color) 7%, transparent); }
.biz-route b { color: var(--biz-color); font-size: 12px; margin-right: 2px; } .biz-route span { font-size: 11.5px; font-weight: 650; } .biz-route i { color: var(--faint); font-style: normal; }
.biz-note { margin: 10px 0 0; padding: 9px 11px; border-radius: 9px; color: #7c2d12; background: #fff7ed; border: 1px solid #fed7aa; font-size: 11.8px; line-height: 1.6; }

@media (max-width: 980px) { .eval-metrics { grid-template-columns: repeat(2, 1fr); } .eval-detail-grid, .business-grid { grid-template-columns: 1fr; } .business-card.biz-similar { grid-column: auto; } }
@media (max-width: 620px) { .eval-metrics, .biz-type-grid { grid-template-columns: 1fr; } .business-card-head { grid-template-columns: auto 1fr; } .biz-stage { grid-column: 2; justify-self: start; } .eval-detail-grid { grid-template-columns: 1fr; } }

:root[data-theme="dark"] .eval-scope { background: rgba(129,140,248,.16); color: #a5b4fc; }
:root[data-theme="dark"] .eval-loading, :root[data-theme="dark"] .eval-matrix, :root[data-theme="dark"] .eval-disclaimer,
:root[data-theme="dark"] .eval-stat, :root[data-theme="dark"] .business-card, :root[data-theme="dark"] .biz-type-grid > div { background: #0f1728; }
:root[data-theme="dark"] .matrix-cell.good { background: rgba(5,150,105,.16); color: #34d399; }
:root[data-theme="dark"] .matrix-cell.bad { background: rgba(225,29,72,.14); color: #fb7185; }
:root[data-theme="dark"] .eval-disclaimer p.warn, :root[data-theme="dark"] .biz-note { background: rgba(217,119,6,.14); color: #fbbf24; border-color: #5a3f1a; }
:root[data-theme="dark"] .biz-type-grid > div.highlight { background: rgba(225,29,72,.10); border-color: #5a2233; }
:root[data-theme="dark"] .biz-stage { background: rgba(5,150,105,.18); color: #34d399; }
:root[data-theme="dark"] .biz-stage.next { background: rgba(217,119,6,.16); color: #fbbf24; }
'''


DUAL_EVAL_CSS = r'''

/* VERIDOC_DUAL_SCOPE_CSS_20260718 */
.eval-compare { display: grid; gap: 10px; margin-top: 16px; }
.eval-compare-row { display: grid; grid-template-columns: minmax(230px, 1.25fr) repeat(4, minmax(118px, 1fr)); border: 1px solid var(--line-2); border-left: 4px solid var(--blue); border-radius: var(--r-lg); overflow: hidden; background: #fbfcfe; box-shadow: var(--shadow-sm); }
.eval-compare-row.tone-audit { border-left-color: var(--amber); }
.eval-compare-scope, .eval-compare-cell { min-width: 0; padding: 15px 14px; }
.eval-compare-scope { display: flex; flex-direction: column; justify-content: center; background: color-mix(in srgb, var(--blue) 6%, transparent); }
.tone-audit .eval-compare-scope { background: color-mix(in srgb, var(--amber) 8%, transparent); }
.eval-compare-scope strong { font-size: 14px; }
.eval-compare-scope span { margin-top: 5px; color: var(--muted); font-size: 11.5px; line-height: 1.5; }
.eval-compare-cell { border-left: 1px solid var(--line-2); }
.eval-compare-cell span { display: block; color: var(--muted); font-size: 10.5px; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.eval-compare-cell strong { display: block; margin: 7px 0 3px; color: var(--blue); font-size: 27px; line-height: 1; font-weight: 850; font-variant-numeric: tabular-nums; }
.tone-audit .eval-compare-cell strong { color: var(--amber); }
.eval-compare-cell small { color: var(--faint); font-size: 10.5px; line-height: 1.35; }
.eval-detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.eval-disclaimer { margin-top: 14px; }
.eval-matrix.unavailable { display: block; color: var(--muted); }
.eval-matrix.unavailable p { margin: 8px 0 0; font-size: 12px; line-height: 1.6; }

@media (max-width: 980px) {
  .eval-compare { overflow-x: auto; padding-bottom: 4px; }
  .eval-compare-row { min-width: 870px; }
}
@media (max-width: 620px) { .eval-detail-grid { grid-template-columns: 1fr; } }

:root[data-theme="dark"] .eval-compare-row { background: #0f1728; }
'''


VISUAL_REFRESH_CSS = r'''

/* VERIDOC_VISUAL_REFRESH_CSS_20260718_V2 */
:root {
  --hero-ink: #0b1020;
  --hero-blue: #6673ff;
  --hero-cyan: #29c7e8;
  --surface-glass: rgba(255, 255, 255, .72);
}

body::before {
  content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none; opacity: .24;
  background-image: linear-gradient(rgba(99,102,241,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,.055) 1px, transparent 1px);
  background-size: 48px 48px; mask-image: linear-gradient(to bottom, #000, transparent 72%);
}

.topbar {
  min-height: 76px; padding: 13px max(24px, calc((100vw - 1520px) / 2)); gap: 16px;
  border-bottom-color: rgba(214,221,235,.78); box-shadow: 0 8px 30px -24px rgba(15,23,42,.42);
}
.topbar h1 { font-size: 18px; letter-spacing: -.015em; }
.brand-mark { width: 44px; height: 44px; border-radius: 14px; position: relative; }
.brand-mark::after { content: ""; position: absolute; inset: 5px; border: 1px solid rgba(255,255,255,.28); border-radius: 10px; }
.tabs { gap: 3px; padding: 4px; border-radius: 14px; }
.tab { display: inline-flex; align-items: center; gap: 7px; border-radius: 10px; padding: 9px 13px; }
.tab svg { width: 16px; height: 16px; }
.tab.active { box-shadow: 0 8px 20px -10px rgba(79,70,229,.8); }
.theme-toggle, .status { box-shadow: none; }
.status { padding: 9px 12px; font-size: 12px; }

main { margin-top: 22px; }
.panel { border-color: rgba(222,227,239,.9); box-shadow: 0 12px 34px -28px rgba(15,23,42,.5), 0 1px 2px rgba(15,23,42,.03); }
.panel-head { margin-bottom: 18px; }
.panel-head h2 { font-size: 17px; }
.panel-head h2::before { content: ""; width: 4px; height: 17px; border-radius: 9px; background: linear-gradient(180deg, var(--blue-2), var(--sky)); }

/* 首屏核验指挥台 */
.command-hero {
  position: relative; isolation: isolate; overflow: hidden; display: grid; grid-template-columns: minmax(0, 1.22fr) minmax(390px, .78fr);
  min-height: 390px; margin-bottom: 26px; border: 1px solid rgba(109,120,255,.22); border-radius: 26px;
  background: linear-gradient(120deg, #0c1328 0%, #111b38 48%, #142444 100%); color: #f8fbff;
  box-shadow: 0 30px 70px -38px rgba(30,41,100,.72); animation: heroReveal .58s cubic-bezier(.2,.7,.2,1) both;
}
.command-hero::before { content: ""; position: absolute; width: 520px; height: 520px; top: -290px; left: 36%; border-radius: 50%; background: radial-gradient(circle, rgba(98,111,255,.35), transparent 67%); z-index: -1; }
.command-hero::after { content: ""; position: absolute; width: 380px; height: 380px; right: -140px; bottom: -235px; border-radius: 50%; background: radial-gradient(circle, rgba(41,199,232,.25), transparent 68%); z-index: -1; }
.command-hero-copy { position: relative; padding: 48px 48px 40px; }
.command-hero-copy::after { content: ""; position: absolute; right: 6%; top: 16%; width: 150px; height: 150px; opacity: .1; border: 1px solid #fff; border-radius: 34px; transform: rotate(25deg); }
.hero-kicker { display: flex; align-items: center; gap: 9px; color: #aab7d8; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
.live-pulse { width: 8px; height: 8px; border-radius: 50%; background: #44dfb1; box-shadow: 0 0 0 5px rgba(68,223,177,.12); animation: livePulse 2s ease-in-out infinite; }
.command-hero h2 { position: relative; z-index: 1; max-width: 780px; margin-top: 20px; font-size: clamp(30px, 3vw, 48px); line-height: 1.17; font-weight: 850; letter-spacing: -.045em; }
.command-hero h2 em { color: #9ea8ff; font-style: normal; text-shadow: 0 0 32px rgba(126,137,255,.22); }
.command-hero-copy > p { position: relative; z-index: 1; max-width: 680px; margin-top: 18px; color: #aebad3; font-size: 14px; line-height: 1.8; }
.hero-actions { position: relative; z-index: 1; display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 27px; }
.hero-button { min-height: 44px; display: inline-flex; align-items: center; gap: 8px; padding: 0 17px; border: 1px solid rgba(190,201,229,.2); border-radius: 11px; color: #e8eefb; background: rgba(255,255,255,.075); transition: transform .18s, background .18s, border-color .18s; }
.hero-button svg { width: 17px; height: 17px; }
.hero-button:hover { transform: translateY(-2px); background: rgba(255,255,255,.12); border-color: rgba(190,201,229,.35); }
.hero-button.primary { border-color: transparent; color: #fff; background: linear-gradient(135deg, #5968f7, #7b68ee 62%, #32b9df); box-shadow: 0 12px 30px -14px rgba(92,108,255,.85); }
.hero-link { border: 0; padding: 10px 8px; color: #9eabd0; background: transparent; font-size: 13px; }
.hero-link:hover { color: #fff; }
.hero-capabilities { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 27px; }
.hero-capabilities span { padding: 5px 9px; border: 1px solid rgba(180,194,226,.14); border-radius: 7px; color: #8998bb; background: rgba(255,255,255,.035); font-size: 10.5px; }

.hero-console { align-self: stretch; min-width: 0; margin: 24px 24px 24px 0; padding: 22px; border: 1px solid rgba(190,202,230,.16); border-radius: 20px; background: rgba(7,13,29,.54); box-shadow: inset 0 1px rgba(255,255,255,.04); backdrop-filter: blur(18px); }
.console-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-bottom: 16px; border-bottom: 1px solid rgba(180,194,226,.12); }
.console-head > div { display: grid; gap: 4px; }
.console-label { color: #6f82ac; font-size: 9.5px; font-weight: 800; letter-spacing: .14em; }
.console-head strong { font-size: 15px; }
.online-badge { display: inline-flex; align-items: center; gap: 7px; padding: 6px 9px; border-radius: 999px; color: #80e7c7; background: rgba(47,201,157,.1); font-size: 10.5px; font-weight: 700; }
.online-badge i { width: 6px; height: 6px; border-radius: 50%; background: #44dfb1; box-shadow: 0 0 10px #44dfb1; }
.console-focus { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 16px; padding: 22px 2px 18px; }
.console-focus > div:first-child { display: grid; gap: 5px; }
.console-focus span { color: #8998bb; font-size: 11px; }
.console-focus strong { font-size: 38px; line-height: 1; letter-spacing: -.04em; color: #f5b75e; font-variant-numeric: tabular-nums; }
.console-focus small { color: #687b9f; font-size: 9.5px; line-height: 1.45; }
.console-ring { width: 76px; height: 76px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; background: radial-gradient(circle at center, #0c1730 52%, transparent 54%), conic-gradient(#f3aa46 0 19%, rgba(255,255,255,.08) 19% 100%); }
.console-ring span { color: #fff; font-size: 15px; font-weight: 800; }
.console-ring small { color: #7586a8; font-size: 9px; }
.console-stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; }
.console-stats > div { display: grid; gap: 5px; padding: 12px; border: 1px solid rgba(180,194,226,.1); border-radius: 10px; background: rgba(255,255,255,.035); }
.console-stats span { color: #7789ad; font-size: 10px; }
.console-stats strong { font-size: 19px; font-variant-numeric: tabular-nums; }
.pipeline-status { display: grid; grid-template-columns: auto 1fr auto 1fr auto; align-items: center; gap: 6px; margin: 18px 0 14px; }
.pipeline-status span { display: inline-flex; align-items: center; gap: 5px; color: #8998bb; font-size: 9.5px; white-space: nowrap; }
.pipeline-status i { width: 6px; height: 6px; border-radius: 50%; }
.pipeline-status i.done { background: #44dfb1; }.pipeline-status i.active { background: #6f7dff; box-shadow: 0 0 0 4px rgba(111,125,255,.12); }
.pipeline-status b { height: 1px; background: linear-gradient(90deg, rgba(68,223,177,.55), rgba(111,125,255,.18)); }
.console-foot { display: flex; justify-content: space-between; gap: 12px; padding-top: 12px; border-top: 1px solid rgba(180,194,226,.1); color: #617295; font-size: 9.5px; }

.overview-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin: 0 2px 13px; }
.overview-heading > div { display: grid; gap: 3px; }
.overview-heading span { color: var(--blue); font-size: 9px; font-weight: 850; letter-spacing: .16em; }
.overview-heading h2 { font-size: 20px; letter-spacing: -.02em; }
.overview-heading p { color: var(--muted); font-size: 11.5px; }

/* 指标卡重新分层 */
.metrics { gap: 12px; margin-bottom: 22px; }
.metric { min-height: 138px; display: grid; grid-template-columns: auto 1fr; grid-template-rows: auto auto 1fr; align-items: start; gap: 2px 13px; padding: 18px; border-radius: 16px; background: linear-gradient(145deg, var(--panel), color-mix(in srgb, var(--blue) 2.5%, var(--panel))); }
.metric::after { content: ""; position: absolute; width: 82px; height: 82px; right: -37px; top: -37px; border-radius: 50%; background: color-mix(in srgb, currentColor 7%, transparent); }
.metric::before { inset: auto 16px 0; width: auto; height: 3px; border-radius: 3px 3px 0 0; opacity: .45; }
.metric.clickable:hover::before { opacity: 1; }
.metric .m-ico { grid-row: 1 / span 2; width: 42px; height: 42px; border-radius: 12px; box-shadow: 0 9px 18px -10px rgba(15,23,42,.65); }
.metric .label { align-self: end; font-size: 11.5px; }
.metric .value { font-size: 30px; }
.metric-context { grid-column: 1 / -1; align-self: end; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 13px; margin-top: 9px; border-top: 1px solid var(--line-2); }
.metric-context small { overflow: hidden; color: var(--faint); font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }
.metric-context span { padding: 3px 6px; border-radius: 5px; color: var(--muted); background: color-mix(in srgb, var(--blue) 6%, transparent); font-size: 8.5px; font-weight: 750; white-space: nowrap; }

/* 评估区作为首页核心信息 */
.eval-overview { margin-bottom: 18px; padding: 24px; border-color: rgba(99,102,241,.22); }
.eval-overview::after { content: "MODEL EVALUATION"; position: absolute; right: 24px; bottom: 12px; color: color-mix(in srgb, var(--blue) 7%, transparent); font-size: 34px; font-weight: 900; letter-spacing: -.04em; pointer-events: none; }
.eval-compare { position: relative; z-index: 1; gap: 12px; }
.eval-compare-row { min-height: 120px; border-left-width: 0; border-radius: 15px; box-shadow: none; }
.eval-compare-row.tone-full { border-color: color-mix(in srgb, var(--blue) 22%, var(--line-2)); }
.eval-compare-row.tone-audit { position: relative; border: 1px solid color-mix(in srgb, var(--amber) 40%, var(--line-2)); box-shadow: 0 14px 30px -27px rgba(217,119,6,.7); }
.eval-compare-row.tone-audit::before { content: "推荐关注"; position: absolute; right: 12px; top: 9px; padding: 3px 7px; border-radius: 5px; color: #b45309; background: #fff7ed; font-size: 8.5px; font-weight: 800; z-index: 2; }
.eval-compare-scope { padding: 18px; }
.eval-compare-scope strong { font-size: 15px; }
.eval-compare-cell { display: flex; flex-direction: column; justify-content: center; }
.eval-compare-cell strong { font-size: 30px; }
.eval-detail-grid { gap: 12px; }
.eval-matrix { padding: 16px; border-radius: 14px; }
.matrix-cell { padding: 12px 13px; }
.eval-disclaimer { position: relative; z-index: 1; border-style: dashed; }

/* 内容面板、图表与表格 */
.grid.two { gap: 14px; margin-bottom: 14px; }
.bars { gap: 7px; }
.bar-row { padding: 7px 8px; }
.bar-row.interactive:hover { transform: translateX(2px); }
.track { height: 8px; }
.reason { min-height: 43px; border-color: var(--line-2); }
.reason::before { content: ""; width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: var(--amber); box-shadow: 0 0 0 4px color-mix(in srgb, var(--amber) 10%, transparent); }
.reason span { margin-right: auto; }

#view-documents > .panel, #view-upload .panel, #view-principles > .panel, #view-principles > article { border-radius: 18px; }
.filters { padding: 7px; border: 1px solid var(--line-2); border-radius: 13px; background: color-mix(in srgb, var(--blue) 2%, var(--panel)); }
.filters input, .filters select { min-height: 39px; border-color: transparent; box-shadow: none; }
.filters input { min-width: 310px; }
.table-wrap { max-height: calc(100vh - 205px); border-radius: 13px; }
thead th { top: 0; padding-top: 14px; padding-bottom: 14px; backdrop-filter: blur(10px); }
tbody tr:nth-child(even) { background: color-mix(in srgb, var(--blue) 1.8%, transparent); }
tbody tr:hover { background: color-mix(in srgb, var(--blue) 6%, transparent); }
tbody td:first-child { max-width: 230px; font-weight: 650; word-break: break-word; }
.badge { border: 1px solid transparent; }
.pdf-link { display: inline-flex; align-items: center; gap: 4px; }
.pdf-link:not(.disabled)::before { content: "◉"; font-size: 8px; }

.drawer::before { background: rgba(5,10,22,.55); backdrop-filter: blur(4px); }
.drawer-card { width: min(650px, 100%); padding: 30px 32px 50px; border-left: 1px solid var(--line); }
#detail-title { padding-bottom: 16px; border-bottom: 1px solid var(--line-2); font-size: 20px; }
.score-hero { border-radius: 15px; }
.ev-section { margin-top: 20px; }
.detail-grid { gap: 8px; }
.detail-grid > div { min-height: 66px; border-color: var(--line-2); background: color-mix(in srgb, var(--blue) 1.6%, var(--panel)); }

.upload-box { gap: 14px; }
.drop-zone { min-height: 285px; border-radius: 17px; background: linear-gradient(145deg, color-mix(in srgb, var(--blue) 3%, var(--panel)), var(--panel)); }
#upload-button { min-height: 46px; border-radius: 11px; box-shadow: 0 12px 24px -15px rgba(79,70,229,.65); }
.pr-intro { overflow: hidden; background: linear-gradient(135deg, var(--panel), color-mix(in srgb, var(--blue) 5%, var(--panel))); }
.business-card { transition: transform .2s, box-shadow .2s, border-color .2s; }
.business-card:hover { transform: translateY(-3px); box-shadow: 0 18px 38px -30px color-mix(in srgb, var(--biz-color) 60%, #000); border-color: color-mix(in srgb, var(--biz-color) 35%, var(--line)); }
.biz-no { border-radius: 12px; }
.biz-type-grid > div { transition: transform .18s, border-color .18s; }
.biz-type-grid > div:hover { transform: translateY(-2px); border-color: color-mix(in srgb, var(--biz-color) 30%, var(--line-2)); }

@keyframes heroReveal { from { opacity: 0; transform: translateY(12px) scale(.995); } to { opacity: 1; transform: none; } }
@keyframes livePulse { 50% { box-shadow: 0 0 0 9px rgba(68,223,177,0); } }

@media (max-width: 1220px) {
  .command-hero { grid-template-columns: minmax(0, 1fr) 390px; }
  .command-hero-copy { padding: 42px 36px 36px; }
  .metrics { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 960px) {
  .topbar { grid-template-columns: 1fr auto auto; }
  .tabs { grid-column: 1 / -1; grid-row: 2; justify-self: center; }
  .command-hero { grid-template-columns: 1fr; }
  .hero-console { margin: 0 24px 24px; }
  .eval-compare { overflow: visible; }
  .eval-compare-row { min-width: 0; grid-template-columns: minmax(190px, 1fr) repeat(4, minmax(105px, .8fr)); }
  .eval-compare-scope, .eval-compare-cell { padding: 13px 11px; }
}
@media (max-width: 720px) {
  .topbar { display: grid; grid-template-columns: minmax(0, 1fr) auto; padding: 12px 14px 10px; }
  .topbar h1 { max-width: 220px; overflow: hidden; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
  .topbar .eyebrow, .status { display: none; }
  .theme-toggle { grid-column: 2; grid-row: 1; }
  .tabs { grid-column: 1 / -1; grid-row: 2; width: 100%; justify-content: flex-start; overflow-x: auto; border: 0; border-radius: 10px; background: transparent; box-shadow: none; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .tab { flex: 0 0 auto; padding: 8px 12px; }
  main { width: calc(100% - 22px); margin-top: 12px; }
  .command-hero { min-height: 0; border-radius: 20px; }
  .command-hero-copy { padding: 30px 22px 26px; }
  .command-hero h2 { margin-top: 16px; font-size: 30px; }
  .command-hero-copy > p { font-size: 13px; }
  .hero-actions { align-items: stretch; }
  .hero-button { justify-content: center; flex: 1 1 150px; }
  .hero-link { width: 100%; text-align: left; }
  .hero-capabilities { margin-top: 20px; }
  .hero-console { margin: 0 12px 12px; padding: 17px; border-radius: 15px; }
  .console-focus strong { font-size: 32px; }
  .pipeline-status { overflow-x: auto; }
  .overview-heading { align-items: flex-start; }
  .overview-heading p { display: none; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
  .metric { min-height: 128px; padding: 14px; }
  .metric .m-ico { width: 36px; height: 36px; }
  .metric .value { font-size: 25px; }
  .metric-context { align-items: flex-start; }
  .metric-context small { white-space: normal; }
  .metric-context span { display: none; }
  .eval-overview { padding: 17px; }
  .eval-overview .panel-head { align-items: flex-start; }
  .eval-scope { display: none; }
  .eval-compare-row { grid-template-columns: repeat(2, 1fr); border-radius: 13px; overflow: visible; }
  .eval-compare-scope { grid-column: 1 / -1; min-height: 80px; border-radius: 12px 12px 0 0; }
  .eval-compare-cell { min-height: 90px; border-top: 1px solid var(--line-2); border-left: 0; }
  .eval-compare-cell:nth-child(even) { border-right: 1px solid var(--line-2); }
  .eval-compare-cell strong { font-size: 26px; }
  .eval-detail-grid { grid-template-columns: 1fr; }
  .eval-overview::after { display: none; }
  .panel { padding: 16px; }
  .panel-head { align-items: flex-start; }
  .filters { width: 100%; }
  .filters input, .filters select { flex: 1 1 140px; min-width: 0; width: auto; }
  .table-wrap { max-height: none; }
  .drawer-card { padding: 22px 18px 40px; }
  .business-card { padding: 15px; }
}
@media (max-width: 430px) {
  .brand-mark { width: 38px; height: 38px; }
  .brand-mark svg { width: 21px; height: 21px; }
  .command-hero h2 { font-size: 27px; }
  .console-ring { width: 68px; height: 68px; }
  .console-stats { grid-template-columns: 1fr 1fr; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .metric { min-height: 120px; }
  .metric-context { padding-top: 9px; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
}

:root[data-theme="dark"] { --surface-glass: rgba(18,26,43,.72); }
:root[data-theme="dark"] body::before { opacity: .15; }
:root[data-theme="dark"] .panel { border-color: rgba(39,52,78,.9); }
:root[data-theme="dark"] .metric { background: linear-gradient(145deg, var(--panel), color-mix(in srgb, var(--blue) 3%, var(--panel))); }
:root[data-theme="dark"] .metric-context span { background: rgba(129,140,248,.1); }
:root[data-theme="dark"] .eval-compare-row.tone-audit::before { color: #fbbf24; background: rgba(217,119,6,.17); }
:root[data-theme="dark"] .filters { background: rgba(129,140,248,.035); }
:root[data-theme="dark"] tbody tr:nth-child(even) { background: rgba(129,140,248,.025); }
:root[data-theme="dark"] .detail-grid > div { background: rgba(129,140,248,.025); }
'''


BUSINESS_ACCEPTANCE_CSS = r'''

/* VERIDOC_BUSINESS_ACCEPTANCE_CSS_20260719 */
.business-acceptance { position: relative; overflow: hidden; margin-bottom: 18px; padding: 24px; border-top: 3px solid var(--sky); }
.business-acceptance::after { content: "ACCEPTANCE"; position: absolute; right: 24px; top: 14px; color: color-mix(in srgb, var(--sky) 7%, transparent); font-size: 30px; font-weight: 900; letter-spacing: -.04em; pointer-events: none; }
.acceptance-scope { display: inline-flex; align-items: center; padding: 5px 11px; border-radius: 999px; color: #0369a1; background: #e0f2fe; font-size: 12px; font-weight: 750; }
.acceptance-loading, .acceptance-error { padding: 22px; border: 1px dashed var(--line); border-radius: var(--r); color: var(--muted); background: #fbfcfe; text-align: center; }
.acceptance-error { color: var(--red); background: #fff5f6; border-color: #fecdd5; }
.acceptance-grid { position: relative; z-index: 1; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-top: 17px; }
.acceptance-card { --acceptance-color: var(--blue); min-width: 0; display: flex; flex-direction: column; padding: 16px; border: 1px solid var(--line-2); border-top: 3px solid var(--acceptance-color); border-radius: 15px; background: linear-gradient(155deg, var(--panel), color-mix(in srgb, var(--acceptance-color) 4%, var(--panel))); box-shadow: var(--shadow-sm); }
.acceptance-card.tone-blue { --acceptance-color: var(--blue); } .acceptance-card.tone-red { --acceptance-color: var(--red); }.acceptance-card.tone-amber { --acceptance-color: var(--amber); } .acceptance-card.tone-green { --acceptance-color: var(--green); }.acceptance-card.tone-violet { --acceptance-color: var(--violet); }
.acceptance-card-head { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 9px; align-items: start; }.acceptance-no { grid-row: 1 / span 2; width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center; border-radius: 9px; color: #fff; background: var(--acceptance-color); font-size: 10px; font-weight: 850; }
.acceptance-card-head small { display: block; color: var(--acceptance-color); font-size: 8.5px; font-weight: 850; letter-spacing: .12em; }.acceptance-card h3 { min-height: 36px; margin: 3px 0 0; font-size: 14px; line-height: 1.35; }
.acceptance-status { grid-column: 1 / -1; justify-self: start; margin-top: 2px; padding: 4px 7px; border-radius: 6px; color: #92400e; background: #fff7ed; font-size: 9px; font-weight: 800; }.status-insufficient .acceptance-status, .status-unavailable .acceptance-status { color: #64748b; background: #f1f5f9; }
.acceptance-score { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 9px; margin-top: 15px; padding: 13px 0; border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }.acceptance-score > div:first-child { display: grid; gap: 3px; }
.acceptance-score span { color: var(--muted); font-size: 9px; font-weight: 750; letter-spacing: .05em; text-transform: uppercase; }.acceptance-score strong { color: var(--acceptance-color); font-size: 26px; line-height: 1; font-weight: 850; letter-spacing: -.035em; font-variant-numeric: tabular-nums; }.acceptance-score small { color: var(--faint); font-size: 9px; }
.acceptance-ring { width: 54px; height: 54px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; background: radial-gradient(circle at center, var(--panel) 57%, transparent 59%), conic-gradient(var(--acceptance-color) var(--acceptance-value), var(--line-2) 0); }.acceptance-ring span { color: var(--text); font-size: 13px; font-weight: 850; line-height: 1; }.acceptance-ring small { color: var(--faint); font-size: 8px; }
.acceptance-submetrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin-top: 10px; }.acceptance-submetrics > div { padding: 8px; border-radius: 8px; background: color-mix(in srgb, var(--acceptance-color) 5%, transparent); }.acceptance-submetrics span { display: block; color: var(--muted); font-size: 8.5px; }.acceptance-submetrics strong { display: block; margin-top: 3px; font-size: 14px; font-variant-numeric: tabular-nums; }
.acceptance-samples { display: flex; flex-wrap: wrap; gap: 5px 9px; margin-top: 10px; color: var(--faint); font-size: 9px; }.acceptance-samples b { color: var(--text); font-variant-numeric: tabular-nums; }.acceptance-card > p { margin: 9px 0 0; color: var(--muted); font-size: 9.5px; line-height: 1.5; }
.acceptance-note { position: relative; z-index: 1; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; margin-top: 12px; padding: 11px 13px; border: 1px dashed var(--line); border-radius: 10px; background: color-mix(in srgb, var(--sky) 4%, var(--panel)); font-size: 11px; }.acceptance-note strong { color: #0369a1; }.acceptance-note span { color: var(--muted); }.acceptance-note b { color: var(--text); font-size: 10px; }

/* VERIDOC_TECHNICAL_PERSPECTIVE_CSS_20260719 */
.technical-perspective { border-top: 3px solid var(--blue); }.technical-stack { display: grid; gap: 13px; margin-top: 18px; }.technical-perspective .pr-card, .technical-perspective .pr-eval { margin: 0; padding: 20px; border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); border-radius: 13px; background: color-mix(in srgb, var(--blue) 1.7%, var(--panel)); box-shadow: none; }.technical-perspective .pr-eval { border-left: 4px solid var(--blue); }
@media (max-width: 1180px) { .acceptance-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 760px) { .business-acceptance { padding: 17px; }.business-acceptance::after, .acceptance-scope { display: none; }.acceptance-grid { grid-template-columns: 1fr; }.acceptance-card h3 { min-height: 0; }.acceptance-note { grid-template-columns: 1fr; }.technical-perspective .pr-card, .technical-perspective .pr-eval { padding: 15px; overflow-x: auto; } }
:root[data-theme="dark"] .acceptance-scope { color: #7dd3fc; background: rgba(14,165,233,.14); }:root[data-theme="dark"] .acceptance-loading, :root[data-theme="dark"] .acceptance-card { background: #0f1728; }:root[data-theme="dark"] .acceptance-status { color: #fbbf24; background: rgba(217,119,6,.16); }:root[data-theme="dark"] .status-insufficient .acceptance-status, :root[data-theme="dark"] .status-unavailable .acceptance-status { color: #94a3b8; background: rgba(100,116,139,.16); }:root[data-theme="dark"] .technical-perspective .pr-card, :root[data-theme="dark"] .technical-perspective .pr-eval { background: rgba(15,23,40,.78); }
'''


def _insert_after(text: str, anchor: str, addition: str, label: str) -> str:
    if anchor not in text:
        raise ValueError(f"cannot find {label} anchor")
    return text.replace(anchor, anchor + addition, 1)


def _insert_before(text: str, anchor: str, addition: str, label: str) -> str:
    if anchor not in text:
        raise ValueError(f"cannot find {label} anchor")
    return text.replace(anchor, addition + "\n" + anchor, 1)


def patch_index(text: str) -> str:
    if VISUAL_REFRESH_MARKER not in text:
        text = _insert_before(
            text,
            '        <section class="metrics" id="metrics"></section>',
            COMMAND_HERO_SECTION,
            "overview command hero",
        )
    if EVAL_MARKER not in text:
        text = _insert_after(
            text,
            '        <section class="metrics" id="metrics"></section>\n',
            EVALUATION_SECTION,
            "overview metrics",
        )
    if BUSINESS_ACCEPTANCE_MARKER not in text:
        text = _insert_after(
            text,
            EVALUATION_SECTION,
            BUSINESS_ACCEPTANCE_SECTION,
            "overall labeled evaluation section",
        )
    if BUSINESS_MARKER not in text:
        text = _insert_before(
            text,
            '        <article class="panel pr-card c-blue">',
            BUSINESS_SECTION,
            "first technical-principle card",
        )
    if TECHNICAL_PERSPECTIVE_MARKER not in text:
        technical_anchor = '        <article class="panel pr-card c-blue">'
        if technical_anchor not in text:
            raise ValueError("cannot find first technical card anchor")
        start = text.index(technical_anchor)
        text = text[:start] + TECHNICAL_PERSPECTIVE_OPEN + text[start:]
        view_end_anchor = "      </section>\n    </main>"
        view_end = text.index(view_end_anchor, start + len(TECHNICAL_PERSPECTIVE_OPEN))
        text = text[:view_end] + TECHNICAL_PERSPECTIVE_CLOSE + text[view_end:]
    text = text.replace("<h2>五类风险检测原理</h2>", "<h2>检测原理：技术与业务双视角</h2>", 1)
    text = text.replace("<h2>带标签样本检测表现</h2>", "<h2>带标签样本检测表现 · 双口径</h2>", 1)
    text = text.replace("<h2>五大业务分项准确度 · 验收基线</h2>", "<h2>五大业务分项指标 · 内部评测</h2>", 1)
    text = text.replace("<span>去显式 marker 口径 · 同时展示 Recall、F1 与样本结构</span>", "<span>去显式 marker 口径 · 当前结果尚未完成独立盲测验收</span>", 1)
    text = text.replace('<span class="acceptance-scope">五项独立看板</span>', '<span class="acceptance-scope">未正式验收</span>', 1)
    text = text.replace(
        '<span>以综合风险分 ≥ 25 判定“疑似虚假” · 页面实时聚合</span>',
        '<span>全标签口径与去显式 marker 审计口径并列展示 · 风险分 ≥ 25 判定“疑似虚假”</span>',
        1,
    )
    text = text.replace('<span class="eval-scope">当前标签集</span>', '<span class="eval-scope">同一标签集 · 两种评分口径</span>', 1)
    text = text.replace(
        '<button class="tab active" data-view="overview">总览</button>',
        '<button class="tab active" data-view="overview"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11 12 3l9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></svg>总览</button>',
        1,
    )
    text = text.replace(
        '<button class="tab" data-view="documents">样本核查</button>',
        '<button class="tab" data-view="documents"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16M4 12h16M4 19h10"/><path d="m17 16 3 3-3 3"/></svg>样本核查</button>',
        1,
    )
    text = text.replace(
        '<button class="tab" data-view="upload">上传检测</button>',
        '<button class="tab" data-view="upload"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 16V4M8 8l4-4 4 4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>上传检测</button>',
        1,
    )
    text = text.replace(
        '<button class="tab" data-view="principles">检测原理</button>',
        '<button class="tab" data-view="principles"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4v15.5M6.5 4H20v13H6.5A2.5 2.5 0 0 0 4 19.5"/></svg>检测原理</button>',
        1,
    )
    text = text.replace('href="/styles.css"', 'href="/styles.css?v=20260719-v3"', 1)
    text = text.replace('href="/styles.css?v=20260718-v2"', 'href="/styles.css?v=20260719-v3"', 1)
    text = text.replace('src="/app.js"', 'src="/app.js?v=20260719-v3"', 1)
    text = text.replace('src="/app.js?v=20260718-v2"', 'src="/app.js?v=20260719-v3"', 1)
    text = text.replace('<span>0.0.0.0:3002</span>', '<span>生产环境 · 引擎在线</span>', 1)
    return text


def patch_javascript(text: str) -> str:
    if DUAL_EVAL_JS_MARKER not in text and JS_MARKER in text:
        start = text.index("// " + JS_MARKER)
        end = text.index("const RISK_BANDS = [", start)
        text = text[:start] + EVALUATION_JS + "\n" + text[end:]
    elif JS_MARKER not in text:
        text = _insert_before(text, "const RISK_BANDS = [", EVALUATION_JS, "risk bands")
    if VISUAL_REFRESH_JS_MARKER not in text:
        text = _insert_before(text, "const RISK_BANDS = [", VISUAL_REFRESH_JS, "risk bands for visual refresh")
    if BUSINESS_ACCEPTANCE_JS_MARKER not in text:
        text = _insert_before(text, "const RISK_BANDS = [", BUSINESS_ACCEPTANCE_JS, "risk bands for business acceptance")
    text = text.replace('item.status_label || "待验收"', 'item.status_label || "内部评测 · 未验收"', 1)
    if SEAL_REASON_JS_MARKER not in text:
        text = _insert_after(
            text,
            '  "visual:red_stamp_like_region": ["seal", "红章区域"],\n',
            SEAL_REASON_JS,
            "red seal reason metadata",
        )
    if SEAL_CLASS_REASON_JS_MARKER not in text:
        text = _insert_after(
            text,
            '  "visual:seal_ocr_low_confidence_review": ["seal", "印章 OCR 低置信复核"],\n',
            SEAL_CLASS_REASON_JS,
            "seal classification reason metadata",
        )
    if SEAL_DETAIL_JS_MARKER not in text:
        text = _insert_after(
            text,
            '    ["字体警告", row.font_warning_count],\n',
            SEAL_DETAIL_JS,
            "detail drawer font-warning field",
        )
    if SEAL_CLASS_DETAIL_JS_MARKER not in text:
        text = _insert_after(
            text,
            '    ["AI 印章候选数", row.qwen_seal_count ?? "-"],\n',
            SEAL_CLASS_DETAIL_JS,
            "seal classification detail fields",
        )
    if OCR_REASON_JS_MARKER not in text:
        text = _insert_after(
            text,
            '  "visual:red_stamp_like_region": ["seal", "红章区域"],\n',
            OCR_REASON_JS,
            "OCR reason metadata",
        )
    call = "  await renderLabeledEvaluation();\n"
    if call not in text:
        anchor = "  renderRiskDist(state.dashboard);\n"
        text = _insert_after(text, anchor, call, "dashboard render")
    acceptance_call = "  renderBusinessAcceptance(state.dashboard);\n"
    if acceptance_call not in text:
        anchor = "  await renderLabeledEvaluation();\n"
        text = _insert_after(text, anchor, acceptance_call, "labeled evaluation for business acceptance")
    visual_calls = "  renderCommandHero(state.dashboard);\n  decorateMetricCards();\n"
    if visual_calls not in text:
        anchor = "  renderRiskDist(state.dashboard);\n"
        text = _insert_after(text, anchor, visual_calls, "dashboard visual refresh")
    return text


def patch_css(text: str) -> str:
    if CSS_MARKER not in text:
        text = text.rstrip() + ENHANCEMENT_CSS + "\n"
    if DUAL_EVAL_CSS_MARKER not in text:
        text = text.rstrip() + DUAL_EVAL_CSS + "\n"
    if VISUAL_REFRESH_CSS_MARKER not in text:
        text = text.rstrip() + VISUAL_REFRESH_CSS + "\n"
    if BUSINESS_ACCEPTANCE_CSS_MARKER not in text or TECHNICAL_PERSPECTIVE_CSS_MARKER not in text:
        text = text.rstrip() + BUSINESS_ACCEPTANCE_CSS + "\n"
    return text


def find_web_files(root: Path) -> tuple[Path, Path, Path]:
    ignored = {".git", ".venv", "venv", "data", "outputs", "models", "node_modules", "__pycache__"}
    candidates: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in ignored and not name.startswith(".backup")]
        if "index.html" not in filenames:
            continue
        path = Path(directory) / "index.html"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "view-principles" in content and "明鉴 · 材料真伪智能核验平台" in content:
            candidates.append(path)
    if len(candidates) != 1:
        rendered = ", ".join(str(path) for path in candidates) or "none"
        raise RuntimeError(f"expected exactly one VeriDoc index.html under {root}, found: {rendered}")
    index = candidates[0]
    app_js = index.with_name("app.js")
    styles = index.with_name("styles.css")
    missing = [str(path) for path in (app_js, styles) if not path.exists()]
    if missing:
        raise FileNotFoundError("missing dashboard assets: " + ", ".join(missing))
    return index, app_js, styles


def write_patched(root: Path, dry_run: bool = False) -> list[Path]:
    index, app_js, styles = find_web_files(root)
    patches = {
        index: patch_index(index.read_text(encoding="utf-8")),
        app_js: patch_javascript(app_js.read_text(encoding="utf-8")),
        styles: patch_css(styles.read_text(encoding="utf-8")),
    }
    changed = [path for path, content in patches.items() if content != path.read_text(encoding="utf-8")]
    if dry_run or not changed:
        return changed

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = root / f".backup_homepage_{stamp}"
    for path in changed:
        relative = path.relative_to(root)
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    for path in changed:
        path.write_text(patches[path], encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="deployment project root")
    parser.add_argument("--dry-run", action="store_true", help="validate anchors without writing files")
    args = parser.parse_args()
    changed = write_patched(args.root.resolve(), dry_run=args.dry_run)
    action = "would patch" if args.dry_run else "patched"
    if changed:
        print(f"{action} {len(changed)} dashboard files:")
        for path in changed:
            print(f"- {path}")
    else:
        print("dashboard is already patched; no changes needed")


if __name__ == "__main__":
    main()
