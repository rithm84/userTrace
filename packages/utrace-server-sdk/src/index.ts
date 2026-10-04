/**
 * `@utrace/server-sdk` public surface.
 *
 * The typed annotation helpers may stay linked in production application code.
 * Without a prepared-preview activation they are inert: they install no hooks,
 * create no provider, open no connection, and export nothing, and product logic
 * must not depend on their result.
 */

export {
  attachEntityClass,
  attachSafeValue,
  classifyExpectedOutcome,
  recordWorkflowMilestone,
  withBackgroundRoot,
  withProductOperation,
} from './annotations/api.js';

export { register } from './register.js';
export { activateServerSdk, defaultOptions } from './sdk.js';
export type { ServerSdkHandle, ServerSdkOptions } from './sdk.js';

export { ENVIRONMENT_VARIABLES } from './environment.js';
export type { BootstrapEnvironment, ManifestTrustAnchor } from './environment.js';

export {
  FAILURE_CODES,
  LIFECYCLE_STATES,
  ServerSdkError,
} from './errors.js';
export type {
  FailureCode,
  LifecycleState,
  ReadinessConsequence,
  RetryAdvice,
  ServerSdkFailure,
} from './errors.js';

export { PREVIEW_ENTRY_HEADER, SDK_VERSION, CONTRACT_MAJOR } from './constants.js';

export type { ServerSdkHealthReport } from './contracts/health.js';
export type { ServerActivation } from './contracts/activation.js';
export type {
  ServerInstrumentationManifest,
  SignedServerInstrumentationManifest,
} from './contracts/manifest.js';
