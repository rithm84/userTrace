/**
 * The shape every session Server Action returns.
 *
 * A control-plane refusal is an answer the participant needs to see, so it
 * comes back as a message rather than a thrown digest. Programming faults still
 * throw and reach the error boundary.
 */
export type ActionResult<Value> =
  | Readonly<{ ok: true; value: Value }>
  | Readonly<{ ok: false; message: string }>;
