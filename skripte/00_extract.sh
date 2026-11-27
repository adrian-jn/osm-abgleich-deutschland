#!/bin/bash
# Topic extracts from a Geofabrik Germany PBF. Usage: 00_extract.sh germany-latest.osm.pbf OUTDIR
set -euo pipefail
pbf=${1:?germany pbf}; out=${2:?output directory}
mkdir -p "$out"
osmium tags-filter -O -o "$out/poi.osm.pbf" "$pbf" \
  nwr/amenity=pharmacy,doctors,clinic,dentist,hospital,kindergarten,childcare,school,post_office,parcel_locker,bus_station \
  nwr/shop=supermarket nwr/healthcare nwr/post_office \
  nwr/highway=bus_stop nwr/public_transport=platform,stop_position,station \
  nwr/railway=tram_stop,halt,station,platform w/addr:interpolation
osmium tags-filter -O -o "$out/addr.osm.pbf" "$pbf" nwr/addr:housenumber
osmium tags-filter -O -o "$out/buildings.osm.pbf" "$pbf" w/building
osmium tags-filter -O -o "$out/landuse.osm.pbf" "$pbf" wr/landuse=farmland,meadow,grass wr/natural=grassland
osmium tags-filter -O -R -o "$out/lines.osm.pbf" "$pbf" w/highway w/waterway
osmium tags-filter -O -o "$out/boundaries.osm.pbf" "$pbf" r/boundary=administrative
