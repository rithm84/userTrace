import type { Metadata } from "next";
import { Landing } from "@/components/landing/Landing";

const DESCRIPTION =
  "Forward-deployed agents that turn community feedback into merge-ready changes by interviewing users, building improvements, and validating outcomes.";

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
