import type { Metadata, Viewport } from "next";
import { Jost } from "next/font/google";
import { NO_FLASH_SCRIPT } from "@/components/theme";
import "./globals.css";

const jost = Jost({
  subsets: ["latin"],
  weight: ["300", "400", "500"],
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
