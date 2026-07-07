# SJTU 虚假材料智能检测项目说明

## 1. 项目目标

本项目用于对申请材料、财务材料、征信材料、合同、发票等文件进行初步真实性风险分析。

系统当前覆盖三类能力：

- 历史样本可视化核查：查看已有样本、风险分、风险原因和 PDF 原件。
- 上传 PDF 检测：上传单份 PDF，自动提取对象结构特征并生成风险分。
- 训练迭代闭环：对上传样本进行人工标注，标注后的样本进入下一轮轻量模型训练。

当前系统更接近“材料风险辅助核查平台”，不是最终司法鉴定系统。输出结果应作为复核线索，而不是直接定性结论。

## 2. 输入内容

### 2.1 离线数据输入

原始离线数据来自两个材料包：

| 输入 | 含义 |
| --- | --- |
| `正常材料包.zip` | 默认归为 `normal` 的材料 |
| `虚假材料包.zip` | 默认归为 `fake` 的材料 |

支持的文件类型：

- PDF
- JPG/JPEG
- PNG

当前部署版主要展示和检测 PDF。图片类材料在原始 manifest 中存在，但尚未进入 PDF 对象风险检测。

### 2.2 在线上传输入

前端“上传检测”页支持上传：

- 单份 PDF 文件

上传后系统会保存文件，并立即执行 PDF 对象结构扫描。

上传样本初始标签为：

```text
uploaded
```

这个标签表示“已上传但未人工确认”，不会直接进入训练。

只有人工标注为以下标签后，才会进入训练数据：

```text
normal
fake
```

## 3. 输出内容

### 3.1 样本级输出

每个样本会输出以下信息：

| 字段 | 含义 |
| --- | --- |
| `document_id` | 样本 ID |
| `label` | 标签：`normal`、`fake` 或 `uploaded` |
| `doc_type` | 材料类型，如 `credit_report`、`contract`、`invoice` |
| `ext` | 文件扩展名 |
| `size_bytes` | 文件大小 |
| `sha256` | 文件哈希 |
| `pages` | PDF 页数 |
| `creator` | PDF Creator 元数据 |
| `producer` | PDF Producer 元数据 |
| `pdf_version` | PDF 版本 |
| `image_count` | PDF 图片对象数量 |
| `smask_count` | PDF SMask/透明蒙版数量 |
| `large_image_count` | 大图对象数量 |
| `small_overlay_count` | 小覆盖图层数量 |
| `font_warning_count` | Poppler 字体解析警告数量 |
| `object_risk_score` | 规则风险分 |
| `object_risk_reasons` | 风险原因 |
| `pdf_url` | PDF 在线预览地址 |

### 3.2 页面输出

前端页面提供：

- 总览指标
- 材料类型分布
- 风险分布
- 风险原因排行
- 样本搜索、筛选、排序
- PDF 原件预览
- 上传检测结果
- 人工标注按钮
- 训练状态、训练日志、模型指标、数据质量告警

### 3.3 训练输出

轻量对象特征模型训练后输出：

| 输出 | 含义 |
| --- | --- |
| `object_classifier_metrics.json` | 模型指标、权重、阈值、数据审计 |
| `object_training.log` | 每轮 epoch 的训练日志 |
| `object_training_status.json` | 当前训练任务状态 |

训练页展示：

- Val F1
- Precision
- Recall
- Threshold
- 特征重要性
- 数据可信度
- 类别分布告警

## 4. 中间处理流程

### 4.1 数据准备

脚本：

```text
src/prepare_dataset.py
```

输入：

```text
正常材料包.zip
虚假材料包.zip
```

处理方法：

1. 解压两个 zip。
2. 根据所在目录生成初始标签：
   - 正常材料包 -> `normal`
   - 虚假材料包 -> `fake`
3. 根据文件名关键词推断材料类型：
   - 征信、信用报告 -> `credit_report`
   - 发票 -> `invoice`
   - 合同、购销 -> `contract`
   - 结算单 -> `settlement_statement`
   - 回单 -> `receipt`
   - 网银 -> `bank_page`
4. 计算文件大小和 SHA256。
5. 生成 manifest。

输出：

```text
outputs/manifest.csv
```

当前源数据统计：

