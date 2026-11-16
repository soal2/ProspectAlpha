import { describe, expect, test } from "bun:test";

import { evidenceLocationLabel, pdfPageNumber } from "./evidence-location";

describe("evidence location", () => {
  test("plain text shows offsets and no PDF page", () => {
    expect(pdfPageNumber(4, "text_offset")).toBeNull();
    expect(
      evidenceLocationLabel({
        locator_kind: "text_offset",
        page_number: null,
        char_start: 12,
        char_end: 40,
      }),
    ).toBe("无 PDF 页 · 文本偏移 12–40");
  });

  test("cross-page table keeps pages, header, row and column, and omits a missing unit", () => {
    const label = evidenceLocationLabel({
      locator_kind: "pdf_page",
      page_number: 12,
      pdf_regions: [
        { page_number: 12, bbox: [1, 2, 3, 4] },
        { page_number: 13, bbox: [5, 6, 7, 8] },
      ],
      table_header: "年份 收入",
      table_row: "营业收入",
      table_column: "2",
      table_unit: null,
    });
    expect(label).toBe("P.12、P.13 · 表头 年份 收入 · 行 营业收入 · 列 2");
    expect(label).not.toContain("单位");
  });

  test("a real PDF page is not rewritten", () => {
    expect(pdfPageNumber(7, "pdf_page")).toBe(7);
    expect(evidenceLocationLabel({ locator_kind: "pdf_page", page_number: 7 })).toBe("P.7");
  });
});
