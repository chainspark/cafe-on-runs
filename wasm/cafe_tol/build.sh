#!/usr/bin/env bash
# Build inside the emscripten/emsdk container (see cafes.py: compile()).
#   bash build.sh test      - C++ unit tests, run under node
#   bash build.sh private   - build/cafe_tol_private.js from generated/dataset_private.inc
#   bash build.sh public    - build/cafe_tol_public.js  from generated/dataset_public.inc
set -euo pipefail
cd "$(dirname "$0")"
target="${1:-private}"
mkdir -p build

SRC="dataset.cpp match.cpp gdt.cpp"
FLAGS="-O3 -std=c++20 -Wall -Wextra"

case "$target" in
  test)
    em++ $FLAGS $SRC tests/test_gdt.cpp -sENVIRONMENT=node -sALLOW_MEMORY_GROWTH=1 -o build/test_gdt.js
    node build/test_gdt.js
    ;;
  private|public)
    inc="generated/dataset_${target}.inc"
    if [ ! -f "$inc" ]; then echo "missing $inc - run the Python export first" >&2; exit 2; fi
    exports="_ct_init,_ct_error,_ct_flags,_ct_meta,_ct_run_count,_ct_run_years,_ct_run_ids,_ct_point_count,_ct_points,\
_ct_part_count,_ct_part_start,_ct_part_run,_ct_cafe_count,_ct_cafe_latlon,_ct_cafe_osm,_ct_cafe_name,\
_ct_street_name,_ct_set_radius,_ct_evaluate,_ct_detail,_ct_detail_passes,_ct_detail_part_count,_ct_detail_parts,\
_ct_detail_feature_count,_ct_detail_features,_ct_detail_line_count,_ct_detail_line_start,_ct_detail_lines,\
_ct_detail_band_count,_ct_detail_band_start,_ct_detail_bands,_ct_detail_line_names,_ct_notes,_ct_notes_data,\
_ct_note_counts,_ct_run_dates,_ct_run_lengths,_ct_runs_at,_ct_runs_at_data,_ct_run_report,_ct_run_report_data,\
_ct_run_profile,_ct_run_profile_data,_ct_run_profile_street,_ct_lot,_ct_heat"
    em++ $FLAGS $SRC api.cpp embedded.cpp "-DCT_DATASET_INC=\"$inc\"" \
      -sMODULARIZE=1 -sEXPORT_NAME=CafeTol -sSINGLE_FILE=1 -sENVIRONMENT=web \
      -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=64MB \
      -sEXPORTED_FUNCTIONS="$exports" \
      -sEXPORTED_RUNTIME_METHODS=HEAPF64,HEAPF32,HEAP32,UTF8ToString \
      -o "build/cafe_tol_${target}.js"
    ls -l "build/cafe_tol_${target}.js"
    ;;
  *)
    echo "usage: build.sh test|private|public" >&2; exit 2 ;;
esac
