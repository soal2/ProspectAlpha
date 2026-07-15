---
name: pdf-parse-and-trace
description: 解析港股 IPO 招股书 PDF 并建立可溯源的结构化文本块。当需要将 PDF 原文转为带页码、章节路径、bbox 坐标与内容哈希的分析素材，或需要把任一结论回溯到 PDF 原文精确位置时使用。
---

# PDF 解析与溯源

## 目标

把招股书 PDF 转换为结构化文本块（DocumentBlock），使后续每一条风险结论都能精确回溯到 PDF 页面上的具体位置。

## 解析产物契约

每个文本块必须包含以下字段，缺一不可：

- `block_id`：格式 `block_{document_id}_{page:04d}_{idx:03d}`，全局唯一。
- `document_id`：来源文档 ID，用于回传原始 PDF 文件（`GET /documents/{document_id}/file`）。
- `page_number`：从 1 开始的页码。
- `section_path`：章节路径（如「风险因素/财务风险」），来自 PDF 目录与标题层级。
- `bbox`：`[x0, y0, x1, y1]`，PDF points 坐标、左上原点、y 轴向下，供前端 PDF 预览高亮。
- `char_start` / `char_end`：块文本在全文中的字符偏移。
- `source_hash`：块文本的 SHA-256，用于校验证据未被篡改。

## 工作步骤

1. 通过 `POST /documents/upload` 上传 PDF（≤50MB），解析由 PyMuPDF 完成并自动入库。
2. 校验解析结果：`block_count > 0`、每块 bbox 为 4 元素数组、页码与原 PDF 一致。
3. 引用任何原文时，必须同时给出 `document_id + page_number + bbox + source_hash`，四者齐备才算可溯源。
4. 不得改写、节选后重排原文语序；引用片段必须与块文本逐字一致，否则 source_hash 校验会失败。

## 禁止事项

- 禁止凭记忆或概括转述代替原文引用。
- 禁止引用没有 bbox 的「原文」——那不是可溯源证据。
