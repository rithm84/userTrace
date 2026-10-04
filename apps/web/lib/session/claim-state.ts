/** Form state for the invitation claim action; see `lib/auth/form-state.ts`. */

export type ClaimState = Readonly<{ status: "idle" | "error"; message: string | null }>;

export const initialClaimState: ClaimState = { status: "idle", message: null };
