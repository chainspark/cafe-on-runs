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
std::vector<int32_t> g_line_names;
std::vector<double> g_notes;          // 5 per note: kind, a, b, c, street name id
std::vector<int32_t> g_note_counts;   // per cafe
std::vector<int32_t> g_dates;         // yyyymmdd per run (day 0 in public builds)
std::vector<double> g_run_len;        // metres per run
std::vector<double> g_hits;           // 2 per hit: run, distance
std::vector<double> g_report;         // 9 per row: cafe, d, prof, pos, pro, along, street, q_lat, q_lon
std::vector<double> g_profile;        // 2 per point: x along the street, offset
int g_profile_street = -1;
std::vector<double> g_lot;            // see ct_lot
std::vector<int32_t> g_heat;          // cols x rows run counts

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
    for (size_t r = 0; r < g_engine.data().runs.size(); ++r) {
        const auto& run = g_engine.data().runs[r];
        g_years.push_back(run.year);
        g_run_ids.push_back(double(run.activity_id));
        g_dates.push_back(run.year * 10000 + run.month * 100 + run.day);
        g_run_len.push_back(g_engine.run_length(int(r)));
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
EMSCRIPTEN_KEEPALIVE const char* ct_meta() { return g_engine.data().meta.c_str(); }  // "" for v1 datasets

EMSCRIPTEN_KEEPALIVE int ct_run_count() { return int(g_engine.data().runs.size()); }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_run_years() { return g_years.data(); }
EMSCRIPTEN_KEEPALIVE const double* ct_run_ids() { return g_run_ids.data(); }
EMSCRIPTEN_KEEPALIVE const int32_t* ct_run_dates() { return g_dates.data(); }  // yyyymmdd; dd = 00 in public builds
EMSCRIPTEN_KEEPALIVE const double* ct_run_lengths() { return g_run_len.data(); }
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
    g_line_names = d.nominal_names;
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
EMSCRIPTEN_KEEPALIVE const int32_t* ct_detail_line_names() { return g_line_names.data(); }  // street name id per line/band

// Checker's notes for one cafe, with the settings of the last ct_evaluate; returns the note count.
// The rules live in Engine::notes; the page turns each kind into words.
EMSCRIPTEN_KEEPALIVE int ct_notes(int cafe) {
    const auto notes = ct::Engine::notes(g_engine.detail(cafe, g_settings, false), g_settings);
    g_notes.clear();
    for (const auto& n : notes) g_notes.insert(g_notes.end(), {double(n.kind), n.a, n.b, n.c, double(n.street)});
    return int(notes.size());
}
EMSCRIPTEN_KEEPALIVE const double* ct_notes_data() { return g_notes.data(); }  // 5 per note
// How many notes every cafe has (for the table's marks), with the settings of the last ct_evaluate.
EMSCRIPTEN_KEEPALIVE const int32_t* ct_note_counts() {
    const int n = int(g_engine.data().cafes.size());
    g_note_counts.assign(n, 0);
    for (int c = 0; c < n; ++c)
        g_note_counts[c] = int32_t(ct::Engine::notes(g_engine.detail(c, g_settings, false), g_settings).size());
    return g_note_counts.data();
}

// ---- one run ----
// Runs (in the year mask of the last ct_evaluate) passing within r metres of a point, nearest first.
EMSCRIPTEN_KEEPALIVE int ct_runs_at(double lat, double lon, double r) {
    const auto hits = g_engine.runs_at(ct::project(lat, lon), r, g_settings.year_mask);
    g_hits.clear();
    for (const auto& h : hits) g_hits.insert(g_hits.end(), {double(h.run), h.d});
    return int(hits.size());
}
EMSCRIPTEN_KEEPALIVE const double* ct_runs_at_data() { return g_hits.data(); }  // 2 per hit
// Every cafe the run passed within R, in route order, scored with the settings of the last ct_evaluate.
EMSCRIPTEN_KEEPALIVE int ct_run_report(int run) {
    const auto rows = g_engine.run_report(run, g_settings);
    g_report.clear();
    for (const auto& w : rows) {
        g_report.insert(g_report.end(), {double(w.cafe), w.part.d, w.part.prof, double(w.part.pos), double(w.part.pro),
                                         w.along, double(w.street), ct::unproject_lat(w.q), ct::unproject_lon(w.q)});
    }
    return int(rows.size());
}
EMSCRIPTEN_KEEPALIVE const double* ct_run_report_data() { return g_report.data(); }  // 9 per row
// The run's offset profile at one cafe: the pass that gave its closest approach.
EMSCRIPTEN_KEEPALIVE int ct_run_profile(int cafe, int run) {
    const auto pr = g_engine.run_profile(cafe, run);
    g_profile.clear();
    for (const auto& p : pr.pts) g_profile.insert(g_profile.end(), {p.x, p.off});
    g_profile_street = pr.street;
    return int(pr.pts.size());
}
EMSCRIPTEN_KEEPALIVE const double* ct_run_profile_data() { return g_profile.data(); }  // 2 per point
EMSCRIPTEN_KEEPALIVE int ct_run_profile_street() { return g_profile_street; }

// ---- the lot ----
// The lot as of the last ct_evaluate: [runs, km, median_km, longest_km, parts, accepted, median_cafes,
// most_cafes, most_cafes_run, first_month (year * 12 + month - 1), n_months], then n_months run counts.
EMSCRIPTEN_KEEPALIVE const double* ct_lot() {
    const auto& l = g_engine.lot();
    g_lot = {double(l.runs), l.km, l.median_km, l.longest_km, double(l.parts), double(l.accepted), l.median_cafes,
             double(l.most_cafes), double(l.most_cafes_run), double(l.first_month), double(l.per_month.size())};
    for (int32_t n : l.per_month) g_lot.push_back(n);
    return g_lot.data();
}
// Distinct runs (year mask of the last ct_evaluate) per cell of a cols x rows grid over the box, row 0 north.
EMSCRIPTEN_KEEPALIVE const int32_t* ct_heat(int cols, int rows, double s, double w, double n, double e) {
    g_heat = g_engine.heat(cols, rows, {s, w}, {n, e}, g_settings.year_mask);
    return g_heat.data();
}

}  // extern "C"