| 数据表 | 总数 | normal | fake | 说明 |
| --- | ---: | ---: | ---: | --- |
| `outputs/manifest.csv` | 317 | 313 | 4 | 包含 PDF/JPG/PNG |
| `pdf_object_features.csv` | 261 | 258 | 3 | 只包含 PDF |

说明：有 1 个 fake 是图片格式，因此不会进入 PDF 对象特征表。

### 4.2 PDF 对象特征提取

脚本：

```text
src/extract_pdf_features.py
```

输入：

```text
outputs/manifest.csv
```

只处理：

```text
ext == .pdf
```

使用工具：

- `pdfinfo`
- `pdfimages`

提取内容：

- 页数
- Creator
- Producer
- PDF 版本
- 页面尺寸
- 图片对象数量
- SMask 数量
- 大图数量
- 小覆盖图数量
- 字体解析警告数量

输出：

```text
outputs/features/pdf_object_features.csv
outputs/features/pdf_object_summary.json
```

### 4.3 PDF 风险规则

当前风险分是规则分，不是深度学习模型直接输出。

规则：

| 条件 | 分数 | 风险原因 |
| --- | ---: | --- |
| 存在 SMask/透明蒙版 | +25 | `pdf_smask_present` |
| Poppler 字体解析出现警告 | +20 | `poppler_font_warning` |
| 存在大图，同时存在多个小覆盖图层 | +15 | `full_page_image_with_local_overlays` |

最终分数：

```text
object_risk_score = min(规则分之和, 100)
```

因此当前风险分只会出现少量离散值，例如：

- `0`
- `15`
- `25`
- `60`

当前风险分布：

| 标签 | 风险分布 |
| --- | --- |
| normal | 0 分：217，15 分：41 |
| fake | 25 分：2，60 分：1 |

### 4.4 PDF 页面渲染与图像模型

脚本：

```text
src/render_pages.py
src/train_doc_classifier.py
```

设计用途：

1. 将 PDF 前几页渲染为图片。
2. 将 JPG/PNG 复制为页面图片。
3. 训练 ResNet18 页面分类模型。

训练脚本使用：

- PyTorch
- torchvision
- ResNet18
- CrossEntropyLoss
- AdamW
- 可选 weighted sampler
- 可选 class weighted loss

当前限制：

- 目标部署服务器没有 `torch/torchvision`。
- 目标服务器无法直接访问内网训练机 `192.168.1.85`。
- 当前线上主要运行轻量对象特征模型，ResNet 图像模型作为源码保留。

### 4.5 在线上传 PDF 检测

入口：

```text
POST /api/upload
```

输入：

```text
multipart/form-data
file=<PDF>
```

处理方法：

1. 保存 PDF 到：

```text
data/uploads/
```

2. 使用纯 Python 扫描 PDF 字节结构：
   - PDF 版本
   - `/Type /Page`
   - `/Subtype /Image`
   - `/SMask`
   - `/Font`
   - `/Creator`
   - `/Producer`
3. 计算上传样本的对象风险分。
4. 写入：

```text
data/features/uploaded_documents.csv
```

输出：

- 上传样本 ID
- 风险分
- 风险原因
- PDF 预览地址
- 元数据和对象结构特征

注意：线上上传检测当前不依赖 Poppler，因此精度弱于离线 `pdfinfo/pdfimages` 特征提取。

### 4.6 人工标注闭环

入口：

```text
POST /api/document/label
```

输入：

```json
{
  "document_id": "upload_xxx",
  "label": "normal"
}
```

支持标签：

```text
normal
fake
uploaded
```

逻辑：

- 上传样本默认是 `uploaded`。
- `uploaded` 不进入训练。
- 标为 `normal` 或 `fake` 后，会进入下一轮训练。
- 标回 `uploaded` 后，相当于撤回标注。

### 4.7 轻量对象特征模型训练

脚本：

```text
src/train_object_classifier.py
```

入口：

```text
POST /api/training/start
```

训练数据来源：

1. 历史 PDF 特征：

```text
data/features/pdf_object_features.csv
```

2. 已人工标注的上传样本：

```text
data/features/uploaded_documents.csv
```

训练前会生成合并训练表：

```text
data/features/training_documents.csv
```

只有以下标签进入训练：

```text
normal
fake
```

训练特征：

```text
pages
size_bytes
image_count
smask_count
large_image_count
small_overlay_count
font_warning_count
```

明确排除：

```text
object_risk_score
```

