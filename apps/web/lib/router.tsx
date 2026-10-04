"use client";

import NextLink from "next/link";
import type { ComponentProps, JSX } from "react";

/** Adapt the landing's link props to Next.js navigation. */
export function Link({
  to,
  ...props
}: Omit<ComponentProps<typeof NextLink>, "href"> & { to: string }): JSX.Element {
  return <NextLink href={to} {...props} />;
}
