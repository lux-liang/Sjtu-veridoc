# 五种风险类型检测原理与负样本设计

## 1. 总体检测链路

```text
PDF / 图片
  -> PDF 对象结构检测
  -> 页面渲染为图片
  -> 图片 PS / 拼接痕迹检测
  -> 印章 / 签名贴图检测
  -> OCR / 文本层一致性检测
  -> 业务逻辑一致性检测
  -> 综合评分与证据输出
```

当前系统不是只靠一个模型判断真假，而是把不同证据分开计算：

| 风险类型 | 当前主要脚本/模块 | 输出证据 |
| --- | --- | --- |
| PDF 对象结构异常 | `src/extract_pdf_features.py` | `pdf_object_features.csv` |
| 图片 PS 痕迹 | `src/analyze_visual_forensics.py` | `visual_forensics_features.csv` |
| 印章/签名贴图 | PDF SMask + 视觉红章特征 + Qwen 复核 | `object_risk_reasons` / `visual_risk_reasons` / `qwen_ocr_features.csv` |
| OCR/文本层异常 | `src/analyze_text_business_rules.py`、`src/analyze_ocr_deepseek.py`、`src/analyze_qwen_ocr.py` | 文本坐标、OCR 文本、字段 JSON |
| 业务逻辑一致性 | `src/detectors/business_logic_detector.py` | 字段约束、规则原因 |

## 2. 风险一：PDF 对象结构异常

### 检测目标

识别 PDF 是否经历过截图转 PDF、局部覆盖、贴图、二次编辑、增量保存、嵌入脚本等异常处理。

### 当前实现

脚本：

```text
src/extract_pdf_features.py
```

核心工具：

- `pdfinfo`
- `pdfimages -list`
- PDF 字节级正则扫描

核心特征：

| 特征 | 原理 |
| --- | --- |
| `smask_count` | SMask 是透明蒙版，贴章、贴签名、透明 PNG 覆盖常会产生 |
| `image_count` | 图片对象数量异常多，说明可能是截图、扫描图或多层贴图 |
| `large_image_count` | 整页大图常见于截图转 PDF、扫描 PDF |
| `small_overlay_count` | 小图层常见于局部贴图、局部遮盖重写 |
| `font_warning_count` | Poppler 字体警告，常见于异常嵌入字体或编辑后字体对象不一致 |
| `eof_count` / `startxref_count` | 多个 EOF/startxref 说明可能经历过增量保存 |
| `javascript_count` | PDF 内嵌 JS，普通业务材料通常不应出现 |
| `embedded_file_count` | 嵌入文件/附件结构异常 |
| `creator_missing` / `producer_missing` | 生成器元数据缺失或异常 |

### 典型命中原因

| 原因 | 说明 |
| --- | --- |
| `pdf_smask_present` | 发现透明蒙版，疑似透明贴图 |
| `full_page_image_with_local_overlays` | 整页图 + 局部小图层，疑似扫描底图上覆盖修改 |
| `dense_pdf_image_overlays` | 每页图片对象密度异常 |
| `incremental_update_trace` | PDF 增量编辑痕迹 |
| `embedded_script_or_file` | JS 或嵌入文件异常 |
| `high_font_object_count` | 字体对象过多，疑似混排或编辑 |

### 对应负样本

- `stamp_paste`：透明印章 PNG 贴入 PDF。
- `signature_paste`：签名图片贴入合同/回单。
- `screenshot_to_pdf`：截图或扫描图片重新生成 PDF。
- `local_cover_overlay`：白块覆盖后重新写字。
- `page_splice`：插入或替换页面。

## 3. 风险二：图片 PS / 拼接痕迹检测

### 检测目标

识别扫描件、截图件、图片发票、网页截图中是否存在局部 PS、拼接、重压缩、噪声不一致等痕迹。

### 当前实现

脚本：

```text
src/analyze_visual_forensics.py
```

PDF 会先通过 `pdftoppm` 渲染为图片，然后对图片做视觉取证。

### 使用技术

| 技术 | 实现字段 | 原理 |
| --- | --- | --- |
| ELA Error Level Analysis | `ela_score` | 篡改区域经过不同压缩历史，JPEG 重压缩误差分布会异常 |
| 局部噪声块一致性 | `block_variance_score` | 同一扫描/截图的纸面噪声应相对一致，局部粘贴区域噪声不同 |
| 边缘密度 | `edge_density` | 贴图边界、锐化文字、PS 边缘会产生异常边缘响应 |
| 红色区域比例 | `red_stamp_score` | 辅助发现红章、红色水印、异常红色贴图 |

