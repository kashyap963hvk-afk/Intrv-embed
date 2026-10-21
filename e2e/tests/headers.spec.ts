import { expect, test } from "@playwright/test";
import { API_URL } from "./support";

test("pages send the security headers", async ({ request }) => {
  const page = await request.get("/login");
  expect(page.headers()["x-frame-options"]).toBe("DENY");
  expect(page.headers()["x-content-type-options"]).toBe("nosniff");
  expect(page.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  const join = await request.get("/join/not-a-real-token");
  expect(join.headers()["referrer-policy"]).toBe("no-referrer"); // invite tokens never leak as a referrer
});

test("the API sends the security headers", async ({ request }) => {
  const health = await request.get(`${API_URL}/healthz`);
  expect(health.ok()).toBe(true);
  expect(health.headers()["content-security-policy"]).toContain("default-src 'none'");
  expect(health.headers()["x-frame-options"]).toBe("DENY");
});
