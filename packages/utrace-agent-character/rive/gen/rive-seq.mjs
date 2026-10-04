// Capture state transitions of an AgentCharacter .riv at ~30 fps with the canvas runtime (in-page rAF capture, no round trips).
import { chromium } from "playwright-core";
import fs from "node:fs"; import path from "node:path";
const [riv, outDir, statesArg] = process.argv.slice(2);
const RT = process.env.RIVE_RT || path.join(path.dirname(new URL(import.meta.url).pathname), "rt");   // @rive-app/canvas rive.js + rive.wasm
fs.mkdirSync(outDir, { recursive: true });
const STATES = (statesArg || "investigating,listening,thinking,speaking,acknowledgement,concern,building,completion,ship,reconnecting,paused,sleep,pointer").split(",");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 320, height: 320 }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.log("pageerror:", e.message));
await page.route("http://rivetest.local/**", (route) => { const p = new URL(route.request().url()).pathname;
  const map = { "/": `<!doctype html><html><body style="margin:0;background:#fff"><canvas id="c" width="300" height="300" style="width:300px;height:300px;display:block"></canvas><script src="/rive.js"></script></body></html>`,
    "/rive.js": fs.readFileSync(path.join(RT, "rive-canvas.js")), "/rive.wasm": fs.readFileSync(path.join(RT, "rive-canvas.wasm")), "/char.riv": fs.readFileSync(riv) };
  if (!(p in map)) return route.fulfill({ status: 404 });
  route.fulfill({ status: 200, contentType: p.endsWith(".js") ? "text/javascript" : p.endsWith(".wasm") ? "application/wasm" : p === "/" ? "text/html" : "application/octet-stream", body: map[p] }); });
await page.goto("http://rivetest.local/");
const loaded = await page.evaluate(() => new Promise((resolve) => { rive.RuntimeLoader.setWasmUrl("/rive.wasm");
  window.r = new rive.Rive({ src: "/char.riv", canvas: document.getElementById("c"), artboard: "AgentCharacter", stateMachines: "AgentCharacterMachine", autoplay: true, autoBind: true,
    layout: new rive.Layout({ fit: rive.Fit.Contain, alignment: rive.Alignment.Center }), onLoad: () => { window.r.resizeDrawingSurfaceToCanvas(); resolve({ ok: true, values: window.r.viewModelInstance.enum("state").values }); }, onLoadError: (e) => resolve({ ok: false, e: String(e) }) }); }));
console.log("loaded", JSON.stringify(loaded));
for (const s of STATES) {
  const frames = await page.evaluate(async (state) => {
    const vmi = window.r.viewModelInstance; const c = document.getElementById("c");
    vmi.enum("state").value = "idle"; await new Promise((res) => setTimeout(res, 900));
    const out = []; const off = document.createElement("canvas"); off.width = 300; off.height = 300; const octx = off.getContext("2d"); const t0 = performance.now(); let phase = state; vmi.enum("state").value = state;
    await new Promise((done) => { let last = -1; function tick() { const t = performance.now() - t0; if (t > 1700 && phase === state) { phase = "idle"; vmi.enum("state").value = "idle"; }
      const slot = Math.floor(t / 33.3); if (slot !== last) { last = slot; octx.fillStyle = "#fff"; octx.fillRect(0, 0, 300, 300); octx.drawImage(c, 0, 0); out.push([Math.round(t), off.toDataURL("image/jpeg", 0.85)]); }
      if (t < 2700) requestAnimationFrame(tick); else done(); } requestAnimationFrame(tick); });
    return out; }, s);
  frames.forEach(([t, url], i) => fs.writeFileSync(path.join(outDir, `${s}-${String(i).padStart(3, "0")}-${t}.jpg`), Buffer.from(url.split(",")[1], "base64")));
  console.log(s, frames.length, "frames");
}
await browser.close();
