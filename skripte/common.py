"""Shared paths and helpers. Every input is an open dataset, see README.md.

Paths come from environment variables so the scripts run anywhere:

    OSM_POI        osmium extract of the POI, stop and interpolation tags (00_extract.sh)
    OSM_ADDR       osmium extract of every object with addr:housenumber
    OSM_BUILDINGS  osmium extract of every way with building=*
    VG250          BKG VG250 zip (vg250_01-01.utm32s.shape.ebenen.zip)
    ZENSUS_POP     Zensus2022_Bevoelkerungszahl_100m-Gitter.csv
    DELFI_GTFS     DELFI Germany-wide GTFS zip
    OUT            output directory (default: ../listen next to this folder)
"""
import os
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
DATEN = HERE.parent / "daten"
OUT = Path(os.environ.get("OUT", HERE.parent / "listen"))
OUT.mkdir(parents=True, exist_ok=True)
TO_LAEA = Transformer.from_crs(4326, 3035, always_xy=True)


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"set {name} (see the docstring of common.py)")
    return value


def kreise(crs: int = 3035) -> gpd.GeoDataFrame:
    """The 400 Kreise and kreisfreie Städte of VG250 (land areas only, GF 4)."""
    zip_path = env("VG250")
    inner = "vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_KRS.shp"
    k = gpd.read_file(f"zip://{zip_path}!{inner}")
    k = k[k.GF == 4][["ARS", "GEN", "BEZ", "geometry"]].copy()
    k["ars5"] = k.ARS.str[:5]
    return k.to_crs(crs)


def laender(crs: int = 3035) -> gpd.GeoDataFrame:
    zip_path = env("VG250")
    inner = "vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_LAN.shp"
    k = gpd.read_file(f"zip://{zip_path}!{inner}")
    return k[k.GF == 4][["ARS", "GEN", "geometry"]].to_crs(crs)


def assign_kreis(df, x="x", y="y"):
    """Adds ars5, GEN and BEZ to a frame with LAEA coordinates."""
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[x], df[y]), crs=3035)
    j = gpd.sjoin(pts, kreise(), predicate="within", how="left")
    return j.drop(columns=["geometry", "index_right", "ARS"])
