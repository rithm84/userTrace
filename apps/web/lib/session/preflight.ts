"use client";

import type { PreflightReport } from "@/lib/api/schemas";

import { probeExtension } from "./extension";

/**
 * The participant compatibility preflight the session tab runs before any
 * interview activity. It gathers what this browser can prove — supported
 * browser, extension installation and linkage, host permission, microphone —
 * and the control plane decides whether the session may be entered.
 */

/** The extension's minimum supported Chrome, from the extension contract. */
export const MINIMUM_CHROME_MAJOR = 137;

type UserAgentBrand = Readonly<{ brand: string; version: string }>;

declare global {
  interface Navigator {
    readonly userAgentData?: Readonly<{ brands?: readonly UserAgentBrand[] }>;
  }
}

export type BrowserIdentity = Readonly<{ name: string; majorVersion: number; supported: boolean }>;

/**
 * Chromium brands report themselves in `userAgentData`; the user-agent string
 * is the fallback for browsers that do not. A browser that names no Chrome
 * version is reported as unsupported rather than guessed at.
 */
export function identifyBrowser(navigatorLike: Navigator): BrowserIdentity {
  const brands = navigatorLike.userAgentData?.brands;
  if (brands !== undefined) {
    const chrome = brands.find((brand) => brand.brand === "Google Chrome");
    if (chrome !== undefined) {
      const major = Number.parseInt(chrome.version, 10);
      return {
        name: "Google Chrome",
        majorVersion: Number.isNaN(major) ? 0 : major,
        supported: !Number.isNaN(major) && major >= MINIMUM_CHROME_MAJOR,
      };
    }
    const chromium = brands.find((brand) => brand.brand === "Chromium");
    if (chromium !== undefined) {
      const major = Number.parseInt(chromium.version, 10);
      return { name: "Chromium", majorVersion: Number.isNaN(major) ? 0 : major, supported: false };
    }
  }

  const match = /Chrome\/(\d+)/u.exec(navigatorLike.userAgent);
  const isEdgeOrOpera = /Edg\/|OPR\//u.test(navigatorLike.userAgent);
  if (match === null || isEdgeOrOpera) {
    return { name: describeOtherBrowser(navigatorLike.userAgent), majorVersion: 0, supported: false };
  }
  const major = Number.parseInt(match[1], 10);
  return { name: "Google Chrome", majorVersion: major, supported: major >= MINIMUM_CHROME_MAJOR };
}

function describeOtherBrowser(userAgent: string): string {
  if (/Edg\//u.test(userAgent)) return "Microsoft Edge";
  if (/OPR\//u.test(userAgent)) return "Opera";
  if (/Firefox\//u.test(userAgent)) return "Firefox";
  if (/Safari\//u.test(userAgent)) return "Safari";
  return "This browser";
}

/**
 * Asks for the microphone once. A refusal is an expected answer, not a fault:
 * it comes back as `false` so the preflight can say what is missing.
 */
export async function checkMicrophone(): Promise<boolean> {
  const media = navigator.mediaDevices;
  if (media === undefined) {
    return false;
  }
  try {
    const stream = await media.getUserMedia({ audio: true });
    for (const track of stream.getTracks()) {
      track.stop();
    }
    return true;
  } catch {
    return false;
  }
}

/** Collects the full report the control plane evaluates. */
export async function collectPreflightReport(extensionId: string): Promise<PreflightReport> {
  const browser = identifyBrowser(navigator);
  const extension = browser.supported ? await probeExtension(extensionId) : null;
  const microphone = await checkMicrophone();

  return {
    browser: { name: browser.name, major_version: browser.majorVersion, supported: browser.supported },
    extension:
      extension === null
        ? { installed: false, account_linked: false }
        : {
            installed: true,
            account_linked: extension.account_linked,
            installation_proof: extension.installation_proof ?? undefined,
            protocol_version: extension.protocol_version,
          },
    permissions: { host: extension?.host_permission ?? false, microphone },
  };
}
