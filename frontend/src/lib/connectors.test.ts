import { describe, expect, it } from "vitest";

import {
  accountDetail,
  buildAzureAccountIn,
  buildS3AccountIn,
  buildSourceIn,
  buildUrlSourceIn,
  canManageSource,
  clampInterval,
  connectorTabs,
  EMPTY_AZURE_FORM,
  EMPTY_S3_FORM,
  enterFolder,
  folderPrefix,
  formatRelative,
  intervalLabel,
  intervalOptions,
  mergeItems,
  oauthMessage,
  oauthProvider,
  oauthResultFromParams,
  parseFolderIds,
  parseOAuthMessage,
  parseSyncResult,
  pickedToItems,
  ROOT_CRUMB,
  s3FormReady,
  azureFormReady,
  suggestSourceName,
  syncResultSummary,
  toggleItem,
  toInterval,
  trailTo,
  urlSourceName,
  type ConnectorAccount,
  type ConnectorKindInfo,
  type RemoteItem,
  type Source,
} from "./connectors";

function account(overrides: Partial<ConnectorAccount> = {}): ConnectorAccount {
  return {
    id: "acc-1",
    kind: "S3_COMPATIBLE",
    name: "Hiring bucket",
    config: {},
    status: "OK",
    last_error: null,
    org_level: false,
    last_validated_at: null,
    created_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

function kind(k: ConnectorKindInfo["kind"], overrides: Partial<ConnectorKindInfo> = {}): ConnectorKindInfo {
  return { kind: k, label: k, auth: "oauth", available: true, reason: null, owners_only: false, ...overrides };
}

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: "src-1",
    space_id: "space-1",
    account_id: "acc-1",
    kind: "S3_COMPATIBLE",
    name: "Reports",
    selection: {},
    doc_type: null,
    sync_enabled: true,
    interval_minutes: 360,
    status: "IDLE",
    last_sync_at: null,
    next_sync_at: null,
    last_error: null,
    failure_count: 0,
    last_result: {},
    document_count: 0,
    created_by_me: false,
    created_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

const folder: RemoteItem = {
  id: "docs/specs/",
  name: "specs",
  path: "docs/specs/",
  is_folder: true,
  size: null,
  modified: null,
};
const file: RemoteItem = {
  id: "docs/a.pdf",
  name: "a.pdf",
  path: "docs/a.pdf",
  is_folder: false,
  size: 10,
  modified: null,
};

describe("connectorTabs", () => {
  const kinds = [
    kind("URL", { auth: "none" }),
    kind("DROPBOX"),
    kind("GOOGLE_DRIVE_ORG", { auth: "service_account", owners_only: true }),
    kind("S3_COMPATIBLE", { auth: "credentials" }),
    kind("GOOGLE_DRIVE"),
  ];

  it("orders tabs, leaves URL to the Link tab and hides owners-only kinds from members", () => {
    expect(connectorTabs(kinds, false).map((t) => t.info.kind)).toEqual(["GOOGLE_DRIVE", "S3_COMPATIBLE", "DROPBOX"]);
    expect(connectorTabs(kinds, true).map((t) => t.tab)).toEqual([
      "Google Drive",
      "Org Drive",
      "S3 / Cloud bucket",
      "Dropbox",
    ]);
  });

  it("keeps unavailable kinds so their tab can explain why", () => {
    const tabs = connectorTabs([kind("MS_GRAPH", { available: false, reason: "Set MS_CLIENT_ID." })], false);
    expect(tabs[0].info.reason).toBe("Set MS_CLIENT_ID.");
  });
});

describe("oauthProvider", () => {
  it("maps OAuth kinds to their provider and others to null", () => {
    expect(oauthProvider("GOOGLE_DRIVE")).toBe("google");
    expect(oauthProvider("MS_GRAPH")).toBe("microsoft");
    expect(oauthProvider("DROPBOX")).toBe("dropbox");
    expect(oauthProvider("S3_COMPATIBLE")).toBeNull();
    expect(oauthProvider("GOOGLE_DRIVE_ORG")).toBeNull();
  });
});

describe("accountDetail", () => {
  it("shows who an OAuth account is, or the bucket and root prefix", () => {
    expect(accountDetail(account({ config: { connected_as: "ada@example.com" } }))).toBe("ada@example.com");
    expect(accountDetail(account({ config: { bucket: "hiring", root_prefix: "2026" } }))).toBe("hiring/2026");
    expect(accountDetail(account({ config: { container: "cvs", root_prefix: "" } }))).toBe("cvs");
    expect(accountDetail(account({ config: { bucket: 42 } }))).toBe("");
  });
});

describe("browsing", () => {
  it("enters folders and goes back along the trail", () => {
    const trail = enterFolder(enterFolder([ROOT_CRUMB], { ...folder, id: "docs/", name: "docs" }), folder);
    expect(trail.map((c) => c.id)).toEqual([null, "docs/", "docs/specs/"]);
    expect(trailTo(trail, 1).map((c) => c.id)).toEqual([null, "docs/"]);
    expect(trailTo(trail, -1)).toEqual([ROOT_CRUMB]);
  });

  it("toggles and merges selections by id", () => {
    const one = toggleItem([], { id: file.id, name: file.name, path: file.path, is_folder: false });
    expect(one).toHaveLength(1);
    expect(toggleItem(one, { id: file.id })).toEqual([]);
    const merged = mergeItems(one, [{ id: file.id }, { id: "x", name: "x" }, { id: "x" }]);
    expect(merged.map((i) => i.id)).toEqual([file.id, "x"]);
  });

  it("imports a whole folder by its prefix, or the account root at the top", () => {
    expect(folderPrefix(account(), "docs/specs/")).toBe("docs/specs/");
    expect(folderPrefix(account({ config: { root_prefix: "team-a" } }), null)).toBe("team-a/");
    expect(folderPrefix(account({ config: { root_prefix: "" } }), null)).toBe("");
  });

  it("maps Google Picker documents, recognising folders", () => {
    expect(
      pickedToItems([
        { id: "1", name: "Specs", mimeType: "application/vnd.google-apps.folder" },
        { id: "2", name: "cv.pdf", mimeType: "application/pdf" },
        { id: "3" },
      ]),
    ).toEqual([
      { id: "1", name: "Specs", path: "Specs", is_folder: true },
      { id: "2", name: "cv.pdf", path: "cv.pdf", is_folder: false },
      { id: "3", name: "", path: "", is_folder: false },
    ]);
  });
});

describe("source bodies", () => {
  it("suggests a name from the selection", () => {
    expect(suggestSourceName([], null, "hiring")).toBe("hiring");
    expect(suggestSourceName([], "docs/specs/", "hiring")).toBe("specs");
    expect(suggestSourceName([], "", "hiring")).toBe("hiring");
    expect(suggestSourceName([{ id: "1", name: "a.pdf" }], null, "x")).toBe("a.pdf");
    expect(suggestSourceName([{ id: "1", name: "a.pdf" }, { id: "2" }], "docs/", "x")).toBe("a.pdf and 2 more");
    expect(suggestSourceName([{ id: "id-only" }], null, "x")).toBe("id-only");
  });

  it("builds SourceIn from a selection", () => {
    expect(
      buildSourceIn({
        kind: "S3_COMPATIBLE",
        accountId: "acc-1",
        name: "  Specs  ",
        items: [{ id: "docs/a.pdf", name: "a.pdf", path: "docs/a.pdf", is_folder: false }],
        prefix: "docs/specs/",
        docType: "DATASHEET",
        syncEnabled: true,
        intervalMinutes: 5,
      }),
    ).toEqual({
      kind: "S3_COMPATIBLE",
      account_id: "acc-1",
      name: "Specs",
      items: [{ id: "docs/a.pdf", name: "a.pdf", path: "docs/a.pdf", is_folder: false }],
      prefix: "docs/specs/",
      doc_type: "DATASHEET",
      sync_enabled: true,
      interval_minutes: 15,
    });
  });

  it("builds a URL source that is kept in sync", () => {
    expect(
      buildUrlSourceIn({ url: " https://example.com/docs/ ", title: "", docType: "AUTO", intervalMinutes: 1440 }),
    ).toEqual({
      kind: "URL",
      name: "example.com/docs",
      url: "https://example.com/docs/",
      items: [],
      doc_type: "AUTO",
      sync_enabled: true,
      interval_minutes: 1440,
    });
    expect(buildUrlSourceIn({ url: "https://a.io", title: " A ", docType: "JD", intervalMinutes: 60 }).name).toBe("A");
    expect(urlSourceName("https://example.com/")).toBe("example.com");
    expect(urlSourceName("not a url")).toBe("not a url");
  });
});

describe("intervals", () => {
  it("labels presets and other values", () => {
    expect(intervalLabel(60)).toBe("Every hour");
    expect(intervalLabel(10_080)).toBe("Weekly");
    expect(intervalLabel(2880)).toBe("Every 2 days");
    expect(intervalLabel(120)).toBe("Every 2 hours");
    expect(intervalLabel(45)).toBe("Every 45 minutes");
  });

  it("offers the presets plus a custom current value, in order", () => {
    expect(intervalOptions(360).map((o) => o.minutes)).toEqual([15, 60, 360, 1440, 10_080]);
    expect(intervalOptions(120).map((o) => o.minutes)).toEqual([15, 60, 120, 360, 1440, 10_080]);
  });

  it("keeps intervals within the API's range", () => {
    expect(clampInterval(1)).toBe(15);
    expect(clampInterval(99_999)).toBe(10_080);
    expect(toInterval("1440")).toBe(1440);
    expect(toInterval("nope")).toBe(360);
  });
});

describe("credentials forms", () => {
  it("builds an S3 body, trimming and dropping empty optional fields", () => {
    const body = buildS3AccountIn({
      ...EMPTY_S3_FORM,
      bucket: " hiring ",
      rootPrefix: "/team-a/",
      endpointUrl: " ",
      accessKey: " AK ",
      secretKey: " SK ",
    });
    expect(body).toEqual({
      kind: "S3_COMPATIBLE",
      name: "hiring",
      endpoint_url: null,
      region: "us-east-1",
      bucket: "hiring",
      root_prefix: "team-a",
      force_path_style: true,
      access_key: "AK",
      secret_key: "SK",
      org_level: false,
    });
    expect(buildS3AccountIn({ ...EMPTY_S3_FORM, bucket: "pub", secretKey: "stray" }).secret_key).toBeNull();
  });

  it("needs a bucket and both keys or neither", () => {
    expect(s3FormReady({ ...EMPTY_S3_FORM, bucket: "ab" })).toBe(false);
    expect(s3FormReady({ ...EMPTY_S3_FORM, bucket: "abc" })).toBe(true);
    expect(s3FormReady({ ...EMPTY_S3_FORM, bucket: "abc", accessKey: "AK" })).toBe(false);
  });

  it("builds an Azure body for a connection string or a SAS URL", () => {
    expect(
      buildAzureAccountIn({ ...EMPTY_AZURE_FORM, connectionString: " cs ", container: " cvs ", orgLevel: true }),
    ).toEqual({
      kind: "AZURE_BLOB",
      name: "cvs",
      container: "cvs",
      root_prefix: "",
      connection_string: "cs",
      container_sas_url: null,
      org_level: true,
    });
    const sas = buildAzureAccountIn({
      ...EMPTY_AZURE_FORM,
      mode: "sas_url",
      sasUrl: "https://acct.blob.core.windows.net/cvs?sv=1&sig=x",
      connectionString: "ignored",
    });
    expect(sas).toMatchObject({ name: "cvs", container: null, connection_string: null });
    expect(sas.container_sas_url).toBe("https://acct.blob.core.windows.net/cvs?sv=1&sig=x");
    expect(azureFormReady({ ...EMPTY_AZURE_FORM, mode: "sas_url", sasUrl: "http://x" })).toBe(false);
    expect(azureFormReady({ ...EMPTY_AZURE_FORM, connectionString: "cs" })).toBe(false);
  });
});

describe("parseFolderIds", () => {
  it("accepts ids and Drive links, one per line, without duplicates", () => {
    expect(
      parseFolderIds(
        "1AbCdEf\n\n https://drive.google.com/drive/folders/1XyZ_-9?usp=sharing \n1AbCdEf\nhttps://drive.google.com/open?id=0Bq12345",
      ),
    ).toEqual({ ok: true, ids: ["1AbCdEf", "1XyZ_-9", "0Bq12345"] });
  });

  it("rejects malformed lines, empty input and too many folders", () => {
    expect(parseFolderIds("good-id-1\nbad id")).toEqual({
      ok: false,
      error: "Line 2: not a Google Drive folder id or link.",
    });
    expect(parseFolderIds("  \n")).toEqual({ ok: false, error: "Enter at least one folder id." });
    const many = Array.from({ length: 21 }, (_, i) => `folder-${i}`).join("\n");
    expect(parseFolderIds(many)).toEqual({ ok: false, error: "At most 20 folders." });
  });
});

describe("OAuth popup messages", () => {
  it("reads the outcome from the query string", () => {
    const params = new URLSearchParams("status=ok&provider=google&detail=Connected+as+ada%40example.com");
    expect(oauthResultFromParams(params)).toEqual({
      status: "ok",
      provider: "google",
      detail: "Connected as ada@example.com",
    });
    expect(oauthResultFromParams(new URLSearchParams("status=weird")).status).toBe("error");
  });

  it("round-trips a message and ignores anything else", () => {
    const result = { status: "error" as const, provider: "dropbox", detail: "Access was not granted." };
    expect(parseOAuthMessage(oauthMessage(result))).toEqual(result);
    expect(parseOAuthMessage({ ...oauthMessage(result), type: "other" })).toBeNull();
    expect(parseOAuthMessage({ type: "intrv:oauth", status: "ok" })).toBeNull();
    expect(parseOAuthMessage("intrv:oauth")).toBeNull();
    expect(parseOAuthMessage(null)).toBeNull();
  });
});

describe("sync results", () => {
  it("is null before the first run", () => {
    expect(parseSyncResult({})).toBeNull();
  });

  it("summarises a run", () => {
    const result = parseSyncResult({
      added: 3,
      updated: 1,
      unchanged: 12,
      deleted: 0,
      skipped: ["a.exe: unsupported type"],
      failed: [],
      truncated: true,
    });
    expect(result).not.toBeNull();
    expect(syncResultSummary(result!)).toBe(
      "3 added, 1 updated, 12 unchanged · 1 skipped · more files remain; the next sync continues",
    );
  });

  it("tolerates missing or malformed fields", () => {
    const result = parseSyncResult({ added: "x", failed: ["b.pdf: timeout", 7] });
    expect(result).toEqual({
      added: 0,
      updated: 0,
      unchanged: 0,
      deleted: 0,
      skipped: [],
      failed: [],
      truncated: false,
    });
    expect(syncResultSummary(result!)).toBe("No files found");
    expect(syncResultSummary({ ...result!, deleted: 2, failed: ["x"] })).toBe("2 removed · 1 failed");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-10-18T12:00:00Z");

  it("describes past and future times", () => {
    expect(formatRelative("2026-10-18T11:59:30Z", now)).toBe("just now");
    expect(formatRelative("2026-10-18T12:00:20Z", now)).toBe("in a moment");
    expect(formatRelative("2026-10-18T11:55:00Z", now)).toBe("5 minutes ago");
    expect(formatRelative("2026-10-18T15:00:00Z", now)).toBe("in 3 hours");
    expect(formatRelative("2026-10-17T12:00:00Z", now)).toBe("yesterday");
    expect(formatRelative("2026-10-25T12:00:00Z", now)).toBe("in 7 days");
  });

  it("returns null for missing or invalid times", () => {
    expect(formatRelative(null, now)).toBeNull();
    expect(formatRelative("not a date", now)).toBeNull();
  });
});

describe("canManageSource", () => {
  it("lets the creator or an owner manage a source", () => {
    expect(canManageSource(source({ created_by_me: true }), false)).toBe(true);
    expect(canManageSource(source(), true)).toBe(true);
    expect(canManageSource(source(), false)).toBe(false);
  });
});
