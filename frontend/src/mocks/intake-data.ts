export interface UploadedFile {
  name: string;
  pages: number;
  size: string;
  uploadedAt: string;
}

export interface IntakeCheck {
  label: string;
  value: string;
  ok: boolean;
}

export interface IntakeParsing {
  label: string;
  pct: number;
  status: "done" | "running" | "queued";
}

export interface IntakeData {
  checks: IntakeCheck[];
  parsing: IntakeParsing[];
  uploadedFile: UploadedFile;
  progressText: string;
}

export const intakeData: IntakeData = {
  checks: [
    { label: "申请编号", value: "A1-2025-03750", ok: true },
    { label: "拟上市代码", value: "03750.HK（待分配）", ok: true },
    { label: "公司名称", value: "宁德时代", ok: true },
    { label: "上市日期", value: "2025-05-20", ok: true },
    { label: "行业分类", value: "新能源 · 动力电池", ok: true },
    { label: "发行价", value: "HK$ 263.00", ok: true },
    { label: "发行规模", value: "HK$ 410.1 亿", ok: true },
    { label: "Sponsor", value: "JPMorgan / 中金", ok: true },
    { label: "审计机构", value: "安永会计师事务所", ok: false },
  ],
  parsing: [
    { label: "PDF 解析（428 页）", pct: 100, status: "done" },
    { label: "OCR 表格抽取（67 张表）", pct: 100, status: "done" },
    { label: "章节识别 & 页码定位", pct: 100, status: "done" },
    { label: "证据块向量化（4 218 段）", pct: 86, status: "running" },
    { label: "财务三表抽取", pct: 60, status: "running" },
    { label: "风险因素章节抽取", pct: 0, status: "queued" },
  ],
  uploadedFile: {
    name: "03750_CATL_zh_final.pdf",
    pages: 428,
    size: "38.2 MB",
    uploadedAt: "17:24",
  },
  progressText: "总进度 74% · 预计还需 1 分 32 秒",
};
