import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/problem";

import {
  addCompetency,
  addRole,
  anchorText,
  canArchive,
  canPublish,
  decodeDraft,
  describePath,
  diffIsEmpty,
  draftYaml,
  encodeDraft,
  formatWeight,
  groupPacks,
  idFromName,
  linesToList,
  listToLines,
  normaliseWeights,
  packExportFilename,
  parseSpec,
  problemsFromError,
  removeCompetency,
  setAnchor,
  setWeight,
  toYaml,
  weightChangeRows,
  weightsBalanced,
  weightTotal,
  type PackDiff,
  type PackSpec,
  type RolePack,
} from "./rolepacks";

function pack(overrides: Partial<RolePack> = {}): RolePack {
  return {
    id: "p1",
    slug: "embedded-systems",
    name: "Embedded",
    description: "",
    domain_tags: [],
    visibility: "PRIVATE",
    version: 3,
    is_mine: true,
    editable: true,
    archived: false,
    forked_from_id: null,
    variants: [],
    competency_count: 0,
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

const RAW_SPEC = {
  slug: "emb",
  version: "1.0.0",
  name: "Embedded",
  description: "Firmware roles",
  domain_tags: ["embedded"],
  seniority_levels: ["JUNIOR", "MID", "SENIOR", "STAFF"],
  competencies: [
    {
      id: "emb.c",
      name: "C",
      description: "",
      anchors: { "1": "a", "2": "b", "3": "c", "4": "d" },
      signals: ["volatile"],
      red_flags: [],
      probe_ladder: ["Why?"],
      seed_questions: [{ text: "Q?", difficulty: 2, level: ["MID"], expected_signals: [], follow_ups: [] }],
    },
    {
      id: "emb.rtos",
      name: "RTOS",
      description: "",
      anchors: { "1": "a", "2": "b", "3": "c", "4": "d" },
      signals: [],
      red_flags: [],
      probe_ladder: [],
      seed_questions: [],
    },
  ],
  roles: [{ id: "fw", name: "Firmware", weights: { "emb.c": 0.6, "emb.rtos": 0.4 }, default_structure: [] }],
  practical_tasks: [{ id: "t1", kind: "WRITE_CODE" }],
  behavioral: [],
  persona_defaults: { interviewer_name: "Alex", style: "" },
  kb_hints: [],
};

function spec(): PackSpec {
  const parsed = parseSpec(structuredClone(RAW_SPEC));
  if (!parsed) throw new Error("fixture must parse");
  return parsed;
}

describe("groupPacks and permissions", () => {
  it("groups by visibility and sorts by name", () => {
    const groups = groupPacks([
      pack({ id: "b", name: "Zeta", visibility: "SYSTEM" }),
      pack({ id: "a", name: "Alpha", visibility: "SYSTEM" }),
      pack({ id: "o", visibility: "ORG" }),
      pack({ id: "m", visibility: "PRIVATE" }),
    ]);
    expect(groups.builtIn.map((p) => p.id)).toEqual(["a", "b"]);
    expect(groups.organization.map((p) => p.id)).toEqual(["o"]);
    expect(groups.mine.map((p) => p.id)).toEqual(["m"]);
  });

  it("lets only owners publish their own private packs", () => {
    expect(canPublish(pack(), true)).toBe(true);
    expect(canPublish(pack(), false)).toBe(false);
    expect(canPublish(pack({ visibility: "ORG" }), true)).toBe(false);
    expect(canPublish(pack({ is_mine: false }), true)).toBe(false);
  });

  it("archives only editable packs", () => {
    expect(canArchive(pack())).toBe(true);
    expect(canArchive(pack({ visibility: "SYSTEM", editable: false }))).toBe(false);
    expect(canArchive(pack({ archived: true }))).toBe(false);
  });

  it("names the YAML export like the API", () => {
    expect(packExportFilename({ slug: "embedded-systems", version: 3 })).toBe("embedded-systems-v3.yaml");
  });
});

describe("spec model", () => {
  it("keeps fields the form does not edit", () => {
    const s = spec();
    expect(s.practical_tasks).toEqual([{ id: "t1", kind: "WRITE_CODE" }]);
    expect(s.competencies[0].probe_ladder).toEqual(["Why?"]);
    expect(s.roles[0].default_structure).toEqual([]);
  });

  it("returns null for non-objects and fills defaults for missing fields", () => {
    expect(parseSpec(null)).toBeNull();
    expect(parseSpec([1])).toBeNull();
    const empty = parseSpec({});
    expect(empty?.competencies).toEqual([]);
    expect(empty?.seniority_levels).toEqual(["JUNIOR", "MID", "SENIOR", "STAFF"]);
  });

  it("reads and writes anchors keyed by level", () => {
    const c = spec().competencies[0];
    expect(anchorText(c, 3)).toBe("c");
    expect(anchorText(setAnchor(c, 3, "new"), 3)).toBe("new");
    expect(c.anchors["3"]).toBe("c");
  });

  it("derives unique ids from names", () => {
    expect(idFromName("Board bring-up", [])).toBe("board_bring-up");
    expect(idFromName("Board bring-up", ["board_bring-up"])).toBe("board_bring-up_2");
    expect(idFromName("42 things", [])).toBe("things");
    expect(idFromName("!!", [])).toBe("item");
    expect(idFromName("x", [])).toBe("x_x");
  });

  it("adds a competency with empty anchors", () => {
    const next = addCompetency(spec(), "Low power");
    const added = next.competencies.at(-1);
    expect(added?.id).toBe("low_power");
    expect(added?.anchors).toEqual({ "1": "", "2": "", "3": "", "4": "" });
  });

  it("removes a competency and its weights", () => {
    const next = removeCompetency(spec(), 1);
    expect(next.competencies.map((c) => c.id)).toEqual(["emb.c"]);
    expect(next.roles[0].weights).toEqual({ "emb.c": 0.6 });
  });

  it("adds a role variant with even weights summing to 1", () => {
    const next = addRole(spec(), "Drivers");
    const role = next.roles.at(-1);
    expect(role?.id).toBe("drivers");
    expect(role && weightTotal(role.weights)).toBeCloseTo(1);
  });

  it("splits list fields by line or comma", () => {
    expect(linesToList(" a \n\n b\n")).toEqual(["a", "b"]);
    expect(linesToList("x, y,,", ",")).toEqual(["x", "y"]);
    expect(listToLines(["x", "y"], ",")).toBe("x, y");
    expect(listToLines(["x", "y"])).toBe("x\ny");
  });
});

describe("weights", () => {
  it("totals and checks the tolerance", () => {
    expect(weightTotal({ a: 0.5, b: 0.495 })).toBeCloseTo(0.995);
    expect(weightsBalanced({ a: 0.5, b: 0.495 })).toBe(true);
    expect(weightsBalanced({ a: 0.5, b: 0.4 })).toBe(false);
    expect(formatWeight(0.333)).toBe("0.33");
  });

  it("removes zero weights and rounds set values", () => {
    expect(setWeight({ a: 0.5 }, "a", 0)).toEqual({});
    expect(setWeight({ a: 0.5 }, "b", Number.NaN)).toEqual({ a: 0.5 });
    expect(setWeight({}, "a", 0.12345)).toEqual({ a: 0.123 });
  });

  it("normalises to exactly 1.00 in hundredths", () => {
    const thirds = normaliseWeights({ a: 1, b: 1, c: 1 });
    expect(Object.values(thirds).reduce((s, w) => s + w, 0)).toBeCloseTo(1, 10);
    expect(thirds).toEqual({ a: 0.34, b: 0.33, c: 0.33 });
    expect(normaliseWeights({ a: 0.3, b: 0.3 })).toEqual({ a: 0.5, b: 0.5 });
    expect(normaliseWeights({ a: 0, b: -1 })).toEqual({});
  });

  it("keeps tiny weights at the 0.01 floor", () => {
    const result = normaliseWeights({ a: 1000, b: 1 });
    expect(result.b).toBe(0.01);
    expect(Math.round((result.a + result.b) * 100)).toBe(100);
  });
});

describe("toYaml", () => {
  it("writes mappings, sequences and scalars", () => {
    expect(
      toYaml({
        slug: "emb",
        version: "1.0.0",
        tags: ["embedded", "true", "a: b"],
        anchors: { "1": "weak", "2": "ok" },
        weights: { "emb.c": 0.6 },
        empty: [],
        none: {},
        flag: false,
        missing: null,
      }),
    ).toBe(
      [
        "slug: emb",
        'version: "1.0.0"',
        "tags:",
        "  - embedded",
        '  - "true"',
        '  - "a: b"',
        "anchors:",
        "  1: weak",
        "  2: ok",
        "weights:",
        "  emb.c: 0.6",
        "empty: []",
        "none: {}",
        "flag: false",
        "missing: null",
        "",
      ].join("\n"),
    );
  });

  it("writes objects in lists with the first key on the dash line", () => {
    expect(toYaml({ roles: [{ id: "fw", weights: { a: 1 } }] })).toBe(
      ["roles:", "  - id: fw", "    weights:", "      a: 1", ""].join("\n"),
    );
  });

  it("uses block literals for multi-line text and quotes what a block cannot hold", () => {
    expect(toYaml({ code: "int x;\nreturn x;" })).toBe("code: |-\n  int x;\n  return x;\n");
    expect(toYaml({ code: "a\n" })).toBe("code: |\n  a\n");
    expect(toYaml({ code: "\tindented\nx" })).toBe('code: "\\tindented\\nx"\n');
    expect(toYaml({ code: "trailing \nx" })).toBe('code: "trailing \\nx"\n');
  });

  it("quotes strings that would read as something else", () => {
    expect(toYaml(["", "no", "12", "- x", "#c", "ok text"])).toBe(
      ['- ""', '- "no"', '- "12"', '- "- x"', '- "#c"', "- ok text", ""].join("\n"),
    );
  });
});

describe("problems", () => {
  it("reads spec problems from a rejected save", () => {
    const error = new ApiError({
      type: "invalid-role-pack",
      title: "The role pack is not valid",
      status: 422,
      problems: [{ path: "roles.0.weights", message: "must sum to 1.0" }],
    });
    expect(problemsFromError(error)).toEqual([{ path: "roles.0.weights", message: "must sum to 1.0" }]);
    expect(problemsFromError(new Error("x"))).toEqual([]);
    expect(problemsFromError(null)).toEqual([]);
  });

  it("describes paths with names and 1-based positions", () => {
    const s = spec();
    expect(describePath("competencies.1.anchors.3", s)).toBe("competency RTOS › anchors › level 3");
    expect(describePath("competencies.0.seed_questions.0.text", s)).toBe("competency C › seed questions › #1 › text");
    expect(describePath("roles.0.weights", s)).toBe("role Firmware › weights");
    expect(describePath("line 3", null)).toBe("line 3");
  });
});

describe("Role Builder drafts", () => {
  const diff: PackDiff = {
    added_competencies: ["a"],
    removed_competencies: [],
    changed_competencies: [],
    added_roles: [],
    removed_roles: [],
    weight_changes: { fw: { a: [null, 0.2], b: [0.5, 0.3] } },
  };

  it("flattens weight changes", () => {
    expect(weightChangeRows(diff)).toEqual([
      { role: "fw", competency: "a", before: null, after: 0.2 },
      { role: "fw", competency: "b", before: 0.5, after: 0.3 },
    ]);
    expect(diffIsEmpty(diff)).toBe(false);
    expect(diffIsEmpty({ ...diff, added_competencies: [], weight_changes: { fw: {} } })).toBe(true);
  });

  it("opens invalid drafts as JSON, which YAML accepts", () => {
    expect(draftYaml({ yaml: "slug: x\n", spec: {} })).toBe("slug: x\n");
    expect(draftYaml({ yaml: null, spec: { slug: "x" } })).toBe('{\n  "slug": "x"\n}\n');
  });

  it("round-trips the stored draft and rejects junk", () => {
    expect(decodeDraft(encodeDraft("slug: x\n"))).toBe("slug: x\n");
    expect(decodeDraft(null)).toBeNull();
    expect(decodeDraft("{not json")).toBeNull();
    expect(decodeDraft(JSON.stringify({ yaml: "" }))).toBeNull();
  });
});
