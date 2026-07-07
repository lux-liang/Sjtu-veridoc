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

**技术**：`pdftoppm` 把**第一页**渲染成 PNG（`render_pdf_first_page():29`，仅第 1 页），做三种经典像素取证：

1. **ELA（误差水平分析）** `ela_score():38`：以 JPEG q=85 **重压缩一次**，与原图求像素差均值。局部 PS 区域压缩历史不同 → 重压缩后误差突变。阈值 `≥7.0 → +20`。
2. **分块噪声方差** `block_variance_score():48`：64×64 块算灰度标准差，再求**块间标准差的标准差**。拼接/编辑使局部噪声偏离整体。阈值 `≥22 → +15`。
3. **边缘密度** `edge_density():63`：`FIND_EDGES` 均值，`≥18 → +10`，抓贴图硬边界。

**判别力**：AUC 0.452（略反向）——扫描件本身噪声/边缘高，合成数字件反而干净。这三个特征为"真实照片被 PS"设计，对"纯数字生成假件"几乎无效。

---

## ③ 印章贴图 / 红章覆盖 · `src/analyze_visual_forensics.py` → `seal_overlay_features():83`

**技术**：红色像素分割 + **连通域分析**（自实现 4-邻域 flood-fill）。

- 红色掩膜：逐像素判 `r>120 且 r>1.45g 且 r>1.45b`（`:95`）。
- 对掩膜连通域（≥20 像素）算 4 个特征：

| 特征 | 含义 | 贴图为何异常 |
|---|---|---|
| `red_component_edge_contrast` | 红色组件内平均边缘强度 | **贴图印章边界硬/锐利**；真章渗入纸纤维、边缘柔和 |
| `red_component_color_std` | 红色像素颜色标准差 | **数字贴图颜色纯、方差低**；真章印油有深浅纹理 |
| `max_red_component_ratio` | 最大红块占比 | 定位主印章 |
| `red_component_count` | 红块数量 | — |

**判据**（`analyze_image():192`）：`seal_hard_edge_overlay`（edge_contrast≥22 & ratio≥0.001，+12）、`seal_flat_color_overlay`（ratio∈[0.001,0.08] & color_std≤28，+8）、`red_stamp_like_region`（red_ratio≥0.012，+10）。

**局限**：硬编码 RGB 阈值对偏色扫描脆弱（宜转 HSV）；纯 Python 逐像素双循环极慢（宜 `cv2.inRange`+`connectedComponentsWithStats`）。`red_stamp_like_region` 假 0% / 正常 2.4%（真章在正常件），方向反，v3 已中性化。

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
