# Sjtu-veridoc — 虚假材料智能检测

多信号的**文档真伪 / 造假检测**工具集：对发票、合同、银行流水、征信报告、结算单等业务材料，从 **PDF 结构、图像取证、印章、OCR/文本层、业务逻辑勾稽** 五个维度提取证据并融合评分。

> ⚠️ 本仓库**只含代码与文档**。原始材料、渲染图、抽取特征、模型等因包含**真实个人金融隐私**（征信/流水/身份证/账号）且体量巨大，均已 `.gitignore`，不入库。

## 五类检测一览

| # | 风险类型 | 核心技术 | 关键特征 | 源码 |
|---|---|---|---|---|
| ① | PDF 结构 | pdfinfo/pdfimages + 字节正则 | SMask 蒙版、整页图+小图层、增量更新、嵌入脚本 | `src/extract_pdf_features.py` |
| ② | 图片 PS | ELA 重压缩误差、分块噪声方差、边缘密度 | 局部压缩历史突变、噪声不一致 | `src/analyze_visual_forensics.py` |
| ③ | 印章贴图 | 红色分割 + 颜色无关环形候选定位 | 红章特征、黑白/复印章坐标、环形覆盖、OCR 裁剪 | `src/analyze_visual_forensics.py` |
| ④ | OCR/文本层 | pdftotext -bbox 词坐标、tesseract | 文本层缺失/重复、置信度、坐标版式 | `src/analyze_text_business_rules.py` |
| ⑤ | 业务逻辑 | 正则字段抽取 + 勾稽校验 | 发票金额+税额=价税合计、银行余额连续性 | `src/analyze_text_business_rules.py` |

融合评分：`src/build_combined_risk.py`（`v3_sign_corrected`）。**完整原理见 [`docs/DETECTION_PRINCIPLES.md`](docs/DETECTION_PRINCIPLES.md)**。

## 诚实能力评估（provenance-matched 基准）

| 检测轴 | AUC | 结论 |
|---|---|---|
| 语义 / 业务逻辑勾稽（同源基准） | **1.000** | ✅ 真实有效、可迁移 |
| Qwen-VL 视觉取证（合成可视篡改） | **1.000** | ✅ 抓肉眼级明显篡改，与业务逻辑互补 |
| 经典像素级篡改取证（7 种方法，同源基准） | ≈0.50 | ❌ 扫描文档上不可用 |

> Qwen-VL 仅在**零 PII 合成样本**上验证；真实扫描件含个人金融隐私，外发第三方 API 需显式授权。

关键教训：旧数据上的高 AUC 来自合成假件的**显式水印**与"合成 vs 扫描"**来源泄漏**，非真实造假识别能力。详见 `ITERATION_REPORT_20260707.md`。

## 快速开始

```bash
pip install opencv-python numpy Pillow scikit-learn reportlab   # + poppler-utils, tesseract-ocr(可选)
# 单份检测
python3 run_detection.py --file path/to/doc.pdf --doc-type invoice --format table
# 批量特征 → 融合评分
python3 src/extract_pdf_features.py        --manifest outputs/manifest.csv --out-csv outputs/features/pdf_object_features.csv --out-json /tmp/a.json
python3 src/analyze_text_business_rules.py --manifest outputs/manifest.csv --out-csv outputs/features/text_business_features.csv --out-json /tmp/b.json --out-words-csv outputs/features/text_word_coordinates.csv
python3 src/analyze_visual_forensics.py --manifest outputs/manifest.csv --out-csv outputs/features/visual_forensics_features.csv --out-json /tmp/v.json --seal-crop-dir outputs/seal_candidates --max-pages 3
python3 src/analyze_seal_ocr.py --visual-csv outputs/features/visual_forensics_features.csv --text-csv outputs/features/text_business_features.csv --out-csv outputs/features/seal_ocr_features.csv --out-json /tmp/seal.json
python3 src/build_combined_risk.py --pdf-csv ... --visual-csv ... --text-csv ... --seal-ocr-csv outputs/features/seal_ocr_features.csv --scoring-version v3 --out-csv outputs/features/combined_risk_features.csv --out-json outputs/features/combined_risk_summary.json
```

## 评测基准（可复现）

- `src/make_semantic_tamper.py` — 同源**语义**篡改基准（改金额破坏勾稽），验证业务逻辑检测。
- `src/generate_hard_negatives.py` — 同源**像素**篡改基准（copy-move/拼接/inpaint/重压缩）。
- `src/make_seal_localization_bench.py` + `scripts/eval_seal_localization.py` — 零 PII 红章/灰章/复印章/淡章定位基准，含圆形 Logo 困难负样本。
- `scripts/eval_scoring.py` — AUC / 分层交叉验证评估。

颜色无关印章定位在 120 份合成基准上的首轮结果：Recall **98.75%**、F1 **88.27%**、检测成功样本平均 IoU **0.596**。圆形 Logo 会被召回为印章候选，因此该能力只负责“定位与送 OCR”，不能单独判定虚假印章。

## 线上首页维护

当前部署机上的 Web 服务尚未纳入本仓库。针对首页的“带标签 Accuracy / Precision / Recall / F1”与“业务五大产品线”改版，仓库提供幂等补丁：

```bash
python3 scripts/patch_live_dashboard.py --root /root/Liangjialiang2 --dry-run
python3 scripts/patch_live_dashboard.py --root /root/Liangjialiang2
```

完整的项目剖析、指标限制、黑白印章路线和五大业务拆解见 [`docs/PROJECT_AND_HOMEPAGE_ANALYSIS_20260717.md`](docs/PROJECT_AND_HOMEPAGE_ANALYSIS_20260717.md)。上线后应尽快把 Web 源码和启动配置提交进仓库，替代部署机独有源码。

## 目录

```
src/                检测器与特征提取
  extract_pdf_features.py        ① PDF 结构
  analyze_visual_forensics.py    ②③ 图像取证 + 印章
  analyze_seal_ocr.py            ③ 印章极坐标展开/OCR + 文档主体匹配
  analyze_text_business_rules.py ④⑤ 文本层 + 业务逻辑
  analyze_ocr_deepseek.py        ④ OCR(需 tesseract) + LLM 核验
  extract_text_markers.py        显式伪造标记提取
  build_combined_risk.py         证据融合评分 v3
  detectors/                     集成检测器(CLI 用)
docs/DETECTION_PRINCIPLES.md     五类检测完整原理
ITERATION_REPORT_20260707.md     迭代报告(反转修复 + 基准 + 诚实结论)
```
