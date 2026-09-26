/**
 * Download the webfonts this app uses into src/app/fonts/.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THE FONTS ARE IN THE REPOSITORY RATHER THAN FETCHED BY next/font/google.
 *
 * `next/font/google` self-hosts the files, but it downloads them DURING THE
 * BUILD. That puts fonts.googleapis.com on the critical path of
 * `docker build` — so a transient failure at Google fails the image build
 * with "An error occurred in `next/font`" while nothing about the
 * application is wrong.
 *
 * That is not hypothetical. On 2026-09-26 it failed business-os CI on a
 * commit that changed one SVG path, and then failed that deploy on the
 * server minutes later; both went green on a retry that changed nothing. A
 * deploy somebody else's CDN can break is not a deploy we control.
 *
 * Run this only to add a face, a weight or an axis. Nothing in the build
 * calls it.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *     node scripts/vendor-fonts.mjs
 *
 * ── VARIABLE FAMILIES ARE ONE FILE, NOT ONE PER WEIGHT ─────────────────────
 *
 * Google's css2 API emits a separate @font-face per requested weight even
 * when the family is variable — and for a variable family all of those
 * blocks point at the SAME woff2. Saving one copy per weight writes
 * byte-identical files and makes the browser fetch the same font several
 * times under several hashed names, because nothing downstream knows they
 * are the same.
 *
 * So downloads are deduplicated by URL. A family whose weights collapse to
 * one file is written once as `<family>-variable.woff2` and declared with a
 * weight RANGE; a family with distinct URLs keeps one file per weight.
 *
 * ── AXES ARE NOT OPTIONAL EXTRAS, THEY CHANGE WHICH FILE YOU GET ───────────
 *
 * Asking for `Fraunces:wght@100..900` returns a 36 KB file. Asking for
 * `Fraunces:opsz,wght,SOFT,WONK@...` returns a different, 121 KB file that
 * actually carries the design space. Vendor the first and every
 * `font-variation-settings` in the stylesheet silently does nothing.
 *
 * Axis names go in the query lowercase-alphabetical first, then
 * uppercase-alphabetical — that is the API's rule, and getting it wrong is a
 * 400 rather than a wrong file, which is at least loud.
 *
 * LICENSING. Every family here is under the SIL Open Font License, which
 * permits redistribution including bundled in an application, and requires
 * the licence travel with the files. The `*-OFL.txt` files are the upstream
 * texts, unmodified, and must not be deleted.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

/** Exactly what src/app/layout.tsx asks for. Keep the two in step. */
const WANTED = [{ family: "Jost", weights: [300, 400, 500] }];

// Google serves woff2 only to a UA it believes supports it. Ask as a current
// Chrome or the CSS comes back full of truetype URLs.
const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

const OUT = path.resolve("src/app/fonts");
mkdirSync(OUT, { recursive: true });

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const get = async (url, as) => {
  const r = await fetch(url, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return as === "buf" ? Buffer.from(await r.arrayBuffer()) : r.text();
};

/** The API's ordering: lowercase axes alphabetically, then uppercase. */
const axisOrder = (a, b) => {
  const lowerA = a[0] === a[0].toLowerCase();
  const lowerB = b[0] === b[0].toLowerCase();
  if (lowerA !== lowerB) return lowerA ? -1 : 1;
  return a < b ? -1 : 1;
};

const latinBlocks = (css) =>
  css.split("/* ").slice(1).filter((b) => b.startsWith("latin */"));
const woff2In = (block) => block.match(/url\((https:\/\/[^)]+\.woff2)\)/)?.[1];

const manifest = [];

for (const want of WANTED) {
  const { family } = want;

  if (want.axes) {
    const names = Object.keys(want.axes).sort(axisOrder);
    const spec = `${names.join(",")}@${names.map((n) => want.axes[n]).join(",")}`;
    const css = await get(
      `https://fonts.googleapis.com/css2?family=${encodeURIComponent(family)}:${spec}&display=swap`,
    );
    const block = latinBlocks(css)[0];
    if (!block) throw new Error(`${family}: no latin block`);
    const url = woff2In(block);
    if (!url) throw new Error(`${family}: no woff2 url`);

    // Whatever range the served face actually declares, not what we guessed.
    const weight = block.match(/font-weight:\s*([0-9]+(?:\s+[0-9]+)?)/)?.[1] ?? "400";
    const name = `${slug(family)}-variable.woff2`;
    const bytes = await get(url, "buf");
    writeFileSync(path.join(OUT, name), bytes);
    manifest.push({ file: name, weight });
    console.log(
      `saved ${name}  ${(bytes.length / 1024).toFixed(1)} KB  ` +
        `(variable, axes ${names.join("/")})`,
    );
    continue;
  }

  const weights = want.weights;
  const css = await get(
    `https://fonts.googleapis.com/css2?family=${encodeURIComponent(family)}` +
      `:wght@${weights.join(";")}&display=swap`,
  );
  const latin = latinBlocks(css);

  const byWeight = new Map();
  for (const weight of weights) {
    const block = latin.find((b) => new RegExp(`font-weight:\\s*${weight}\\b`).test(b));
    if (!block) throw new Error(`${family} ${weight}: no latin block`);
    const url = woff2In(block);
    if (!url) throw new Error(`${family} ${weight}: no woff2 url`);
    byWeight.set(weight, url);
  }

  const distinct = new Set(byWeight.values());
  if (distinct.size === 1 && weights.length > 1) {
    const name = `${slug(family)}-variable.woff2`;
    const bytes = await get([...distinct][0], "buf");
    writeFileSync(path.join(OUT, name), bytes);
    const range = `${Math.min(...weights)} ${Math.max(...weights)}`;
    manifest.push({ file: name, weight: range });
    console.log(
      `saved ${name}  ${(bytes.length / 1024).toFixed(1)} KB  ` +
        `(variable, covers ${weights.join("/")})`,
    );
  } else {
    for (const [weight, url] of byWeight) {
      const name = `${slug(family)}-${weight}.woff2`;
      const bytes = await get(url, "buf");
      writeFileSync(path.join(OUT, name), bytes);
      manifest.push({ file: name, weight: String(weight) });
      console.log(`saved ${name}  ${(bytes.length / 1024).toFixed(1)} KB  (static)`);
    }
  }
}

console.log("\nDeclare these in src/app/layout.tsx:");
for (const m of manifest) console.log(`  ${m.file.padEnd(34)} weight: "${m.weight}"`);
