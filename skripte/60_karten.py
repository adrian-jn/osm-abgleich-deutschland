"""Maps of the lists in listen/: SVG always, PNG when Playwright with Chromium is installed.

Writes karten/abdeckung_kreise.(svg|png), karten/adressluecken_kreise.*,
karten/haltestellen_kreise.*.
"""
import re

import numpy as np
import pandas as pd

from common import HERE, OUT, kreise, laender

KARTEN = HERE.parent / "karten"
KARTEN.mkdir(exist_ok=True)
krs = kreise(25832); krs["geometry"] = krs.geometry.simplify(400)
lan = laender(25832); lan["geometry"] = lan.geometry.simplify(400)
X0, Y0, X1, Y1 = krs.total_bounds
INK, MUTED = "#1f2937", "#6b7280"
FONT = "font-family:DejaVu Sans,Arial,sans-serif"
STAND = open(HERE.parent / "STAND").read().strip() if (HERE.parent / "STAND").exists() else ""


def lerp(stops, t):
    t = min(max(t, 0), 1) * (len(stops) - 1); i = min(int(t), len(stops) - 2); f = t - i
    a = [int(stops[i][k:k + 2], 16) for k in (1, 3, 5)]; b = [int(stops[i + 1][k:k + 2], 16) for k in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


DIV = ["#9a3412", "#f59e0b", "#f3f4f6", "#60a5fa", "#1e40af"]
SEQ = ["#f3f4f6", "#fcd34d", "#f59e0b", "#b45309", "#7c2d12"]


class Map:
    def __init__(self, w):
        self.w = w; self.s = w / (X1 - X0); self.h = (Y1 - Y0) * self.s

    def xy(self, x, y):
        return (x - X0) * self.s, (Y1 - y) * self.s

    def path(self, geom):
        out = []
        for poly in getattr(geom, "geoms", [geom]):
            for ring in [poly.exterior, *poly.interiors] if poly.geom_type == "Polygon" else [poly]:
                c = np.asarray(ring.coords)
                px = [self.xy(x, y) for x, y in c]
                out.append("M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in px) + "Z")
        return "".join(out)

    def layer(self, gdf, fill_fn=None, stroke="#ffffff", sw=0.4, fill="none"):
        parts = []
        for _, row in gdf.iterrows():
            f = fill_fn(row) if fill_fn else fill
            parts.append(f'<path d="{self.path(row.geometry)}" fill="{f}" stroke="{stroke}" stroke-width="{sw}"/>')
        return "".join(parts)


def title(x, y, lines, size=17):
    t = [f'<text x="{x}" y="{y}" style="{FONT};font-size:{size}px;font-weight:700" fill="{INK}">{lines[0]}</text>']
    for k, l in enumerate(lines[1:]):
        t.append(f'<text x="{x}" y="{y + 22 + 19 * k}" style="{FONT};font-size:14px" fill="{MUTED}">{l}</text>')
    return "".join(t)


def bar(x, y, w, stops, ticks, label):
    n = 60; out = []
    for k in range(n):
        out.append(f'<rect x="{x + w * k / n:.1f}" y="{y}" width="{w / n + .6:.1f}" height="12" fill="{lerp(stops, k / (n - 1))}"/>')
    for pos, txt in ticks:
        out.append(f'<text x="{x + w * pos:.1f}" y="{y + 30}" text-anchor="middle" style="{FONT};font-size:12px" fill="{INK}">{txt}</text>')
    out.append(f'<text x="{x}" y="{y - 8}" style="{FONT};font-size:12.5px" fill="{MUTED}">{label}</text>')
    return "".join(out)


def svg(w, h, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>'



OSM = f"OpenStreetMap-Mitwirkende (ODbL), Stand {STAND}"
BKG = "Grenzen © GeoBasis-DE / BKG (dl-de/by-2-0)"
out = {}

r = pd.read_csv(OUT / "abdeckung_kreise.csv", dtype={"ars5": str}).set_index("ars5")
panels = [("index_apotheken", "Apotheken", "verhaeltnis"), ("index_arzt_hausaerzte", "Arztpraxen", None),
          ("index_post", "Postfilialen", "verhaeltnis"), ("index_kitas", "Kitas (kindergarten und childcare)", "verhaeltnis")]
import json
summ = json.load(open(OUT / "abdeckung_zusammenfassung.json"))
key = {"index_apotheken": "apotheken", "index_post": "post", "index_kitas": "kitas"}
m = Map(560); body = []; PW, PH = 600, m.h + 80
for k, (col, name, _) in enumerate(panels):
    g = krs.merge(r[[col]].rename(columns={col: "idx"}), left_on="ars5", right_index=True)
    sub = ("Praxen in OSM je Hausärztin oder Hausarzt laut INKAR" if col == "index_arzt_hausaerzte"
           else f"OSM zu amtlich im Bundesschnitt: {summ[key[col]]['verhaeltnis'] * 100:.0f} %")
    ox, oy = 20 + (k % 2) * PW, 20 + (k // 2) * PH
    body.append(title(ox, oy + 18, [name, sub]))
    body.append(f'<g transform="translate({ox},{oy + 60})">')
    body.append(m.layer(g, lambda row: "#e5e7eb" if np.isnan(row.idx) else lerp(DIV, (row.idx - 0.3) / 1.4), sw=0.35))
    body.append(m.layer(lan, stroke=INK, sw=0.7) + "</g>")
y = 20 + 2 * PH + 20
body.append(bar(20, y + 10, 700, DIV, [(0, "0,3"), (0.5 / 1.4, "0,8"), (0.7 / 1.4, "1"), (1.2 / 1.4, "1,5"), (1, "1,7")],
                "Verhältnis OSM zu amtlich im Kreis, geteilt durch den Bundesschnitt (1 = wie im Schnitt, 0,5 = halb so dicht erfasst)"))
body.append(f'<text x="20" y="{y + 68}" style="{FONT};font-size:11px" fill="{MUTED}">Daten: {OSM} · INKAR, BBSR Bonn 2025 (dl-de/by-2-0) · {BKG}</text>')
out["abdeckung_kreise"] = svg(2 * PW + 20, y + 80, "".join(body))


def single(name, csv, col, vmax, ticks, head, src):
    d = pd.read_csv(OUT / csv, dtype={"ars5": str}).set_index("ars5")
    g = krs.merge(d[[col]].rename(columns={col: "v"}), left_on="ars5", right_index=True)
    m = Map(900); b = [title(20, 34, head)]
    b.append('<g transform="translate(20,90)">' + m.layer(g, lambda row: lerp(SEQ, row.v / vmax), sw=0.35) + m.layer(lan, stroke=INK, sw=0.8) + "</g>")
    y = 90 + m.h + 30
    b.append(bar(20, y + 10, 600, SEQ, ticks, "Anteil im Kreis"))
    b.append(f'<text x="20" y="{y + 68}" style="{FONT};font-size:11px" fill="{MUTED}">{src}</text>')
    out[name] = svg(940, y + 80, "".join(b))


single("adressluecken_kreise", "adressluecken_kreise.csv", "anteil", 0.3,
       [(0, "0 %"), (1 / 3, "10 %"), (2 / 3, "20 %"), (1, "30 % und mehr")],
       ["Einwohner ohne Hausnummer in OSM in ihrer Nähe",
        "Anteil der Einwohner, deren 100-m-Zelle und alle acht Nachbarzellen keine Adresse in OSM haben"],
       f"Daten: {OSM} · Zensus 2022, Statistische Ämter des Bundes und der Länder (dl-de/by-2-0) · {BKG}")
single("haltestellen_kreise", "haltestellen_kreise.csv", "anteil_ohne_osm", 0.4,
       [(0, "0 %"), (0.25, "10 %"), (0.5, "20 %"), (0.75, "30 %"), (1, "40 % und mehr")],
       ["Haltestellen aus dem Fahrplan ohne Haltestelle in OSM",
        "Anteil der Haltestellen im DELFI-Fahrplan, bei denen in 100 m kein Haltestellen-Objekt in OSM liegt"],
       f"Daten: {OSM} · DELFI e.V., Deutschlandweite Fahrplandaten GTFS (CC BY 4.0) · {BKG}")

for name, s in out.items():
    (KARTEN / f"{name}.svg").write_text(s)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Playwright not installed: SVG only")
else:
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, s in out.items():
            W, H = map(float, re.search(r'width="([\d.]+)" height="([\d.]+)"', s).groups())
            pg = b.new_page(viewport={"width": int(W), "height": int(H)}, device_scale_factor=2)
            pg.set_content(f'<html><body style="margin:0">{s}</body></html>')
            pg.screenshot(path=str(KARTEN / f"{name}.png"), full_page=True)
            pg.close()
        b.close()
print("maps:", ", ".join(out))
