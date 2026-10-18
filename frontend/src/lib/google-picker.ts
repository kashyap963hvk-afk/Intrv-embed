/**
 * Loads the Google Picker on demand and opens it for a Drive account. Only the parts of `gapi` and `google.picker`
 * used here are declared; see https://developers.google.com/drive/picker/reference.
 */

import type { PickedDocument } from "@/lib/connectors";

interface GapiLoadConfig {
  callback: () => void;
  onerror: () => void;
  timeout: number;
  ontimeout: () => void;
}

interface Gapi {
  load(library: "picker", config: GapiLoadConfig): void;
}

interface PickerCallbackData {
  action: string;
  docs?: PickedDocument[];
}

interface DocsView {
  setIncludeFolders(include: boolean): DocsView;
  setSelectFolderEnabled(enabled: boolean): DocsView;
  setMode(mode: string): DocsView;
}

interface Picker {
  setVisible(visible: boolean): void;
  dispose(): void;
}

interface PickerBuilder {
  addView(view: DocsView): PickerBuilder;
  enableFeature(feature: string): PickerBuilder;
  setOAuthToken(token: string): PickerBuilder;
  setDeveloperKey(key: string): PickerBuilder;
  setAppId(appId: string): PickerBuilder;
  setOrigin(origin: string): PickerBuilder;
  setTitle(title: string): PickerBuilder;
  setCallback(callback: (data: PickerCallbackData) => void): PickerBuilder;
  build(): Picker;
}

interface PickerNamespace {
  DocsView: new (viewId?: string) => DocsView;
  PickerBuilder: new () => PickerBuilder;
  ViewId: { DOCS: string };
  DocsViewMode: { LIST: string };
  Feature: { MULTISELECT_ENABLED: string };
  Action: { PICKED: string; CANCEL: string };
}

declare global {
  interface Window {
    gapi?: Gapi;
    google?: { picker?: PickerNamespace };
  }
}

const API_SCRIPT = "https://apis.google.com/js/api.js";
const LOAD_TIMEOUT_MS = 15_000;

let loading: Promise<PickerNamespace> | null = null;

function loadScript(): Promise<Gapi> {
  return new Promise((resolve, reject) => {
    if (window.gapi) {
      resolve(window.gapi);
      return;
    }
    const script = document.createElement("script");
    script.src = API_SCRIPT;
    script.async = true;
    script.onload = () => (window.gapi ? resolve(window.gapi) : reject(new Error("Google API failed to start.")));
    script.onerror = () => {
      script.remove();
      reject(new Error("Could not load the Google Picker. Check your connection or content blocker."));
    };
    document.head.appendChild(script);
  });
}

/** Load `api.js` and the picker library once per page; a failed load can be retried. */
function loadPicker(): Promise<PickerNamespace> {
  loading ??= loadScript()
    .then(
      (gapi) =>
        new Promise<PickerNamespace>((resolve, reject) => {
          const fail = () => reject(new Error("Could not load the Google Picker."));
          gapi.load("picker", {
            callback: () => (window.google?.picker ? resolve(window.google.picker) : fail()),
            onerror: fail,
            timeout: LOAD_TIMEOUT_MS,
            ontimeout: fail,
          });
        }),
    )
    .catch((error: unknown) => {
      loading = null;
      throw error;
    });
  return loading;
}

export interface OpenPickerOptions {
  accessToken: string;
  developerKey: string;
  appId: string | null;
  onPicked: (docs: PickedDocument[]) => void;
  /** Called once the picker closes, whether files were picked or not. */
  onClosed: () => void;
}

/** Open a multi-select Drive picker (files and folders). Resolves once it is visible. */
export async function openGooglePicker(options: OpenPickerOptions): Promise<void> {
  const picker = await loadPicker();
  const view = new picker.DocsView(picker.ViewId.DOCS)
    .setIncludeFolders(true)
    .setSelectFolderEnabled(true)
    .setMode(picker.DocsViewMode.LIST);
  let builder = new picker.PickerBuilder()
    .addView(view)
    .enableFeature(picker.Feature.MULTISELECT_ENABLED)
    .setOAuthToken(options.accessToken)
    .setDeveloperKey(options.developerKey)
    .setOrigin(window.location.origin)
    .setTitle("Choose files and folders to import");
  if (options.appId) builder = builder.setAppId(options.appId);
  const instance = builder
    .setCallback((data) => {
      if (data.action === picker.Action.PICKED) {
        options.onPicked(data.docs ?? []);
      } else if (data.action !== picker.Action.CANCEL) {
        return; // "loaded" and other progress notifications
      }
      instance.dispose();
      options.onClosed();
    })
    .build();
  instance.setVisible(true);
}
