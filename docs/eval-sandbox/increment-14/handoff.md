# Increment 14: the Foundry logo in the UI — Handoff

**Spec:** ./spec.md   **Date:** 2026-10-31   **Track:** Quick

## What shipped

- **Header:** the TF mark replaces the gradient square, charcoal on light and cyan on dark. It switches with the app's theme toggle, and "Foundry / eval" stays as text.
- **Tab icon:** the same mark in the matching colourway.
  - `index.html` declares both colourways with `media` as the no-JavaScript fallback.
  - Once the app runs, it points the icon at its own theme, so the tab icon follows the toggle too.

## Decisions

- **The user's choices:**
  - "header mark + tab icon" over a yellow brand accent or a mascot in empty states
  - from the comparison mockup (`mockups/logos.html`, `mockups/logos.png`), candidate 6 (charcoal TF) on light and 5 (cyan TF) on dark
- **Files:** the images live in `web/public/assets/brand/` with `-v1` names. The existing `/assets` route serves them as immutable, so a future logo change needs a new version suffix. There's no server change, and no `data:` URIs.
- **Source images in the repo:** only the chosen sources (5 and 6) are committed in full size. The other candidates are kept as 256 px crops, and their originals stay with the user.
- **Unverified assumption A1:** browsers honouring the favicon `media` attribute. No authoritative source was found. The app's own update of the icon in code doesn't rely on it.

## Verification

**Suites:**
- Python: 324 passed.
- Vitest: 18 passed.
- ruff, `tsc` and gitleaks are clean.

| AC | How it was proven |
|---|---|
| L1 | Vitest "logo": both `<img>`s render with `dark:hidden` and `hidden dark:block`, and with `alt=""`. **e2e:** in light and in dark, exactly one header logo is visible (`getComputedStyle`), it has loaded, and it's the right file. Screenshots: [header-light](screenshots/header-light.png), [header-dark](screenshots/header-dark.png). |
| L2 | **e2e:** in each theme, every `link[rel=icon]` points at that theme's colourway. Clicking the app's theme toggle switches them all, from dark to light. Vitest checks the icon follows the app theme on mount. pytest checks `index.html` links both colourways with their `media`. |
| L3 | pytest `test_logo_and_tab_icons_served`: all six PNGs are served from the built bundle as `image/png` with a PNG signature, and `index.html` has no `data:image`. |
| L4 | All of the above. The full e2e passes: run, score, Rubric tab, logo, and the clarity stage on 12 pages, 5 tabs and the dialog at both widths and in both themes. |

**Self-review (Quick):**
- Every AC has a test or live proof.
- There are no new endpoints or HTML sinks, and the CSP is unchanged (`img-src 'self'`).
- The comparison with the approved mockup matches: the header in both themes is as in `logos.png`, rows 5 and 6.

**Exercised live:** yes. The restarted `foundry ui` served the bundle to headless Chromium.

## Follow-ups

- A 180 px Apple touch icon and an SVG version of the mark, if a vector source becomes available.
