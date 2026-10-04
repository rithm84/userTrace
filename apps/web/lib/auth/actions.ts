"use server";

import { redirect } from "next/navigation";
import { z } from "zod";

import { AUTH_CALLBACK_PATH, DASHBOARD_PATH, STAFF_SIGN_IN_PATH, USER_SETTINGS_PATH } from "@/lib/routes";
import { createSupabaseActionClient } from "@/lib/supabase/server";

import type { AuthFormState, DiscordSignInState } from "./form-state";
import { requestOrigin } from "./origin";

const emailSchema = z.email();
const codeSchema = z.string().regex(/^[0-9]{6}$/u);
const intentSchema = z.enum(["request_code", "verify_code"]);
/** Only in-application paths are accepted, so a crafted link cannot redirect off site. */
const nextPathSchema = z.string().regex(/^\/(?!\/)[A-Za-z0-9\-._~!$&'()*+,;=:@/%]*$/u);

function readNextPath(formData: FormData, whenAbsent: string): string {
  const parsed = nextPathSchema.safeParse(formData.get("next"));
  return parsed.success ? parsed.data : whenAbsent;
}

/**
 * Client staff sign-in with an emailed one-time code.
 *
 * `intent` says which step the form submitted, so the two steps never guess at
 * each other. New accounts are not created here: staff membership is an
 * uTrace-owned record, not something a sign-in form can grant.
 */
export async function staffSignIn(
  previous: AuthFormState,
  formData: FormData,
): Promise<AuthFormState> {
  const intent = intentSchema.safeParse(formData.get("intent"));
  if (!intent.success) {
    return { status: "error", email: previous.email, message: "That sign-in step was not recognised." };
  }

  const email = emailSchema.safeParse(formData.get("email") ?? previous.email);
  if (!email.success) {
    return { status: "error", email: null, message: "Enter the work email your uTrace membership uses." };
  }

  const supabase = await createSupabaseActionClient();
  const next = readNextPath(formData, DASHBOARD_PATH);

  if (intent.data === "request_code") {
    const origin = await requestOrigin();
    const { error } = await supabase.auth.signInWithOtp({
      email: email.data,
      options: {
        shouldCreateUser: false,
        emailRedirectTo: `${origin}${AUTH_CALLBACK_PATH}?next=${encodeURIComponent(next)}`,
      },
    });
    if (error !== null) {
      return { status: "error", email: email.data, message: error.message };
    }
    return { status: "code_sent", email: email.data, message: "Check your email for a six-digit code." };
  }

  const code = codeSchema.safeParse(formData.get("code"));
  if (!code.success) {
    return { status: "code_sent", email: email.data, message: "Enter the six-digit code from the email." };
  }

  const { error } = await supabase.auth.verifyOtp({ email: email.data, token: code.data, type: "email" });
  if (error !== null) {
    return { status: "code_sent", email: email.data, message: error.message };
  }
  redirect(next);
}

/**
 * Starts Discord sign-in for an interview participant.
 *
 * The PKCE verifier is written to a cookie here, on the server, and the
 * authorization URL is returned rather than redirected to: a server redirect
 * out of a form submission is a cross-origin form action, which this site's
 * Content-Security-Policy refuses. The browser navigates instead.
 */
export async function startDiscordSignIn(
  _previous: DiscordSignInState,
  formData: FormData,
): Promise<DiscordSignInState> {
  const supabase = await createSupabaseActionClient();
  const origin = await requestOrigin();
  const next = readNextPath(formData, USER_SETTINGS_PATH);
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: "discord",
    options: { redirectTo: `${origin}${AUTH_CALLBACK_PATH}?next=${encodeURIComponent(next)}` },
  });
  if (error !== null) {
    return { authorizeUrl: null, message: `Discord sign-in could not start: ${error.message}` };
  }
  return { authorizeUrl: data.url, message: null };
}

/** Ends the session on this browser and returns to staff sign-in. */
export async function signOut(): Promise<void> {
  const supabase = await createSupabaseActionClient();
  const { error } = await supabase.auth.signOut({ scope: "local" });
  if (error !== null) {
    throw new Error(`Sign-out failed: ${error.message}`);
  }
  redirect(STAFF_SIGN_IN_PATH);
}
