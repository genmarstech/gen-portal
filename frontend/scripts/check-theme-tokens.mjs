/**
 * Guard against the "light panel, light text" bug.
 *
 * Brand constants (--surface, --surface-raised, --canvas, --deep-well) are FIXED
 * colours that never change between themes. Semantic tokens (--bg, --bg-raised,
 * --bg-band, --ink) flip with the theme.
 *
 * Using a brand constant as a background while the text uses a semantic token
 * produces an unreadable section in one theme — a light band with light text on
 * it. That shipped once. This stops it shipping again.
 *
 * Brand constants are legitimate inside globals.css, where the semantic tokens
 * are defined from them.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, basename, relative } from "node:path";

const ROOT = fileURLToPath(new URL("../src", import.meta.url));
const FORBIDDEN = /var\(--(?:surface-raised|surface|canvas|deep-well)\)/g;
const ALLOWLIST = new Set(["globals.css"]);

/**
 * A file may opt out by declaring, on its first few lines:
 *
 *   /* theme-check: fixed-surface — <reason> *\/
 *
 * Only for surfaces that deliberately do NOT follow the theme. The brand panel
 * is the real case: its Ignition gradient is a fixed brand surface, and a
 * gradient that inverted with the theme would make light and dark read as two
 * different products.
 *
 * The marker requires a reason on the same line, so the exception is argued
 * rather than merely taken.
 */
const OPT_OUT = /theme-check:\s*fixed-surface\s*[—-]\s*\S/;

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const problems = [];

for (const file of walk(ROOT).filter((f) => f.endsWith(".css"))) {
  if (ALLOWLIST.has(basename(file))) continue;

  const source = readFileSync(file, "utf8");
  if (OPT_OUT.test(source.slice(0, 600))) continue;

  source
    .split("\n")
    .forEach((line, index) => {
      for (const match of line.matchAll(FORBIDDEN)) {
        problems.push(
          `  src/${relative(ROOT, file).replace(/\\/g, "/")}:${index + 1}` +
            `  ${match[0]}\n      ${line.trim()}`,
        );
      }
    });
}

if (problems.length > 0) {
  console.error(
    "Theme token check FAILED — brand constants used outside globals.css:\n",
  );
  console.error(problems.join("\n"));
  console.error(
    "\nThese do not change between themes. Use a semantic token instead:" +
      "\n  --bg          page ground" +
      "\n  --bg-raised   inset cards and callout panels" +
      "\n  --bg-band     full-width section bands" +
      "\n  --ink         body text\n",
  );
  process.exit(1);
}

console.log(
  "Theme token check passed — no brand constants used as themeable values.",
);
