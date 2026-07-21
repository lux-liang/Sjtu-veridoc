# 五类风险检测原理（对照源码）

> 本文基于**实际源码**逐行核对，而非设计文档。每类给出：用什么技术 / 看什么特征 / 实际阈值权重 / 源码位置 / 判别力与局限。
> 末尾附我们通过 provenance-matched 基准得到的**诚实能力评估**（哪些真有效、哪些是数据泄漏）。

## 总览：五类如何汇成一个分

```
PDF结构  ─┐
图像PS   ─┤
印章贴图  ─┼─→ src/build_combined_risk.py 证据融合 → combined_risk_score
文本/OCR ─┤        (v3_sign_corrected 符号纠正评分)
业务逻辑  ─┘
```
每个子检测器独立产出 `xxx_risk_score` + `xxx_risk_reasons`，融合层按每个 reason 的**经验方向**加权。

运行依赖：`pdfinfo / pdfimages / pdftotext / pdftoppm`（poppler，已装）；`tesseract`（OCR，**当前缺失，OCR 分支未启用**）；`reportlab`（造样本）；`opencv-python / numpy / Pillow`。

---

## ① PDF 结构风险 · `src/extract_pdf_features.py`

**技术**：不渲染，纯**对象层 + 字节层取证**。三路并用：
- `pdfinfo`（元数据：页数、Creator/Producer、PDF 版本）— `analyze_pdf():104`
- `pdfimages -list`（枚举每个图像对象的类型/尺寸）— `parse_pdfimages():24`
- `read_bytes()` 正则扫原始字节 — `inspect_pdf_bytes():45`

**特征 → 判据 → 权重**（`risk_score():69`）：

| 特征 | 判据 | 权重 | 造假原理 |
|---|---|---|---|
| `smask_count≥1` | pdfimages 出现 `smask` 类型对象 | +25 | **SMask 透明蒙版**是贴合成内容最常见手法 |
| 整页大图 + 小图层 | `large_image≥1 且 small_overlay≥3` | +15 | 扫描底图上贴小块（改金额/盖章） |
| 图层密度异常 | `image_count≥页数×3 且 overlay≥3` | +15 | 密集拼贴 |
| 增量更新 | `incremental_update>1`（多个 `%%EOF`/`startxref`） | +15 | PDF 生成后被二次编辑保存 |
| 嵌入脚本/文件 | `/JavaScript` 或 `/EmbeddedFile` | +20 | 异常内容 |
| 来源缺失 | creator & producer 都缺 | +10 | 剥离来源信息 |
| 字体对象过多 / 交互表单 | `font_object>80` / `/AcroForm`,`/XFA` | +10 / +8 | 异常构造 |

**判别力**：只有 `smask_present` 真有效（假 19% / 正常 0%）；`full_page_image_overlays`、`missing_creator` 在正常扫描件高发（反向），v3 已中性化。

---

## ② 图片 PS / 图像取证 · `src/analyze_visual_forensics.py`

**技术**：`pdftoppm` 把前 N 页渲染成 PNG（`--max-pages`，默认 1，远程迭代脚本使用 3），逐页取证后合并原因、保留最大视觉风险，并记录最佳印章候选页码。

1. **ELA（误差水平分析）** `ela_score():38`：以 JPEG q=85 **重压缩一次**，与原图求像素差均值。局部 PS 区域压缩历史不同 → 重压缩后误差突变。阈值 `≥7.0 → +20`。
2. **分块噪声方差** `block_variance_score():48`：64×64 块算灰度标准差，再求**块间标准差的标准差**。拼接/编辑使局部噪声偏离整体。阈值 `≥22 → +15`。
3. **边缘密度** `edge_density():63`：`FIND_EDGES` 均值，`≥18 → +10`，抓贴图硬边界。

**判别力**：AUC 0.452（略反向）——扫描件本身噪声/边缘高，合成数字件反而干净。这三个特征为"真实照片被 PS"设计，对"纯数字生成假件"几乎无效。

---

## ③ 印章贴图 / 黑白复印章候选 · `src/analyze_visual_forensics.py`

当前有两条互补管线：

1. **红章贴图特征** `seal_overlay_features()`：红色像素分割 + 连通域分析。
2. **颜色无关候选定位** `color_agnostic_seal_features()`：用于红章、灰度扫描章、淡章和复印章，不依赖 RGB 红色阈值。

### 红章贴图特征

- 红色掩膜：逐像素判 `r>120 且 r>1.45g 且 r>1.45b`（`:95`）。
- 对掩膜连通域（≥20 像素）算 4 个特征：

