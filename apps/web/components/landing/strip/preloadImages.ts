"use client";

import { getImageProps, type ImageProps } from "next/image";
import { useEffect, useRef } from "react";

/**
 * Starts downloading images a page shows later, with the exact URLs their `<Image>`s will
 * choose (`getImageProps`, the same `srcset` and `sizes`), so each is cached by the time it
 * mounts: a panel's art arrives with the panel instead of popping in after its border. Detached
 * `Image`s rather than `<link rel="preload">`, which Chrome flags as unused when a panel is
 * minutes of scroll away. They stay referenced while the page lives: static files are served
 * `max-age=0, must-revalidate`, and only an image still held in the page's memory cache is
 * reused without a round trip to revalidate it.
 */
export function usePreloadedImages(images: readonly ImageProps[]): void {
  const held = useRef<HTMLImageElement[]>([]);
  useEffect(() => {
    held.current = images.map((image) => {
      const { props } = getImageProps(image);
      const warm = new Image();
      warm.fetchPriority = "low";
      if (props.sizes !== undefined) warm.sizes = props.sizes;
      if (props.srcSet !== undefined) warm.srcset = props.srcSet;
      warm.src = props.src;
      return warm;
    });
    return () => {
      held.current = [];
    };
  }, [images]);
}
