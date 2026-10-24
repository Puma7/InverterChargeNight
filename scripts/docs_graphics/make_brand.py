"""Build the ICN icon and logo SVGs (light and dark); the wordmark is converted to paths.

    python scripts/docs_graphics/make_brand.py <out-dir>

Needs fontTools and the Inter font (Inter-SemiBold.otf). See README.md in this folder for
rendering the PNGs.
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

OUT = Path(sys.argv[1])
NAVY, LIGHT, AMBER, MOON = "#1C2541", "#E8ECF6", "#FFB020", "#FFC94D"

def icon_body(body: str) -> str:
    return f'''
  <defs>
    <mask id="crescent">
      <rect width="512" height="512" fill="#fff"/>
      <circle cx="446" cy="88" r="74" fill="#000"/>
    </mask>
  </defs>
  <rect x="64" y="124" width="240" height="352" rx="46" fill="{body}"/>
  <rect x="136" y="84" width="96" height="56" rx="16" fill="{body}"/>
  <rect x="96" y="268" width="176" height="176" rx="24" fill="{AMBER}"/>
  <path d="M206 282 L130 378 H178 L160 432 L240 330 H192 Z" fill="{body}"/>
  <circle cx="400" cy="128" r="84" fill="{MOON}" mask="url(#crescent)"/>
  <path d="M300 40 l7 17 l17 7 l-17 7 l-7 17 l-7 -17 l-17 -7 l17 -7 Z" fill="{MOON}"/>'''

def icon_svg(body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">{icon_body(body)}\n</svg>\n'

def text_path(text: str, font_file: str, size: float, x0: float, baseline: float):
    font = TTFont(font_file)
    gs = font.getGlyphSet(); cmap = font.getBestCmap(); upm = font["head"].unitsPerEm
    scale = size / upm; x = x0; paths = []
    for ch in text:
        name = cmap[ord(ch)]; glyph = gs[name]
        pen = SVGPathPen(gs)
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        if pen.getCommands():
            paths.append(pen.getCommands())
        x += glyph.width * scale
    return " ".join(paths), x

def logo_svg(body: str, text: str) -> str:
    semibold = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
    first, x = text_path("Inverter Charge ", semibold, 200, 540, 360)
    second, x = text_path("Night", semibold, 200, x, 360)
    width = int(x + 40)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 512" width="{width}" height="512">'
            f'{icon_body(body)}\n  <path d="{first}" fill="{text}"/>\n  <path d="{second}" fill="{AMBER}"/>\n</svg>\n')

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "icon.svg").write_text(icon_svg(NAVY))
(OUT / "dark_icon.svg").write_text(icon_svg(LIGHT))
(OUT / "logo.svg").write_text(logo_svg(NAVY, NAVY))
(OUT / "dark_logo.svg").write_text(logo_svg(LIGHT, LIGHT))
print("ok")
