import { describe, expect, it } from "vitest";

import {
  buildEraseIn,
  buildSubjectIn,
  countSummary,
  describeSubject,
  EMPTY_SUBJECT_FORM,
  ERASE_REASON_MAX,
  eraseConfirmed,
  eraseReasonProblem,
  groupSubjectItems,
  itemCounts,
  MAX_IDS,
  notErased,
  parseIdList,
  retentionCutoff,
  retentionProblem,
  subjectFormProblem,
  type SubjectItem,
} from "./privacy";

const A = "0b6f2c1e-0000-4000-8000-000000000001";
const B = "0B6F2C1E-0000-4000-8000-000000000002";

function item(kind: string, id: string, label: string | null = null): SubjectItem {
  return { kind, id, owner_user_id: "u1", label };
}

describe("subject form", () => {
  it("parses id lists", () => {
    expect(parseIdList(` ${A}, ${B}\n${A};nope `)).toEqual({ ids: [A, B.toLowerCase()], invalid: ["nope"] });
    expect(parseIdList("   ")).toEqual({ ids: [], invalid: [] });
  });

  it("needs an email or ids, and valid ones", () => {
    expect(subjectFormProblem(EMPTY_SUBJECT_FORM)).toContain("email");
    expect(subjectFormProblem({ ...EMPTY_SUBJECT_FORM, email: "not-an-email" })).toContain("does not look right");
    expect(subjectFormProblem({ ...EMPTY_SUBJECT_FORM, email: "ada@example.com" })).toBeNull();
    expect(subjectFormProblem({ ...EMPTY_SUBJECT_FORM, sessionIds: A })).toBeNull();
    expect(subjectFormProblem({ ...EMPTY_SUBJECT_FORM, profileIds: "x1, x2" })).toBe(
      "Candidate profile ids: not an id: x1, x2",
    );
    const many = Array.from({ length: MAX_IDS + 1 }, (_, i) => A.slice(0, -3) + String(i).padStart(3, "0")).join(",");
    expect(subjectFormProblem({ ...EMPTY_SUBJECT_FORM, documentIds: many })).toContain("at most 200");
  });

  it("builds the request body and a description", () => {
    const body = buildSubjectIn({
      email: " ada@example.com ",
      sessionIds: `${A} ${B}`,
      profileIds: "",
      documentIds: A,
    });
    expect(body).toEqual({
      email: "ada@example.com",
      session_ids: [A, B.toLowerCase()],
      profile_ids: [],
      document_ids: [A],
    });
    expect(describeSubject(body)).toBe("ada@example.com + 2 interview ids + 1 CV document id");
    expect("email" in buildSubjectIn({ ...EMPTY_SUBJECT_FORM, sessionIds: A })).toBe(false);
    expect(buildEraseIn(body)).toEqual({ ...body, confirm: true });
    expect(buildEraseIn(body, "   ")).toEqual({ ...body, confirm: true });
    expect(buildEraseIn(body, " DSR-114 ")).toEqual({ ...body, confirm: true, reason: "DSR-114" });
  });
});

describe("what is held", () => {
  const items = [
    item("profile", "p1", "Ada"),
    item("session", "s1", "Firmware round"),
    item("cv", "c1", "ada.pdf"),
    item("session", "s2"),
    item("something_new", "x1"),
  ];

  it("groups by kind, interviews first and unknown kinds last", () => {
    const groups = groupSubjectItems(items);
    expect(groups.map((g) => g.kind)).toEqual(["session", "cv", "profile", "something_new"]);
    expect(groups[0]).toMatchObject({ title: "Interviews", items: [items[1], items[3]] });
    expect(groups[3].title).toBe("something new");
  });

  it("summarises counts", () => {
    expect(countSummary(itemCounts(items))).toBe("2 interviews, 1 CV document, 1 candidate profile, 1 something new");
    expect(countSummary({ session: 1, question_set: 0 })).toBe("1 interview");
    expect(countSummary({})).toBe("nothing");
    expect(notErased(items, { session: 2, cv: 1 })).toBe(2);
    expect(notErased(items.slice(0, 1), { profile: 3 })).toBe(0);
  });
});

describe("erase confirmation and retention", () => {
  it("limits the erase reason", () => {
    expect(eraseReasonProblem("")).toBeNull();
    expect(eraseReasonProblem(`  ${"x".repeat(ERASE_REASON_MAX)}  `)).toBeNull();
    expect(eraseReasonProblem("x".repeat(ERASE_REASON_MAX + 1))).toContain("500");
  });

  it("only accepts the exact word", () => {
    expect(eraseConfirmed("ERASE")).toBe(true);
    expect(eraseConfirmed(" ERASE ")).toBe(true);
    expect(eraseConfirmed("erase")).toBe(false);
    expect(eraseConfirmed("")).toBe(false);
  });

  it("validates the retention period", () => {
    expect(retentionProblem(180)).toBeNull();
    expect(retentionProblem(6)).toContain("7 to 3650");
    expect(retentionProblem(3651)).not.toBeNull();
    expect(retentionProblem(30.5)).not.toBeNull();
    expect(retentionCutoff(10, new Date("2026-10-21T00:00:00Z")).toISOString()).toBe("2026-10-11T00:00:00.000Z");
  });
});
