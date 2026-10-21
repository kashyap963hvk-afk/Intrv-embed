import { describe, expect, it } from "vitest";

import {
  buildBridgeProfileIn,
  buildGgufProfileIn,
  checkLabel,
  deviceVersion,
  EMPTY_GGUF_FORM,
  formatBytes,
  formatCountdown,
  formatMs,
  ggufFormProblem,
  ggufModelName,
  hardwareNotes,
  newDevices,
  pairCommand,
  pairingDeadline,
  parameterBillions,
  parseBridgeModels,
  parseProbe,
  profileForModel,
  profilesForDevice,
  safeDeviceName,
  secondsLeft,
  tierCeiling,
  weakJson,
  type BridgeDevice,
  type LlmProfile,
} from "./bridge";

function device(overrides: Partial<BridgeDevice> = {}): BridgeDevice {
  return {
    id: "d1",
    name: "Laptop",
    platform: "Linux",
    online: true,
    models: [],
    capabilities: {},
    last_seen_at: null,
    created_at: "2026-10-20T10:00:00Z",
    ...overrides,
  };
}

function profile(overrides: Partial<LlmProfile> = {}): LlmProfile {
  return {
    id: "p1",
    name: "Qwen",
    scope: "personal",
    owned_by_me: true,
    kind: "BRIDGE",
    preset_key: null,
    base_url: null,
    has_api_key: false,
    api_key_last4: null,
    model: "qwen2.5-7b-instruct",
    params: { device_id: "d1" },
    capabilities: {},
    quality_tier: null,
    trains_on_data: false,
    rpm_limit: null,
    rpd_limit: null,
    status: "UNTESTED",
    last_probe_at: null,
    last_error: null,
    created_at: "2026-10-20T10:00:00Z",
    ...overrides,
  };
}

describe("device models", () => {
  it("keeps valid entries and tolerates missing or wrong fields", () => {
    const models = parseBridgeModels([
      {
        id: "qwen",
        name: "Qwen 7B",
        source: "gguf",
        size_bytes: 4_700_000_000,
        quant: "Q4_K_M",
        context_length: 32768,
      },
      { id: "llama3.1:8b", source: "ollama" },
      { name: "no id" },
      "garbage",
      { id: "x", name: 7, size_bytes: "big", arch: "" },
    ]);
    expect(models.map((m) => m.id)).toEqual(["qwen", "llama3.1:8b", "x"]);
    expect(models[0]).toMatchObject({ name: "Qwen 7B", size_bytes: 4_700_000_000, quant: "Q4_K_M", arch: null });
    expect(models[1].name).toBe("llama3.1:8b");
    expect(models[2]).toMatchObject({ name: "x", size_bytes: null, arch: null });
  });

  it("reads the bridge version", () => {
    expect(deviceVersion(device({ capabilities: { version: "0.1.0" } }))).toBe("0.1.0");
    expect(deviceVersion(device({ capabilities: { version: 3 } }))).toBeNull();
  });

  it("formats sizes", () => {
    expect(formatBytes(4_700_000_000)).toBe("4.7 GB");
    expect(formatBytes(14_000_000_000)).toBe("14 GB");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(null)).toBeNull();
    expect(formatBytes(0)).toBeNull();
  });
});

describe("pairing", () => {
  it("counts down to the expiry", () => {
    const deadline = pairingDeadline(1_000, 600);
    expect(deadline).toBe(601_000);
    expect(secondsLeft(deadline, 1_000)).toBe(600);
    expect(secondsLeft(deadline, 600_500)).toBe(1);
    expect(secondsLeft(deadline, 700_000)).toBe(0);
    expect(secondsLeft(deadline, 0, 600)).toBe(600);
    expect(formatCountdown(600)).toBe("10:00");
    expect(formatCountdown(65)).toBe("1:05");
    expect(formatCountdown(-3)).toBe("0:00");
  });

  it("builds a shell-safe pair command", () => {
    expect(pairCommand("http://localhost:8000/", "012345", "Ada's laptop")).toBe(
      'intrv-bridge pair http://localhost:8000 012345 --name "Ada s laptop"',
    );
    expect(safeDeviceName('$(rm -rf ~) "`x`"')).toBe("rm -rf x");
    expect(safeDeviceName("   ")).toBe("My PC");
    expect(safeDeviceName("a".repeat(100))).toHaveLength(60);
  });

  it("finds devices paired after the code was created", () => {
    const before = new Set(["d1"]);
    const added = newDevices(before, [device(), device({ id: "d2", name: "Desktop" })]);
    expect(added.map((d) => d.id)).toEqual(["d2"]);
  });
});

