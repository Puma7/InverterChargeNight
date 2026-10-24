"""Draw the explanatory diagrams of the documentation as SVG, in English and German.

    python scripts/docs_graphics/make_diagrams.py docs/images

Writes night-and-day, target-bounds and overview as <name>.<lang>.svg. The SVGs follow the
viewer's light or dark mode. Render the README variant (a PNG on a light card) with
render.js. The curves are illustrative, not the output of the planner.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

FONT = "Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
STYLE = """
  <style>
    svg { --fg: #1C2541; --muted: #5B6478; --grid: #D9DEE8; --soc: #2F6FDE; --pv: #FFB020;
          --cheap: #2E9E6A; --peak: #D9534F; --card: #FFFFFF; --box: #F3F5FA; --line: #9AA3B5; }
    @media (prefers-color-scheme: dark) {
      svg { --fg: #E8ECF6; --muted: #A9B1C3; --grid: #39404F; --soc: #6EA2FF; --pv: #FFC94D;
            --cheap: #4CC38A; --peak: #F07C78; --card: #0D1117; --box: #1B2230; --line: #6B7488; }
    }
    text { font-family: %s; fill: var(--fg); }
    .muted { fill: var(--muted); }
    .small { font-size: 13px; }
    .label { font-size: 14px; }
    .title { font-size: 16px; font-weight: 600; }
  </style>""" % FONT

TEXT = {
    "en": {
        "cheap": "Cheap window", "peak": "High-price period", "target": "Target",
        "s1": "① charge to the target", "s2": "② bridge until the PV takes over",
        "s3": "③ the PV fills the battery", "s4": "④ the battery carries the evening",
        "soc": "Battery state of charge", "pv": "PV output",
        "b_min": "Minimum SOC", "b_bridge": "Morning bridge", "b_evening": "Evening reserve",
        "b_buffer": "Safety buffer", "b_free": "unused", "b_room": "Room for tomorrow's PV surplus",
        "lower": "lower bound", "upper": "upper bound", "t_target": "target = min(lower + buffer, upper)",
        "o_in": "What ICN reads", "o_icn": "Inverter Charge Night", "o_out": "What ICN writes",
        "o_inputs": ["PV forecast", "House consumption", "Sun, weather", "Prices", "Grid import",
                     "State of charge"],
        "o_core": ["plans the target", "charges in the window", "protects the connection",
                   "gives everything back"],
        "o_writes": ["Minimum SOC", "Grid charge switch", "Charge limit (W)"],
        "o_via": "through your inverter integration", "o_inv": "Inverter + battery",
        "o_show": "Sensors, actions, dashboard",
    },
    "de": {
        "cheap": "Günstiges Fenster", "peak": "Hochpreiszeit", "target": "Ziel",
        "s1": "① auf das Ziel laden", "s2": "② überbrücken, bis die PV übernimmt",
        "s3": "③ die PV füllt den Akku", "s4": "④ der Akku trägt den Abend",
        "soc": "Ladestand des Akkus", "pv": "PV-Leistung",
        "b_min": "Min-SOC", "b_bridge": "Morgenbrücke", "b_evening": "Abendreserve",
        "b_buffer": "Sicherheitspuffer", "b_free": "ungenutzt", "b_room": "Platz für den PV-Überschuss von morgen",
        "lower": "Untergrenze", "upper": "Obergrenze", "t_target": "Ziel = min(Untergrenze + Puffer, Obergrenze)",
        "o_in": "Was ICN liest", "o_icn": "Inverter Charge Night", "o_out": "Was ICN schreibt",
        "o_inputs": ["PV-Prognose", "Hausverbrauch", "Sonne, Wetter", "Strompreise", "Netzbezug",
                     "Ladestand"],
        "o_core": ["plant das Ziel", "lädt im Fenster", "schützt den Hausanschluss",
                   "gibt alles zurück"],
        "o_writes": ["Min-SOC", "Netzladeschalter", "Ladegrenze (W)"],
        "o_via": "über deine Wechselrichter-Integration", "o_inv": "Wechselrichter + Akku",
        "o_show": "Sensoren, Aktionen, Dashboard",
    },
}


def svg(width: int, height: int, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="{title}">{STYLE}\n'
            f'<rect width="{width}" height="{height}" rx="14" fill="var(--card)"/>\n{body}\n</svg>\n')


def night_and_day(t: dict[str, str]) -> str:
    """The battery over 26 hours: evening, window, bridge, PV, evening peak."""
    x0, x1, y0, y1 = 56, 884, 300, 52          # plot area; y0 is 0 %, y1 is 100 %
    start = 21.0                               # 21:00 on the first day
    hours = 26.0
    def x(h: float) -> float: return x0 + (h - start) / hours * (x1 - x0)
    def y(soc: float) -> float: return y0 - soc / 100 * (y0 - y1)
    soc_points = [(21, 34), (23, 28), (29, 55), (33.5, 40), (35, 45), (37, 79), (38.5, 96),
                  (40.5, 98), (42, 93), (45, 68), (47, 62)]
    parts = []
    # bands
    for a, b, colour, label in ((23, 29, "var(--cheap)", t["cheap"]), (42, 45, "var(--peak)", t["peak"])):
        parts.append(f'<rect x="{x(a):.1f}" y="{y1 - 22}" width="{x(b) - x(a):.1f}" height="{y0 - y1 + 22}" '
                     f'fill="{colour}" opacity="0.12" rx="6"/>')
        parts.append(f'<text x="{(x(a) + x(b)) / 2:.1f}" y="{y1 - 6}" text-anchor="middle" '
                     f'class="small" style="fill:{colour};font-weight:600">{label}</text>')
    # grid and axis
    for soc in (0, 50, 100):
        parts.append(f'<line x1="{x0}" x2="{x1}" y1="{y(soc):.1f}" y2="{y(soc):.1f}" stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{x0 - 8}" y="{y(soc) + 4:.1f}" text-anchor="end" class="small muted">{soc} %</text>')
    for h in range(21, 48, 3):
        parts.append(f'<text x="{x(h):.1f}" y="{y0 + 20}" text-anchor="middle" class="small muted">{h % 24:02d}:00</text>')
    # PV bell
    pv = []
    for i in range(0, 121):
        h = 31.5 + i * 10 / 120
        value = math.sin(math.pi * (h - 31.5) / 10) ** 2 * 62
        pv.append(f"{x(h):.1f},{y(value):.1f}")
    parts.append(f'<polygon points="{x(31.5):.1f},{y0} {" ".join(pv)} {x(41.5):.1f},{y0}" fill="var(--pv)" opacity="0.35"/>')
    # target line
    parts.append(f'<line x1="{x(23):.1f}" x2="{x(29):.1f}" y1="{y(55):.1f}" y2="{y(55):.1f}" stroke="var(--soc)" '
                 f'stroke-dasharray="5 4" stroke-width="1.5"/>')
    parts.append(f'<text x="{x(23) + 8:.1f}" y="{y(55) - 7:.1f}" class="small" style="fill:var(--soc);font-weight:600">{t["target"]} 55 %</text>')
    # SOC curve, smoothed with a cubic Catmull-Rom path
    pts = [(x(h), y(s)) for h, s in soc_points]
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    parts.append(f'<path d="{d}" fill="none" stroke="var(--soc)" stroke-width="3.5" stroke-linecap="round"/>')
    # steps
    for h, soc, key, anchor in ((24.6, 47, "s1", "start"), (30.6, 31, "s2", "start"),
                                (36.4, 76, "s3", "end"), (43.5, 50, "s4", "middle")):
        parts.append(f'<text x="{x(h):.1f}" y="{y(soc):.1f}" text-anchor="{anchor}" class="label">{t[key]}</text>')
    # legend
    lx, ly = x0, y0 + 46
    parts.append(f'<line x1="{lx}" x2="{lx + 26}" y1="{ly - 4}" y2="{ly - 4}" stroke="var(--soc)" stroke-width="3.5"/>')
    parts.append(f'<text x="{lx + 34}" y="{ly}" class="small muted">{t["soc"]}</text>')
    parts.append(f'<rect x="{lx + 230}" y="{ly - 12}" width="26" height="12" fill="var(--pv)" opacity="0.5" rx="2"/>')
    parts.append(f'<text x="{lx + 264}" y="{ly}" class="small muted">{t["pv"]}</text>')
    return svg(920, 362, "\n".join(parts), t["soc"])


def target_bounds(t: dict[str, str]) -> str:
    """The battery as a horizontal bar, with the parts of the target."""
    x0, x1, top, height = 40, 880, 70, 64
    def x(soc: float) -> float: return x0 + soc / 100 * (x1 - x0)
    segments = [(0, 8, "var(--line)", 0.45, t["b_min"]), (8, 33, "var(--soc)", 0.85, t["b_bridge"]),
                (33, 42, "var(--soc)", 0.6, t["b_evening"]), (42, 50, "var(--pv)", 0.9, t["b_buffer"]),
                (50, 66, "var(--grid)", 0.6, t["b_free"]), (66, 100, "var(--pv)", 0.35, t["b_room"])]
    parts = [f'<rect x="{x0 - 2}" y="{top - 2}" width="{x1 - x0 + 4}" height="{height + 4}" rx="12" '
             f'fill="none" stroke="var(--fg)" stroke-width="2.5"/>',
             f'<rect x="{x1 + 4}" y="{top + 18}" width="10" height="{height - 36}" rx="3" fill="var(--fg)"/>']
    for a, b, colour, opacity, label in segments:
        parts.append(f'<rect x="{x(a):.1f}" y="{top}" width="{x(b) - x(a):.1f}" height="{height}" fill="{colour}" opacity="{opacity}"/>')
        centre = (x(a) + x(b)) / 2
        lower_row = a in (0, 33, 50)
        ly = top + height + (52 if lower_row else 24)
        parts.append(f'<line x1="{centre:.1f}" x2="{centre:.1f}" y1="{top + height}" y2="{ly - 14}" stroke="var(--line)" stroke-width="1"/>')
        parts.append(f'<text x="{centre:.1f}" y="{ly}" text-anchor="middle" class="small">{label}</text>')
    for soc, key, colour in ((42, "lower", "var(--soc)"), (66, "upper", "var(--fg)")):
        parts.append(f'<line x1="{x(soc):.1f}" x2="{x(soc):.1f}" y1="{top - 16}" y2="{top + height}" stroke="{colour}" stroke-width="2" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{x(soc):.1f}" y="{top - 22}" text-anchor="middle" class="small" style="font-weight:600">{t[key]}</text>')
    parts.append(f'<line x1="{x(50):.1f}" x2="{x(50):.1f}" y1="{top - 6}" y2="{top + height + 6}" stroke="var(--peak)" stroke-width="3"/>')
    parts.append(f'<text x="{x(50) + 8:.1f}" y="{top - 22}" class="small" style="fill:var(--peak);font-weight:600">{t["target"]}</text>')
    for soc in (0, 100):
        parts.append(f'<text x="{x(soc):.1f}" y="{top + height / 2 + 5}" text-anchor="{"start" if soc == 0 else "end"}" '
                     f'dx="{8 if soc == 0 else -8}" class="small muted">{soc} %</text>')
    parts.append(f'<text x="{(x0 + x1) / 2}" y="{top + height + 92}" text-anchor="middle" class="label muted">{t["t_target"]}</text>')
    return svg(920, 250, "\n".join(parts), t["t_target"])


def overview(t: dict[str, str]) -> str:
    """Inputs, ICN, outputs."""
    parts = []
    def box(x: float, y: float, w: float, h: float, fill: str = "var(--box)", stroke: str = "var(--line)") -> None:
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
    def arrow(x1: float, y1: float, x2: float, y2: float) -> None:
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2 - 8}" y2="{y2}" stroke="var(--line)" stroke-width="2"/>')
        parts.append(f'<polygon points="{x2},{y2} {x2 - 10},{y2 - 5} {x2 - 10},{y2 + 5}" fill="var(--line)"/>')
    parts.append(f'<text x="120" y="30" text-anchor="middle" class="title">{t["o_in"]}</text>')
    for i, name in enumerate(t["o_inputs"]):
        box(30, 46 + i * 46, 180, 36)
        parts.append(f'<text x="120" y="{69 + i * 46}" text-anchor="middle" class="label">{name}</text>')
        arrow(210, 64 + i * 46, 330, 64 + 2.5 * 46)
    box(330, 92, 250, 180, fill="var(--card)", stroke="var(--soc)")
    parts.append(f'<text x="455" y="124" text-anchor="middle" class="title" style="fill:var(--soc)">{t["o_icn"]}</text>')
    for i, line in enumerate(t["o_core"]):
        parts.append(f'<text x="455" y="{156 + i * 26}" text-anchor="middle" class="label">{line}</text>')
    parts.append(f'<text x="770" y="30" text-anchor="middle" class="title">{t["o_out"]}</text>')
    for i, name in enumerate(t["o_writes"]):
        box(680, 46 + i * 46, 180, 36)
        parts.append(f'<text x="770" y="{69 + i * 46}" text-anchor="middle" class="label">{name}</text>')
        arrow(580, 182, 680, 64 + i * 46)
    parts.append(f'<text x="770" y="200" text-anchor="middle" class="small muted">{t["o_via"]}</text>')
    parts.append('<line x1="770" y1="208" x2="770" y2="228" stroke="var(--line)" stroke-width="2"/>')
    parts.append('<polygon points="770,237 765,227 775,227" fill="var(--line)"/>')
    box(680, 238, 180, 40, fill="var(--card)", stroke="var(--fg)")
    parts.append(f'<text x="770" y="263" text-anchor="middle" class="label" style="font-weight:600">{t["o_inv"]}</text>')
    box(330, 300, 250, 36)
    parts.append(f'<text x="455" y="323" text-anchor="middle" class="label">{t["o_show"]}</text>')
    parts.append('<line x1="455" y1="272" x2="455" y2="300" stroke="var(--line)" stroke-width="2"/>')
    return svg(900, 350, "\n".join(parts), t["o_icn"])


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for lang, text in TEXT.items():
        for name, draw in (("night-and-day", night_and_day), ("target-bounds", target_bounds),
                           ("overview", overview)):
            (out / f"{name}.{lang}.svg").write_text(draw(text), encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
