import Link from "next/link";
import type { JSX, ReactNode } from "react";

import { Logo } from "@/components/brand/Logo";
import { signOut } from "@/lib/auth/actions";

export type ShellLink = Readonly<{ href: string; label: string; key: string }>;

/**
 * How the page under the bar uses the screen: `contained` centres a reading
 * column, `fill` gives the page every pixel below the bar (the session tab).
 */
export type ShellMain = "contained" | "fill";

/**
 * The authenticated frame: the black bar with the mark at the landing navbar's
 * size, the viewer's own navigation and a sign-out control, over the page's own
 * content. `current` names the active link so the bar does not need client-side
 * routing state.
 */
export function ProductShell(
  props: Readonly<{
    links: readonly ShellLink[];
    current: string | null;
    viewerLabel: string | null;
    main: ShellMain;
    children: ReactNode;
  }>,
): JSX.Element {
  return (
    <>
      <header className="pr-bar" data-testid="product-bar">
        <Link href="/" aria-label="uTrace home" className="pr-bar-brand" data-testid="product-bar-mark">
          <Logo tone="light" size={44} wordmarkClassName="text-[36px] leading-none text-white" />
        </Link>
        {props.links.length > 0 ? (
          <nav className="pr-bar-links" aria-label="Product">
            {props.links.map((link) => (
              <Link
                key={link.key}
                href={link.href}
                data-testid={`product-bar-link-${link.key}`}
                aria-current={link.key === props.current ? "page" : undefined}
              >
                {link.label}
              </Link>
            ))}
          </nav>
        ) : null}
        {props.viewerLabel === null ? null : (
          <div className="pr-bar-end">
            <span data-testid="product-bar-viewer">{props.viewerLabel}</span>
            <form action={signOut}>
              <button type="submit" className="pr-btn pr-btn--sm pr-btn--onblack" data-testid="product-sign-out">
                Sign out
              </button>
            </form>
          </div>
        )}
      </header>
      <main className={props.main === "fill" ? "pr-fill" : "pr-main"}>{props.children}</main>
    </>
  );
}

export const STAFF_LINKS: readonly ShellLink[] = [
  { key: "dashboard", href: "/dashboard", label: "Dashboard" },
  { key: "settings", href: "/dashboard/settings", label: "Settings" },
];

export const PARTICIPANT_LINKS: readonly ShellLink[] = [
  { key: "user-settings", href: "/user-settings", label: "Your account" },
];