describe("hardware notes", () => {
  it("reads the parameter count like the server's probe", () => {
    expect(parameterBillions("qwen2.5-7b-instruct-q4_k_m")).toBe(7);
    expect(parameterBillions("Qwen2.5-14B-Instruct")).toBe(14);
    expect(parameterBillions("llama3.1:70b")).toBe(70);
    expect(parameterBillions("qwen2.5-1.5b")).toBe(1.5);
    expect(parameterBillions("phi-3-mini-4k-instruct")).toBeNull();
    expect(parameterBillions("bge-m3")).toBeNull();
  });

  it("maps sizes to the tier ceiling", () => {
    expect(tierCeiling(7)).toBe("BASIC");
    expect(tierCeiling(14)).toBe("USABLE");
    expect(tierCeiling(32)).toBe("GOOD");
    expect(tierCeiling(70)).toBe("EXCELLENT");
    expect(tierCeiling(null)).toBeNull();
  });

  it("explains size, USB load time and CPU speed", () => {
    const notes = hardwareNotes({ id: "m", name: "mistral-7b", source: "gguf", size_bytes: 4_700_000_000 });
    expect(notes[0]).toContain("at most Basic");
    expect(notes[1]).toContain("2.6 min over USB 2.0");
    expect(notes[1]).toContain("20 s–39 s over USB 3");
    expect(notes[2]).toContain("5–15 tokens per second");
    const ollama = hardwareNotes({ id: "x", name: "x", source: "ollama", size_bytes: 4_700_000_000 });
    expect(ollama[0]).toContain("does not say");
    expect(ollama.some((n) => n.includes("USB"))).toBe(false);
  });
});

describe("probe results", () => {
  it("parses stored capabilities", () => {
    const probe = parseProbe({
      stream: true,
      json_schema: false,
      json_object: false,
      ttft_ms: 2400,
      tokens_per_s: 9.5,
      checks: { json_fidelity: false, asks_one_question: true },
      quality_score: 2,
      warnings: ["slow"],
    });
    expect(probe).toMatchObject({ stream: true, tools: false, ttft_ms: 2400, quality_score: 2, warnings: ["slow"] });
    expect(weakJson(probe)).toBe(true);
    expect(parseProbe({ checks: "bad", warnings: [1] })).toMatchObject({ checks: {}, warnings: [], ttft_ms: null });
    expect(checkLabel("json_fidelity")).toBe("Returns valid JSON");
    expect(checkLabel("new_check")).toBe("new check");
    expect(formatMs(3)).toBe("3 ms");
    expect(formatMs(2400)).toBe("2.4 s");
  });
});

describe("profiles", () => {
  it("builds a personal BRIDGE profile", () => {
    expect(buildBridgeProfileIn(device(), { id: "qwen2.5-7b", name: "Qwen 7B" })).toEqual({
      name: "Qwen 7B on Laptop",
      scope: "personal",
      kind: "BRIDGE",
      model: "qwen2.5-7b",
      device_id: "d1",
    });
    expect(buildBridgeProfileIn(device({ name: "x".repeat(200) }), { id: "m", name: "m" }).name).toHaveLength(120);
  });

  it("finds profiles for a device and model", () => {
    const profiles = [
      profile(),
      profile({ id: "p2", model: "other" }),
      profile({ id: "p3", kind: "OPENAI_COMPATIBLE", params: {} }),
    ];
    expect(profileForModel(profiles, "d1", "qwen2.5-7b-instruct")?.id).toBe("p1");
    expect(profileForModel(profiles, "d2", "qwen2.5-7b-instruct")).toBeUndefined();
    expect(profilesForDevice(profiles, "d1").map((p) => p.id)).toEqual(["p1", "p2"]);
  });

  it("validates and builds the server-side GGUF profile", () => {
    expect(ggufFormProblem(EMPTY_GGUF_FORM)).toContain("full path");
    expect(ggufFormProblem({ ...EMPTY_GGUF_FORM, path: "models/x.gguf" })).toContain("absolute");
    expect(ggufFormProblem({ ...EMPTY_GGUF_FORM, path: "/models/x.bin" })).toContain(".gguf");
    expect(ggufFormProblem({ ...EMPTY_GGUF_FORM, path: "/models/x.gguf" })).toBeNull();
    expect(ggufFormProblem({ ...EMPTY_GGUF_FORM, path: "D:\\models\\x.GGUF" })).toBeNull();
    expect(ggufModelName("/srv/models/Qwen2.5-14B.Q4_K_M.gguf")).toBe("Qwen2.5-14B.Q4_K_M");
    expect(buildGgufProfileIn({ name: "", path: " /srv/models/q.gguf ", shareWithOrg: true })).toEqual({
      name: "q",
      scope: "org",
      kind: "GGUF_MANAGED",
      model: "q",
      model_path: "/srv/models/q.gguf",
    });
    expect(buildGgufProfileIn({ name: "Mine", path: "/m/a.gguf", shareWithOrg: false }).scope).toBe("personal");
  });
});
