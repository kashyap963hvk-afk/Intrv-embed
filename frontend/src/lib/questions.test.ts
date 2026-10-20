import { describe, expect, it } from "vitest";

import type { KnowledgeDocument, Space } from "@/lib/knowledge";

import {
  applyOrder,
  applySetEventToDetail,
  applySetEventToList,
  buildCreateSetIn,
  categoryLabel,
  documentOptions,
  EMPTY_SET_FORM,
  exportFilename,
  mixPercentages,
  moveItem,
  parseAnalysis,
  parseItemData,
  parseSetStatusEvent,
  planByCompetency,
  removeItem,
  replaceItem,
  resolveEvidence,
  setEventNeedsRefetch,
  setFormReady,
  toggleId,
  type QuestionItem,
  type QuestionSet,
  type QuestionSetDetail,
  type SetStatusEvent,
} from "./questions";

function set(overrides: Partial<QuestionSet> = {}): QuestionSet {
  return {
    id: "s1",
    title: "Set",
    status: "GENERATING",
    progress: "Queued",
    error: null,
    cv_document_id: "cv",
    jd_document_id: null,
    role_pack_id: "p",
    role_pack_version: 1,
    role_variant: "fw",
    params: {},
    item_count: 0,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

function item(id: string, ord: number): QuestionItem {
  return { id, ord, pinned: false, data: { text: `Question ${id}` } };
}

function detail(items: QuestionItem[]): QuestionSetDetail {
  return {
    ...set({ status: "READY", item_count: items.length }),
    items,
    analysis: {},
    competency_names: {},
    citation_titles: {},
  };
}

function doc(overrides: Partial<KnowledgeDocument>): KnowledgeDocument {
  return {
    id: "d",
    space_id: "sp",
    title: "Doc",
    doc_type: "CV",
    status: "READY",
    chunk_count: 1,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    error: null,
    filename: null,
    mime: null,
    page_count: null,
    risk_flags: {},
    size_bytes: 1,
    source_url: null,
    uploaded_by_me: true,
    ...overrides,
  };
}

function space(id: string, name: string): Space {
  return { id, name, description: "", document_count: 0, is_mine: true, kind: "PERSONAL", permission: "CONTRIBUTE" };
}

describe("labels", () => {
  it("labels known and unknown categories", () => {
    expect(categoryLabel("cv_deep_dive")).toBe("CV deep dive");
    expect(categoryLabel("pair_programming")).toBe("pair programming");
  });
});

describe("item and analysis models", () => {
  it("parses item data with defaults", () => {
    const data = parseItemData({ text: "Explain volatile", difficulty: 2, citations: ["doc"], source: "role_pack" });
    expect(data.text).toBe("Explain volatile");
    expect(data.citations).toEqual(["doc"]);
    expect(data.source).toBe("role_pack");
    expect(data.follow_ups).toEqual([]);
    expect(parseItemData({ difficulty: "hard", source: "x" })).toMatchObject({ difficulty: 3, source: "generated" });
  });

  it("parses the analysis with safe defaults", () => {
    const empty = parseAnalysis({});
    expect(empty.gap_matrix).toBeNull();
    expect(empty.critic).toEqual({ kept: 0, rewritten: 0, dropped: 0, duplicates: 0, topped_up: 0 });
    const full = parseAnalysis({
      gap_matrix: { requirements: [{ text: "RTOS", status: "missing", evidence_refs: ["skills[0]"] }] },
      plan: [
        { competency: "a", category: "technical", difficulty: 2 },
        { competency: "b", category: "technical", difficulty: 2 },
        { competency: "b", category: "debugging", difficulty: 3 },
      ],
      critic: { kept: 5, rewritten: 1, dropped: 2, duplicates: 1, topped_up: 0 },
      notes: ["critic skipped"],
    });
    expect(full.gap_matrix?.requirements[0]).toMatchObject({ status: "missing", competency_id: null, note: "" });
    expect(planByCompetency(full.plan)).toEqual([
      { competency: "b", count: 2 },
      { competency: "a", count: 1 },
    ]);
    expect(full.notes).toEqual(["critic skipped"]);
  });

  it("resolves evidence refs against the CV index", () => {
    expect(resolveEvidence(["roles[0]", "x"], { "roles[0]": "Engineer at Acme" })).toEqual([
      { ref: "roles[0]", text: "Engineer at Acme" },
      { ref: "x", text: null },
    ]);
    expect(resolveEvidence(["roles[0]"], null)).toEqual([{ ref: "roles[0]", text: null }]);
  });
});

describe("reordering and cache updates", () => {
  it("moves an item up or down within bounds", () => {
    expect(moveItem(["a", "b", "c"], 1, -1)).toEqual(["b", "a", "c"]);
    expect(moveItem(["a", "b", "c"], 1, 1)).toEqual(["a", "c", "b"]);
    expect(moveItem(["a", "b"], 0, -1)).toBeNull();
    expect(moveItem(["a", "b"], 1, 1)).toBeNull();
  });

  it("applies an order and renumbers", () => {
    const next = applyOrder(detail([item("a", 0), item("b", 1), item("c", 2)]), ["c", "a", "b"]);
    expect(next.items.map((i) => [i.id, i.ord])).toEqual([
      ["c", 0],
      ["a", 1],
      ["b", 2],
    ]);
  });

  it("replaces and removes items", () => {
    const d = detail([item("a", 0), item("b", 1)]);
    const pinned = { ...item("b", 1), pinned: true };
    expect(replaceItem(d, pinned).items[1].pinned).toBe(true);
    const removed = removeItem(d, "a");
    expect(removed.items.map((i) => i.id)).toEqual(["b"]);
    expect(removed.item_count).toBe(1);
  });
});

describe("generation form", () => {
  it("lists READY documents of a type across spaces", () => {
    const options = documentOptions(
      [space("s1", "Mine"), space("s2", "Shared")],
      [
        [doc({ id: "1", title: "Zoe CV" }), doc({ id: "2", doc_type: "JD" }), doc({ id: "3", status: "PARSING" })],
        [doc({ id: "4", title: "Anna CV" })],
      ],
      "CV",
    );
    expect(options).toEqual([
      { id: "4", title: "Anna CV", spaceName: "Shared" },
      { id: "1", title: "Zoe CV", spaceName: "Mine" },
    ]);
    expect(documentOptions([space("s1", "Mine")], [undefined], "CV")).toEqual([]);
  });

  it("shows mix shares as whole percentages adding up to 100", () => {
    const p = mixPercentages({ technical: 1, cv_deep_dive: 1, debugging: 1, design: 0, behavioral: 0 });
    expect(p.technical + p.cv_deep_dive + p.debugging).toBe(100);
    expect(p.design).toBe(0);
    expect(mixPercentages({ technical: 0, cv_deep_dive: 0, debugging: 0, design: 0, behavioral: 0 }).technical).toBe(0);
  });

  it("needs a CV, a pack and a count in range", () => {
    expect(setFormReady(EMPTY_SET_FORM)).toBe(false);
    const ready = { ...EMPTY_SET_FORM, cvDocumentId: "cv", rolePackId: "p" };
    expect(setFormReady(ready)).toBe(true);
    expect(setFormReady({ ...ready, count: 2 })).toBe(false);
    expect(setFormReady({ ...ready, count: Number.NaN })).toBe(false);
    expect(
      setFormReady({
        ...ready,
        customMix: true,
        mix: { technical: 0, cv_deep_dive: 0, debugging: 0, design: 0, behavioral: 0 },
      }),
    ).toBe(false);
  });

  it("leaves unset options out of the body", () => {
    expect(buildCreateSetIn({ ...EMPTY_SET_FORM, cvDocumentId: "cv", rolePackId: "p" })).toEqual({
      cv_document_id: "cv",
      role_pack_id: "p",
      count: 20,
    });
  });

  it("sends every chosen option", () => {
    expect(
      buildCreateSetIn({
        ...EMPTY_SET_FORM,
        title: "  Senior FW  ",
        cvDocumentId: "cv",
        jdDocumentId: "jd",
        rolePackId: "p",
        roleVariant: "fw",
        count: 12,
        seniority: "SENIOR",
        difficulty: 4,
        customMix: true,
        mix: { technical: 0.5, cv_deep_dive: 0.5, debugging: 0, design: 0, behavioral: 0 },
        spaceIds: ["s1"],
      }),
    ).toEqual({
      title: "Senior FW",
      cv_document_id: "cv",
      jd_document_id: "jd",
      role_pack_id: "p",
      role_variant: "fw",
      count: 12,
      seniority: "SENIOR",
      difficulty: 4,
      mix: { technical: 0.5, cv_deep_dive: 0.5 },
      space_ids: ["s1"],
    });
  });

  it("toggles ids", () => {
    expect(toggleId(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleId(["a", "b"], "a")).toEqual(["b"]);
  });
});

describe("live status events", () => {
  const event: SetStatusEvent = {
    type: "question_set.status",
    set_id: "s1",
    status: "GENERATING",
    progress: "Reading",
  };

  it("parses valid events and ignores junk", () => {
    expect(
      parseSetStatusEvent(
        JSON.stringify({
          type: "question_set.status",
          user_id: "u",
          set_id: "s1",
          status: "READY",
          progress: "3 ready",
        }),
      ),
    ).toEqual({ type: "question_set.status", set_id: "s1", status: "READY", progress: "3 ready" });
    expect(parseSetStatusEvent("nope")).toBeNull();
    expect(parseSetStatusEvent(JSON.stringify({ type: "document.status" }))).toBeNull();
  });

  it("patches the list and keeps the array when nothing changes", () => {
    const list = [set(), set({ id: "s2" })];
    const next = applySetEventToList(list, event);
    expect(next?.[0].progress).toBe("Reading");
    expect(next?.[1]).toBe(list[1]);
    expect(applySetEventToList(next, event)).toBe(next);
    expect(applySetEventToList(list, { ...event, set_id: "other" })).toBe(list);
    expect(applySetEventToList(undefined, event)).toBeUndefined();
  });

  it("patches the matching detail only", () => {
    const d = { ...detail([]), status: "GENERATING" as const };
    expect(applySetEventToDetail(d, event)?.progress).toBe("Reading");
    expect(applySetEventToDetail(d, { ...event, set_id: "other" })).toBe(d);
  });

  it("refetches when a set finishes or is unknown", () => {
    expect(setEventNeedsRefetch([set()], event)).toBe(false);
    expect(setEventNeedsRefetch([set()], { ...event, status: "READY" })).toBe(true);
    expect(setEventNeedsRefetch([], event)).toBe(true);
  });
});

describe("export", () => {
  it("builds the API's file name", () => {
    expect(exportFilename("Jane · Firmware (Senior)", "pdf")).toBe("Jane-Firmware-Senior.pdf");
    expect(exportFilename("···", "md")).toBe("questions.md");
  });
});
