#!/usr/bin/env python3
"""
Builds the animated SVG artwork for the profile README, in dark and light variants.

    pip install fonttools brotli
    python assets/src/build.py

Text is shaped with HarfBuzz and written out as vector outlines (DM Sans / DM Mono,
OFL-licensed), so every browser draws it identically with no font to load.

    pip install fonttools brotli uharfbuzz
"""

import base64
import io
import math
import re
from html import escape
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

SRC = Path(__file__).parent
OUT = SRC.parent
FONTS = SRC / "fonts"

W = 1200

THEMES = {
    "dark": {
        "bg": "#111110", "ink": "#f0efeb", "soft": "#a8a8a4", "muted": "#8f8f8b", "role": "#6b6b69",
        "line": "#2a2a28", "panel": "#161615", "node": "#111110", "dot": "#2a2a28", "ghost": "#191918",
        "accent": "#ff6a3d", "accent_ink": "#ff8a5c", "live": "#5bbf5b", "chip_core_fg": "#111110",
    },
    "light": {
        "bg": "#f5f4f0", "ink": "#111110", "soft": "#545452", "muted": "#6b6b69", "role": "#a0a09e",
        "line": "#dcdbd6", "panel": "#efeee9", "node": "#f5f4f0", "dot": "#d8d8d6", "ghost": "#ebeae5",
        "accent": "#ff4d1a", "accent_ink": "#b93c0b", "live": "#2a7a2a", "chip_core_fg": "#f5f4f0",
    },
}



# ── Fonts → outlines ──────────────────────────────────────────

class Face:
    """One font at fixed settings: shapes text with HarfBuzz and hands out glyph outlines."""

    def __init__(self, key, ttfont):
        buf = io.BytesIO()
        ttfont.flavor = None
        ttfont.save(buf)
        self.key = key
        self.hb_font = hb.Font(hb.Face(buf.getvalue()))
        self.upm = ttfont["head"].unitsPerEm
        self.glyphs = ttfont.getGlyphSet()
        self.order = ttfont.getGlyphOrder()

    def shape(self, text):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"kern": True, "liga": True})
        missing = [text[i.cluster] for i in buf.glyph_infos if i.codepoint == 0]
        if missing:
            raise SystemExit(f"{self.key} has no glyph for {missing!r} in {text!r}")
        return [(i.codepoint, p.x_advance, p.x_offset, p.y_offset)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def outline(self, gid):
        pen = SVGPathPen(self.glyphs, ntos=lambda n: str(round(n)))
        self.glyphs[self.order[gid]].draw(pen)
        return pen.getCommands()

_faces = {}

def get_face(mono, weight, size):
    if mono:
        key = f"m{500 if weight >= 500 else 400}"
        if key not in _faces:
            _faces[key] = Face(key, TTFont(FONTS / f"DMMono-{500 if weight >= 500 else 400}.woff2"))
        return _faces[key]
    opsz = 12 if size < 16 else 18 if size < 30 else 32 if size < 60 else 40
    key = f"s{weight}o{opsz}"
    if key not in _faces:
        _faces[key] = Face(key, instantiateVariableFont(TTFont(FONTS / "DMSans.woff2"), {"wght": weight, "opsz": opsz}))
    return _faces[key]

def run_width(s, size, weight=400, mono=False, spacing=0):
    face = get_face(mono, weight, size)
    return sum(g[1] for g in face.shape(s)) * size / face.upm + spacing * len(s)

def text_width(text, size, weight=400):
    return run_width(text, size, weight)

def mono_width(text, size):
    return run_width(text, size, mono=True)


class Doc:
    """Collects SVG parts plus the glyph outlines they reference."""

    def __init__(self, height, theme):
        self.h = height
        self.t = THEMES[theme]
        self.parts = []
        self.css = []
        self.defs = {}

    def add(self, s):
        self.parts.append(s)

    def runs(self, x, y, runs, anchor="start", spacing=0):
        """Draw consecutive runs [(text, size, weight, fill, mono)] on one baseline."""
        total = sum(run_width(t, sz, w, m, spacing) for t, sz, w, _, m in runs)
        x -= {"start": 0, "middle": total / 2, "end": total}[anchor]
        for text, size, weight, fill, mono in runs:
            face = get_face(mono, weight, size)
            k = size / face.upm
            pen, uses = 0, []
            for gid, adv, dx, dy in face.shape(text):
                gkey = f"{face.key}-{gid}"
                if gkey not in self.defs:
                    self.defs[gkey] = (f"g{len(self.defs):x}", face.outline(gid))
                ref, d = self.defs[gkey]
                if d:
                    uses.append(f'<use href="#{ref}" x="{pen + dx:.0f}"' + (f' y="{-dy:.0f}"' if dy else "") + "/>")
                pen += adv + spacing / k
            self.add(f'<g fill="{fill}" transform="translate({x:.1f} {y:.1f}) scale({k:.5f} {-k:.5f})">{"".join(uses)}</g>')
            x += pen * k

    def text(self, x, y, s, size, fill, weight=400, mono=False, anchor="start", spacing=0):
        self.runs(x, y, [(s, size, weight, fill, mono)], anchor, spacing)

    def render(self, title, desc):
        defs = "".join(f'<path id="{ref}" d="{d}"/>' for ref, d in self.defs.values() if d)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'viewBox="0 0 {W} {self.h}" width="{W}" height="{self.h}" role="img" aria-labelledby="t d">'
                f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>'
                f'<style>{"".join(self.css)}@media (prefers-reduced-motion: reduce){{*{{animation:none!important}}}}</style>'
                f'<defs>{defs}</defs>' + "".join(self.parts) + "</svg>")


