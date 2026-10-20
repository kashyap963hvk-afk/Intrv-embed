/** Saving API responses as files: file names from Content-Disposition and a blob download through a temporary link. */

/**
 * The file name in a Content-Disposition header (RFC 6266 `filename*` first, then `filename`), or the fallback when
 * the header is missing (cross-origin responses only expose it when the API lists it in Expose-Headers).
 */
export function filenameFromDisposition(header: string | null | undefined, fallback: string): string {
  if (!header) return fallback;
  const extended = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (extended) {
    try {
      const decoded = decodeURIComponent(extended[1].trim());
      if (decoded) return decoded;
    } catch {
      // Malformed percent-encoding: fall through to the plain parameter.
    }
  }
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/.exec(header);
  const name = (plain?.[1] ?? plain?.[2] ?? "").trim();
  return name || fallback;
}

/** A title as a safe file name stem, matching the API's export naming (letters, digits, `.`, `_`, `-`). */
export function safeFileStem(title: string, fallback: string): string {
  const stem = title
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return stem || fallback;
}

/** Hand a blob to the browser as a download. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke after the click has been handled, or some browsers cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}
