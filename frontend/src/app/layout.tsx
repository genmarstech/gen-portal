import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { NO_FLASH_SCRIPT } from "@/components/theme";
import "./globals.css";

/**
 * ══════════════════════════════════════════════════════════════════════════
 * next/font/LOCAL, NOT next/font/google, AND THAT IS THE WHOLE POINT.
 *
 * `next/font/google` also self-hosts — but it DOWNLOADS the files during the
 * build, which puts fonts.googleapis.com on the critical path of
 * `docker build`. A transient failure there fails the image with "An error
 * occurred in `next/font`" while nothing about this application is wrong.
 *
 * business-os hit exactly that on 2026-09-26: CI red on a commit that
 * changed one SVG path, then the deploy red on the server minutes later,
 * both green on a retry that changed nothing. A deploy somebody else's CDN
 * can break is not a deploy we control.
 *
 * So the files live in src/app/fonts/ and a build needs no network beyond
 * the npm registry. `npm run build` completes inside `unshare -rn`.
 *
 * To add a face, a weight or an axis: edit WANTED in
 * scripts/vendor-fonts.mjs, run it, and add the declaration here. See
 * src/app/fonts/README.md.
 * ══════════════════════════════════════════════════════════════════════════
 */

/**
 * Jost, served from this origin. One file: it is variable on Google Fonts,
 * so 300/400/500 are a range rather than three faces.
 */
const jost = localFont({
  src: "./fonts/jost-variable.woff2",
  weight: "300 500",
  style: "normal",
  display: "swap",
  variable: "--font-jost",
});

export const metadata: Metadata = {
  title: { default: "Genmars", template: "%s — Genmars" },
  description: "Client portal for Genmars Tech Limited.",
  // A portal is never indexed. There is nothing here for a search engine, and
  // every URL under it is either a sign-in form or someone's private data.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4efec" },
    { media: "(prefers-color-scheme: dark)", color: "#211e27" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-KE" className={jost.variable} suppressHydrationWarning>
      <body>
        {/* Before first paint, or dark-theme users see a white flash. */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
        {children}
      </body>
    </html>
  );
}