def frame(doc, pattern=True):
    t = doc.t
    doc.add(f'<defs><clipPath id="card"><rect width="{W}" height="{doc.h}" rx="18"/></clipPath>'
            f'<pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">'
            f'<rect x="11" y="11" width="2" height="2" fill="{t["dot"]}"/></pattern></defs>')
    doc.add(f'<g clip-path="url(#card)"><rect width="{W}" height="{doc.h}" fill="{t["bg"]}"/>')
    if pattern:
        doc.add(f'<rect width="{W}" height="{doc.h}" fill="url(#dots)"/>')

def close_frame(doc):
    doc.add("</g>")
    doc.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{doc.h - 1}" rx="18" fill="none" stroke="{doc.t["line"]}"/>')


# ── Header ────────────────────────────────────────────────────

def header(theme):
    doc = Doc(420, theme)
    t = doc.t
    frame(doc)

    # Ghost word behind everything on the right
    doc.text(1228, 318, "PLAY", 236, t["ghost"], weight=600, anchor="end", spacing=-10)

    # Ripple: a ring of accent pixels expanding from one point, like a tap on the tile grid
    cx, cy, speed = 930, 170, 300
    doc.css.append("@keyframes rp{0%{opacity:0}5%{opacity:1}18%{opacity:0}100%{opacity:0}}"
                   ".r{opacity:0;animation:rp 6s linear infinite}")
    px = []
    for gy in range(12, 300, 24):
        for gx in range(588, W, 24):
            d = math.hypot(gx - cx, gy - cy)
            if d > 520:
                continue
            strength = max(0.18, 1 - d / 560)
            px.append(f'<rect class="r" x="{gx - 2}" y="{gy - 2}" width="4" height="4" fill="{t["accent"]}" '
                      f'fill-opacity="{strength:.2f}" style="animation-delay:{d / speed + 0.6:.2f}s"/>')
    doc.add("".join(px))

    # Status line
    doc.css.append("@keyframes pl{0%,100%{opacity:1}50%{opacity:.35}}.pl{animation:pl 2s ease-in-out infinite}")
    doc.add(f'<circle class="pl" cx="62" cy="66" r="5" fill="{t["live"]}"/>')
    doc.text(78, 71, "OPEN TO INTERNSHIP & PART-TIME WORK", 15, t["soft"], mono=True, spacing=1.5)

    # Name + role
    doc.runs(54, 172, [("Ahmad", 92, 600, t["ink"], False), (" Nasser", 92, 300, t["ink"], False)], spacing=-3.6)
    doc.text(54, 238, "Gameplay Programmer", 56, t["role"], weight=300, spacing=-1.8)

    doc.runs(58, 286, [("Godot 4 / GDScript", 19, 400, t["soft"], True), ("  ·  ", 19, 400, t["accent_ink"], True),
                       ("Full-stack with Next.js + Supabase", 19, 400, t["soft"], True)])

    # HUD strip
    doc.add(f'<rect x="56" y="320" width="{W - 112}" height="1" fill="{t["line"]}"/>')
    stats = [("06", "GAMES BUILT"), ("02", "GLOBAL GAME JAMS"), ("01", "LIVE WEB APP"), ("17", "CERTIFICATES")]
    x = 56
    for value, label in stats:
        doc.text(x, 372, value, 40, t["ink"], weight=300, spacing=-1.2)
        doc.text(x + 1, 396, label, 11.5, t["muted"], mono=True, spacing=1.3)
        x += max(mono_width(label, 11.5) + 13, 110) + 42
        doc.add(f'<rect x="{x - 22}" y="340" width="1" height="60" fill="{t["line"]}"/>')
    doc.add(f'<circle class="pl" cx="{x + 4}" cy="357" r="4.5" fill="{t["live"]}"/>')
    doc.text(x + 18, 363, "Yalla Tfaddal", 19, t["ink"], weight=500)
    doc.text(x + 1, 396, "NOW BUILDING", 11.5, t["muted"], mono=True, spacing=1.3)

    close_frame(doc)
    return doc.render("Ahmad Nasser — Gameplay Programmer",
                      "Godot 4 / GDScript, full-stack with Next.js and Supabase. Open to internship and part-time work.")


