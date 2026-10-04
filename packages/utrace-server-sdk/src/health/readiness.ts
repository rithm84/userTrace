/**
 * Readiness evaluation for one server service.
 *
 * `ready` requires valid credentials; compatible SDK, Node, manifest, policy,
 * and schema versions; one provider and one hook per library; valid resource
 * identity; every required adapter present; an active observation grant; a
 * healthy control connection and export queue; and an acknowledged end-to-end
 * propagation probe. Every required service reports this independently.
 */

/** The facts a readiness decision is made from. */
export interface ReadinessInputs {
  readonly activationBound: boolean;
  readonly credentialValid: boolean;
  readonly providerInstalled: boolean;
  readonly missingRequiredAdapters: number;
  readonly controlConnected: boolean;
  readonly bufferAboveHighWater: boolean;
  readonly probePassed: boolean;
  readonly openFailures: number;
  readonly paused: boolean;
}

/** The decision plus every blocker, in evaluation order. */
export interface ReadinessDecision {
  readonly ready: boolean;
  readonly blockers: readonly string[];
}

/** Evaluates readiness; an empty blocker list means ready. */
export function evaluateReadiness(inputs: ReadinessInputs): ReadinessDecision {
  const blockers: string[] = [];
  if (!inputs.activationBound) {
    blockers.push('activation_not_bound');
  }
  if (!inputs.credentialValid) {
    blockers.push('credential_expired');
  }
  if (!inputs.providerInstalled) {
    blockers.push('provider_not_installed');
  }
  if (inputs.missingRequiredAdapters > 0) {
    blockers.push('required_adapter_missing');
  }
  if (!inputs.controlConnected) {
    blockers.push('control_connection_unhealthy');
  }
  if (inputs.bufferAboveHighWater) {
    blockers.push('export_queue_above_high_water');
  }
  if (!inputs.probePassed) {
    blockers.push('propagation_probe_incomplete');
  }
  if (inputs.openFailures > 0) {
    blockers.push('open_failures');
  }
  if (inputs.paused) {
    blockers.push('paused_by_control_plane');
  }
  return { ready: blockers.length === 0, blockers };
}
