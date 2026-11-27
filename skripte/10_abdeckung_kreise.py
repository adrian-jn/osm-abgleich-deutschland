"""OSM objects per Kreis against INKAR counts (pharmacies, Kitas, post offices, doctors).

One point per object: the node, or the mean of the way's or area's outer nodes.
The index is (OSM / official in the Kreis) / (OSM / official nationally), so 1
is the national level and 0.5 half of it. Doctors have no official count of
practices: they are compared twice, with Hausärzte and with all Ärzte, and a
Kreis is listed as weak only when it is below half in both.

Writes listen/abdeckung_kreise.csv, listen/supermarkt_marken.csv,
listen/abdeckung_zusammenfassung.json.
"""
import collections
import json

import numpy as np
import osmium
import pandas as pd
import geopandas as gpd

from common import DATEN, OUT, env, kreise

NON_DP = ("Hermes", "DPD", "GLS", "UPS", "Mail Boxes", "Citipost", "Amazon")
pts = collections.defaultdict(list)
sup = []


def cats(t):
    am, sh = t.get("amenity"), t.get("shop")
    out = []
    if am == "pharmacy":
        out.append("apotheke")
    if sh == "supermarket":
        out.append("supermarkt")
    if am == "kindergarten":
        out.append("kindergarten")
    if am in ("kindergarten", "childcare"):
        out.append("kita")
    if am in ("doctors", "clinic"):
        out.append("arztpraxis")
    po = t.get("post_office")
    who = (t.get("brand") or "") + (t.get("operator") or "")
    if (am == "post_office" or (po and po != "no")) and not any(b in who for b in NON_DP):
        out.append("post")
    return out


class H(osmium.SimpleHandler):
    def add(self, c, lon, lat, tags):
        for k in c:
            pts[k].append((lon, lat))
        if "supermarkt" in c:
            sup.append((lon, lat, (tags.get("brand") or "").strip().lower()))

    def node(self, n):
        c = cats(n.tags)
        if c:
            self.add(c, n.location.lon, n.location.lat, n.tags)

    def way(self, w):
        c = cats(w.tags)
        if not c or w.is_closed():       # closed ways arrive through area()
            return
        try:
            xs = [nd.lon for nd in w.nodes]; ys = [nd.lat for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.add(c, sum(xs) / len(xs), sum(ys) / len(ys), w.tags)

    def area(self, a):
        c = cats(a.tags)
        if not c:
            return
        xs, ys = [], []
        try:
            for ring in a.outer_rings():
                for nd in ring:
                    xs.append(nd.lon); ys.append(nd.lat)
        except osmium.InvalidLocationError:
            return
        if xs:
            self.add(c, sum(xs) / len(xs), sum(ys) / len(ys), a.tags)


H().apply_file(env("OSM_POI"), locations=True, idx="flex_mem")
krs = kreise(4326)
res = krs[["ars5", "GEN", "BEZ"]].set_index("ars5")
for k, lst in pts.items():
    g = gpd.GeoDataFrame(geometry=gpd.points_from_xy(*zip(*lst)), crs=4326)
    j = gpd.sjoin(g, krs[["ars5", "geometry"]], predicate="within")
    res[f"osm_{k}"] = j.groupby("ars5").size().reindex(res.index).fillna(0).astype(int)

sg = gpd.GeoDataFrame({"brand": [b for *_, b in sup]},
                      geometry=gpd.points_from_xy([x for x, *_ in sup], [y for _, y, _ in sup]), crs=4326)
sg = gpd.sjoin(sg, krs[["geometry"]], predicate="within")
brands = sg.brand.replace("", "(ohne brand)").value_counts()
brands.rename_axis("brand").rename("objekte_in_osm").head(40).to_csv(OUT / "supermarkt_marken.csv")

ik = pd.read_csv(DATEN / "inkar_kreise.csv", dtype={"kennziffer": str})
ik["ars5"] = ik.kennziffer.str.zfill(8).str[:5]
p = ik.pivot(index="ars5", columns="kuerzel", values="wert")
res["amtlich_apotheken"] = p["apoth"]
res["amtlich_kitas"] = p["Kitas"]
res["amtlich_postfilialen"] = p["H01_pfil_"]
res["einwohner"] = p["bev_korr"]
res["amtlich_hausaerzte"] = p["q_hausarzt_bev"] * p["bev_korr"] / 1e4
res["amtlich_aerzte"] = p["q_ärzte_bev"] * p["bev_korr"] / 1e4

pairs = {"apotheken": ("osm_apotheke", "amtlich_apotheken"), "kitas": ("osm_kita", "amtlich_kitas"),
         "post": ("osm_post", "amtlich_postfilialen"), "arzt_hausaerzte": ("osm_arztpraxis", "amtlich_hausaerzte"),
         "arzt_aerzte": ("osm_arztpraxis", "amtlich_aerzte")}
summary = {"osm_objekte_bundesweit": {k: len(v) for k, v in pts.items()}}
for name, (o, i) in pairs.items():
    ok = res[i] > 0                      # an official zero is a gap in INKAR, not a count
    nat = res.loc[ok, o].sum() / res.loc[ok, i].sum()
    res[f"index_{name}"] = np.where(ok, (res[o] / res[i]) / nat, np.nan)
    r = res[ok]
    lo5, lo7 = r[r[f"index_{name}"] < .5], r[r[f"index_{name}"] < .7]
    summary[name] = {"osm": int(r[o].sum()), "amtlich": round(float(r[i].sum())), "verhaeltnis": round(float(nat), 3),
                     "kreise_unter_0_5": len(lo5), "einwohner_unter_0_5": int(lo5.einwohner.sum()),
                     "kreise_unter_0_7": len(lo7), "einwohner_unter_0_7": int(lo7.einwohner.sum()),
                     "minimum": round(float(r[f"index_{name}"].min()), 3)}
both = res[(res.index_arzt_hausaerzte < .5) & (res.index_arzt_aerzte < .5)]
summary["arzt_beide_unter_0_5"] = {"kreise": len(both), "einwohner": int(both.einwohner.sum()),
                                   "liste": both.sort_values("index_arzt_hausaerzte").GEN.tolist()}
res["arzt_schwach_in_beiden_vergleichen"] = res.index.isin(both.index)
summary["marken"] = brands.head(15).to_dict()
res.round(3).to_csv(OUT / "abdeckung_kreise.csv")
json.dump(summary, open(OUT / "abdeckung_zusammenfassung.json", "w"), indent=1, ensure_ascii=False, default=float)
print(json.dumps(summary, indent=1, ensure_ascii=False, default=float))