# ── Case study cards ──────────────────────────────────────────

CASES = {
    "yalla": {
        "index": "CASE 01", "period": "2026 – PRESENT", "title": "Yalla Tfaddal",
        "genre": "LOCAL MULTIPLAYER PARTY GAME · GODOT 4 · SOLO", "icon": "png",
        "nodes": [("Phone ×8", "browser, no install"), ("Cloudflare tunnel", "LAN-IP fallback"),
                  ("HTTP · WebSocket", ":8080 page · :9080 input"), ("Godot host", "1 peer per client"),
                  ("9 minigames", "shared scoring")],
        "accent": 2,
        "edges": ["scan QR", "route", "session token", "signals"],
        "metrics": [("8", "players per room"), ("9", "minigames"), ("6", "autoload singletons"), ("0", "apps to install")],
    },
    "jadwlak": {
        "index": "CASE 02", "period": "2026 · LIVE", "title": "Jadwlak", "title_ar": "جدولك",
        "genre": "COURSE PLANNER · NEXT.JS 15 · SUPABASE", "icon": "svg",
        "nodes": [("GitHub Actions", "daily schedule"), ("Playwright", "headless scraper"),
                  ("Supabase", "Postgres + RLS"), ("Vercel CDN", "cached catalog"),
                  ("Every student", "plans in localStorage")],
        "accent": 3,
        "edges": ["run", "upsert", "1 query", "fan-out"],
        "metrics": [("4", "campuses covered"), ("3", "parallel plans"), ("1", "query for everyone"), ("0", "student accounts")],
    },
}

