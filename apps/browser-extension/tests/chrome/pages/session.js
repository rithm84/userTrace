/**
 * A stand-in for the uTrace session tab. It speaks the two messages the real
 * session tab sends over externally connectable messaging, which is the only
 * path either may take: the participant compatibility preflight and the
 * single-use binding offer.
 */
(() => {
  window.__utraceSession = { answer: null, preflight: null };

  const call = (extensionId, message) =>
    new Promise((resolve) => {
      chrome.runtime.sendMessage(extensionId, message, (answer) => {
        resolve(answer ?? { type: "utrace_binding_offer_ack", accepted: false, detail: "no_answer" });
      });
    });

  window.runPreflight = async (extensionId) => {
    const answer = await call(extensionId, {
      type: "utrace_preflight_request",
      protocol_version: "1",
    });
    window.__utraceSession.preflight = answer;
    return answer;
  };

  window.sendBindingOffer = async (extensionId, offer) => {
    const answer = await call(extensionId, {
      type: "utrace_binding_offer",
      session_id: offer.session_id,
      binding_offer: offer,
    });
    window.__utraceSession.answer = answer;
    document.getElementById("state").textContent = JSON.stringify(answer);
    return answer;
  };
})();
