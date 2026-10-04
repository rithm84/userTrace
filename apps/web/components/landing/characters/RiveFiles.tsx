"use client";

import { useRiveFile } from "@rive-app/react-webgl2";
import type { RiveFile } from "@rive-app/webgl2";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type JSX, type ReactNode } from "react";

/** How many times a rig download is tried before its failure is raised, and the first backoff. */
const RIG_FETCH_ATTEMPTS = 4;
const RIG_FETCH_BACKOFF_MS = 500;
/** Statuses a later attempt can succeed past: a timeout, rate limiting, or a server error. */
const TRANSIENT_STATUS = (status: number): boolean => status === 408 || status === 429 || status >= 500;

class RigFetchError extends Error {
  readonly transient: boolean;

  constructor(message: string, transient: boolean) {
    super(message);
    this.name = "RigFetchError";
    this.transient = transient;
  }
}

/**
 * Fetches a rig, retrying a dropped connection or a transient server answer with a warning and
 * a doubling backoff, then raising the last failure. A flaky network must not take the page
 * down on one lost request; a missing or corrupt file fails at once.
 */
async function fetchRig(src: string): Promise<ArrayBuffer> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fetchRigOnce(src);
    } catch (error) {
      // fetch reports a lost connection as a TypeError
      const transient = error instanceof TypeError || (error instanceof RigFetchError && error.transient);
      if (!transient || attempt >= RIG_FETCH_ATTEMPTS) throw error;
      const delayMs = RIG_FETCH_BACKOFF_MS * 2 ** (attempt - 1);
      console.warn("landing_rig_fetch_retrying", { src, attempt, delayMs, error: String(error) });
      await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    }
  }
}

/**
 * Fetches a rig once as its gzipped copy (`<src>.gz`, written by the asset sync) and inflates it:
 * static hosts send .riv uncompressed, and gzipped the rigs are about a fifth of the size.
 */
async function fetchRigOnce(src: string): Promise<ArrayBuffer> {
  const url = `${src}.gz`;
  const response = await fetch(url);
  if (!response.ok) throw new RigFetchError(`Could not fetch the landing character file ${url}: HTTP ${response.status} ${response.statusText}.`, TRANSIENT_STATUS(response.status));
  const packed = new Uint8Array(await response.arrayBuffer());
  if (packed[0] !== 0x1f || packed[1] !== 0x8b) throw new Error(`The landing character file ${url} is not gzip data (first bytes ${packed[0]}, ${packed[1]}); the host may have decoded it.`);
  const bytes = await new Response(new Blob([packed]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
  if (new TextDecoder().decode(bytes.slice(0, 4)) !== "RIVE") throw new Error(`The landing character file ${url} does not inflate to a Rive file.`);
  return bytes;
}

type FileCache = Readonly<{
  files: ReadonlyMap<string, RiveFile>;
  request: (src: string) => void;
}>;
const Files = createContext<FileCache | null>(null);

/** Each requested file has one hook-owned reference for the landing page's lifetime. */
export function RiveFilesProvider(props: Readonly<{ children: ReactNode }>): JSX.Element {
  const [sources, setSources] = useState<readonly string[]>([]);
  const [files, setFiles] = useState<ReadonlyMap<string, RiveFile>>(() => new Map());
  const request = useCallback((src: string): void => {
    setSources((previous) => previous.includes(src) ? previous : [...previous, src]);
  }, []);
  const loaded = useCallback((src: string, file: RiveFile): void => {
    setFiles((previous) => previous.get(src) === file ? previous : new Map(previous).set(src, file));
  }, []);
  const cache = useMemo(() => ({ files, request }), [files, request]);
  return <Files.Provider value={cache}>
    {sources.map((src) => <FileLoader key={src} src={src} loaded={loaded} />)}
    {props.children}
  </Files.Provider>;
}

function FileLoader(props: Readonly<{ src: string; loaded: (src: string, file: RiveFile) => void }>): JSX.Element | null {
  const [bytes, setBytes] = useState<ArrayBuffer | null>(null);
  const [failure, setFailure] = useState<Error | null>(null);
  const { src } = props;
  useEffect(() => {
    let current = true;
    fetchRig(src).then((buffer) => { if (current) setBytes(buffer); }, (error: Error) => { if (current) setFailure(error); });
    return () => { current = false; };
  }, [src]);
  if (failure !== null) throw failure;
  return bytes !== null ? <FileParser src={src} buffer={bytes} loaded={props.loaded} /> : null;
}

function FileParser(props: Readonly<{ src: string; buffer: ArrayBuffer; loaded: (src: string, file: RiveFile) => void }>): null {
  const { riveFile, status } = useRiveFile({ buffer: props.buffer, enableRiveAssetCDN: false });
  const { src, loaded } = props;
  useEffect(() => {
    if (status === "success" && riveFile !== null) loaded(src, riveFile);
  }, [status, riveFile, src, loaded]);
  if (status === "failed") throw new Error(`Could not load the shared landing character file: ${src}.`);
  return null;
}

/** Share parsed vectors, while useRive creates an independent artboard and view model. */
export function useLandingRiveFile(src: string, enabled: boolean): RiveFile | null {
  const cache = useContext(Files);
  const request = cache?.request;
  useEffect(() => {
    if (!enabled) return;
    if (request === undefined) throw new Error(`RiveFilesProvider is required to load ${src}.`);
    request(src);
  }, [enabled, request, src]);
  return enabled ? cache?.files.get(src) ?? null : null;
}