def icon_markup(kind, x, y, size):
    if kind == "png":
        data = base64.b64encode((SRC / "yalla-tfaddal-icon.png").read_bytes()).decode()
        return (f'<clipPath id="ic"><rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{size * 0.23:.0f}"/></clipPath>'
                f'<image x="{x}" y="{y}" width="{size}" height="{size}" clip-path="url(#ic)" '
                f'href="data:image/png;base64,{data}"/>')
    svg = (SRC / "jadwlak-icon.svg").read_text(encoding="utf-8")
    inner = svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 100 100">{inner}</svg>'

def case_card(key, theme):
    c = CASES[key]
    doc = Doc(440, theme)
    t = doc.t
    frame(doc, pattern=False)
    pad = 44

    # Corner brackets, inset so they sit inside the rounded corners
    for x1, y1, dx, dy in ((0, 0, 1, 1), (W, doc.h, -1, -1)):
        doc.add(f'<path d="M{x1 + dx * 12} {y1 + dy * 36} V{y1 + dy * 12} H{x1 + dx * 36}" fill="none" '
                f'stroke="{t["accent"]}" stroke-width="2.5"/>')

    # Head row
    doc.text(pad, 52, c["index"], 14, t["accent_ink"], weight=500, mono=True, spacing=1.4)
    idx_w = run_width(c["index"], 14, 500, True, 1.4)
    per_w = run_width(c["period"], 13, 400, True, 1.3)
    doc.add(f'<rect x="{pad + idx_w + 16:.0f}" y="47" width="{W - 2 * pad - idx_w - per_w - 32:.0f}" height="1" fill="{t["line"]}"/>')
    doc.text(W - pad, 52, c["period"], 13, t["muted"], mono=True, anchor="end", spacing=1.3)

    # Title block
    doc.add(icon_markup(c["icon"], pad, 78, 60))
    doc.text(pad + 80, 118, c["title"], 44, t["ink"], weight=600, spacing=-1.5)
    if c.get("title_ar"):
        tw = run_width(c["title"], 44, 600, spacing=-1.5)
        doc.add(f'<text x="{pad + 80 + tw + 18:.0f}" y="116" font-family="\'Noto Sans Arabic\', \'Segoe UI\', Tahoma, sans-serif" '
                f'font-size="30" fill="{t["muted"]}">{c["title_ar"]}</text>')
    doc.text(pad + 81, 142, c["genre"], 12.5, t["muted"], mono=True, spacing=1.2)

    # Flow panel
    top, ph = 172, 146
    doc.add(f'<rect x="{pad}" y="{top}" width="{W - 2 * pad}" height="{ph}" fill="{t["panel"]}" stroke="{t["line"]}"/>')
    doc.add(f'<rect x="{pad}" y="{top}" width="{W - 2 * pad}" height="{ph}" fill="url(#dots)" opacity=".8"/>'
            f'<defs><pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">'
            f'<rect x="7" y="7" width="1.5" height="1.5" fill="{t["dot"]}"/></pattern></defs>')
    inner = W - 2 * pad - 40
    nw, nh = 188, 72
    gap = (inner - 5 * nw) / 4
    ny = top + 22
    doc.css.append("@keyframes pk{0%{transform:translateX(0);opacity:0}10%{opacity:1}"
                   f"45%{{transform:translateX({gap - 22:.0f}px);opacity:1}}"
                   f"52%,100%{{transform:translateX({gap - 22:.0f}px);opacity:0}}}}"
                   ".pk{opacity:0;animation:pk 2.8s linear infinite}")
    for i, (label, note) in enumerate(c["nodes"]):
        nx = pad + 20 + i * (nw + gap)
        accent = i == c["accent"]
        stroke = t["accent"] if accent else t["line"]
        if accent:
            doc.add(f'<rect x="{nx - 4:.1f}" y="{ny - 4}" width="{nw + 8}" height="{nh + 8}" fill="none" '
                    f'stroke="{t["accent"]}" stroke-opacity=".2" stroke-width="4"/>')
        doc.add(f'<rect x="{nx:.1f}" y="{ny}" width="{nw}" height="{nh}" fill="{t["node"]}" stroke="{stroke}"/>')
        doc.text(nx + 14, ny + 30, label, 16, t["ink"], weight=600)
        doc.text(nx + 14, ny + 52, note, 11.5, t["muted"], mono=True)
        if i < len(c["nodes"]) - 1:
            ex1, ex2, ey = nx + nw + 5, nx + nw + gap - 5, ny + nh / 2
            doc.add(f'<line x1="{ex1:.1f}" y1="{ey}" x2="{ex2:.1f}" y2="{ey}" stroke="{t["role"]}"/>'
                    f'<path d="M{ex2 - 6:.1f} {ey - 4} L{ex2:.1f} {ey} L{ex2 - 6:.1f} {ey + 4}" fill="none" stroke="{t["role"]}"/>')
            doc.add(f'<circle class="pk" cx="{ex1 + 4:.1f}" cy="{ey}" r="3.5" fill="{t["accent"]}" '
                    f'style="animation-delay:{i * 0.7:.1f}s"/>')
            mid = (ex1 + ex2) / 2
            doc.add(f'<line x1="{mid:.1f}" y1="{ey + 6}" x2="{mid:.1f}" y2="{ny + nh + 16}" stroke="{t["line"]}" stroke-dasharray="2 3"/>')
            doc.text(mid, ny + nh + 32, c["edges"][i], 11.5, t["muted"], mono=True, anchor="middle")

    # Metrics
    my = top + ph + 26
    cw = (W - 2 * pad) / 4
    for i, (value, label) in enumerate(c["metrics"]):
        mx = pad + i * cw
        if i:
            doc.add(f'<rect x="{mx:.0f}" y="{my}" width="1" height="72" fill="{t["line"]}"/>')
        ox = mx + (22 if i else 0)
        doc.text(ox, my + 46, value, 44, t["ink"], weight=300, spacing=-1)
        doc.text(ox + 2, my + 70, label, 12.5, t["muted"], mono=True)

    close_frame(doc)
    flow = " → ".join(n[0] for n in c["nodes"])
    return doc.render(f'{c["title"]} — architecture', f"Flow: {flow}.")


