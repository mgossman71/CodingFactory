import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "../src/clipboard";

/** Minimal fake Document for the legacy execCommand fallback (no jsdom here). */
function fakeDoc(copyResult: boolean | Error = true): Document {
  const el = {
    value: "",
    style: {} as CSSStyleDeclaration,
    setAttribute: () => {},
    select: () => {},
  };
  return {
    createElement: () => el,
    body: { appendChild: () => {}, removeChild: () => {} },
    execCommand: (cmd: string) => {
      if (cmd !== "copy") return false;
      if (copyResult instanceof Error) throw copyResult;
      return copyResult;
    },
  } as unknown as Document;
}

function setClipboard(clipboard: Clipboard | undefined) {
  Object.defineProperty(globalThis, "navigator", {
    value: { ...(globalThis.navigator ?? {}), clipboard },
    configurable: true,
  });
}

afterEach(() => setClipboard(undefined));

describe("copyText", () => {
  it("uses navigator.clipboard when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText } as Clipboard);
    expect(await copyText("hello", fakeDoc())).toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("falls back to execCommand when the Clipboard API throws (insecure origin)", async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) } as Clipboard);
    expect(await copyText("hello", fakeDoc(true))).toBe(true);
  });

  it("falls back to execCommand when navigator.clipboard is undefined (http on LAN host)", async () => {
    setClipboard(undefined);
    expect(await copyText("hello", fakeDoc(true))).toBe(true);
  });

  it("returns false when execCommand fails", async () => {
    expect(await copyText("hello", fakeDoc(false))).toBe(false);
  });

  it("returns false when execCommand throws", async () => {
    expect(await copyText("hello", fakeDoc(new Error("nope")))).toBe(false);
  });

  it("returns false with no clipboard and no document", async () => {
    setClipboard(undefined);
    expect(await copyText("hello", undefined)).toBe(false);
  });
});
