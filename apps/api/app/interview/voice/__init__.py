"""The GPT-Live voice runtime.

Backend-authorized session setup, the durable sideband worker that persists
transcript fragments and answers client delegations, and the adapter that binds
a provider session to one `voice_run_id`.

The contract is [interview and voice](../../../../../docs/arch-components/interview-and-voice.md#webrtc-session-setup).
"""
