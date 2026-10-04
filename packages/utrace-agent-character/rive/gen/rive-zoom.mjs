// Render one state of a .riv at 1000 px for close inspection.
import { chromium } from "playwright-core";
import fs from "node:fs"; import path from "node:path";
const [riv, state, ms, out] = process.argv.slice(2); const RT = "/Users/rithvik/uTrace/apps/web/node_modules/@rive-app/webgl2";
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1000, height: 1000 }, deviceScaleFactor: 1 });
await page.route("http://rivetest.local/**", (route) => { const p = new URL(route.request().url()).pathname;
  const map = { "/": `<!doctype html><html><body style="margin:0;background:#fff"><canvas id="c" width="1000" height="1000" style="width:1000px;height:1000px;display:block"></canvas><script src="/rive.js"></script></body></html>`,
    "/rive.js": fs.readFileSync(path.join(RT, "rive.js")), "/rive.wasm": fs.readFileSync(path.join(RT, "rive.wasm")), "/char.riv": fs.readFileSync(riv) };
  if (!(p in map)) return route.fulfill({ status: 404 });
  route.fulfill({ status: 200, contentType: p.endsWith(".js") ? "text/javascript" : p.endsWith(".wasm") ? "application/wasm" : p === "/" ? "text/html" : "application/octet-stream", body: map[p] }); });
await page.goto("http://rivetest.local/");
await page.evaluate(() => new Promise((resolve) => { rive.RuntimeLoader.setWasmUrl("/rive.wasm");
  window.r = new rive.Rive({ src: "/char.riv", canvas: document.getElementById("c"), artboard: "AgentCharacter", stateMachines: "AgentCharacterMachine", autoplay: true, autoBind: true,
    layout: new rive.Layout({ fit: rive.Fit.Contain, alignment: rive.Alignment.Center }), onLoad: () => { window.r.resizeDrawingSurfaceToCanvas(); resolve(); } }); }));
await page.waitForTimeout(300);
await page.evaluate((s) => { const vmi = window.r.viewModelInstance; const b = vmi.boolean(s); if (b) b.value = true; else vmi.enum("state").value = s; }, state);
await page.waitForTimeout(parseInt(ms));
await page.locator("#c").screenshot({ path: out });
await browser.close();