### 典型命中原因

| 原因 | 说明 |
| --- | --- |
| `high_ela_recompression_error` | 局部重压缩误差高，疑似 PS |
| `local_noise_block_inconsistency` | 局部噪声块不一致，疑似拼接/覆盖 |
| `dense_edge_or_paste_boundary` | 边缘密度高，疑似贴图边缘或异常锐化 |
| `red_stamp_like_region` | 存在红章/红色区域候选 |

### 对应负样本

- `noise_patch`：局部噪声块 + 改字。
- `screenshot_to_pdf`：网页/图片截图转 PDF。
- `qr_replace`：替换二维码区域。
- `local_cover_overlay`：局部覆盖重写。
- 真实扫描底图上 PS 金额、日期、姓名。

## 4. 风险三：印章 / 签名贴图检测

### 检测目标

识别合同、发票、回单、结算单里印章或签名是否是后贴图片，而不是自然盖章/签署。

### 当前可检测特征

| 特征 | 来源 | 原理 |
| --- | --- | --- |
| `SMask` / alpha | PDF 对象结构 | 透明 PNG 印章、签名常带 alpha 或 SMask |
| 小图层对象 | PDF 图片对象 | 贴章通常是一个局部小图片 |
| 红色区域比例 | 视觉取证 | 印章红色通道集中，和背景颜色分布不同 |
| 边缘密度 | 视觉取证 | 贴图边缘可能过锐或不自然 |
| 噪声不一致 | 视觉取证 | 印章区域噪声与纸面/扫描背景不同 |
| Qwen 视觉复核 | `analyze_qwen_ocr.py` | 可识别“红章遮挡”“水印”“贴图感”等语义线索 |

### 印章贴图为什么能被发现

真实盖章通常有：

- 墨迹深浅不均。
- 纸张纹理透出。
- 边缘有自然扩散。
- 扫描噪声和纸面一致。
- 盖章角度、压印形变自然。

贴图印章常见异常：

- 透明边缘或 SMask。
- 红色通道过纯、过均匀。
- 边缘过锐或有锯齿。
- 分辨率与整页扫描 DPI 不一致。
- 多份材料出现完全相同印章图案。
- 印章压在文字上但没有真实墨迹融合。

### 对应负样本

- `stamp_paste`：透明红章贴图。
- `signature_paste`：签名贴图。
- `qr_replace`：局部贴图替换。
- `local_cover_overlay`：盖章区域遮盖后重写。

## 5. 风险四：OCR / 文本层异常

### 检测目标

识别 PDF 文本层、页面视觉文字、OCR 结果之间是否一致；发现隐藏文本、覆盖重写、扫描件不可读、字段缺失等问题。

### 当前实现

| 脚本 | 作用 |
| --- | --- |
| `src/analyze_text_business_rules.py` | 用 `pdftotext -bbox` 提取 PDF 文本层 word 坐标 |
| `src/analyze_ocr_deepseek.py` | 用 Tesseract OCR 输出文字、置信度和坐标 |
| `src/analyze_qwen_ocr.py` | 用 Qwen-VL 直接做图片 OCR 和视觉复核 |

### 关键特征

| 特征 | 原理 |
| --- | --- |
| `text_word_count` | PDF 文本层词数，判断是否缺失文本层 |
| `text_word_coordinates.csv` | word 级坐标，用于定位字段证据 |
| `ocr_word_count` | OCR 识别词数，扫描件可补全文本 |
| `ocr_mean_confidence` | OCR 平均置信度，低置信度可能来自模糊/遮挡/压缩 |
| `ocr_text_preview` | OCR 文本预览 |
| `ocr_fields_json` | OCR 抽取金额、日期、证件号、账号 |
| `qwen_ocr_text` | Qwen-VL 视觉 OCR 文本 |
| `qwen_review_note` | Qwen 对页面视觉内容的复核建议 |

### 典型命中原因

| 原因 | 说明 |
| --- | --- |
| `pdf_text_layer_missing_or_unreadable` | PDF 无可读文本层，可能是扫描件，也可能是截图转 PDF |
| `very_sparse_text_layer` | 文本层过少，疑似图片化或覆盖 |
| `text_layer_repetition` | 文本层重复异常 |
| `ocr_low_mean_confidence` | OCR 置信度低，疑似模糊/遮挡/压缩 |
| `ocr_many_low_confidence_words` | 低置信度词过多 |

