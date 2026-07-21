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


def _insert_after(text: str, anchor: str, addition: str, label: str) -> str:
    if anchor not in text:
        raise ValueError(f"cannot find {label} anchor")
    return text.replace(anchor, anchor + addition, 1)


def _insert_before(text: str, anchor: str, addition: str, label: str) -> str:
    if anchor not in text:
        raise ValueError(f"cannot find {label} anchor")
    return text.replace(anchor, addition + "\n" + anchor, 1)


def patch_index(text: str) -> str:
    if EVAL_MARKER not in text:
        text = _insert_after(
            text,
            '        <section class="metrics" id="metrics"></section>\n',
            EVALUATION_SECTION,
            "overview metrics",
        )
    if BUSINESS_MARKER not in text:
        text = _insert_before(
            text,
            '        <article class="panel pr-card c-blue">',
            BUSINESS_SECTION,
            "first technical-principle card",
        )
    text = text.replace("<h2>五类风险检测原理</h2>", "<h2>检测原理：技术与业务双视角</h2>", 1)
    text = text.replace("<h2>带标签样本检测表现</h2>", "<h2>带标签样本检测表现 · 双口径</h2>", 1)
    text = text.replace(
        '<span>以综合风险分 ≥ 25 判定“疑似虚假” · 页面实时聚合</span>',
        '<span>全标签口径与去显式 marker 审计口径并列展示 · 风险分 ≥ 25 判定“疑似虚假”</span>',
        1,
    )
    text = text.replace('<span class="eval-scope">当前标签集</span>', '<span class="eval-scope">同一标签集 · 两种评分口径</span>', 1)
    return text


def patch_javascript(text: str) -> str:
    if DUAL_EVAL_JS_MARKER not in text and JS_MARKER in text:
        start = text.index("// " + JS_MARKER)
        end = text.index("const RISK_BANDS = [", start)
        text = text[:start] + EVALUATION_JS + "\n" + text[end:]
    elif JS_MARKER not in text:
        text = _insert_before(text, "const RISK_BANDS = [", EVALUATION_JS, "risk bands")
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
    return text


def patch_css(text: str) -> str:
    if CSS_MARKER not in text:
        text = text.rstrip() + ENHANCEMENT_CSS + "\n"
    if DUAL_EVAL_CSS_MARKER not in text:
        text = text.rstrip() + DUAL_EVAL_CSS + "\n"
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
