import localFont from "next/font/local";

/**
 * The two families the interface is designed in.
 *
 * They are vendored rather than fetched from a font CDN: Nightjar is self-hosted and
 * frequently runs on a network with no outbound route, and a typeface that fails to
 * load is a page that reflows mid-incident. `next/font/local` fingerprints the files,
 * emits the `@font-face` rules at build time, and serves them from our own origin, so
 * there is no third-party request on any page.
 *
 * See `app/fonts/README.md` for versions, hashes, and licenses.
 */

export const inter = localFont({
  src: "./fonts/InterVariable.woff2",
  variable: "--font-inter",
  display: "swap",
  weight: "100 900",
});

export const jetbrainsMono = localFont({
  src: "./fonts/JetBrainsMono-Variable.woff2",
  variable: "--font-jetbrains",
  display: "swap",
  weight: "100 800",
});
