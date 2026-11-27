"""Large landuse=farmland/meadow/grass and natural=grassland polygons glued to roads,
waterways or boundaries.

The German forum thread "Viele fehlende Landnutzungsflächen in Deutschland"
(community.openstreetmap.org/t/149385) describes the pattern: huge polygons
drawn to fill white space, sharing nodes with highways, ditches and
administrative boundaries, which mappers then take apart by hand. Size alone
is no defect, so a polygon is listed only when it is glued AND large or made
of many member ways.

Inputs (00_extract.sh): OSM_LANDUSE (wr/landuse=farmland,meadow,grass and
wr/natural=grassland with members), OSM_LINES (w/highway w/waterway, ways only), OSM_BOUNDARIES
(r/boundary=administrative with members), VG250.

A node counts as glued when the same node also belongs to a highway, a
waterway or a way of an administrative boundary relation. Shared nodes with
other landuse are normal and not counted.

Writes listen/landuse_verklebt.csv, listen/landuse_verklebt.geojson (points)
and listen/landuse_verklebt_zusammenfassung.json. With KEEP_ALL=<path.parquet>
the full candidate table is written there as well, for choosing thresholds.
"""
import json
import math
import os
from array import array

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd

from common import OUT, env, kreise

LANDUSE = {"farmland", "meadow", "grass"}
MIN_HA_CANDIDATE = 10      # polygons below this are not looked at
LIST_MIN_GLUED = 25        # listed: at least this many glued nodes ...
LIST_MIN_HA = 50           # ... and at least this large
LIST_MIN_MEMBERS = 30      # ... or a relation of at least this many members
# Fixed in OSM after the extract (checked in the live database on 2026-11-27).
FIXED_AFTER_EXTRACT = {"r215294": "national park boundary, natural=grassland removed 2026-11-26 21:37 UTC"}
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0


def kind_of(tags):
    if tags.get("landuse") in LANDUSE:
        return "landuse=" + tags.get("landuse")
    if tags.get("natural") == "grassland":
        return "natural=grassland"
    return None


def ring_area_m2(lons, lats, lat0):
    x = np.asarray(lons) * M_PER_DEG_LON * math.cos(math.radians(lat0))
    y = np.asarray(lats) * M_PER_DEG_LAT
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


