# 综合检测器与 CLI 工具说明

## 新增文件

| 文件 | 说明 |
| --- | --- |
| `src/detectors/business_logic_detector.py` | 业务逻辑一致性检测器，覆盖发票、合同、银行流水、征信报告、结算单/回单 |
| `src/detectors/integrated_detector.py` | 综合检测管线，统一 PDF 结构、图片取证、印章贴图、OCR 文本、业务逻辑 5 类风险 |
| `run_detection.py` | CLI 入口，支持单文件、document_id、manifest 批量检测，支持选择性检测器 |

## CLI 示例

```bash
python3 run_detection.py --project-root . --document-id fake_000391 --format table
python3 run_detection.py --project-root . --batch-manifest /tmp/integrated_test_manifest.csv --format table
python3 run_detection.py --project-root . --file data/prepared/fake/credit_report/credit_report_000001.pdf --doc-type credit_report --format json
python3 run_detection.py --project-root . --document-id fake_000391 --detectors pdf_structure,business_logic --format json
```

## 线上 6 文件测试结果

测试环境：

```text
/opt/sjtu_material_visual
http://81.70.178.203:3002/
```

| document_id | 标签 | 类型 | 得分 | 等级 |
| --- | --- | --- | ---: | --- |
| `normal_000302` | normal | credit_report | 5.2 | clean |
| `normal_000083` | normal | credit_report | 4.5 | clean |
| `normal_000254` | normal | credit_report | 15.7 | low |
| `fake_000449` | fake | settlement_statement | 16.9 | low |
| `fake_000371` | fake | contract | 22.2 | low |
| `fake_000391` | fake | credit_report | 72.0 | high |

统计：

| 指标 | 值 |
| --- | ---: |
| normal 平均分 | 8.5 |
| fake 平均分 | 37.0 |
| 分离度 | 28.5 |

说明：

- `fake_000391` 命中 SMask、字体警告、全页图片覆盖、小图层密度、元数据缺失、嵌入脚本等多重强证据，因此综合管线判为 high。
- 线上版本已接入 Tesseract OCR 特征，因此分数会和未接入 OCR 的源项目测试略有差异。
- DeepSeek 文本复核逻辑已接入，但当前 API 返回 `HTTP 402 Insufficient Balance`，因此线上没有 DeepSeek 复核意见。

## 风险管线

```text
输入 PDF/图片
  -> PDF 结构分析：SMask、图层、元数据、字体、JS/嵌入文件
  -> 页面渲染：pdftoppm
  -> 图片 PS 检测：ELA、噪声、JPEG 块、边缘
  -> 印章贴图检测：红色区域、透明层、边缘、颜色/噪声线索
  -> OCR 文本检测：Tesseract OCR、文本置信度、字段抽取
  -> 业务逻辑校验：发票/合同/银行流水/征信/结算单规则
  -> 加权综合评分：0-100
```
