#!/usr/bin/env node
/*
Render an HTML mock to PNG and audit what the browser actually drew.

usage:
  node render.cjs PAGE OUT.png [--size 1920x1080] [--scale 1] [--wait 600]
                  [--frames 0,250,500] [--state name=js ...] [--no-audit]

  PAGE      local .html path or http(s) URL
  --size    viewport in CSS px (default 1920x1080, the user's screen)
  --scale   device scale factor; 2 gives the "2x" detail check
  --wait    ms to wait after load before the first shot (fonts, images)
  --frames  extra shots at these ms after the first one -> OUT-f0250.png ...
            (animations driven by CSS/JS clocks; shot times are approximate)
  --state   run this JS in the page and shoot again -> OUT-<name>.png
            e.g. --state hover="document.body.classList.add('hover')"

Writes OUT.png and OUT.audit.json (unless --no-audit) and prints a short report:
  fonts actually used per text node (catches silent fallback to system fonts),
  font-size steps and jump ratio, distinct radii, shadows, boxed elements,
  uppercase tracked labels, pure black/white. These are smells, not verdicts.

Browser: playwright (or playwright-core) from ./node_modules, the design-lab
folder, or the global npm root; Chromium from PLAYWRIGHT_BROWSERS_PATH, else Edge.
*/
const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");

function loadPlaywright() {
  const roots = [process.cwd(), path.resolve(__dirname, "../../../../design-lab"), __dirname];
  try { roots.push(execSync("npm root -g", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim()); } catch {}
  for (const name of ["playwright", "playwright-core"]) {
    for (const r of roots) {
      try { return require(require.resolve(name, { paths: [r] })); } catch {}
    }
  }
  console.error("playwright not found: run design-lab/setup.sh (or setup.ps1)");
  process.exit(2);
}

function parse(argv) {
  const o = { size: "1920x1080", scale: 1, wait: 600, frames: [], states: [], audit: true };
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--size") o.size = argv[++i];
    else if (a === "--scale") o.scale = parseFloat(argv[++i]);
    else if (a === "--wait") o.wait = parseInt(argv[++i], 10);
    else if (a === "--frames") o.frames = argv[++i].split(",").map(Number);
    else if (a === "--state") { const s = argv[++i]; const k = s.indexOf("="); o.states.push([s.slice(0, k), s.slice(k + 1)]); }
    else if (a === "--no-audit") o.audit = false;
    else pos.push(a);
  }
  if (pos.length < 2) { console.error(fs.readFileSync(__filename, "utf8").split("*/")[0]); process.exit(1); }
  [o.page, o.out] = pos;
  const [w, h] = o.size.split("x").map(Number);
  o.w = w; o.h = h;
  return o;
}

async function launch(chromium) {
  try { return await chromium.launch(); } catch (e1) {
    try { return await chromium.launch({ channel: "msedge" }); } catch { throw e1; }
  }
}

// Runs in the page: collect style facts about every visible element.
function collect() {
  const els = [...document.querySelectorAll("body *")].filter(e => {
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity > 0;
  });
  const text = [], radii = {}, shadows = {}, colors = {}, boxed = [], caps = [];
  let id = 0;
  for (const e of els) {
    const cs = getComputedStyle(e);
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own) {
      e.setAttribute("data-audit", String(id++));
      const t = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join("").trim();
      text.push({ id: id - 1, size: parseFloat(cs.fontSize), weight: cs.fontWeight, family: cs.fontFamily, color: cs.color, text: t.slice(0, 24) });
      if (cs.textTransform === "uppercase" || (/^[A-Z0-9 &·\-/]{3,}$/.test(t) && /[A-Z]/.test(t)))
        if (parseFloat(cs.letterSpacing) > 0.5 || parseFloat(cs.fontSize) <= 13) caps.push(t.slice(0, 24));
      colors[cs.color] = (colors[cs.color] || 0) + 1;
    }
    const r = cs.borderTopLeftRadius;
    if (r && r !== "0px") radii[r] = (radii[r] || 0) + 1;
    if (cs.boxShadow && cs.boxShadow !== "none") shadows[cs.boxShadow] = (shadows[cs.boxShadow] || 0) + 1;
    const bg = cs.backgroundColor !== "rgba(0, 0, 0, 0)" || cs.backgroundImage !== "none";
    const border = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== "none";
    const rect = e.getBoundingClientRect();
    if ((bg || border) && r !== "0px" && rect.width < innerWidth * 0.9 && rect.height > 24) boxed.push(e.tagName.toLowerCase());
    for (const c of [cs.backgroundColor, cs.color]) colors[c] = colors[c] || 0;
  }
  return { text, radii, shadows, colors, boxed: boxed.length, caps, elements: els.length };
}

