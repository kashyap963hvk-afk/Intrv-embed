/**
 * The template editor's PATCH body. The API treats an explicit null role pack as "no role pack" (which also resets
 * the variant) and an explicit null variant as "the pack's default variant"; other null or missing fields are left
 * unchanged. Role fields are only sent when they changed, so saving other fields never re-validates the pack.
 */

import { settingsToApi, type TemplateForm, type TemplatePatch } from "@/lib/interviews";

export function roleChanged(form: TemplateForm, saved: TemplateForm): boolean {
  return form.rolePackId !== saved.rolePackId || form.roleVariant !== saved.roleVariant;
}

export function buildTemplateSave(form: TemplateForm, saved: TemplateForm): TemplatePatch {
  const interviewer = form.interviewerName.trim();
  const patch: TemplatePatch = {
    name: form.name.trim(),
    persona_prompt: form.persona,
    settings: settingsToApi({ ...form.settings, interviewer_name: interviewer || null }),
    interviewer_name: interviewer,
  };
  if (roleChanged(form, saved)) {
    patch.role_pack_id = form.rolePackId || null;
    patch.role_variant = form.rolePackId && form.roleVariant ? form.roleVariant : null;
  }
  return patch;
}

/** Owners only: move a template between "only me" and "the whole organization". */
export function shareTemplatePatch(shared: boolean): TemplatePatch {
  return { shared };
}
