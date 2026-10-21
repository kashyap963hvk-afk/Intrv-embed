import { expect, test } from "@playwright/test";
import { Api, owner } from "./support";

const ANSWER_1 = "I owned the CAN bootloader: A/B images with a boot counter and a CRC check before every jump.";
const ANSWER_2 = "I moved the ADC to DMA in circular mode and processed each half buffer in the transfer callback.";
const PLAN = {
  topics: [
    {
      title: "CAN bootloader ownership",
      section: "CV_WALKTHROUGH",
      competencies: ["emb.debugging_tools"],
      seed_question: "Could you walk me through the CAN bootloader and what you personally owned in it?",
      probe_angles: ["What happens on power loss during an update?"],
      expected_signals: ["A/B images"],
    },
    {
      title: "ADC sampling with DMA",
      section: "TECHNICAL",
      competencies: ["emb.debugging_tools"],
      seed_question: "How would you sample an ADC at 10 kHz without loading the CPU?",
      probe_angles: ["How do you avoid tearing?"],
      expected_signals: ["DMA"],
    },
  ],
};
const COMPETENCIES = ["emb.debugging_tools", "emb.c_language", "emb.interrupts_concurrency", "emb.mcu_architecture"];
const SCRIPT = [
  { on: "system", match: "minute interview for the role", reply: JSON.stringify(PLAN) },
  {
    on: "system",
    match: "You are the turn controller",
    reply: JSON.stringify({ answer_quality: "adequate", next_action: "next_topic", acknowledgement: "none" }),
  },
  {
    on: "system",
    match: "You grade one interview topic",
    reply: JSON.stringify({
      grades: COMPETENCIES.map((competency) => ({
        competency,
        anchor_level: 3,
        score: 7,
        evidence: [{ turn_seq: 2, quote: "A/B images with a boot counter" }],
        confidence: "high",
      })),
    }),
  },
  {
    on: "system",
    match: "narrative of an interview scorecard",
    reply: JSON.stringify({
      summary: "Owned a fail-safe bootloader and knows DMA-based sampling.",
      claims_verified: [],
      claims_contradicted: [],
      claims_untested: [],
      strengths: ["Fail-safe update design"],
      concerns: [],
      knowledge_gaps: [],
      next_round_focus: ["RTOS scheduling"],
      recommendation: "lean_yes",
      recommendation_reason: "Clear ownership and sound DMA design.",
    }),
  },
];

test("owner plans an interview, the candidate takes it, the owner reviews and decides", async ({ page, browser }) => {
  const who = owner();
  const api = await Api.signIn(who);
  const profile = await api.post("/api/llm/profiles", { name: "E2E scripted", kind: "FAKE", model: "fake", script: SCRIPT });
  expect(profile.status(), await profile.text()).toBe(201);
  const profileId = ((await profile.json()) as { id: string }).id;

  const hydrationErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /hydrat/i.test(message.text())) hydrationErrors.push(message.text());
  });

  try {
    // Owner signs in through the UI.
    await page.goto("/login");
    await page.getByLabel("Email").fill(who.email);
    await page.getByLabel("Password").fill(who.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).not.toHaveURL(/\/login/);

    // Plan an interview (API) and see it in the UI.
    const packs = await api.json<{ id: string; slug: string }[]>("/api/role-packs");
    const pack = packs.find((p) => p.slug === "embedded-systems");
    expect(pack).toBeDefined();
    const created = await api.post("/api/interviews", {
      role_pack_id: pack?.id,
      settings: { turn_mode: "two_call", follow_up_depth: 0, grading_runs: 1 },
    });
    expect(created.status(), await created.text()).toBe(202);
    const sessionId = ((await created.json()) as { id: string }).id;
    await api.until<{ status: string }>(`/api/interviews/${sessionId}`, (s) => s.status === "PLANNED");
    await page.goto(`/app/interviews/${sessionId}`);
    await expect(page.getByText("CAN bootloader ownership")).toBeVisible();
    const invite = await api.post(`/api/interviews/${sessionId}/invite`, {});
    const token = ((await invite.json()) as { url: string }).url.split("/").pop() ?? "";

    // The candidate: disclosure, consent, a typed interview, then ending it.
    const candidate = await (await browser.newContext()).newPage();
    await candidate.goto(`/join/${token}`);
    await expect(candidate.getByRole("heading", { name: "What is recorded" })).toBeVisible();
    await candidate.getByText("I have read the information above").click();
    await candidate.getByRole("button", { name: "I agree and continue" }).click();
    const type = candidate.getByRole("radio", { name: "Type" });
    if (await type.isVisible().catch(() => false)) await type.click();
    await candidate.getByRole("button", { name: "Start the interview" }).click();
    await expect(candidate.getByText(PLAN.topics[0].seed_question)).toBeVisible();

    const box = candidate.getByPlaceholder("Type your answer…");
    await box.fill(ANSWER_1);
    await candidate.getByRole("button", { name: "Send" }).click();
    await expect(candidate.getByText(PLAN.topics[1].seed_question)).toBeVisible();
    await box.fill(ANSWER_2);
    await box.press("Control+Enter");
    await expect(candidate.getByText(ANSWER_2)).toBeVisible();

    await candidate.getByRole("button", { name: "End interview" }).click();
    await candidate.getByRole("dialog").getByRole("button", { name: "End interview" }).click();
    await expect(candidate.getByRole("heading", { name: "Thank you for your time" })).toBeVisible();

    // The owner's report: graded with verified quotes, advisory, and a human decision.
    await api.until<{ scorecard: { status: string } | null }>(
      `/api/interviews/${sessionId}/report`,
      (r) => r.scorecard?.status === "READY",
      90,
    );
    await page.goto(`/app/interviews/${sessionId}/report`);
    await expect(page.getByRole("heading", { name: "Human decision" })).toBeVisible();
    await expect(page.getByText("A/B images with a boot counter").first()).toBeVisible();
    expect(hydrationErrors).toEqual([]);
  } finally {
    await api.delete(`/api/llm/profiles/${profileId}`);
    await api.http.dispose();
  }
});
