# Vendored webfonts

1 `.woff2` file, ~26 KB, latin subset only.

## Why these are in git

`next/font/google` self-hosts the files, but it **downloads them during the
build** — which puts `fonts.googleapis.com` on the critical path of
`docker build`. A transient failure at Google then fails the image build with
`An error occurred in next/font` and nothing about the application is wrong.

business-os hit exactly that on 2026-09-26: CI red on a commit that changed
one SVG path, then the deploy red on the server minutes later, both green on
a retry that changed nothing. A deploy somebody else's CDN can break is not a
deploy we control.

So the files are here, `layout.tsx` uses `next/font/local`, and a build needs
no network beyond the npm registry. Verified with `unshare -rn` — no network
namespace at all.

## One file per family, not one per weight

Every family here is **variable** on Google Fonts: a single woff2 serves the
whole weight range, so each is declared with a range rather than one entry
per weight.

Google's css2 API emits a separate `@font-face` per requested weight even for
a variable family, all pointing at the same file. Saving per weight writes
byte-identical copies and makes the browser fetch the same font several times
under several hashed names. `scripts/vendor-fonts.mjs` deduplicates by URL to
prevent it.

## Licensing

Every family is under the **SIL Open Font License 1.1**, which permits
redistribution including bundled in an application, and requires the licence
travel with the files. Each `*-OFL.txt` is the upstream text, unmodified.

| Family | Weights | Copyright |
|---|---|---|
| Jost | 300–500 (variable) | The Jost Project Authors |

Do not delete the `*-OFL.txt` files. Shipping the fonts without them is a
licence breach, and they cost a few KB.

## Adding a face, a weight or an axis

Edit `WANTED` in `scripts/vendor-fonts.mjs`, run it, then add the matching
entry in `src/app/layout.tsx`. Nothing in the build calls the script.
