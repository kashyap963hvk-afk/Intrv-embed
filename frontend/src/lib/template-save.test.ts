import { describe, expect, it } from "vitest";

import { templateForm, type Template } from "./interviews";
import { buildTemplateSave, roleChanged, shareTemplatePatch } from "./template-save";

const template: Template = {
  id: "t1",
  name: "Friendly",
  shared: false,
  mine: true,
  role_pack_id: "p1",
  role_variant: "fw",
  persona_prompt: "Warm.",
  settings: { duration_minutes: 30, interviewer_name: "Kai" },
  warnings: [],
  updated_at: "2026-10-20T10:00:00Z",
};

describe("buildTemplateSave", () => {
  const saved = templateForm(template);

  it("leaves the role pack alone when it did not change", () => {
    const patch = buildTemplateSave({ ...saved, name: " Renamed ", interviewerName: " Ada " }, saved);
    expect(patch.name).toBe("Renamed");
    expect(patch.interviewer_name).toBe("Ada");
    expect(patch.settings?.interviewer_name).toBe("Ada");
    expect("role_pack_id" in patch).toBe(false);
    expect("role_variant" in patch).toBe(false);
    expect(roleChanged(saved, saved)).toBe(false);
  });

  it("resets the variant to the pack default with an explicit null", () => {
    const patch = buildTemplateSave({ ...saved, roleVariant: "" }, saved);
    expect(patch.role_pack_id).toBe("p1");
    expect(patch.role_variant).toBeNull();
  });

  it("clears the role pack with explicit nulls", () => {
    const patch = buildTemplateSave({ ...saved, rolePackId: "", roleVariant: "" }, saved);
    expect(patch.role_pack_id).toBeNull();
    expect(patch.role_variant).toBeNull();
  });

  it("sends a newly chosen pack and variant", () => {
    const empty = { ...saved, rolePackId: "", roleVariant: "" };
    expect(buildTemplateSave({ ...empty, rolePackId: "p2" }, empty)).toMatchObject({
      role_pack_id: "p2",
      role_variant: null,
    });
    expect(buildTemplateSave({ ...empty, rolePackId: "p2", roleVariant: "bsp" }, empty)).toMatchObject({
      role_pack_id: "p2",
      role_variant: "bsp",
    });
  });

  it("builds the share patch", () => {
    expect(shareTemplatePatch(true)).toEqual({ shared: true });
    expect(shareTemplatePatch(false)).toEqual({ shared: false });
  });
});
