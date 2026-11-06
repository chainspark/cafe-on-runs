// The WebAssembly interface. Every function returns plain numbers or pointers into buffers owned
// here; the page reads them through typed-array views on the module's heap.
#include <emscripten/emscripten.h>

#include <cstdint>
#include <string>
#include <vector>

#include "dataset.hpp"
#include "gdt.hpp"

const unsigned char* ct_embedded_data();
size_t ct_embedded_size();

namespace {

ct::Engine g_engine;
ct::Settings g_settings;
std::string g_error;
std::vector<float> g_points;           // lat, lon per run point
std::vector<int32_t> g_years;
std::vector<double> g_run_ids, g_cafe_ll, g_cafe_osm, g_results;
std::vector<double> g_passes, g_parts, g_features;
std::vector<int32_t> g_line_start, g_band_start;
std::vector<float> g_lines, g_bands;

void flatten(const std::vector<std::vector<ct::LatLon>>& polys, std::vector<int32_t>& start, std::vector<float>& xy) {
    start.assign(1, 0);
    xy.clear();
    for (const auto& poly : polys) {
        for (const auto& q : poly) { xy.push_back(float(q.lat)); xy.push_back(float(q.lon)); }
        start.push_back(int32_t(xy.size() / 2));
    }
}

}  // namespace

extern "C" {

EMSCRIPTEN_KEEPALIVE int ct_init() {
    ct::Dataset ds;
    if (!ct::decode(ct_embedded_data(), ct_embedded_size(), ds, g_error)) return -1;
    if (!g_engine.load(std::move(ds), g_error)) return -1;
    const auto& tr = g_engine.track();
    g_points.clear();
    for (const auto& q : tr.ll) { g_points.push_back(float(q.lat)); g_points.push_back(float(q.lon)); }
    for (const auto& run : g_engine.data().runs) {
        g_years.push_back(run.year);
        g_run_ids.push_back(double(run.activity_id));
    }
    for (const auto& c : g_engine.data().cafes) {
        g_cafe_ll.push_back(c.pos.lat);
        g_cafe_ll.push_back(c.pos.lon);
        g_cafe_osm.push_back(double(c.osm_id));
    }
    return 0;
}
EMSCRIPTEN_KEEPALIVE const char* ct_error() { return g_error.c_str(); }
EMSCRIPTEN_KEEPALIVE int ct_flags() { return int(g_engine.data().flags); }

EMSCRIPTEN_KEEPALIVE int ct_run_count() { return int(g_engine.data().runs.size()); }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_run_years() { return g_years.data(); }
EMSCRIPTEN_KEEPALIVE const double* ct_run_ids() { return g_run_ids.data(); }
EMSCRIPTEN_KEEPALIVE int ct_point_count() { return int(g_engine.track().p.size()); }
EMSCRIPTEN_KEEPALIVE const float* ct_points() { return g_points.data(); }
EMSCRIPTEN_KEEPALIVE int ct_part_count() { return int(g_engine.track().part_run.size()); }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_part_start() { return g_engine.track().part_start.data(); }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_part_run() { return g_engine.track().part_run.data(); }

EMSCRIPTEN_KEEPALIVE int ct_cafe_count() { return int(g_engine.data().cafes.size()); }
EMSCRIPTEN_KEEPALIVE const double* ct_cafe_latlon() { return g_cafe_ll.data(); }
EMSCRIPTEN_KEEPALIVE const double* ct_cafe_osm() { return g_cafe_osm.data(); }
EMSCRIPTEN_KEEPALIVE const char* ct_cafe_name(int i) { return g_engine.data().cafes[i].name.c_str(); }
EMSCRIPTEN_KEEPALIVE const char* ct_street_name(int i) { return g_engine.network().names[i].c_str(); }

EMSCRIPTEN_KEEPALIVE void ct_set_radius(double r) { g_engine.set_radius(r); }

// Per cafe, 7 doubles: runs, status, ⌖ yield, ⌒ yield, combined yield, best Ø, median Ø.
EMSCRIPTEN_KEEPALIVE const double* ct_evaluate(double tol, double band, double acceptance, uint32_t year_mask) {
    g_settings = {tol, band, acceptance, year_mask};
    g_engine.evaluate(g_settings);
    g_results.clear();
    for (const auto& r : g_engine.results()) {
        g_results.insert(g_results.end(), {double(r.runs), double(r.status), r.pos_yield, r.prof_yield, r.both_yield,
                                           r.best_diam, r.median_diam});
    }
    return g_results.data();
}

// Fills the detail buffers for one cafe with the settings of the last ct_evaluate; returns the pass count.
EMSCRIPTEN_KEEPALIVE int ct_detail(int cafe) {
    const ct::Detail d = g_engine.detail(cafe, g_settings);
    g_passes.clear();
    for (const auto& p : d.passes) {
        g_passes.insert(g_passes.end(), {double(p.run), double(p.name), double(p.start), double(p.end), p.d,
                                         ct::unproject_lat(p.q), ct::unproject_lon(p.q), p.prof, p.mean_off});
    }
    g_parts.clear();
    for (const auto& p : d.parts) g_parts.insert(g_parts.end(), {double(p.run), p.d, p.prof, double(p.pos), double(p.pro)});
    g_features.clear();
    for (const auto& f : d.features) {
        g_features.insert(g_features.end(), {double(f.name), double(f.passes), double(f.pos), double(f.pro),
                                             double(f.both), f.p95, f.bias, f.nx, f.ny});
    }
    flatten(d.nominals, g_line_start, g_lines);
    flatten(d.bands, g_band_start, g_bands);
    return int(d.passes.size());
}
EMSCRIPTEN_KEEPALIVE const double* ct_detail_passes() { return g_passes.data(); }  // 9 per pass
EMSCRIPTEN_KEEPALIVE int ct_detail_part_count() { return int(g_parts.size() / 5); }
EMSCRIPTEN_KEEPALIVE const double* ct_detail_parts() { return g_parts.data(); }  // 5 per run
EMSCRIPTEN_KEEPALIVE int ct_detail_feature_count() { return int(g_features.size() / 9); }
EMSCRIPTEN_KEEPALIVE const double* ct_detail_features() { return g_features.data(); }  // 9 per street
EMSCRIPTEN_KEEPALIVE int ct_detail_line_count() { return int(g_line_start.size()) - 1; }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_detail_line_start() { return g_line_start.data(); }
EMSCRIPTEN_KEEPALIVE const float* ct_detail_lines() { return g_lines.data(); }
EMSCRIPTEN_KEEPALIVE int ct_detail_band_count() { return int(g_band_start.size()) - 1; }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_detail_band_start() { return g_band_start.data(); }
EMSCRIPTEN_KEEPALIVE const float* ct_detail_bands() { return g_bands.data(); }

}  // extern "C"
