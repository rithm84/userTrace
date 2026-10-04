import type { Metadata } from "next";

import { VercelInsights } from "@/components/VercelInsights";

import "./globals.css";

const browserIcon = "/icon.svg?v=20260711-continuous-contour";

export const metadata: Metadata = {
  title: "uTrace - forward-deployed agents for product teams",
  description:
    "uTrace turns scattered customer truths into validated product changes with voice-first interviews, bounded repairs, and Case Files your team can act on.",
  icons: {
    icon: browserIcon,
    shortcut: browserIcon,
    apple: browserIcon,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        {children}
        <VercelInsights />
      </body>
    </html>
  );
}
