"""Inhabited Zensus cells without any OSM house number in their 3x3 neighbourhood.

Address = node or way with addr:housenumber (a way by the mean of its nodes;
multipolygon relations are left out, they are few). Building = way with
building=*, by its first node. Cells are the Zensus 2022 100 m LAEA grid.
A resident counts as without address when neither the own cell nor any of the
eight neighbours holds an address. For those cells: is a building mapped?

Writes listen/adressluecken_kreise.csv, listen/adressluecken_laender.csv,
listen/adressluecken_zellen.csv, listen/adressen_zusammenfassung.json.
"""
import json

import numpy as np
import osmium
import pandas as pd

from common import OUT, TO_LAEA, env, assign_kreis

K = 10_000_000  # key = ix * K + iy, ix/iy = 100 m cell indices in EPSG:3035


class Points(osmium.SimpleHandler):
    def __init__(self, key, first_node_only=False):
        super().__init__()
        self.key, self.first = key, first_node_only
        self.lon, self.lat = [], []

    def node(self, n):
        if self.key in n.tags:
            self.lon.append(n.location.lon); self.lat.append(n.location.lat)

    def way(self, w):
        if self.key not in w.tags:
            return
        try:
            if self.first:
                loc = w.nodes[0].location
                self.lon.append(loc.lon); self.lat.append(loc.lat)
            else:
                xs = [nd.lon for nd in w.nodes]; ys = [nd.lat for nd in w.nodes]
                self.lon.append(sum(xs) / len(xs)); self.lat.append(sum(ys) / len(ys))
        except osmium.InvalidLocationError:
            pass


def cell_counts(path, key, first=False, nodes=True):
    h = Points(key, first)
    if not nodes:
        h.node = lambda n: None
    h.apply_file(path, locations=True, idx="flex_mem")
    x, y = TO_LAEA.transform(np.array(h.lon), np.array(h.lat))
    keys = (x // 100).astype(np.int64) * K + (y // 100).astype(np.int64)
    u, n = np.unique(keys, return_counts=True)
    return pd.Series(n, index=u), len(keys)


addr, n_addr = cell_counts(env("OSM_ADDR"), "addr:housenumber")
bld, n_bld = cell_counts(env("OSM_BUILDINGS"), "building", first=True, nodes=False)
interp, n_interp = cell_counts(env("OSM_POI"), "addr:interpolation", nodes=False)

z = pd.read_csv(env("ZENSUS_POP"), sep=";", usecols=["GITTER_ID_100m", "x_mp_100m", "y_mp_100m", "Einwohner"])
z["einwohner"] = pd.to_numeric(z.Einwohner, errors="coerce")
z = z[z.einwohner > 0].copy()
z["ix"] = (z.x_mp_100m // 100).astype(np.int64); z["iy"] = (z.y_mp_100m // 100).astype(np.int64)
z["key"] = z.ix * K + z.iy
addr_set = set(addr.index.tolist())
z["adresse_3x3"] = False
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        z["adresse_3x3"] |= (z.key + dx * K + dy).isin(addr_set)
gap = z[~z.adresse_3x3].copy()
gap["gebaeude_in_zelle"] = gap.key.map(bld).fillna(0).astype(int)
near_interp = set()
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        near_interp |= set((interp.index + dx * K + dy).tolist())
gap["interpolation_nah"] = gap.key.isin(near_interp)

z = assign_kreis(z.rename(columns={"x_mp_100m": "x", "y_mp_100m": "y"}))
z["land"] = z.ars5.str[:2]
gap = gap.merge(z[["key", "ars5", "GEN", "land"]], on="key", how="left")
z["luecke"] = ~z.adresse_3x3
z["luecke_ew"] = z.einwohner.where(z.luecke, 0)
gk = gap.groupby("ars5").apply(lambda d: d.einwohner[d.gebaeude_in_zelle > 0].sum(), include_groups=False)
k = z.groupby(["ars5", "GEN", "BEZ"]).agg(einwohner=("einwohner", "sum"), einwohner_ohne_adresse=("luecke_ew", "sum"),
                                         zellen_ohne_adresse=("luecke", "sum")).reset_index().set_index("ars5")
k["anteil"] = k.einwohner_ohne_adresse / k.einwohner
k["davon_mit_gebaeude_in_zelle"] = gk.reindex(k.index).fillna(0)
k.sort_values("anteil", ascending=False).round(4).to_csv(OUT / "adressluecken_kreise.csv")
LAND = {"01": "Schleswig-Holstein", "02": "Hamburg", "03": "Niedersachsen", "04": "Bremen", "05": "Nordrhein-Westfalen",
        "06": "Hessen", "07": "Rheinland-Pfalz", "08": "Baden-Württemberg", "09": "Bayern", "10": "Saarland",
        "11": "Berlin", "12": "Brandenburg", "13": "Mecklenburg-Vorpommern", "14": "Sachsen", "15": "Sachsen-Anhalt", "16": "Thüringen"}
lnd = z.groupby("land").agg(einwohner=("einwohner", "sum"), einwohner_ohne_adresse=("luecke_ew", "sum"))
lnd["anteil"] = lnd.einwohner_ohne_adresse / lnd.einwohner
lnd.index = lnd.index.map(LAND)
lnd.sort_values("anteil", ascending=False).round(4).to_csv(OUT / "adressluecken_laender.csv")

lon, lat = TO_LAEA.transform(gap.ix * 100 + 50, gap.iy * 100 + 50, direction="INVERSE")
gap["lat"], gap["lon"] = np.round(lat, 6), np.round(lon, 6)
gap[["GITTER_ID_100m", "lat", "lon", "einwohner", "gebaeude_in_zelle", "ars5", "GEN"]].rename(
    columns={"GEN": "kreis"}).sort_values(["ars5", "einwohner"], ascending=[True, False]).to_csv(OUT / "adressluecken_zellen.csv", index=False)

w = gap.einwohner
s = {"adressen_osm": n_addr, "gebaeude_osm": n_bld, "interpolationslinien_osm": n_interp,
     "bewohnte_zellen": int(len(z)), "einwohner": int(z.einwohner.sum()),
     "zellen_ohne_adresse_3x3": int(len(gap)), "einwohner_ohne_adresse_3x3": int(w.sum()),
     "zellen_mind_10_einwohner": int((w >= 10).sum()), "einwohner_in_zellen_mind_10": int(w[w >= 10].sum()),
     "anteil_mit_gebaeude_in_zelle": round(float(w[gap.gebaeude_in_zelle > 0].sum() / w.sum()), 4),
     "zellen_nahe_interpolation": int(gap.interpolation_nah.sum()),
     "laender": lnd.sort_values("anteil", ascending=False).round(4).reset_index().values.tolist(),
     "kreise_top": k.sort_values("anteil", ascending=False).head(15).reset_index()[["GEN", "anteil", "davon_mit_gebaeude_in_zelle", "einwohner_ohne_adresse"]].round(4).values.tolist()}
json.dump(s, open(OUT / "adressen_zusammenfassung.json", "w"), indent=1, ensure_ascii=False, default=float)
print(json.dumps(s, indent=1, ensure_ascii=False, default=float))
