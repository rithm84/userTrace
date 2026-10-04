/**
 * Real Postgres for the `pg` adapter tests.
 *
 * The connection string comes from the worktree's `.env.local`; it is never
 * logged or written anywhere. Only the workstream's own
 * `utrace_server_sdk_test` schema is created or modified.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { Client } from 'pg';

/** The schema this workstream owns. */
export const TEST_SCHEMA = 'utrace_server_sdk_test';

/** Table the product path reads during the tests. */
export const TEST_TABLE = 'saved_views';

function repositoryRoot(): string {
  return join(import.meta.dirname, '..', '..', '..', '..');
}

/** Reads `UTRACE_DATABASE_URL` from the worktree's `.env.local`. */
export function databaseUrl(): string {
  const path = join(repositoryRoot(), '.env.local');
  const contents = readFileSync(path, 'utf8');
  for (const line of contents.split('\n')) {
    const separator = line.indexOf('=');
    if (separator === -1) {
      continue;
    }
    if (line.slice(0, separator).trim() !== 'UTRACE_DATABASE_URL') {
      continue;
    }
    const value = line.slice(separator + 1).trim().replace(/^["']|["']$/gu, '');
    if (value === '') {
      break;
    }
    return value;
  }
  throw new Error(`UTRACE_DATABASE_URL is not set in ${path}`);
}

/** The same URL with the session search path pinned to the test schema. */
export function scopedDatabaseUrl(): string {
  const url = new URL(databaseUrl());
  url.searchParams.set('options', `-c search_path=${TEST_SCHEMA}`);
  return url.toString();
}

/** Creates the workstream's schema and its one table, idempotently. */
export async function prepareSchema(): Promise<void> {
  const client = new Client({ connectionString: databaseUrl() });
  await client.connect();
  try {
    await client.query(`CREATE SCHEMA IF NOT EXISTS ${TEST_SCHEMA}`);
    await client.query(
      `CREATE TABLE IF NOT EXISTS ${TEST_SCHEMA}.${TEST_TABLE} (
         view_id integer PRIMARY KEY,
         label text NOT NULL
       )`,
    );
    await client.query(
      `INSERT INTO ${TEST_SCHEMA}.${TEST_TABLE} (view_id, label)
       VALUES (1, 'tiktok-women-25-34')
       ON CONFLICT (view_id) DO NOTHING`,
    );
  } finally {
    await client.end();
  }
}