| 特征 | 含义 | 贴图为何异常 |
|---|---|---|
| `red_component_edge_contrast` | 红色组件内平均边缘强度 | **贴图印章边界硬/锐利**；真章渗入纸纤维、边缘柔和 |
| `red_component_color_std` | 红色像素颜色标准差 | **数字贴图颜色纯、方差低**；真章印油有深浅纹理 |
| `max_red_component_ratio` | 最大红块占比 | 定位主印章 |
| `red_component_count` | 红块数量 | — |

**判据**（`analyze_image():192`）：`seal_hard_edge_overlay`（edge_contrast≥22 & ratio≥0.001，+12）、`seal_flat_color_overlay`（ratio∈[0.001,0.08] & color_std≤28，+8）、`red_stamp_like_region`（red_ratio≥0.012，+10）。

### 颜色无关候选定位

1. 以局部高斯背景减灰度图，得到暗色墨迹强度，适应偏黄纸张和低对比度扫描。
2. 先剔除贯穿页面的表格横线/竖线，避免盖章与表格线相交后被合并成巨大矩形组件。
3. 对剩余墨迹闭运算并做 8 邻域连通域，筛选尺寸合理、近圆/椭圆的候选框。
4. 在候选框内计算椭圆环带密度、24 扇区角向覆盖、中心墨迹密度、边缘、纹理和高频/中间灰度启发特征。
5. 用 HSV 饱和度把彩色章与黑白/复印章候选分流，并输出归一化坐标。
6. 可通过 `--seal-crop-dir` 自动保存保留上下文的彩色裁剪和对比度增强 OCR 裁剪。

### 印章 OCR 与主体勾稽

`src/analyze_seal_ocr.py` 对最佳候选继续处理：

1. 将候选外环从极坐标展开成矩形条带，使环形文字近似水平排列。
2. 本地 Tesseract 分别识别 OCR 增强原图、极坐标条带和镜像条带，并聚合文本与置信度。
3. 从业务字段 JSON 提取甲乙方、购销方、付款方、银行/机构名称等文档主体。
4. 用归一化字符局部相似度比较印章 OCR 与文档主体，输出最佳匹配实体和相似度。
5. `seal_ocr_entity_match_context` 表示一致性支持；`seal_ocr_entity_mismatch_review` 和 `seal_ocr_low_confidence_review` 只进入人工复核，当前均不增加 v3 风险分。

该链路完全本地运行；未安装 Tesseract 时会稳定输出 `tesseract_not_found`，不会阻断其他检测器。多模态大模型可作为困难样本兜底，但真实金融材料外发前必须取得授权。

可选的 `analyze_qwen_forensics.py` 也已扩展印章结构化输出：模型需返回归一化 bbox、颜色、可辨文字和贴图怀疑分；代码会严格校验坐标范围后写入 `qwen_fx_seal_candidates`。该通道用于困难样本兜底/弱标注，不替代本地候选定位。

新增字段：`seal_candidate_best_score`、`seal_candidate_bbox_norm`、`seal_candidate_ring_density`、`seal_candidate_angular_coverage`、`seal_candidate_halftone_score`、`seal_candidate_is_monochrome`、`seal_candidate_ocr_path` 等。

新增原因 `seal_color_agnostic_candidate / seal_monochrome_candidate / seal_candidate_hard_edge_review` 在 v3 中均为**中性定位/复核标签**，目前不增加文档风险分。

**零 PII 合成定位基准（120 份）**：红章、灰章、复印章全部召回，淡章召回 95%；总体 Recall=0.9875、Precision=0.7980、F1=0.8827，检测成功样本平均 IoU=0.596。所有圆形 Logo 困难负样本也被召回，说明几何定位不能区分“印章 vs Logo”，必须接印章 OCR、机构主体勾稽与位置语义。

**局限**：红章颜色与候选存在本身都不是造假证据；圆形 Logo 仍需依靠 OCR、主体和位置语义排除；多页模式目前只扫描前 N 页而非智能风险页抽样。`red_stamp_like_region` 在正常真章中会触发，v3 已中性化。

---

## ④ OCR 与文本层坐标 · `src/analyze_text_business_rules.py` + `src/analyze_ocr_deepseek.py`

**两条腿，当前只有一条能跑**：
- **文本层坐标（能跑）** `extract_words_pdf():42`：`pdftotext -bbox` 输出带**每词 xMin/yMin/xMax/yMax** 的 XML，`words_to_lines()` 按 y 坐标聚行。支撑版式/重复检测。
- **OCR（缺 tesseract，未启用）** `analyze_ocr_deepseek.py`：`tesseract --psm 6 tsv` 输出文字+坐标+**置信度**。规则：`mean_conf<45→+18`、低置信词占比>0.35→+12、发票三金额两两相加不等→+25。
- **LLM 核验（可选）** `deepseek_verify()`：把 OCR 文本喂 DeepSeek 返回 JSON risk（需 `DEEPSEEK_API_KEY` 环境变量）。

