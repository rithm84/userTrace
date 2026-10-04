"use client";

import type { JSX } from "react";

import { Logo } from "@/components/brand/Logo";
import { CONTACT_HREF } from "./Waitlist";

const LINKS: readonly Readonly<{ label: string; href: string }>[] = [
  { label: "How it works", href: "#how-it-works" },
  { label: "For your team", href: "#what-you-get" },
  { label: "For your users", href: "#for-your-users" },
  { label: "Contact", href: CONTACT_HREF },
];

/**
 * A plain black colophon: the mark and wordmark, the four links, the
 * copyright line and a Back to top control. Links draw their coral underline
 * on hover; nothing else moves.
 */
export function Footer(): JSX.Element {
  return (
    <footer data-testid="landing-footer" style={{ background: "var(--ink)", color: "var(--paper)", padding: "56px 0 44px" }}>
      <div className="lp-wrap" style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 40, alignItems: "start" }}>
        <div>
          <Logo tone="light" size={40} wordmarkClassName="text-[32px] leading-none text-white" />
          <p style={{ margin: "18px 0 0", font: "500 15px/1.5 var(--body)", color: "rgba(255,255,255,.72)", maxWidth: 420 }}>
            Forward-deployed agents for product teams.
          </p>
        </div>
        <nav aria-label="Footer" style={{ display: "flex", gap: 28, alignItems: "center", paddingTop: 8 }}>
          {LINKS.map((link) => (
            <a key={link.label} href={link.href} className="lp-nav-link" style={{ font: "500 16px/1 var(--body)" }}>
              {link.label}
            </a>
          ))}
        </nav>
      </div>
      <div className="lp-wrap" style={{ marginTop: 44, paddingTop: 20, borderTop: "1px solid rgba(255,255,255,.22)", display: "flex", justifyContent: "space-between", alignItems: "center", font: "500 14px/1 var(--body)", color: "rgba(255,255,255,.6)" }}>
        <span>© 2026 Rithvik Manikandan and Ming Lu. All rights reserved.</span>
        <a href="#top" className="lp-nav-link" data-testid="footer-back-to-top" style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "rgba(255,255,255,.85)" }} onClick={(event) => { event.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
          Back to top
          <svg aria-hidden="true" viewBox="0 0 16 16" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
          </svg>
        </a>
      </div>
    </footer>
  );
}