class Landuse(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows, self.node_ids, self.node_idx = [], array("q"), array("i")
        self.members = {}

    def relation(self, r):
        if kind_of(r.tags):
            self.members[r.id] = len(r.members)

    def area(self, a):
        tag = kind_of(a.tags)
        if tag is None:
            return
        refs, area, lat0, cx, cy, n_outer = [], 0.0, None, 0.0, 0.0, 0
        try:
            for outer in a.outer_rings():
                lons = [nd.lon for nd in outer]; lats = [nd.lat for nd in outer]
                if lat0 is None:
                    lat0 = lats[0]; cx = sum(lons) / len(lons); cy = sum(lats) / len(lats)
                area += ring_area_m2(lons, lats, lat0)
                refs.extend(nd.ref for nd in outer)
                n_outer += 1
                for inner in a.inner_rings(outer):
                    area -= ring_area_m2([nd.lon for nd in inner], [nd.lat for nd in inner], lat0)
                    refs.extend(nd.ref for nd in inner)
        except osmium.InvalidLocationError:
            return
        if lat0 is None or area < MIN_HA_CANDIDATE * 10_000:
            return
        kind = "w" if a.from_way() else "r"
        idx = len(self.rows)
        u = set(refs)
        self.rows.append((f"{kind}{a.orig_id()}", kind, a.orig_id(), tag, area / 10_000, len(u), n_outer, cx, cy))
        self.node_ids.extend(u)
        self.node_idx.extend([idx] * len(u))


class Lines(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.hw, self.ww = array("q"), array("q")

    def way(self, w):
        if "highway" in w.tags:
            self.hw.extend(n.ref for n in w.nodes)
        elif "waterway" in w.tags:
            self.ww.extend(n.ref for n in w.nodes)


class BoundaryRelations(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = set()

    def relation(self, r):
        if r.tags.get("boundary") == "administrative":
            self.ways.update(m.ref for m in r.members if m.type == "w")


class BoundaryWays(osmium.SimpleHandler):
    def __init__(self, ways):
        super().__init__()
        self.ways, self.nodes = ways, array("q")

    def way(self, w):
        if w.id in self.ways:
            self.nodes.extend(n.ref for n in w.nodes)


h = Landuse()
h.apply_file(env("OSM_LANDUSE"), locations=True, idx="flex_mem")
cand = pd.DataFrame(h.rows, columns=["osm", "typ", "id", "tag", "flaeche_ha", "knoten", "aussenringe", "lon", "lat"])
cand["mitglieder"] = [h.members.get(i, 0) if t == "r" else 0 for t, i in zip(cand.typ, cand.id)]
nid = np.frombuffer(h.node_ids, dtype=np.int64); nix = np.frombuffer(h.node_idx, dtype=np.int32)
print(f"candidates >= {MIN_HA_CANDIDATE} ha: {len(cand)}, nodes {len(nid)}", flush=True)

ln = Lines(); ln.apply_file(env("OSM_LINES"))
br = BoundaryRelations(); br.apply_file(env("OSM_BOUNDARIES"))
bw = BoundaryWays(br.ways); bw.apply_file(env("OSM_BOUNDARIES"))
glued_any = np.zeros(len(nid), dtype=bool)
for name, arr in (("an_strassen", ln.hw), ("an_gewaessern", ln.ww), ("an_grenzen", bw.nodes)):
    other = np.unique(np.frombuffer(arr, dtype=np.int64))
    pos = np.minimum(np.searchsorted(other, nid), len(other) - 1)
    hit = other[pos] == nid
    glued_any |= hit
    cand[f"knoten_{name}"] = np.bincount(nix[hit], minlength=len(cand))
    print(f"{name}: {len(other)} distinct nodes", flush=True)
    del other
cand["knoten_verklebt"] = np.bincount(nix[glued_any], minlength=len(cand))
cand["anteil_verklebt"] = (cand.knoten_verklebt / cand.knoten).round(3)

pts = gpd.GeoDataFrame(cand, geometry=gpd.points_from_xy(cand.lon, cand.lat), crs=4326).to_crs(3035)
gem = gpd.read_file(f"zip://{env('VG250')}!vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_GEM.shp")
gem = gem[gem.GF == 4][["GEN", "geometry"]].rename(columns={"GEN": "gemeinde"}).to_crs(3035)
pts = gpd.sjoin(pts, kreise()[["GEN", "ars5", "geometry"]].rename(columns={"GEN": "kreis"}), predicate="within", how="left").drop(columns="index_right")
pts = gpd.sjoin(pts, gem, predicate="within", how="left").drop(columns="index_right")
pts = pts[~pts.index.duplicated()]
cand = pd.DataFrame(pts.drop(columns="geometry"))
if os.environ.get("KEEP_ALL"):
    cand.to_parquet(os.environ["KEEP_ALL"])

cand["link"] = np.where(cand.typ == "w", "https://www.openstreetmap.org/way/", "https://www.openstreetmap.org/relation/") + cand.id.astype(str)
sel = cand[~cand.osm.isin(FIXED_AFTER_EXTRACT) & (cand.knoten_verklebt >= LIST_MIN_GLUED) & ((cand.flaeche_ha >= LIST_MIN_HA) | (cand.mitglieder >= LIST_MIN_MEMBERS))].copy()
sel["flaeche_ha"] = sel.flaeche_ha.round(1)
sel["lon"] = sel.lon.round(6); sel["lat"] = sel.lat.round(6)
cols = ["osm", "link", "tag", "flaeche_ha", "mitglieder", "knoten", "knoten_verklebt", "anteil_verklebt",
        "knoten_an_strassen", "knoten_an_gewaessern", "knoten_an_grenzen", "kreis", "gemeinde", "ars5", "lat", "lon"]
sel = sel.sort_values(["knoten_verklebt", "flaeche_ha"], ascending=False)[cols]
sel.to_csv(OUT / "landuse_verklebt.csv", index=False)
gpd.GeoDataFrame(sel, geometry=gpd.points_from_xy(sel.lon, sel.lat), crs=4326).to_file(OUT / "landuse_verklebt.geojson", driver="GeoJSON")
s = {"kandidaten_ab_ha": MIN_HA_CANDIDATE, "kandidaten": int(len(cand)),
     "liste_kriterium": f"mindestens {LIST_MIN_GLUED} verklebte Knoten und mindestens {LIST_MIN_HA} ha oder {LIST_MIN_MEMBERS} Mitglieder",
     "liste": int(len(sel)), "liste_flaeche_ha": round(float(sel.flaeche_ha.sum())),
     "nach_tag": sel.tag.value_counts().to_dict(),
     "relationen": int((sel.osm.str[0] == "r").sum()),
     "kreise_top": sel.groupby("kreis").size().sort_values(ascending=False).head(15).to_dict()}
json.dump(s, open(OUT / "landuse_verklebt_zusammenfassung.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(s, indent=1, ensure_ascii=False))
