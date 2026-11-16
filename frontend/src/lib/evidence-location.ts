export type EvidenceLocatorKind = "pdf_page" | "text_offset" | "legacy_unknown";

export type EvidenceRegion = {
  page_number?: number | null;
  bbox?: number[];
};

export type EvidenceLocationSource = {
  locator_kind?: EvidenceLocatorKind | null;
  page_number?: number | null;
  char_start?: number | null;
  char_end?: number | null;
  table_header?: string | null;
  table_row?: string | null;
  table_column?: string | null;
  table_unit?: string | null;
  pdf_regions?: EvidenceRegion[] | null;
  section?: string | null;
};

export function pdfPageNumber(
  page: number | null | undefined,
  locator?: string | null,
): number | null {
  if (locator === "text_offset" || locator === "legacy_unknown") return null;
  if (page == null || !Number.isFinite(page) || page < 1) return null;
  return page;
}

export function evidenceLocationLabel(item: EvidenceLocationSource): string {
  const primary = pdfPageNumber(item.page_number, item.locator_kind);
  const extra = (item.pdf_regions ?? [])
    .map((region) => pdfPageNumber(region.page_number, item.locator_kind))
    .filter((page): page is number => page != null);
  const pages = [...new Set(primary == null ? extra : [primary, ...extra])];
  if (item.locator_kind === "legacy_unknown") return "历史定位未核实";
  const parts: string[] = [];
  if (item.locator_kind === "text_offset" || pages.length === 0) {
    parts.push("无 PDF 页");
    if (item.locator_kind === "text_offset") {
      parts.push(`文本偏移 ${item.char_start ?? 0}–${item.char_end ?? 0}`);
    }
  } else {
    parts.push(pages.map((page) => `P.${page}`).join("、"));
  }
  if (item.table_header) parts.push(`表头 ${item.table_header}`);
  if (item.table_row) parts.push(`行 ${item.table_row}`);
  if (item.table_column) parts.push(`列 ${item.table_column}`);
  if (item.table_unit) parts.push(`单位 ${item.table_unit}`);
  return parts.join(" · ");
}
