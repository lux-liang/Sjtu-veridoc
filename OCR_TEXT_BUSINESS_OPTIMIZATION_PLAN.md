# OCR、文本层坐标、字段抽取与业务规则持续优化计划

## 1. 原则

本项目不需要把 DeepSeek 或其他大模型 API 放在主检测链路里。

推荐架构：

1. 本地确定性检测为主：PDF 文本层、坐标、对象结构、视觉取证、字段规则。
2. OCR 作为文本层缺失时的补充：扫描件、截图 PDF、图片材料。
3. DeepSeek 作为可选增强：只用于规则命中后的解释、字段纠错建议、人工复核摘要。
4. API Key 只通过环境变量传入，不能写入仓库、CSV、日志或 Markdown。

环境变量约定：

```bash
export DEEPSEEK_API_KEY="***"
export DEEPSEEK_MODEL="deepseek-chat"
```

默认命令不会调用 DeepSeek。只有脚本显式传入 `--use-deepseek` 时才会调用。

## 2. 本轮新增能力

新增脚本：

```text
src/analyze_text_business_rules.py
```

输出文件：

```text
outputs/features/text_business_features.csv
outputs/features/text_business_summary.json
outputs/features/text_word_coordinates.csv
```

能力：

| 能力 | 实现方式 | 当前状态 |
| --- | --- | --- |
| PDF 文本层坐标 | `pdftotext -bbox` 提取 word 级坐标 | 已实现 |
| 行级文本聚合 | 按页码和 y 坐标聚合 word | 已实现 |
| 字段抽取 | 正则抽取金额、日期、证件号、账号、税号、发票号 | 已实现 |
| 发票规则 | `金额 + 税额 = 合计` 校验 | 已实现基础版 |
| 合同规则 | 甲乙方、日期、金额缺失检查 | 已实现基础版 |
| 银行流水规则 | 账号、金额序列稀疏检查 | 已实现基础版 |
| 征信规则 | 身份证号、日期缺失检查 | 已实现基础版 |
| 回单/结算单规则 | 核心账号/金额字段缺失检查 | 已实现基础版 |
| DeepSeek 解释 | 规则命中后生成一句复核建议 | 可选，默认关闭 |
| 综合风险合并 | 合并 PDF 对象、视觉取证、文本业务规则 | 已实现 |
| Tesseract OCR | PDF 首页渲染后 OCR，输出文字、置信度和坐标 | 已实现 |
| DeepSeek OCR 文本复核 | OCR 文本送入 DeepSeek 做字段核验和解释 | 已接入；当前 API 返回余额不足 |

新增综合风险脚本：

```text
src/build_combined_risk.py
```

输出文件：

```text
outputs/features/combined_risk_features.csv
outputs/features/combined_risk_summary.json
outputs/features/ocr_deepseek_features.csv
outputs/features/ocr_deepseek_summary.json
outputs/features/ocr_word_coordinates.csv
```

注意：当前很多正常扫描 PDF 本身没有可读文本层，所以 `pdf_text_layer_missing_or_unreadable` 不能直接判定为虚假，只能作为“需要 OCR 或人工复核”的线索。OCR 未接入前，文本层缺失类规则需要降权使用。

2026-07-06 线上部署结果：

- 81.70.178.203:3002 已同步新数据和 OCR 结果。
- PDF 总数：407。
- fake PDF：149。
- OCR 核验：407/407。
- OCR word 坐标输出：`data/features/ocr_word_coordinates.csv`。
- DeepSeek 文本复核已接入脚本，但当前 API 返回 `HTTP 402 Insufficient Balance`，因此线上 `deepseek_reviewed=0`。
- DeepSeek 账户充值或更换 key 后，可重新运行 `src/analyze_ocr_deepseek.py --use-deepseek`，无需重新同步前端。

## 3. 五类风险和落地路径

### 3.1 PDF 对象结构异常

已由 `src/extract_pdf_features.py` 实现：

- SMask/透明蒙版
- 图片对象、大图、小覆盖图
- PDF 对象数、stream 数、字体对象数
- EOF/startxref 增量更新痕迹
- JS、嵌入文件、AcroForm/XFA

下一步：

- 与文本业务规则合并出综合风险分。
- 对 `screenshot_to_pdf` 和 `local_cover_overlay` 单独建立子规则。

### 3.2 图片 PS 痕迹

已由 `src/analyze_visual_forensics.py` 实现：

- ELA 重压缩误差
- 局部噪声块一致性
- 边缘密度
- 红色印章区域比例

下一步：

- 补真实扫描底图局部 PS 合成样本。
- 增加印章区域周边背景噪声对比。
- 增加二维码区域质量和位置检测。

### 3.3 印章/签名贴图

当前可由 PDF SMask、红色区域和视觉边缘特征间接发现。

下一步：

- 增加红章候选区域定位。
- 计算红章区域 alpha/边缘/噪声/颜色分布。
- 建立重复印章模板指纹。

### 3.4 字体与文本层异常

本轮新增了 word 级坐标和文本层统计。

下一步：

- 提取同一行字号、基线、间距异常。
- 对金额、日期、姓名等敏感字段做局部字体一致性判断。
- 接入 OCR 后比较“PDF 文本层”和“视觉文字”是否一致。

### 3.5 业务逻辑一致性

本轮实现基础规则。

下一步按材料类型扩展：

| 材料 | 规则 |
| --- | --- |
| 发票 | 价税合计、税率、发票号格式、二维码区域 |
| 合同 | 小写金额与大写金额、签署日期、生效日期、主体字段 |
| 银行流水 | 余额递推、收入支出方向、交易时间排序 |
| 征信报告 | 汇总账户数、逾期统计、查询记录与明细一致 |
| 回单/结算单 | 户名、账号、金额、流水号、时间格式一致 |

## 4. 迭代脚本

已接入：

```text
scripts/run_remote_iteration.sh
```

默认流程：

1. 准备 manifest。
2. 提取 PDF 对象特征。
3. 提取文本层坐标、字段和业务规则。
4. 提取视觉取证特征。
5. 训练对象特征模型。
6. 渲染页面。
7. 训练页面图像分类器。

## 5. 待办队列

| 优先级 | 优化点 | 说明 |
| --- | --- | --- |
| P0 | 综合风险分合并 | 合并 PDF 对象、视觉取证、文本业务规则为统一 `combined_risk_score` |
| P0 | 前端展示文本业务规则 | 页面显示字段抽取结果、风险原因、word 坐标证据 |
| P0 | 文本规则校准 | 降低文本层缺失、证件号缺失在扫描件 normal 上的误报权重 |
| P1 | OCR 引擎接入 | 优先本地 PaddleOCR/Tesseract；DeepSeek 不作为 OCR 主引擎 |
| P1 | OCR 与 PDF 文本层对齐 | 发现隐藏文本、覆盖重写、视觉文字与文本层不一致 |
| P1 | 发票规则增强 | 金额、税额、合计、税率、发票号和二维码区域 |
| P1 | 合同规则增强 | 大小写金额一致、主体字段、签署日期 |
| P2 | 印章定位模型 | 红章候选区域定位与贴图判别 |
| P2 | 真实扫描 PS 样本 | 增加扫描底图局部改字、贴章、噪声不一致样本 |
| P2 | DeepSeek 复核摘要 | 对规则命中样本生成审查建议，严格禁止替代规则判断 |
