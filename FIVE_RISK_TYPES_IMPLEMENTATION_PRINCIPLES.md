# 五类风险检测原理与当前实现

更新时间：2026-07-07

## 结论

五类检测已经实现并接入线上主评分，入口为 `src/build_combined_risk.py`，线上服务为 `http://81.70.178.203:3002/`。

当前不是单一模型黑盒评分，而是多检测器证据融合：

1. PDF 结构风险
2. 图片 PS / 图像取证风险
3. 印章贴图 / 红章覆盖风险
4. OCR 与文本层坐标风险
5. 字段抽取与业务逻辑风险

综合评分采用 `v2_evidence_calibrated`：强证据直接贡献主风险，弱证据只做提示；扫描件上下文会降权；OCR 识别到 `edited/void/fake/synthetic/training/作废/伪造/篡改` 等明确标记会拉高风险。

最近一次校准：

- 新增印章贴图特征：红色连通组件、最大红色组件面积、边缘硬度、颜色均匀度。
- 将普通红色区域、红色连通组件、颜色过平调整为弱证据。
- 只有 `seal_hard_edge_overlay`、SMask、密集覆盖层、ELA/JPEG 异常等更硬证据才触发强风险或跨域加分。
- 扫描件中的普通边缘/噪声/红章信号会降级为复核提示。

## 1. PDF 结构检测

实现文件：`src/extract_pdf_features.py`

核心原理：篡改 PDF 往往不是从原始业务系统导出，而是在原 PDF 或扫描图上叠加图片、蒙版、注释、脚本、增量更新。PDF 对象结构会留下痕迹。

当前检测特征：

- `smask_count`：透明蒙版数量。贴图、抠图、半透明印章经常生成 SMask。
- `large_image_count`：整页大图数量。扫描件正常会有，但和小覆盖层组合时需要复核。
- `small_overlay_count`：小图片覆盖层数量。常见于局部改金额、姓名、日期、印章。
- `font_warning_count`：Poppler 解析字体异常。篡改或生成器不规范时会触发。
- `incremental_update_count`：多次 `%%EOF/startxref`，说明 PDF 可能被追加编辑。
- `javascript_count / embedded_file_count`：嵌入脚本或文件，属于高危结构。
- `creator_missing / producer_missing`：元数据缺失，弱证据。

典型风险原因：

- `pdf_smask_present`
- `full_page_image_with_local_overlays`
- `dense_pdf_image_overlays`
- `incremental_update_trace`
- `embedded_script_or_file`
- `missing_creator_producer`

已做优化：

- 对扫描件来源如 `intsig / DocuCentre / Quartz / QQBrowser / scanner` 做上下文识别。
- 扫描件里的 `full_page_image_with_local_overlays` 降为 `scanner_image_layer_review`，避免把正常扫描 App 误判为 PS。

## 2. 图片 PS / 图像取证检测

实现文件：`src/analyze_visual_forensics.py`

核心原理：图像被局部编辑后，被编辑区域和原图在压缩误差、噪声分布、边缘密度上通常不一致。

当前检测技术：

- ELA，Error Level Analysis：把页面重新 JPEG 压缩，再计算原图和重压缩图差异。局部二次压缩、贴图区域误差会异常。
- 分块噪声方差：把灰度图切成 64x64 块，统计每块标准差，再看块间差异。粘贴区域常出现噪声水平突变。
- 边缘密度：用 `ImageFilter.FIND_EDGES` 检测硬边界。贴图、遮盖、文字块粘贴常有异常边缘。
- 红色区域比例：初步定位红章/红色标记。

当前输出字段：

- `ela_score`
- `block_variance_score`
- `edge_density`
- `red_stamp_score`
- `visual_risk_score`
- `visual_risk_reasons`

当前风险原因：

- `high_ela_recompression_error`
- `local_noise_block_inconsistency`
- `dense_edge_or_paste_boundary`
- `red_stamp_like_region`

局限：

- 当前只渲染第一页。
- ELA 对原始扫描质量、压缩链路敏感，不能单独作为高风险结论。
- 需要和 PDF 结构、OCR、业务规则交叉印证。

## 3. 印章贴图检测

实现文件：`src/analyze_visual_forensics.py`

核心原理：真实盖章通常有墨迹扩散、局部深浅不均、纸张纹理融合；后贴 PNG/JPG 印章往往有硬边界、颜色过平、透明蒙版或红色连通区域异常。

已实现特征：

- `red_stamp_score`：红色像素占比，检测是否存在明显红章区域。
- `red_component_count`：红色连通组件数量。印章贴图通常形成较稳定的红色组件。
- `max_red_component_ratio`：最大红色组件面积占整页比例。过滤零散红字，定位大块印章。
- `red_component_edge_contrast`：红色组件边缘强度。贴图边界通常比真实盖章更硬。
- `red_component_color_std`：红色组件内部颜色标准差。贴图印章颜色可能过于均匀。
- PDF 侧 `smask_count`：透明蒙版，常见于 PNG 印章贴图。

