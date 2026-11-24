/**
 * Clipboard write that also works over plain HTTP (http://host:port), where
 * browsers disable navigator.clipboard because it requires a secure context
 * (HTTPS or localhost). Falls back to the legacy hidden-textarea +
 * execCommand("copy") path, which still works on insecure origins.
 * Returns true when the text was placed on the clipboard.
 *
 * `doc` is injectable so this stays unit-testable without a DOM (vitest
 * runs in the node environment here).
 */
export async function copyText(
  text: string,
  doc: Document | undefined = typeof document !== "undefined" ? document : undefined,
): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through: permission denial or an insecure-context edge case
    }
  }
  if (!doc) return false;
  const ta = doc.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1px";
  ta.style.opacity = "0";
  doc.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = doc.execCommand("copy");
  } catch {
    ok = false;
  }
  doc.body.removeChild(ta);
  return ok;
}