# ── Toolbox ───────────────────────────────────────────────────

GROUPS = [
    ("Gameplay", False, ["Godot 4", "GDScript"],
     ["Godot 4", "GDScript", "C#", "Unity", "2D & 3D gameplay", "State machines", "Game AI", "Audio & UI systems", "HTML5 export"],
     "Tower Tiles · The Veiled Arcana · Dizzy Dash"),
    ("Networking & Architecture", False, ["WebSockets", "Client–server"],
     ["WebSockets", "Client–server", "TCP & HTTP servers", "Session management", "Autoload / singletons", "Design patterns", "SOLID"],
     "Yalla Tfaddal"),
    ("Web & Full-Stack", True, ["Next.js", "Supabase"],
     ["Next.js", "React", "TypeScript", "Supabase", "Postgres & RLS", "Upstash Redis", "Vercel", "Playwright", "GitHub Actions", "Chrome MV3", "PHP & MySQL", "Flutter & Dart"],
     "Jadwlak"),
    ("Writing & Localization", False, ["Narrative design", "Arabic localization"],
     ["Narrative design", "Arabic localization", "Dialogue writing", "Game writing", "RTL UI layout", "Creative writing"],
     "Yalla Tfaddal"),
]

def layout_chips(items, core, width, size=14, padx=12, gap=8):
    rows, row, x = [], [], 0
    for s in items:
        w = text_width(s, size, 500 if s in core else 400) + 2 * padx
        if row and x + w > width:
            rows.append(row); row, x = [], 0
        row.append((s, x, w))
        x += w + gap
    if row:
        rows.append(row)
    return rows

