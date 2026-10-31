# Increment 14: the Foundry logo in the UI

**Track:** Quick   **Date:** 2026-10-31   **Status:** Built (verified 2026-10-31)

## Problem

The UI has no logo: a blue-to-teal gradient square stands in for one in the header, and the browser tab shows no icon at all. The user supplied candidate logos and chose the geometric "TF" mark: charcoal (candidate 6) on light backgrounds, cyan (candidate 5) on dark ones.

## Acceptance criteria

- **L1:** The header's gradient square is replaced by the TF mark at 32×32 CSS px, from a 64 px image for sharp rendering on retina screens:
  - the charcoal mark (`logo-light-v1.png`) in the light theme
  - the cyan mark (`logo-dark-v1.png`) in the dark theme
  - it switches with the app's theme toggle
  - the text "Foundry / eval" stays
  - the image has `alt=""` because the text next to it names the app, and the link keeps its accessible name "Foundry / eval"
- **L2:** The browser tab shows the TF mark as its icon, with 16 and 32 px PNGs for each colourway:
  - `index.html` declares both, each with `media="(prefers-color-scheme: light|dark)"`, as a fallback before JavaScript runs
  - once the app runs, it points the icon at the colourway matching the app's current theme and updates it when the theme changes
  - in the dark theme the tab icon is cyan, and in the light theme it is charcoal
- **L3:** The images are served from the built bundle (`src/foundry/ui/web_dist/`) under the existing CSP (`img-src 'self'`), with no `data:` URIs. They're generated from the user's source files (`mockups/src/logo-5.png` and `logo-6.png`), cropped to the mark without the wordmark, on transparent squares.
- **L4:** Tests:
  - **Vitest:** the header renders both theme images with the right theme classes.
  - **pytest:** `/assets/brand/favicon-light-32-v1.png`, `favicon-dark-32-v1.png`, `logo-light-v1.png` and `logo-dark-v1.png` are served as `image/png`; `index.html` links both icons with their `media`.
  - **e2e:** in dark and light, the header shows the matching mark (the visible one is checked with `getComputedStyle`), and the tab icon's `href` matches the theme after toggling.

## Non-goals

- Restyling the UI's colours around the logo (the user chose "header mark + tab icon" only).
- Using the full lockup with the wordmark in the UI, a PWA manifest, or an Apple touch icon.
- The classic UI at `/classic`.

## Assumptions and open questions

- **A1 (unverified):** browsers pick between `<link rel="icon">` tags by their `media` attribute. Search found only secondary sources (spacejelly.dev, joyofcode.xyz), and no authoritative per-browser table. So the design doesn't depend on it: the app sets the icon from its own theme in code, and the `media` links are only the no-JavaScript fallback.
- **A2:** some browsers (Safari in particular) may not refresh a tab icon that changes after load. If one doesn't, that browser keeps the icon from page load. Accepted.

## Plan

**Design review:** approved (2026-10-31).
- The user picked "header mark + tab icon" over a yellow brand accent or a mascot in empty states.
- From the comparison mockup `mockups/logos.html` (six candidates plus the earlier "R", in the real header and as tab icons), the user chose 6 on light and 5 on dark.

1. Copy the generated crops into `web/public/assets/brand/`:
   - `logo-6-64.png` becomes `logo-light-v1.png`, and `logo-5-64.png` becomes `logo-dark-v1.png`
   - the 16 and 32 px crops become `favicon-{light,dark}-{16,32}-v1.png`

   Vite copies `public/` as is, so they land in `web_dist/assets/brand/`. The existing `/assets/{name}` route serves them, and it marks them immutable, which is why the names carry `-v1`. There's no server change, and nothing goes through Vite's inliner, so there are no `data:` URIs.
2. `index.html` gets the two icon links with `media`, and `Shell.tsx` swaps the header square for two `<img>`s (`dark:hidden` and `hidden dark:block`).
3. `useTheme` updates `link[rel=icon]`'s `href` to the matching colourway on every theme change.
4. No server change: the files are served by the existing `/assets` route (`src/foundry/ui/api.py:373`).
5. Tests per L4, then build, the e2e, screenshots, and the handoff.