原因：`object_risk_score` 是规则引擎输出，如果作为训练特征会造成规则分泄漏。

模型方法：

- 标准化特征
- Logistic Regression
- 类别加权
- 分层训练/验证划分
- 按训练集 F1 搜索阈值

输出：

```text
data/models/object_classifier_metrics.json
data/jobs/object_training.log
data/jobs/object_training_status.json
```

## 5. 前端功能

前端文件：

```text
web/index.html
web/styles.css
web/app.js
```

主要页面：

| 页面 | 功能 |
| --- | --- |
| 总览 | 展示样本数量、风险分布、材料类型分布、训练曲线 |
| 样本核查 | 搜索、筛选、排序、查看详情、PDF 预览 |
| 上传检测 | 上传 PDF、查看风险分和预览、人工标注 |
| 训练迭代 | 启动训练、查看日志、指标、特征重要性和数据告警 |

交互能力：

- 点击指标卡片筛选样本。
- 点击图表进入样本核查。
- 点击样本行打开详情抽屉。
- 在详情中预览 PDF。
- 对上传样本进行人工标注。
- 启动后端训练并轮询训练状态。

## 6. 后端接口

后端文件：

```text
app.py
```

主要接口：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/dashboard` | 总览数据 |
| GET | `/api/documents` | 样本列表、搜索、筛选、排序 |
| GET | `/api/document?id=...` | 单个样本详情 |
| GET | `/api/pdf/<document_id>` | PDF 原件预览 |
| POST | `/api/upload` | 上传 PDF 并检测 |
| POST | `/api/document/label` | 标注上传样本 |
| POST | `/api/training/start` | 启动训练 |
| GET | `/api/training/status` | 查询训练状态和日志 |

PDF 预览支持：

- `Content-Type: application/pdf`
- `Content-Disposition: inline`
- `Accept-Ranges: bytes`
- HTTP Range 分段加载

## 7. 部署方式

当前部署目录：

```text
/opt/sjtu_material_visual
```

启动方式：

```bash
python3 app.py --host 0.0.0.0 --port 3002
```

systemd 服务：

```text
sjtu-material-visual.service
```

公网地址：

```text
http://81.70.178.203:3002
```

端口要求：

- 使用 `3002`
- 监听 `0.0.0.0:3002`
- 不使用 `3001`

## 8. 当前限制

### 8.1 数据限制

- fake 样本太少。
- PDF 特征表中只有 3 个 fake。
- 有 1 个 fake 是图片，不在 PDF 风险分析中。
- 当前 normal/fake 主要来自目录归属，不是逐文件专业人工标注。

### 8.2 风险规则限制

- 当前规则只有 3 类，因此风险分离散。
- 风险分不是最终判定，只是核查线索。
- 上传检测使用纯 Python 字节扫描，弱于 Poppler 解析。

### 8.3 训练限制

- 验证集 fake 只有 1 个，指标不可靠。
- 当前训练页会显示 `reliability: low`。
- `object_risk_score` 已从训练特征中排除，避免泄漏。
- ResNet 图像模型源码保留，但线上服务器暂不具备 PyTorch 环境。

### 8.4 部署限制

- 目标服务器无法访问内网训练机 `192.168.1.85`。
- 历史 PDF 原件尚未全量同步，当前只有部分 PDF 可在线预览。

## 9. 后续优化方向

优先级建议：

1. 补充 fake 样本，尤其是 PDF fake 样本。
2. 批量上传材料包，而不是单 PDF 上传。
3. 增加人工复核状态：待复核、正常、虚假、不确定。
4. 将 JPG/PNG 纳入检测流程。
5. 安装或容器化 Poppler，统一离线和在线 PDF 特征提取。
6. 保存每次训练的样本快照、模型版本和指标报告。
7. 增加报告导出功能。
8. 增加登录鉴权和操作日志。

## 10. 总结

当前项目已经具备一个完整的最小闭环：

```text
材料输入 -> 特征提取 -> 风险规则 -> 前端核查 -> PDF 预览 -> 上传检测 -> 人工标注 -> 训练迭代
```

但系统质量的关键瓶颈在数据：

- fake 样本数量不足。
- 人工标注体系刚开始建立。
- 图片类材料尚未进入完整检测链路。

因此，下一阶段重点应放在数据标注、样本扩充、规则扩展和训练版本管理上。
