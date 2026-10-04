// Play one linear animation directly (no state machine) and report per-frame dark-pixel rows to time eye changes.
import { chromium } from "playwright-core";
import fs from "node:fs"; import path from "node:path";
const [riv, anim, mode] = process.argv.slice(2); const RT = process.env.RIVE_RT || path.join(path.dirname(new URL(import.meta.url).pathname), "rt");
const browser = await chromium.launch(); const page = await browser.newPage({ viewport: { width: 320, height: 320 } });
await page.route("http://rivetest.local/**", (route) => { const p = new URL(route.request().url()).pathname;
  const map = { "/": `<!doctype html><html><body style="margin:0;background:#fff"><canvas id="c" width="300" height="300"></canvas><script src="/rive.js"></script></body></html>`,
    "/rive.js": fs.readFileSync(path.join(RT, "rive-canvas.js")), "/rive.wasm": fs.readFileSync(path.join(RT, "rive-canvas.wasm")), "/char.riv": fs.readFileSync(riv) };
  if (!(p in map)) return route.fulfill({ status: 404 });
  route.fulfill({ status: 200, contentType: p.endsWith(".js") ? "text/javascript" : p.endsWith(".wasm") ? "application/wasm" : p === "/" ? "text/html" : "application/octet-stream", body: map[p] }); });
await page.goto("http://rivetest.local/");
const rows = await page.evaluate((anim) => new Promise((resolve) => { rive.RuntimeLoader.setWasmUrl("/rive.wasm");
  const c = document.getElementById("c"); const out = [];
  const sm = anim.startsWith("sm:"); const name = anim.replace("sm:", "");
  const r = new rive.Rive({ src: "/char.riv", canvas: c, artboard: "AgentCharacter", ...(sm ? { stateMachines: "AgentCharacterMachine", autoBind: true } : { animations: [name] }), autoplay: true, layout: new rive.Layout({ fit: rive.Fit.Contain }),
    onLoad: () => { r.resizeDrawingSurfaceToCanvas(); if (sm) { setTimeout(() => { r.viewModelInstance.enum("state").value = name; start(); }, 700); } else start();
    function start() { const t0 = performance.now();
      function tick() { const t = performance.now() - t0; const ctx = c.getContext("2d"); const d = ctx.getImageData(0, 0, 300, 300).data; let dark = 0, top = 300, bottom = 0;
        for (let y = 0; y < 300; y++) for (let x = 0; x < 300; x++) { const i = (y * 300 + x) * 4; if (d[i + 3] > 100 && d[i] < 60) { dark++; if (y < top) top = y; if (y > bottom) bottom = y; } }
        out.push([Math.round(t), dark, top, bottom]); if (t < 1300) requestAnimationFrame(tick); else resolve(out); }
      requestAnimationFrame(tick); } } }); }), anim);
let last = null; for (const [t, dark, top, bottom] of rows) { const sig = `${Math.round(dark / 200)}:${Math.round(top / 10)}:${Math.round(bottom / 10)}`; if (sig !== last) { console.log(t + "ms dark=" + dark + " rows " + top + "-" + bottom); last = sig; } }
await browser.close();
