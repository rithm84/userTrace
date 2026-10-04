/**
 * Extension storage.
 *
 * Durable `chrome.storage.local` holds only installation identity, device-local
 * presentation settings, protocol metadata, and a cache of the control-plane
 * owned global preferences. `chrome.storage.session` holds the minimum
 * short-lived credential and recovery checkpoint needed across a service-worker
 * suspension. Neither is workflow truth, and neither keeps session content.
 */
import { z } from "zod";

import {
  parseWith,
  protocolVersionSchema,
  revisionSchema,
  timestampSchema,
  uuidSchema,
} from "../protocol/common";
import { DEFAULT_ORB_SETTINGS, orbSettingsSchema, type OrbSettings } from "../shared/portMessages";

const INSTALLATION_KEY = "installation";
/**
 * The orb key carries its record version: the diameter range is part of the
 * stored shape, so a record written for another range is never read.
 */
export const ORB_KEY = "orb.v2";
const PREFERENCES_KEY = "preferences";
const CHECKPOINT_KEY = "checkpoint";

export const installationRecordSchema = z.strictObject({
  installation_id: uuidSchema,
  account_link_state: z.enum(["linked", "revoked"]),
  negotiated_protocol_version: protocolVersionSchema,
  registered_at: timestampSchema,
});

export const preferencesCacheSchema = z.strictObject({
  preferences_revision: revisionSchema,
  visual_guidance_enabled: z.boolean(),
  agent_cursor_enabled: z.boolean(),
});

/**
 * What a resumed service worker needs to continue an encounter without
 * replaying effects: the acknowledged control sequence and the commands whose
 * outcome is still unknown.
 */
export const recoveryCheckpointSchema = z.strictObject({
  acknowledged_sequence: z.int().min(0),
  session_id: uuidSchema.optional(),
  extension_binding_id: uuidSchema.optional(),
  document_attachment_id: uuidSchema.optional(),
  tab_id: z.int().min(0).optional(),
  pending_command_ids: z.array(uuidSchema).max(32),
});

export type InstallationRecord = z.infer<typeof installationRecordSchema>;
export type PreferencesCache = z.infer<typeof preferencesCacheSchema>;
export type RecoveryCheckpoint = z.infer<typeof recoveryCheckpointSchema>;

export const EMPTY_CHECKPOINT: RecoveryCheckpoint = Object.freeze({
  acknowledged_sequence: 0,
  pending_command_ids: [],
});

export const DEFAULT_PREFERENCES: PreferencesCache = Object.freeze({
  preferences_revision: 0,
  visual_guidance_enabled: true,
  agent_cursor_enabled: true,
});

async function readLocal(key: string): Promise<unknown> {
  const stored = await chrome.storage.local.get(key);
  return stored[key];
}

async function readSession(key: string): Promise<unknown> {
  const stored = await chrome.storage.session.get(key);
  return stored[key];
}

export async function readInstallation(): Promise<InstallationRecord | null> {
  const stored = await readLocal(INSTALLATION_KEY);
  if (stored === undefined) {
    return null;
  }
  return parseWith(
    installationRecordSchema,
    stored,
    "invalid_installation_message",
    "The stored installation record is unusable",
  );
}

export async function writeInstallation(record: InstallationRecord): Promise<void> {
  await chrome.storage.local.set({ [INSTALLATION_KEY]: record });
}

export async function clearInstallation(): Promise<void> {
  await chrome.storage.local.remove(INSTALLATION_KEY);
}

export async function readOrbSettings(): Promise<OrbSettings> {
  const stored = await readLocal(ORB_KEY);
  if (stored === undefined) {
    return DEFAULT_ORB_SETTINGS;
  }
  return parseWith(
    orbSettingsSchema,
    stored,
    "invalid_bridge_frame",
    "The stored orb settings are unusable",
  );
}

export async function writeOrbSettings(settings: OrbSettings): Promise<void> {
  await chrome.storage.local.set({ [ORB_KEY]: settings });
}

export async function readPreferences(): Promise<PreferencesCache> {
  const stored = await readLocal(PREFERENCES_KEY);
  if (stored === undefined) {
    return DEFAULT_PREFERENCES;
  }
  return parseWith(
    preferencesCacheSchema,
    stored,
    "invalid_inbound_frame",
    "The cached global preferences are unusable",
  );
}

export async function writePreferences(preferences: PreferencesCache): Promise<void> {
  await chrome.storage.local.set({ [PREFERENCES_KEY]: preferences });
}

export async function readCheckpoint(): Promise<RecoveryCheckpoint> {
  const stored = await readSession(CHECKPOINT_KEY);
  if (stored === undefined) {
    return EMPTY_CHECKPOINT;
  }
  return parseWith(
    recoveryCheckpointSchema,
    stored,
    "invalid_inbound_frame",
    "The stored recovery checkpoint is unusable",
  );
}

export async function writeCheckpoint(checkpoint: RecoveryCheckpoint): Promise<void> {
  await chrome.storage.session.set({ [CHECKPOINT_KEY]: checkpoint });
}

export async function clearSessionState(): Promise<void> {
  await chrome.storage.session.clear();
}
