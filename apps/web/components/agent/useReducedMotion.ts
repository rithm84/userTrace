"use client";

import { useEffect, useState } from "react";

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export type ReducedMotionPreference = Readonly<{
  reducedMotion: boolean;
  resolved: boolean;
}>;

const UNRESOLVED_REDUCED_MOTION_PREFERENCE: ReducedMotionPreference = Object.freeze({
  reducedMotion: true,
  resolved: false,
});

export function useReducedMotionPreference(): ReducedMotionPreference {
  const [preference, setPreference] = useState<ReducedMotionPreference>(
    UNRESOLVED_REDUCED_MOTION_PREFERENCE,
  );

  useEffect(() => {
    const media = window.matchMedia(MOTION_QUERY);
    const updatePreference = (): void => {
      setPreference((currentPreference) => {
        if (currentPreference.reducedMotion === media.matches && currentPreference.resolved) {
          return currentPreference;
        }

        return { reducedMotion: media.matches, resolved: true };
      });
    };

    updatePreference();
    media.addEventListener("change", updatePreference);
    return () => media.removeEventListener("change", updatePreference);
  }, []);

  return preference;
}

export function useReducedMotion(): boolean {
  return useReducedMotionPreference().reducedMotion;
}
