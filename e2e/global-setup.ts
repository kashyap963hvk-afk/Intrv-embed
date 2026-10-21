import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const STATE_FILE = join(dirname(fileURLToPath(import.meta.url)), ".state", "owner.json");

export default function globalSetup(): void {
  let owner: { email: string; password: string; org: string };
  if (process.env.E2E_COMPOSE === "0") {
    owner = {
      email: required("E2E_EMAIL"),
      password: required("E2E_PASSWORD"),
      org: required("E2E_ORG"),
    };
  } else {
    const suffix = randomBytes(4).toString("hex");
    owner = { email: `e2e-${suffix}@example.com`, password: `E2e-${randomBytes(12).toString("base64url")}`, org: `e2e-${suffix}` };
    const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
    execFileSync(
      "docker",
      ["compose", "--profile", "core", "exec", "-T", "backend", "python", "-m", "app.cli", "create-owner",
        "--org-slug", owner.org, "--org-name", "E2E", "--email", owner.email, "--name", "E2E Owner", "--password-stdin"],
      { cwd: repo, input: `${owner.password}\n`, stdio: ["pipe", "inherit", "inherit"] },
    );
  }
  mkdirSync(dirname(STATE_FILE), { recursive: true });
  writeFileSync(STATE_FILE, JSON.stringify(owner), { mode: 0o600 });
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required when E2E_COMPOSE=0`);
  return value;
}
