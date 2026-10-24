# Documentation graphics

Scripts that draw the logo, the brand images and the diagrams of the documentation, so they can
be changed and drawn again instead of edited by hand.

| Script | Draws |
|---|---|
| `make_brand.py <dir>` | `icon.svg`, `dark_icon.svg`, `logo.svg`, `dark_logo.svg` (needs `fonttools` and the Inter font; `INTER_SEMIBOLD` points to `Inter-SemiBold.otf` if it is not in `/usr/share/fonts/opentype/inter/`) |
| `make_diagrams.py docs/images` | `night-and-day`, `target-bounds`, `overview`, in English and German |
| `render.js <svg> <png> <w> <h> [bg]` | A PNG of an SVG with the preinstalled Playwright Chromium. `COLOR_SCHEME=dark` and `SCALE=2` are optional. |

Brand images (`custom_components/inverter_charge_night/brand/`, transparent):

```bash
python scripts/docs_graphics/make_brand.py /tmp/brand
B=custom_components/inverter_charge_night/brand
node scripts/docs_graphics/render.js /tmp/brand/icon.svg $B/icon.png 256 256
node scripts/docs_graphics/render.js /tmp/brand/icon.svg $B/icon@2x.png 512 512
node scripts/docs_graphics/render.js /tmp/brand/dark_icon.svg $B/dark_icon.png 256 256
node scripts/docs_graphics/render.js /tmp/brand/dark_icon.svg $B/dark_icon@2x.png 512 512
# logos: height 128 and 256, width from the SVG's viewBox (width × height ÷ 512)
```

The README shows `docs/images/night-and-day.<lang>.png`, drawn with `SCALE=2` from the SVG,
because the HACS page cannot rely on SVG. The screenshots come from the lab:
`lab/docs_screenshots.py`.
