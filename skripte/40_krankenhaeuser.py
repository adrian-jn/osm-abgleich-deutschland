"""Hospitals of the Destatis register with an emergency level, against OSM.

Inputs: daten/krankenhaeuser_notfallstufe.csv (Krankenhausverzeichnis 2024,
sites in Notfallstufe 1 to 3, closures after the register's date removed,
located by their address) and daten/krankenhaus_schliessungen.csv (closures
and closed emergency departments 2025/2026 with sources). OSM:
amenity=hospital or healthcare=hospital. A site is matched to OSM objects
within 250 m, preferring one with emergency=yes, then the nearest.

Writes listen/krankenhaeuser_ohne_emergency.csv, listen/krankenhaeuser_emergency_no.csv,
listen/krankenhaeuser_ohne_osm.csv, listen/krankenhaeuser_geschlossen_in_osm.csv,
listen/krankenhaeuser_zusammenfassung.json.
"""
import json

import geopandas as gpd
import osmium
import pandas as pd
from shapely import wkb

from common import DATEN, OUT, env

fab = osmium.geom.WKBFactory()
rows = []

# Candidates read in the live OSM database on 2026-11-27 that are mapped correctly.
REVIEWED_OK = {"w159485889": "OSM nennt heute die Tagesklinik am Standort",
               "w1524607991": "psychiatrische Notfälle werden dort weiter versorgt"}


class H(osmium.SimpleHandler):
    def keep(self, o):
        return o.tags.get("amenity") == "hospital" or o.tags.get("healthcare") == "hospital"

    def rec(self, kind, oid, t, g):
        rows.append({"osm": f"{kind}{oid}", "osm_name": t.get("name", ""), "amenity": t.get("amenity", ""),
                     "healthcare": t.get("healthcare", ""), "emergency": t.get("emergency", ""),
                     "disused": t.get("disused:amenity", ""), "geometry": g})

    def node(self, n):
        if self.keep(n):
            self.rec("n", n.id, n.tags, wkb.loads(fab.create_point(n), hex=True))

    def way(self, w):
        if self.keep(w) and not w.is_closed():
            try:
                self.rec("w", w.id, w.tags, wkb.loads(fab.create_linestring(w), hex=True))
            except Exception:
                pass

    def area(self, a):
        if self.keep(a):
            try:
                g = wkb.loads(fab.create_multipolygon(a), hex=True)
            except Exception:
                return
            self.rec("w" if a.from_way() else "r", a.orig_id(), a.tags, g)


H().apply_file(env("OSM_POI"), locations=True, idx="flex_mem")
osm = gpd.GeoDataFrame(rows, crs=4326).to_crs(3035)


def load(name):
    d = pd.read_csv(DATEN / name)
    d = d[d.lat.notna()]
    return gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.lon, d.lat), crs=4326).to_crs(3035)


def nearest(pts, maxd=250):
    j = gpd.sjoin_nearest(pts, osm, how="left", max_distance=maxd, distance_col="entfernung_m")
    j["pref"] = (j.emergency != "yes").astype(int)
    return j.sort_values(["pref", "entfernung_m"]).groupby(level=0).first()


act = load("krankenhaeuser_notfallstufe.csv")
a = nearest(act)
cols = ["kh_id", "name", "standort", "strasse", "hausnummer", "plz", "ort", "notfallstufe", "lat", "lon", "osm", "osm_name", "emergency"]
a[a.osm.notna() & (a.emergency == "")][cols].sort_values(["notfallstufe", "ort"], ascending=[False, True]).to_csv(OUT / "krankenhaeuser_ohne_emergency.csv", index=False)
a[a.emergency == "no"][cols].to_csv(OUT / "krankenhaeuser_emergency_no.csv", index=False)
a[a.osm.isna()][cols[:10] + ["verortung"]].to_csv(OUT / "krankenhaeuser_ohne_osm.csv", index=False)
s = {"osm_krankenhaus_objekte": len(osm), "standorte_notfallstufe_1_3": len(act),
     "ohne_osm_250m": int(a.osm.isna().sum()), "emergency_yes": int((a.emergency == "yes").sum()),
     "emergency_fehlt": int((a.osm.notna() & (a.emergency == "")).sum()), "emergency_no": int((a.emergency == "no").sum())}
s["emergency_anderer_wert"] = int(a.osm.notna().sum()) - s["emergency_yes"] - s["emergency_fehlt"] - s["emergency_no"]
for st in (1, 2, 3):
    m = a[a.notfallstufe == st]
    s[f"stufe_{st}"] = {"standorte": len(m), "emergency_yes": int((m.emergency == "yes").sum()),
                        "mit_osm": int(m.osm.notna().sum())}

cl = nearest(load("krankenhaus_schliessungen.csv"))
cl = cl[cl.osm.notna() & ((cl.amenity == "hospital") | (cl.healthcare == "hospital"))].copy()
FULL = ("Krankenhaus geschlossen", "Krankenhaus am", "Krankenhaus 06/2026", "stationäre Versorgung", "Akutversorgung beendet", "Stationen")
cl["haus_zu"] = cl.was.apply(lambda w: any(f in w for f in FULL))
# A closed house still mapped as a hospital, or a closed emergency department still emergency=yes.
cl["hinweis"] = ""
cl.loc[cl.haus_zu, "hinweis"] = "Haus geschlossen, in OSM noch Krankenhaus"
cl.loc[cl.haus_zu & (cl.emergency == "yes"), "hinweis"] = "Haus geschlossen, in OSM noch Krankenhaus mit emergency=yes"
cl.loc[~cl.haus_zu & (cl.emergency == "yes"), "hinweis"] = "Notaufnahme geschlossen, in OSM noch emergency=yes"
cl.loc[cl.haus_zu & (cl.amenity == "") & (cl.disused == "hospital"), "hinweis"] = "disused:amenity gesetzt, healthcare=hospital noch da"
cl = cl[(cl.hinweis != "") & ~cl.osm.isin(REVIEWED_OK)]
cl[["name", "seit", "was", "quelle", "osm", "osm_name", "amenity", "healthcare", "disused", "emergency", "hinweis", "entfernung_m"]].round(0).to_csv(
    OUT / "krankenhaeuser_geschlossen_in_osm.csv", index=False)
s["geschlossen_auffaellig"] = len(cl)
s["geschlossen_liste"] = cl[["name", "hinweis", "osm"]].values.tolist()
json.dump(s, open(OUT / "krankenhaeuser_zusammenfassung.json", "w"), indent=1, ensure_ascii=False, default=float)
print(json.dumps(s, indent=1, ensure_ascii=False, default=float))