当前风险原因：

- `red_stamp_like_region`
- `seal_red_connected_component`
- `seal_hard_edge_overlay`
- `seal_flat_color_overlay`
- `object:pdf_smask_present`

判断方式：

- 单独出现红色区域不直接判伪，只提示存在印章区域。
- 红色连通组件、颜色过平是弱证据。
- 硬边缘印章、SMask、密集覆盖层、图像压缩/噪声异常互相印证时，才构成更强印章贴图证据。
- 对扫描件来源会降权，避免真实盖章扫描件误报。

## 4. OCR 与文本层坐标检测

实现文件：

- `src/analyze_text_business_rules.py`
- `src/analyze_ocr_deepseek.py`
- `src/analyze_qwen_ocr.py`

核心原理：电子 PDF 的文本层和扫描 OCR 可以互相校验。篡改材料常出现文本层缺失、字段识别异常、OCR 坐标和内容不连续、低置信文本集中等问题。

当前实现：

- `pdftotext -bbox` 抽取 PDF 文本层和单词坐标。
- Tesseract OCR 渲染页面后输出 TSV，包含文字、置信度、坐标。
- 保存 `ocr_word_coordinates.csv`，用于后续字段坐标和版式校验。
- 支持 DeepSeek 文本核验，但当前 DeepSeek 余额不足，不依赖。
- 支持 Qwen-VL OCR/视觉核验，但当前线上主分没有用 Qwen 直接覆盖。

当前 OCR 风险原因：

- `ocr_text_missing`
- `ocr_low_mean_confidence`
- `ocr_many_low_confidence_words`
- `ocr_credit_id_missing`
- `ocr_invoice_amount_logic_suspicious`
- `ocr_contract_date_missing`
- `ocr_bank_amount_sequence_sparse`
- `training_synthetic_marker`
- `edited_marker`
- `void_marker`
- `fake_marker`

已做优化：

- 低置信 OCR 不再直接覆盖综合分，只作为复核提示。
- 明确识别到 `edited/void/fake/synthetic/training/作废/伪造/篡改` 才作为强文本证据。

## 5. 字段抽取与业务逻辑检测

实现文件：`src/analyze_text_business_rules.py`

核心原理：真实业务材料内部字段应满足数学和业务约束。伪造材料常在局部改字段后破坏这些约束。

当前支持文档类型：

- 发票
- 合同
- 银行流水
- 征信报告
- 结算单

当前字段抽取：

- 金额：`AMOUNT_RE`
- 日期：`DATE_RE`
- 身份证/证件号：`ID_RE`
- 银行账号：`ACCOUNT_RE`
- 税号：`TAX_ID_RE`
- 发票号：`INVOICE_NO_RE`
- 甲乙方、购买方、金额行、税额行、合计行、余额行等关键词邻近抽取

当前业务规则：

- 发票：金额 + 税额 是否等于价税合计。
- 发票：发票号缺失提示。
- 合同：甲方/乙方/日期缺失提示。
- 银行流水：账号缺失、金额序列过稀提示。
- 征信报告：身份证号、日期缺失提示。
- 结算单：账号和金额同时缺失提示。
- 文本层：文本过少、重复行比例高、文本层不可读。

风险分层：

- 金额/税额/合计不一致是强证据。
- 字段缺失是弱证据，不能单独判高风险。

## 综合评分实现

实现文件：`src/build_combined_risk.py`

当前版本：`v2_evidence_calibrated`

评分逻辑：

- 强证据按权重累加，例如 `pdf_smask_present`、`dense_pdf_image_overlays`、`embedded_script_or_file`、`seal_hard_edge_overlay`、`invoice_amount_tax_total_mismatch`。
- 弱证据最多加到较低上限，例如字段缺失、元数据缺失。
- 多域硬证据同时出现，增加 `cross:v2_strong_multi_domain_agreement`。
- 硬证据和弱证据同时出现，增加 `cross:v2_strong_weak_corroboration`。
- 扫描件上下文降权，减少正常扫描件误报。
- OCR 明确伪造/编辑标记可直接拉高风险。

当前线上统计快照：

- 数据规模：537 份 PDF。
- 标注分布：258 normal，279 fake。
- OCR 覆盖：537 份。
- 当前风险分布：131 low，255 medium，151 high。

## 当前仍需继续优化

1. 图片 PS 检测需要从第一页扩展到多页。
2. 印章检测需要增加圆形/椭圆形形态、文字环形分布、透明边 alpha 过渡检测。
3. OCR 坐标需要做字段区域模板，比如发票号码、金额、日期应出现在合理区域。
4. 业务规则需要按文档类型增加更多数学约束，比如银行流水余额递推、合同金额大小写一致。
5. 合成负样本要减少明显 `Synthetic/Training` 水印依赖，增加更接近真实篡改的负样本。
6. Qwen-VL 应做抽样复核，不直接覆盖主评分。
