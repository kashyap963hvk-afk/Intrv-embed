import { describe, expect, it } from "vitest";

import { filenameFromDisposition, safeFileStem } from "./download";

describe("filenameFromDisposition", () => {
  it("reads quoted, bare and RFC 5987 file names", () => {
    expect(filenameFromDisposition('attachment; filename="set.pdf"', "x")).toBe("set.pdf");
    expect(filenameFromDisposition("attachment; filename=set.md", "x")).toBe("set.md");
    expect(filenameFromDisposition("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.docx", "x")).toBe("résumé.docx");
  });

  it("falls back when the header is missing or empty", () => {
    expect(filenameFromDisposition(null, "fallback.md")).toBe("fallback.md");
    expect(filenameFromDisposition("attachment", "fallback.md")).toBe("fallback.md");
    expect(filenameFromDisposition('attachment; filename=""', "fallback.md")).toBe("fallback.md");
  });
});

describe("safeFileStem", () => {
  it("keeps letters, digits, dots, dashes and underscores", () => {
    expect(safeFileStem("  Q&A: firmware v2.1 ", "f")).toBe("Q-A-firmware-v2.1");
    expect(safeFileStem("", "f")).toBe("f");
    expect(safeFileStem("a".repeat(100), "f")).toHaveLength(80);
  });
});