def toolbox(theme):
    col_w, pad, chip_h, row_gap = (W - 1) / 2, 36, 32, 8
    laid = [layout_chips(items, set(core), col_w - 2 * pad) for _, _, core, items, _ in GROUPS]
    cell_h = [64 + len(rows) * (chip_h + row_gap) + 44 for rows in laid]
    row_h = [max(cell_h[0], cell_h[1]), max(cell_h[2], cell_h[3])]
    doc = Doc(sum(row_h) + 1, theme)
    t = doc.t
    frame(doc, pattern=False)
    doc.add(f'<rect x="{col_w:.1f}" y="0" width="1" height="{doc.h}" fill="{t["line"]}"/>'
            f'<rect x="0" y="{row_h[0]}" width="{W}" height="1" fill="{t["line"]}"/>')
    for gi, (name, new, core, items, proof) in enumerate(GROUPS):
        ox = (gi % 2) * (col_w + 1) + pad
        oy = (gi // 2) * (row_h[0] + 1)
        doc.text(ox, oy + 50, name, 19, t["ink"], weight=600, spacing=-0.3)
        if new:
            bx = ox + run_width(name, 19, 600, spacing=-0.3) + 14
            doc.add(f'<rect x="{bx:.0f}" y="{oy + 34}" width="46" height="21" fill="none" stroke="{t["accent_ink"]}"/>')
            doc.text(bx + 23, oy + 49, "NEW", 11, t["accent_ink"], weight=500, mono=True, anchor="middle", spacing=1.2)
        cy = oy + 72
        for row in laid[gi]:
            for s, x, w in row:
                is_core = s in core
                fill = t["ink"] if is_core else "none"
                stroke = t["ink"] if is_core else t["line"]
                doc.add(f'<rect x="{ox + x:.1f}" y="{cy}" width="{w:.1f}" height="{chip_h}" fill="{fill}" stroke="{stroke}"/>')
                doc.text(ox + x + w / 2, cy + 21, s, 14, t["chip_core_fg"] if is_core else t["soft"],
                         weight=500 if is_core else 400, anchor="middle")
            cy += chip_h + row_gap
        doc.text(ox, oy + row_h[gi // 2] - 26, f"Proof: {proof}", 12, t["muted"], mono=True)
    close_frame(doc)
    return doc.render("Toolbox", "Gameplay, networking, web and writing skills, each tied to the project that proves it.")


# ── Footer ────────────────────────────────────────────────────

def footer(theme):
    doc = Doc(96, theme)
    t = doc.t
    frame(doc)
    doc.css.append("@keyframes bl{0%,49%{opacity:1}50%,100%{opacity:0}}.bl{animation:bl 1.1s steps(1) infinite}")
    doc.text(44, 58, "> continue?", 22, t["ink"], weight=500, mono=True)
    doc.text(44 + mono_width("> continue? ", 22), 58, "[Y/n]", 22, t["accent_ink"], weight=500, mono=True)
    cx = 44 + mono_width("> continue? [Y/n] ", 22)
    doc.add(f'<rect class="bl" x="{cx:.0f}" y="38" width="12" height="24" fill="{t["accent"]}"/>')
    # DM Mono has no ↗ glyph, so the arrow is drawn
    doc.text(W - 66, 58, "ahmadnasserx.com", 18, t["soft"], mono=True, anchor="end")
    doc.add(f'<path d="M{W - 56} 57 L{W - 45} 46 M{W - 53} 46 H{W - 45} V54" fill="none" '
            f'stroke="{t["soft"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
    close_frame(doc)
    return doc.render("Continue at ahmadnasserx.com", "A blinking terminal prompt linking to the portfolio.")


if __name__ == "__main__":
    builds = {"header": header, "case-yalla": lambda th: case_card("yalla", th),
              "case-jadwlak": lambda th: case_card("jadwlak", th), "toolbox": toolbox, "footer": footer}
    for name, fn in builds.items():
        for theme in THEMES:
            svg = fn(theme)
            (OUT / f"{name}-{theme}.svg").write_text(svg, encoding="utf-8")
            print(f"  {name}-{theme}.svg  {len(svg) / 1024:.0f} KB")
