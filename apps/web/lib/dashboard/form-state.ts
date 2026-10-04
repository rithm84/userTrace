/** Form state for the dashboard actions; see `lib/auth/form-state.ts` for why. */

export type StopCaseState = Readonly<{ status: "idle" | "error"; message: string | null }>;

export const initialStopCaseState: StopCaseState = { status: "idle", message: null };

export type BudgetState = Readonly<{ status: "idle" | "error" | "saved"; message: string | null }>;

export const initialBudgetState: BudgetState = { status: "idle", message: null };
