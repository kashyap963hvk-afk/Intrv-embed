"use client";

// Loaded only in the browser (next/dynamic with ssr: false).
import { Excalidraw, exportToBlob, MIME_TYPES } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import type { ExcalidrawImperativeAPI } from "@excalidraw/excalidraw/types";

import { PRACTICAL_LIMITS } from "@/lib/practical";
import { usePrefersDark } from "@/lib/use-prefers-dark";

declare global {
  interface Window {
    EXCALIDRAW_ASSET_PATH?: string | string[];
  }
}

// Fonts are served from this app (scripts/copy-vendor-assets.mjs) instead of a public CDN.
if (typeof window !== "undefined") window.EXCALIDRAW_ASSET_PATH = "/vendor/excalidraw/";

export interface BoardHandle {
  /** The drawing as a PNG within the upload limit, or null when nothing is drawn. */
  exportPng: () => Promise<Blob | null>;
}

const EXPORT_SIZES = [1600, 1000, 700];

export default function ExcalidrawBoard({ onReady }: { onReady: (handle: BoardHandle) => void }) {
  const dark = usePrefersDark();

  const ready = (api: ExcalidrawImperativeAPI) =>
    onReady({
      exportPng: async () => {
        const elements = api.getSceneElements();
        if (elements.length === 0) return null;
        for (const maxWidthOrHeight of EXPORT_SIZES) {
          const blob = await exportToBlob({
            elements,
            appState: { ...api.getAppState(), exportBackground: true, viewBackgroundColor: "#ffffff" },
            files: api.getFiles(),
            mimeType: MIME_TYPES.png,
            maxWidthOrHeight,
          });
          if (blob.size <= PRACTICAL_LIMITS.imageMaxBytes) return blob;
        }
        return null;
      },
    });

  return (
    <div className="h-[28rem] overflow-hidden rounded-md border">
      <Excalidraw
        excalidrawAPI={ready}
        theme={dark ? "dark" : "light"}
        aiEnabled={false}
        UIOptions={{
          canvasActions: {
            loadScene: false,
            saveToActiveFile: false,
            export: false,
            saveAsImage: false,
            toggleTheme: null,
          },
          tools: { image: false },
        }}
      />
    </div>
  );
}
