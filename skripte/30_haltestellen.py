"""DELFI stop areas (GTFS, CC BY 4.0) without any OSM stop object nearby.

Stop area = the first three parts of the DHID stop_id (de:<kreis>:<number>);
only platforms that appear in stop_times count. OSM stop objects:
highway=bus_stop, public_transport=platform/stop_position/station,
railway=tram_stop/halt/station/platform, amenity=bus_station, one point each.
An area is found when any of its platforms lies within 100 m of such an
object. Route types per area tell regular buses from demand-responsive ones
(GTFS route_type 715).

Writes listen/haltestellen_ohne_osm.csv, listen/haltestellen_kreise.csv,
listen/haltestellen_zusammenfassung.json.
"""
import json
import zipfile

import numpy as np
import osmium
import pandas as pd
from scipy.spatial import cKDTree

from common import OUT, TO_LAEA, env, assign_kreis

STOP_TAGS = {("highway", "bus_stop"), ("public_transport", "platform"), ("public_transport", "stop_position"),
             ("public_transport", "station"), ("railway", "tram_stop"), ("railway", "halt"),
             ("railway", "station"), ("railway", "platform"), ("amenity", "bus_station")}
RAIL = {"0", "1", "2", "100", "101", "102", "103", "106", "109", "400", "900"}


def is_stop(tags):
    return any((k, tags.get(k)) in STOP_TAGS for k in ("highway", "public_transport", "railway", "amenity"))


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.lon, self.lat = [], []

    def node(self, n):
        if is_stop(n.tags):
            self.lon.append(n.location.lon); self.lat.append(n.location.lat)

    def way(self, w):
        if not is_stop(w.tags):
            return
        try:
            xs = [nd.lon for nd in w.nodes]; ys = [nd.lat for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.lon.append(sum(xs) / len(xs)); self.lat.append(sum(ys) / len(ys))


z = zipfile.ZipFile(env("DELFI_GTFS"))
routes = pd.read_csv(z.open("routes.txt"), dtype=str, usecols=["route_id", "route_type"])
trips = pd.read_csv(z.open("trips.txt"), dtype=str, usecols=["trip_id", "route_id"])
trip_type = trips.merge(routes, on="route_id").set_index("trip_id").route_type
types = {}
for ch in pd.read_csv(z.open("stop_times.txt"), usecols=["trip_id", "stop_id"], dtype=str, chunksize=5_000_000):
    ch["rt"] = ch.trip_id.map(trip_type)
    for sid, rt in ch[["stop_id", "rt"]].drop_duplicates().itertuples(index=False):
        types.setdefault(sid, set()).add(rt)
s = pd.read_csv(z.open("stops.txt"), dtype=str)
s = s[s.location_type.fillna("0").eq("0") & s.stop_id.isin(types.keys()) & s.stop_id.str.startswith("de:")].copy()
s["area"] = s.stop_id.str.split(":").str[:3].str.join(":")
s["x"], s["y"] = TO_LAEA.transform(s.stop_lon.astype(float).values, s.stop_lat.astype(float).values)

h = H(); h.apply_file(env("OSM_POI"), locations=True, idx="flex_mem")
ox, oy = TO_LAEA.transform(np.array(h.lon), np.array(h.lat))
s["dist"] = cKDTree(np.c_[ox, oy]).query(np.c_[s.x, s.y], k=1)[0]
s["types"] = s.stop_id.map(types)
a = s.groupby("area").agg(name=("stop_name", "first"), dist=("dist", "min"), x=("x", "mean"), y=("y", "mean"),
                          lat=("stop_lat", "first"), lon=("stop_lon", "first"), steige=("stop_id", "size"),
                          types=("types", lambda v: set().union(*v)))


def kind(t):
    if t <= {"715"}:
        return "nur Rufbus"
    if t & RAIL:
        return "Bahn oder Tram"
    return "Bus"


a["verkehr"] = a.types.map(kind)
a["gefunden"] = a.dist <= 100
a = assign_kreis(a.reset_index())
miss = a[~a.gefunden].copy()
miss["entfernung_naechstes_osm_objekt_m"] = miss.dist.round().astype(int)
miss[["area", "name", "lat", "lon", "steige", "verkehr", "entfernung_naechstes_osm_objekt_m", "ars5", "GEN"]].rename(
    columns={"area": "dhid", "GEN": "kreis"}).sort_values(["ars5", "name"]).to_csv(OUT / "haltestellen_ohne_osm.csv", index=False)
k = a.groupby(["ars5", "GEN", "BEZ"]).agg(haltestellen=("gefunden", "size"), ohne_osm=("gefunden", lambda v: int((~v).sum())))
k["anteil_ohne_osm"] = k.ohne_osm / k.haltestellen
k.sort_values("anteil_ohne_osm", ascending=False).round(4).to_csv(OUT / "haltestellen_kreise.csv")
summary = {"osm_haltestellen_objekte": len(ox), "haltestellen_im_fahrplan": int(len(a)),
           "gefunden_100m": int(a.gefunden.sum()), "anteil_gefunden": round(float(a.gefunden.mean()), 4),
           "ohne_osm_100m": int((~a.gefunden).sum()), "ohne_osm_250m": int((a.dist > 250).sum()),
           "ohne_osm_nach_verkehr": miss.verkehr.value_counts().to_dict(),
           "alle_nach_verkehr": a.verkehr.value_counts().to_dict(),
           "kreise_ab_20_prozent": int((k.anteil_ohne_osm >= .2).sum()),
           "kreise_bis_2_prozent": int((k.anteil_ohne_osm <= .02).sum()),
           "kreise_top": k.sort_values("anteil_ohne_osm", ascending=False).head(15).reset_index().round(3).values.tolist()}
json.dump(summary, open(OUT / "haltestellen_zusammenfassung.json", "w"), indent=1, ensure_ascii=False, default=float)
print(json.dumps(summary, indent=1, ensure_ascii=False, default=float))