### 注意

“没有文本层”不能直接判定为 fake，因为很多正常扫描件本来就是图片 PDF。它只能作为“需要 OCR 或人工复核”的线索。

## 6. 风险五：业务逻辑一致性检测

### 检测目标

检查材料内部字段是否符合业务数学约束、格式约束、时间约束和跨字段一致性。

### 当前实现

模块：

```text
src/detectors/business_logic_detector.py
```

覆盖 5 类文档：

| 文档类型 | 当前规则 |
| --- | --- |
| 发票 `invoice` | 金额 + 税额 = 价税合计；发票号；税号 |
| 合同 `contract` | 金额、签署日期、甲乙方、大写金额候选 |
| 银行流水 `bank_page` | 账号、交易日期、金额序列、余额递推前置检查 |
| 征信报告 `credit_report` | 身份证号、报告日期、数值摘要字段 |
| 结算单/回单 `settlement_statement` / `receipt` | 金额、账号、交易日期 |

### 典型命中原因

| 原因 | 说明 |
| --- | --- |
| `invoice_amount_tax_total_mismatch` | 发票金额、税额、合计不满足加法 |
| `contract_date_missing` | 合同日期缺失 |
| `contract_party_a_missing` / `contract_party_b_missing` | 合同主体缺失 |
| `bank_amount_sequence_sparse` | 银行流水金额序列不足，无法递推余额 |
| `credit_report_id_missing` | 征信报告身份证号缺失 |
| `settlement_account_missing` | 回单/结算单账号字段缺失 |

### 对应负样本

- `logic_conflict`：金额、税额、合计故意不一致。
- `amount_rewrite`：金额改写。
- `date_rewrite`：日期改写。
- `identity_rewrite`：姓名、证件号、主体字段改写。
- `page_splice`：替换页导致汇总与明细不一致。

## 7. 本轮新增负样本

本轮已将合成负样本生成扩展到：

```text
每类材料 30 个
总计 150 个 synthetic fake PDF
```

覆盖材料类型：

| 材料类型 | 数量 |
| --- | ---: |
| `credit_report` | 30 |
| `contract` | 30 |
| `invoice` | 30 |
| `bank_page` | 30 |
| `settlement_statement` | 30 |

覆盖伪造方式：

| 伪造方式 | 对应风险 |
| --- | --- |
| `amount_rewrite` | 文本层异常、业务逻辑 |
| `date_rewrite` | 文本层异常、业务逻辑 |
| `identity_rewrite` | 文本层异常、身份字段一致性 |
| `stamp_paste` | 印章贴图、SMask、红色区域 |
| `signature_paste` | 签名贴图、SMask、边缘融合 |
| `qr_replace` | 局部贴图、发票/二维码一致性 |
| `screenshot_to_pdf` | 图片化 PDF、PS 痕迹 |
| `local_cover_overlay` | 局部覆盖重写 |
| `font_mismatch` | 字体/基线/字号异常 |
| `page_splice` | 页面拼接、结构不一致 |
| `logic_conflict` | 业务逻辑冲突 |
| `noise_patch` | 局部噪声不一致 |
| `text_layer_mismatch` | 文本层与视觉文字不一致 |

## 8. 当前数据状态

本轮重建后：

| 数据 | 数量 |
| --- | ---: |
| 总记录 | 593 |
| normal | 313 |
| fake | 280 |
| synthetic fake | 150 |

说明：

- PDF 对象结构对贴图、透明层、截图转 PDF 最敏感。
- 图片取证对真实扫描底图上的 PS 更有效。
- Qwen-VL 对带显式水印的合成 fake 检出很强，但主观风险分不能单独作为最终判定。
- 业务逻辑规则依赖 OCR/字段抽取质量；后续应继续做字段定位和按模板校准。

## 9. 后续优化建议

| 优先级 | 事项 |
| --- | --- |
| P0 | 把 Qwen-VL 输出作为证据展示，不直接覆盖规则综合分 |
| P0 | 对文本层缺失、身份证缺失等规则降权，减少 normal 扫描件误报 |
| P1 | 加入真实扫描底图 PS 样本，而不只是矢量合成 PDF |
| P1 | 做印章区域定位和印章模板指纹 |
| P1 | 发票二维码解析与金额税额强校验 |
| P2 | 银行流水余额递推、合同大小写金额一致性 |
