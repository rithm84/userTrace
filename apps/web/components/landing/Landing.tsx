"use client";

import { useEffect, type JSX } from "react";

import "./landing.css";
import { Closing } from "./closing/Closing";
import { CaseFileSection } from "./casefile/CaseFileSection";
import { HowItFits } from "./fits/HowItFits";
import { Footer } from "./Footer";
import { LandingJourney } from "./journey/LandingJourney";
import { Navbar } from "./Navbar";
import { WhereItHelps } from "./helps/WhereItHelps";
import { ForYourUsers } from "./users/ForYourUsers";
import { RiveFilesProvider } from "./characters/RiveFiles";
import { WaitlistProvider } from "./Waitlist";

/**
 * The marketing page. The hero, the cover, the comic strip and Shopalytics story share
 * one pinned stage, followed by
 * where it helps, the Case File for your team, why your users say yes, how it fits, the
 * closing panel; then the black footer.
 */
export function Landing(): JSX.Element {
  useLightGround();
  return (
    <WaitlistProvider><RiveFilesProvider><div data-landing="" data-testid="landing-root">
      <Navbar />
      <main>
        <LandingJourney />
        <WhereItHelps />
        <CaseFileSection />
        <ForYourUsers />
        <HowItFits />
        <Closing />
      </main>
      <Footer />
    </div></RiveFilesProvider></WaitlistProvider>
  );
}

/** The app shell is dark; the landing paints the document white for its lifetime. */
function useLightGround(): void {
  useEffect(() => {
    const root = document.documentElement;
    const body = document.body;
    const previous = {
      colorScheme: root.style.colorScheme,
      background: body.style.backgroundColor,
    };
    root.style.colorScheme = "light";
    body.style.backgroundColor = "#ffffff";
    return () => {
      root.style.colorScheme = previous.colorScheme;
      body.style.backgroundColor = previous.background;
    };
  }, []);
}
