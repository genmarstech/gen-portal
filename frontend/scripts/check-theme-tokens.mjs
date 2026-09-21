/**
 * Two guards against text you cannot read.
 *
 * ── 1. THE "LIGHT PANEL, LIGHT TEXT" BUG ────────────────────────────────────
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
 *
 * ── 2. TEXT THAT DOES NOT CONTRAST WITH THE THING BEHIND IT ─────────────────
 *
 * The first check only looks at WHICH tokens are used, and a rule can use two
 * perfectly correct semantic tokens and still be unreadable. That shipped too:
 * the sign-on consent screen set `background: var(--accent)` with
 * `color: var(--accent-text)`, which is mahogany on ignition at 2.19:1 in
 * light and — because both tokens are Ignition in dark — ignition on ignition
 * at 1.00:1. The button's label was simply not there, on the one page a
 * stranger sees before they have an account.
 *
 * Five more rules had the same shape with `color: var(--bg)`, which reads fine
 * in dark and fails at 2.64:1 in light. A bug that is invisible in the theme
 * you happen to develop in is one nobody finds by looking.
 *
 * So the second check RESOLVES the tokens, in both themes, and computes the
 * actual WCAG ratio. It is arithmetic rather than a naming convention, which
 * is why it catches the cases the first one cannot.
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

/* ── 2. contrast ───────────────────────────────────────────────────────────
 *
 * The token tables are read out of globals.css rather than restated here.
 * A second copy of the palette would drift, and a guard that checks a stale
 * palette is worse than no guard: it passes while the product is broken.
 */
const GLOBALS = readFileSync(join(ROOT, "app", "globals.css"), "utf8");

/** The body of the brace block that starts at `from`. */
function block(text, from) {
  const open = text.indexOf("{", from);
  let depth = 0;
  for (let i = open; i < text.length; i += 1) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(open + 1, i);
    }
  }
  return "";
}

function declarations(body, into) {
  for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    into.set(name, value.trim());
  }
  return into;
}

const light = declarations(block(GLOBALS, GLOBALS.search(/^:root\s*\{/m)), new Map());
const dark = new Map(light);
/* Both ways a reader ends up in dark: an explicit choice, and the OS default
   with nothing stamped on the root. A token overridden in only one of them is
   its own bug, and this at least evaluates what each actually produces. */
for (const pattern of [/:root\[data-theme="dark"\]\s*\{/g, /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{/g]) {
  for (const match of GLOBALS.matchAll(pattern)) {
    declarations(block(GLOBALS, match.index), dark);
  }
}

function resolve(value, table, depth = 0) {
  if (depth > 6) return null;
  const ref = /^var\((--[\w-]+)\)$/.exec(value.trim());
  if (ref) return resolve(table.get(ref[1]) ?? "", table, depth + 1);
  const hex = value.trim();
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : null;
}

function luminance(hex) {
  const channel = (c) => {
    const v = parseInt(c, 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const [r, g, b] = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map(channel);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/*
 * AA for normal text. Deliberately not the 3:1 large-text allowance: this
 * cannot see a font-size, and guessing generously is how 2.64:1 survived.
 * A rule that genuinely is large display type can opt out in the same way a
 * fixed surface does.
 */
const MINIMUM = 4.5;
const COLOR = /(?<![-\w])color\s*:\s*(var\(--[\w-]+\)|#[0-9a-fA-F]{6})\s*;/;
const BACKGROUND = /(?<![-\w])background(?:-color)?\s*:\s*(var\(--[\w-]+\)|#[0-9a-fA-F]{6})\s*;/;

for (const file of walk(ROOT).filter((f) => f.endsWith(".css"))) {
  const source = readFileSync(file, "utf8");
  if (OPT_OUT.test(source.slice(0, 600))) continue;

  for (const rule of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const body = rule[2];
    const bg = BACKGROUND.exec(body);
    const fg = COLOR.exec(body);
    if (!bg || !fg) continue;

    for (const [theme, table] of [["light", light], ["dark", dark]]) {
      const behind = resolve(bg[1], table);
      const on = resolve(fg[1], table);
      /* Unresolvable means rgba(), a gradient or a token defined elsewhere.
         Silence rather than a false alarm — this check earns its place by
         being believed when it does fire. */
      if (!behind || !on) continue;

      const ratio = contrast(behind, on);
      if (ratio >= MINIMUM) continue;

      const selector = rule[1].trim().split("\n").pop().trim();
      problems.push(
        `  src/${relative(ROOT, file).replace(/\\/g, "/")}  ${selector}` +
          `\n      ${fg[1]} on ${bg[1]} = ${ratio.toFixed(2)}:1 in ${theme}` +
          ` (${on} on ${behind}, needs ${MINIMUM}:1)`,
      );
    }
  }
}

if (problems.length > 0) {
  console.error("Theme token check FAILED:\n");
  console.error(problems.join("\n"));
  console.error(
    "\nA brand constant does not change between themes. Use a semantic token:" +
      "\n  --bg          page ground" +
      "\n  --bg-raised   inset cards and callout panels" +
      "\n  --bg-band     full-width section bands" +
      "\n  --ink         body text" +
      "\n\nAnd for text SITTING ON the accent, the ink is --on-accent." +
      "\n--accent-text is accent-coloured text on the page ground, which is" +
      "\nthe opposite thing and is invisible on --accent in dark.\n",
  );
  process.exit(1);
}

console.log(
  "Theme token check passed — no brand constants used as themeable values.",
);