文本层派生信号：文本层缺失(+20)、稀疏(+12)、**重复率**（唯一词/总词<0.28，+10，抓复制粘贴拼版）。

---

## ⑤ 字段抽取与业务逻辑 · `src/analyze_text_business_rules.py` → `business_rules():154`

**技术**：正则字段抽取 + 按单据类型的逻辑校验（唯一"语义级"检测，也是**真正有效**的一类）。

- 字段正则：金额、日期、身份证、账号、税号、发票号。
- **健壮标签抽取** `labeled_amount()`：标签后**必须紧跟数字**才算命中（修掉了 "VALUE ADDED **TAX** INVOICE" 标题里 "Tax" 被误当税额的 bug）。
- 分单据规则：
  - **发票**：`|金额+税额 − 价税合计| > 0.05 → +35`（勾稽不平，全系统最强的真实语义信号）。
  - **银行流水**：`balance_rows()` 校验 `余额_i = 余额_{i-1} + 发生额_i`，破坏则 `bank_balance_sequence_broken`（+30）。
  - 合同：甲乙方/日期缺失；征信：身份证/日期缺失（注意：这些"缺失类"在扫描件上误报高，v3 已弱化）。

---

## 诚实能力评估（provenance-matched 基准）

我们构造了**同源**基准（控制/篡改仅差异于篡改本身，无水印、无"合成 vs 扫描"泄漏），得到：

| 检测轴 | 方法 | 同源 AUC | 结论 |
|---|---|---|---|
| **语义/业务逻辑** | 发票勾稽 / 银行余额连续性 | **1.000** | ✅ 真实有效，可迁移 |
| 像素级篡改取证 | ELA/块方差/边缘/局部ELA/局部噪声/ORB copy-move/块连通/JPEG-ghost（7 种） | **≈0.50** | ❌ 在扫描文档上不可用 |

**为什么像素取证失效**：文档含大量**合法重复结构**（模板/表格线）→ copy-move 误报地板极高；扫描噪声淹没局部编辑痕迹；经典取证为"照片被 PS"设计，与文档场景不匹配。

**为什么旧数据曾出现 AUC 0.99**：旧合成假件带 `SYNTHETIC/TRAINING/VOID` 显式水印，且"合成数字 vs 真实扫描"来源统计差异巨大——模型学的是**来源**不是**造假**，是数据泄漏而非真本事。详见 `ITERATION_REPORT_20260707.md`。

**方向**：把重心放在**语义/业务逻辑核验**（可解释、可迁移），并**安装 tesseract** 让扫描件也能进入语义核验；像素级篡改定位除非上 GPU 深度模型，否则不再投入。

---

## 补充：多模态大模型（Qwen-VL）视觉取证 · `src/analyze_qwen_forensics.py`

**技术**：不再做像素统计，而是把整页图交给 **Qwen-VL（qwen-vl-max，DashScope）**，用取证提示词让模型"看图"判断篡改，返回结构化 JSON（`tampered / risk_score / findings / reason_tags`）。端点 `dashscope.aliyuncs.com/compatible-mode/v1`，env `DASHSCOPE_API_KEY`/`QWEN_API_KEY`。

**在合成可视篡改基准上的结果**（`src/make_visual_tamper_synth.py` 造的零 PII 匹配对：贴歪假章 + 字体不符的改数字）：

| 指标 | 值 |
|---|---|
| AUC | **1.000** |
| 篡改件均分 / 对照件均分 | 85 / 10 |
| 阈值25 命中 / 误报 | 15/15 / 0/15 |

模型给出的 reason_tags 准确（篡改：字体不一致/疑似拼接/可疑印章/排版异常；对照：字体一致/无拼接/无篡改标记）。

**能力边界（务必如实）**：
- ✅ 能抓**肉眼级明显篡改**（错位印章、字体/颜色不符的改字、明显水印）——正是经典像素取证抓不到的，为**互补**能力。现实中大量业余造假属此类。
- ⚠️ **未验证**亚像素级细微篡改；**未在真实扫描件上测**——真实征信/流水含个人 PII，外发第三方 API 属数据外泄，需用户显式授权后方可。
- 本质是"AI 替人看图"，抓"人也能看出的破绽"，非像素级取证。

**隐私红线**：对真实业务材料调用外部多模态 API 前，必须评估个人金融数据外泄风险（身份证/账号/征信）。
