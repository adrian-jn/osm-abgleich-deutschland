"""Pairs of amenity=kindergarten objects less than 25 m apart (possible duplicates).

One point per object (node, or the mean of the outer nodes). For each pair:
same name, one without a name, or different names. Different names are often
two real facilities in one building (Krippe and Kindergarten).

Writes listen/kita_paare.csv and listen/kita_zusammenfassung.json.
"""
import json

import numpy as np
import osmium
import pandas as pd
from scipy.spatial import cKDTree

from common import OUT, TO_LAEA, env

rows = []


class H(osmium.SimpleHandler):
    def rec(self, oid, t, lon, lat):
        rows.append((oid, t.get("amenity"), (t.get("name") or "").strip(), lon, lat))

    def node(self, n):
        if n.tags.get("amenity") in ("kindergarten", "childcare"):
            self.rec(f"n{n.id}", n.tags, n.location.lon, n.location.lat)

    def area(self, a):
        if a.tags.get("amenity") not in ("kindergarten", "childcare"):
            return
        xs, ys = [], []
        try:
            for ring in a.outer_rings():
                for nd in ring:
                    xs.append(nd.lon); ys.append(nd.lat)
        except osmium.InvalidLocationError:
            return
        if xs:
            self.rec(("w" if a.from_way() else "r") + str(a.orig_id()), a.tags, sum(xs) / len(xs), sum(ys) / len(ys))


H().apply_file(env("OSM_POI"), locations=True, idx="flex_mem")
d = pd.DataFrame(rows, columns=["osm", "amenity", "name", "lon", "lat"])
k = d[d.amenity == "kindergarten"].reset_index(drop=True)
x, y = TO_LAEA.transform(k.lon.values, k.lat.values)
pairs = cKDTree(np.c_[x, y]).query_pairs(25, output_type="ndarray")
p = pd.DataFrame({"osm_a": k.osm.values[pairs[:, 0]], "name_a": k.name.values[pairs[:, 0]],
                  "osm_b": k.osm.values[pairs[:, 1]], "name_b": k.name.values[pairs[:, 1]],
                  "abstand_m": np.hypot(x[pairs[:, 0]] - x[pairs[:, 1]], y[pairs[:, 0]] - y[pairs[:, 1]]).round(1),
                  "lat": k.lat.values[pairs[:, 0]].round(6), "lon": k.lon.values[pairs[:, 0]].round(6)})
same = p.name_a.str.lower() == p.name_b.str.lower()
noname = (p.name_a == "") | (p.name_b == "")
p["art"] = np.where(noname, "einer ohne Namen", np.where(same, "gleicher Name", "verschiedene Namen"))
p.sort_values(["art", "abstand_m"]).to_csv(OUT / "kita_paare.csv", index=False)
# places after merging everything within 25 m (connected components)
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
ax, ay = TO_LAEA.transform(d.lon.values, d.lat.values)
allp = cKDTree(np.c_[ax, ay]).query_pairs(25, output_type="ndarray")
g = sp.coo_matrix((np.ones(len(allp)), (allp[:, 0], allp[:, 1])), shape=(len(d), len(d)))
n_places = connected_components(g, directed=False)[0]   # kindergarten and childcare together
s = {"kindergarten": int((d.amenity == "kindergarten").sum()), "childcare": int((d.amenity == "childcare").sum()),
     "paare_unter_25m": int(len(p)), "nach_art": p.art.value_counts().to_dict(), "orte_kindergarten_und_childcare_nach_zusammenfassen": int(n_places)}
json.dump(s, open(OUT / "kita_zusammenfassung.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(s, indent=1, ensure_ascii=False))
