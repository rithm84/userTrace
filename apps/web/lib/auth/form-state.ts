/**
 * Form state for the sign-in actions. A `"use server"` module may only export
 * async functions, so the shapes and their initial values live here.
 */

export type AuthFormState = Readonly<{
  status: "idle" | "code_sent" | "error";
  email: string | null;
  message: string | null;
}>;

export const initialAuthFormState: AuthFormState = { status: "idle", email: null, message: null };

export type DiscordSignInState = Readonly<{ authorizeUrl: string | null; message: string | null }>;

export const initialDiscordSignInState: DiscordSignInState = { authorizeUrl: null, message: null };