function report(a, fonts) {
  const lines = [];
  const sizes = [...new Set(a.text.map(t => Math.round(t.size)))].sort((x, y) => x - y);
  const counts = {};
  for (const t of a.text) counts[Math.round(t.size)] = (counts[Math.round(t.size)] || 0) + t.text.length;
  const body = +Object.entries(counts).sort((x, y) => y[1] - x[1])[0]?.[0] || 0;
  const max = sizes[sizes.length - 1] || 0;
  lines.push(`type: ${sizes.length} sizes [${sizes.join(" ")}], body≈${body}, jump ${(max / (body || 1)).toFixed(1)}`);
  const tight = sizes.filter((s, i) => i && s / sizes[i - 1] < 1.15 && s >= body);
  if (tight.length) lines.push(`  smell: neighbouring sizes closer than 1.15x above body: ${tight.join(" ")}`);
  const radii = Object.entries(a.radii).sort((x, y) => y[1] - x[1]);
  lines.push(`radii: ${radii.map(([r, n]) => `${r}×${n}`).join("  ") || "none"}`);
  if (radii.length === 1 && radii[0][1] >= 3) lines.push("  smell: one radius on every box (card kit)");
  const sh = Object.entries(a.shadows);
  for (const [s, n] of sh) {
    const m = s.match(/rgba?\(([^)]+)\)\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px/);
    if (!m) continue;
    const [r, g, b] = m[1].split(",").map(Number);
    const grey = Math.max(r, g, b) - Math.min(r, g, b) < 8;
    const [x, y, blur] = [+m[2], +m[3], +m[4]];
    const tags = [];
    if (blur >= 16) tags.push("large blur");
    if (Math.abs(x) + Math.abs(y) < 1 && blur > 0) tags.push("no offset (glow)");
    if (grey) tags.push("grey");
    lines.push(`shadow ×${n}: ${s.slice(0, 60)}${tags.length ? "  smell: " + tags.join(", ") : ""}`);
  }
  lines.push(`boxed elements (bg/border + radius): ${a.boxed}`);
  if (a.caps.length) lines.push(`  smell: uppercase/tracked labels: ${[...new Set(a.caps)].slice(0, 8).join(" | ")}`);
  const pure = Object.keys(a.colors).filter(c => c === "rgb(0, 0, 0)" || c === "rgb(255, 255, 255)");
  if (pure.length) lines.push(`  note: pure ${pure.join(" / ")} in use (neutrals usually carry a hue)`);
  const fam = Object.entries(fonts).sort((x, y) => y[1] - x[1]);
  lines.push(`fonts drawn: ${fam.map(([f, n]) => `${f} (${n})`).join(", ") || "?"}`);
  const fallback = fam.filter(([f]) => /WenQuanYi|DejaVu|Liberation|Unifont|Microsoft YaHei|Segoe UI|Arial|SimSun|Noto Sans CJK|Noto Serif CJK/i.test(f));
  if (fallback.length) lines.push(`  smell: system/fallback fonts drawn: ${fallback.map(f => f[0]).join(", ")} — check font-family and glyph coverage`);
  const common = fam.filter(([f]) => /^(Inter|Poppins|Roboto|Space Grotesk|DM Sans|Playfair Display|Fraunces|Montserrat|Open Sans|Lato)$/i.test(f));
  if (common.length) lines.push(`  note: very common web fonts: ${common.map(f => f[0]).join(", ")} — fine only if chosen on purpose`);
  return lines.join("\n");
}

(async () => {
  const o = parse(process.argv.slice(2));
  const { chromium } = loadPlaywright();
  const browser = await launch(chromium);
  const page = await browser.newPage({ viewport: { width: o.w, height: o.h }, deviceScaleFactor: o.scale });
  const url = /^(https?|file):/.test(o.page) ? o.page : "file://" + path.resolve(o.page);
  await page.goto(url, { waitUntil: "load" });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.waitForTimeout(o.wait);
  const base = o.out.replace(/\.png$/i, "");
  await page.screenshot({ path: base + ".png" });
  console.log(`rendered ${base}.png (${o.w}x${o.h} @${o.scale}x)`);

  if (o.audit) {
    const a = await page.evaluate(collect);
    const fonts = {};
    try {
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("DOM.enable"); await cdp.send("CSS.enable");
      const { root } = await cdp.send("DOM.getDocument", { depth: -1 });
      const { nodeIds } = await cdp.send("DOM.querySelectorAll", { nodeId: root.nodeId, selector: "[data-audit]" });
      for (const nodeId of nodeIds.slice(0, 400)) {
        const { fonts: f } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
        for (const x of f) fonts[x.familyName] = (fonts[x.familyName] || 0) + x.glyphCount;
      }
    } catch (e) { fonts["(font probe failed: " + e.message.slice(0, 40) + ")"] = 0; }
    fs.writeFileSync(base + ".audit.json", JSON.stringify({ ...a, fontsDrawn: fonts }, null, 1));
    console.log(report(a, fonts));
  }

  let last = 0;
  for (const t of o.frames) {
    await page.waitForTimeout(Math.max(0, t - last)); last = t;
    const p = `${base}-f${String(t).padStart(4, "0")}.png`;
    await page.screenshot({ path: p }); console.log(`frame ${p}`);
  }
  for (const [name, js] of o.states) {
    await page.evaluate(js); await page.waitForTimeout(250);
    const p = `${base}-${name}.png`;
    await page.screenshot({ path: p }); console.log(`state ${p}`);
  }
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
