import type { Metadata } from "next";
import { Landing } from "@/components/landing/Landing";

const DESCRIPTION =
  "Forward-deployed agents that investigate product feedback through user interviews, uncover underlying pain points, and build solutions those same users validate.";

export const metadata: Metadata = {
  title: "uTrace — Ship features your customers actually want",
  description: DESCRIPTION,
  openGraph: {
    title: "uTrace — Ship features your customers actually want",
    description: DESCRIPTION,
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "uTrace — Ship features your customers actually want",
    description: DESCRIPTION,
  },
};

export default function HomePage() {
  return <Landing />;
}
