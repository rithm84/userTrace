"use client";

import { Menu } from "lucide-react";
import { Link } from "@/lib/router";
import { Logo } from "@/components/brand/Logo";
import { CONTACT_HREF, focusLandingWaitlist } from "./Waitlist";

type NavLink = Readonly<{ label: string; href: string }>;

/** Every link lands on a section of this page, in page order; there are no docs to link yet. */
const LINKS: readonly NavLink[] = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Where it helps", href: "#where-it-helps" },
  { label: "For your team", href: "#what-you-get" },
  { label: "For your users", href: "#for-your-users" },
  { label: "How it fits", href: "#integrations" },
];

/**
 * Black sticky bar: a large white mark and wordmark, the links centred
 * on the bar itself (three-column grid), with email contact and waitlist controls on the right.
 */
export function Navbar() {
  return (
    <header
      className="sticky top-0 z-50"
      data-testid="landing-navbar"
      style={{ background: "var(--ink)", color: "var(--paper)", height: "var(--nav-h)" }}
    >
      <div
        className="v3-nav-inner grid h-full items-center"
        style={{
          gridTemplateColumns: "1fr auto 1fr",
          padding: "0 var(--gutter)",
        }}
      >
        <Link
          className="v3-nav-brand flex items-center justify-self-start"
          to="/"
          aria-label="uTrace home"
          onClick={(event) => {
            // on the landing itself the mark is a way back to the hero, never a reload
            if (window.location.pathname !== "/") return;
            event.preventDefault();
            window.scrollTo({ top: 0, behavior: "instant" });
          }}
        >
          <Logo tone="light" size={44} wordmarkClassName="text-[36px] leading-none text-white" />
        </Link>

        <nav className="v3-nav-primary hidden items-center gap-8 justify-self-center md:flex" aria-label="Primary">
          {LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              data-testid={`nav-${link.href.slice(1)}`}
              className="lp-nav-link"
              style={{ font: "500 17px/1 var(--body)" }}
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="v3-nav-actions flex items-center gap-5 justify-self-end">
          <a className="lp-btn lp-btn--onblack" style={{ height: 42 }} href={CONTACT_HREF} data-testid="nav-book-demo">
            Book a demo
          </a>
          <a className="lp-btn" style={{ height: 42 }} href="#waitlist" data-testid="nav-waitlist" onClick={(event) => { event.preventDefault(); focusLandingWaitlist(); }}>
            Waitlist
          </a>
        </div>
        <details className="v3-nav-menu">
          <summary data-testid="mobile-menu" aria-label="Open navigation"><Menu size={23} /></summary>
          <nav aria-label="Mobile navigation">
            {LINKS.map((link) => <a key={link.href} href={link.href} data-testid={`mobile-${link.href.slice(1)}`} onClick={(event) => event.currentTarget.closest("details")?.removeAttribute("open")}>{link.label}</a>)}
            <a href={CONTACT_HREF} data-testid="mobile-book-demo" onClick={(event) => event.currentTarget.closest("details")?.removeAttribute("open")}>Book a demo</a>
            <a className="lp-btn" href="#waitlist" data-testid="mobile-waitlist" onClick={(event) => { event.preventDefault(); event.currentTarget.closest("details")?.removeAttribute("open"); focusLandingWaitlist(); }}>Waitlist</a>
          </nav>
        </details>
      </div>
    </header>
  );
}
