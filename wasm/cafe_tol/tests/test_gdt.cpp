// Synthetic-geometry tests for the GD&T engine. Coordinates are built in local metres and converted
// to lat/lon, so expected values can be written in metres.
#include <cmath>
#include <cstdio>
#include <string>

#include "../dataset.hpp"
#include "../gdt.hpp"

using namespace ct;

static int g_fail = 0, g_pass = 0;
#define CHECK(cond, msg)                                                   \
    do {                                                                   \
        if (cond) { ++g_pass; }                                            \
        else { ++g_fail; std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, msg); } \
    } while (0)
#define NEAR(a, b, tol, msg) CHECK(std::fabs((a) - (b)) <= (tol), msg)

static LatLon ll(double x, double y) { return {y / kKy, x / kKx}; }

static std::vector<LatLon> line(double x0, double y0, double x1, double y1, double step = 5) {
    std::vector<LatLon> out;
    const double l = std::hypot(x1 - x0, y1 - y0);
    const int n = int(std::ceil(l / step));
    for (int i = 0; i <= n; ++i) out.push_back(ll(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n));
    return out;
}
static std::vector<LatLon> concat(std::vector<LatLon> a, const std::vector<LatLon>& b) {
    a.insert(a.end(), b.begin() + 1, b.end());
    return a;
}

static Street street(const char* name, double x0, double y0, double x1, double y1) {
    Street s;
    s.name = name;
    s.pts = {ll(x0, y0), ll(x1, y1)};
    return s;
}
static Run run(std::vector<LatLon> pts, int year = 2025) {
    Run r;
    r.activity_id = 1000 + year;
    r.year = year;
    r.parts.push_back(std::move(pts));
    return r;
}
static Cafe cafe(double x, double y) {
    Cafe c;
    c.name = "Test cafe";
    c.pos = ll(x, y);
    return c;
}
static Settings settings(double tol = 50, double band = 30, double acc = 0.75) {
    Settings s;
    s.tol_diam = tol;
    s.band = band;
    s.acceptance = acc;
    return s;
}
static Engine engine(Dataset ds, double r = 75) {
    Engine e;
    std::string err;
    e.load(std::move(ds), err);
    e.set_radius(r);
    return e;
}

static void test_roundtrip() {
    Dataset ds;
    ds.flags = 1;
    ds.runs.push_back(run(line(-300, -40, 300, 25)));
    ds.streets.push_back(street("Main St", -300, 0, 300, 0));
    ds.cafes.push_back(cafe(-12.5, 16));
    ds.meta = R"({"schema":1,"sources":[{"id":"cafes","licence":"ODbL 1.0 – ©"}]})";
    const auto bytes = encode(ds);
    Dataset back;
    std::string err;
    CHECK(decode(bytes.data(), bytes.size(), back, err), "decode encoded dataset");
    CHECK(back.flags == 1 && back.runs.size() == 1 && back.streets.size() == 1 && back.cafes.size() == 1, "counts survive");
    const auto& a = ds.runs[0].parts[0];
    const auto& b = back.runs[0].parts[0];
    bool same = a.size() == b.size();
    for (size_t i = 0; same && i < a.size(); ++i) same = std::fabs(a[i].lat - b[i].lat) < 1e-5 && std::fabs(a[i].lon - b[i].lon) < 1e-5;
    CHECK(same, "varint coordinates round-trip to 1e-5 deg");
    CHECK(back.streets[0].name == "Main St" && back.cafes[0].name == "Test cafe", "names round-trip");
    CHECK(back.version == kVersion && back.meta == ds.meta, "provenance metadata round-trips (non-ASCII too)");
    Dataset bad;
    CHECK(!decode(bytes.data(), bytes.size() - 3, bad, err), "truncated data is rejected");
}

static void test_position_diameter() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(run(line(-300, 0, 300, 0)));
    ds.cafes.push_back(cafe(0, 30));
    Engine e = engine(ds);
    e.evaluate(settings());
    CHECK(e.passes(0).size() == 1, "one pass");
    NEAR(e.results()[0].best_diam, 60, 0.5, "a run 30 m from the cafe gives Ø60");
}

static void test_out_and_back() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(run(concat(line(-300, 0, 300, 0), line(300, 0, -300, 0))));
    ds.cafes.push_back(cafe(0, 30));
    Engine e = engine(ds);
    e.evaluate(settings());
    CHECK(e.passes(0).size() == 2, "leaving R and coming back gives 2 passes");
    CHECK(e.results()[0].runs == 1, "... but still one part (run)");
}

static void test_sidewalks() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(run(line(-300, 8, 300, 8)));
    ds.runs.push_back(run(line(300, -8, -300, -8)));
    ds.cafes.push_back(cafe(0, 30));
    Engine e = engine(ds);
    e.evaluate(settings());
    const auto& ps = e.passes(0);
    CHECK(ps.size() == 2, "two passes");
    for (const auto& p : ps) NEAR(p.prof, 16, 0.3, "runs at ±8 m give a profile actual of 16");
    const Detail d = e.detail(0, settings());
    CHECK(d.features.size() == 1, "one street feature");
    NEAR(d.features[0].bias, 0, 0.3, "opposite sidewalks average to no bias");
    bool named = !d.nominals.empty() && d.nominal_names.size() == d.nominals.size() && d.bands.size() == d.nominals.size();
    for (int32_t n : d.nominal_names) named = named && e.network().names[n] == "Main St";
    CHECK(named, "each nominal line and band carries its street name");
    NEAR(ps[0].mean_off * ps[1].mean_off, -64, 3, "offsets have opposite signs");
}

static void test_stray_point() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    auto pts = line(-300, 0, 300, 0);
    pts[pts.size() / 2] = ll(0, 20);  // one point dashes 20 m off the line
    ds.runs.push_back(run(pts));
    ds.cafes.push_back(cafe(0, -40));
    Engine e = engine(ds);
    e.evaluate(settings(100, 30));
    NEAR(e.passes(0)[0].prof, 40, 0.3, "one stray point at +20 m gives an actual of 40");
    CHECK(e.results()[0].prof_yield == 0, "... which fails a 30 m band");
}

static void test_heading_at_intersection() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.streets.push_back(street("Cross Ave", 0, -400, 0, 400));
    ds.runs.push_back(run(line(-300, 3, 300, 3)));
    ds.cafes.push_back(cafe(10, 30));
    Engine e = engine(ds);
    e.evaluate(settings());
    const auto& ps = e.passes(0);
    CHECK(ps.size() == 1, "crossing an intersection does not split the pass");
    CHECK(!ps.empty() && e.network().names[ps[0].name] == "Main St", "heading keeps the run on Main St");
    NEAR(ps.empty() ? 0 : ps[0].prof, 6, 0.3, "... measured against Main St (actual 6)");
}

static void test_turning_run_is_one_part() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.streets.push_back(street("Cross Ave", 0, -400, 0, 400));
    ds.runs.push_back(run(concat(line(-300, 4, -4, 4), line(-4, 4, -4, 300))));
    ds.cafes.push_back(cafe(20, 20));
    Engine e = engine(ds);
    e.evaluate(settings());
    CHECK(e.passes(0).size() == 2, "a run turning a corner makes two passes (two streets)");
    CHECK(e.results()[0].runs == 1, "... and counts as one part");
}

static void test_classify() {
    CHECK(Engine::classify(10, 0.9, 0.9, 0.9, 0.75) == kBoth, "90/90/90 conforms to both");
    CHECK(Engine::classify(10, 0.8, 0.8, 0.6, 0.75) == kApart, "80/80 failing on different runs: each alone, not together");
    CHECK(Engine::classify(10, 0.8, 0.5, 0.5, 0.75) == kPosOnly, "position only");
    CHECK(Engine::classify(10, 0.5, 0.8, 0.5, 0.75) == kProfOnly, "profile only");
    CHECK(Engine::classify(10, 0.5, 0.5, 0.4, 0.75) == kNeither, "neither");
    CHECK(Engine::classify(2, 1, 1, 1, 0.75) == kFew, "fewer than 3 runs");
}

static void test_combined_yield() {
    // 6 good runs, 2 on the far sidewalk (fail ⌖ only), 2 that dash toward the door (fail ⌒ only).
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    for (int i = 0; i < 6; ++i) ds.runs.push_back(run(line(-300, 2, 300, 2)));
    for (int i = 0; i < 2; ++i) ds.runs.push_back(run(line(-300, -12, 300, -12)));
    for (int i = 0; i < 2; ++i) {
        auto pts = line(-300, 2, 300, 2);
        pts[pts.size() / 2] = ll(0, 19);
        ds.runs.push_back(run(pts));
    }
    ds.cafes.push_back(cafe(0, 22));
    Engine e = engine(ds);
    e.evaluate(settings(50, 30, 0.75));
    const auto& r = e.results()[0];
    NEAR(r.pos_yield, 0.8, 1e-9, "⌖ yield 80%");
    NEAR(r.prof_yield, 0.8, 1e-9, "⌒ yield 80%");
    NEAR(r.both_yield, 0.6, 1e-9, "combined yield 60%");
    CHECK(r.status == kApart, "status: each alone, not together");
    Settings y = settings();
    y.year_mask = 1u << (2024 - 2000);
    e.evaluate(y);
    CHECK(e.results()[0].runs == 0, "year filter removes runs from other years");
}

// ---- checker's notes ----
static Part part(double d, double prof, const Settings& s) {
    Part p;
    p.d = d;
    p.prof = prof;
    p.pos = 2 * d <= s.tol_diam;
    p.pro = prof <= s.band;
    return p;
}
static Detail parts_detail(const std::vector<Part>& parts) {
    Detail d;
    d.parts = parts;
    for (size_t k = 0; k < d.parts.size(); ++k) d.parts[k].run = int32_t(k);
    return d;
}
// the main street (name 7, east-west) with `north` of `m` passes north of the centreline
static void add_street(Detail& d, int north, int m) {
    Feature f;
    f.name = 7;
    f.passes = m;
    f.nx = 0;
    f.ny = 1;
    d.features.insert(d.features.begin(), f);
    for (int k = 0; k < m; ++k) {
        Pass p;
        p.name = 7;
        p.mean_off = k < north ? 4 : -4;
        d.passes.push_back(p);
    }
}
static bool has(const std::vector<Note>& ns, int kind) {
    for (const auto& n : ns) if (n.kind == kind) return true;
    return false;
}

static void test_notes_rules() {
    const Settings s = settings(50, 30, 0.75);
    std::vector<Part> good(10, part(10, 12, s));
    CHECK(Engine::notes(parts_detail({part(1, 5, s), part(1, 5, s)}), s).empty(), "no notes under 3 runs");

    auto door = good;
    door[3] = part(1.5, 12, s);
    auto ns = Engine::notes(parts_detail(door), s);
    CHECK(!ns.empty() && ns[0].kind == kNoteDoor, "a run within 2 m of the cafe: door note, listed first");
    NEAR(ns.empty() ? 0 : ns[0].a, 1.5, 1e-9, "... carrying the closest approach");
    CHECK(has(ns, kNoteAllAccepted) && ns.back().a == 10, "all 10 runs accepted: all-accepted note with the run count");
    door.pop_back();
    CHECK(Engine::notes(parts_detail(door), s).empty(), "9 runs: below the minimum, so neither note");

    Detail side = parts_detail(good);
    add_street(side, 11, 12);
    ns = Engine::notes(side, s);
    CHECK(has(ns, kNoteSide), "11 of 12 passes on one side: side note");
    for (const auto& n : ns)
        if (n.kind == kNoteSide) CHECK(n.a == 0 && n.b == 11 && n.c == 12 && n.street == 7, "... north side, 11 of 12, on street 7");
    Detail mixed = parts_detail(good);
    add_street(mixed, 10, 12);
    CHECK(!has(Engine::notes(mixed, s), kNoteSide), "10 of 12 (83%) is not one-sided enough");
    Detail south = parts_detail(good);
    add_street(south, 0, 12);
    ns = Engine::notes(south, s);
    for (const auto& n : ns)
        if (n.kind == kNoteSide) CHECK(n.a == 1 && n.b == 12, "all 12 passes south: south side note");

    std::vector<Part> apart(6, part(10, 12, s));                   // both
    for (int k = 0; k < 2; ++k) apart.push_back(part(40, 12, s));  // ⌒ only
    for (int k = 0; k < 2; ++k) apart.push_back(part(10, 50, s));  // ⌖ only
    CHECK(has(Engine::notes(parts_detail(apart), s), kNoteApart), "80/80/60: each alone, not together");

    std::vector<Part> near(9, part(10, 20, s));
    near.push_back(part(10, 35, s));
    ns = Engine::notes(parts_detail(near), s);
    CHECK(has(ns, kNoteNearBand), "p95 35 over a 30 m band while 9 of 10 hold it: near-band note");
    for (const auto& n : ns)
        if (n.kind == kNoteNearBand) CHECK(n.a == 35 && n.b == 9 && n.c == 10, "... carrying p95, holding runs and runs");
    near.back() = part(10, 40, s);
    CHECK(!has(Engine::notes(parts_detail(near), s), kNoteNearBand), "p95 40 is more than 8 m over: no near-band note");

    std::vector<Part> detour(14, part(10, 12, s));
    detour.push_back(part(40, 50, s));
    ns = Engine::notes(parts_detail(detour), s);
    CHECK(has(ns, kNoteOneDetour) && !has(ns, kNoteAllAccepted), "one of 15 runs fails both: detour note");
    detour.pop_back();
    detour.pop_back();
    detour.push_back(part(40, 50, s));
    CHECK(!has(Engine::notes(parts_detail(detour), s), kNoteOneDetour), "14 runs: below the detour minimum");

    // door + side + near band + detour: four rules fire, three notes are kept, in rule order
    std::vector<Part> many(13, part(10, 20, s));
    many.push_back(part(1, 34, s));
    many.push_back(part(40, 50, s));
    Detail four = parts_detail(many);
    add_street(four, 15, 15);
    ns = Engine::notes(four, s);
    CHECK(ns.size() == 3 && ns[0].kind == kNoteDoor && ns[1].kind == kNoteSide && ns[2].kind == kNoteNearBand,
          "at most three notes, most striking first");
}

static void test_notes_engine() {
    // 10 runs on the north sidewalk, 1 m from the door of a cafe 7 m north of the centreline
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    for (int i = 0; i < 10; ++i) ds.runs.push_back(run(line(-300, 6, 300, 6)));
    ds.cafes.push_back(cafe(0, 7));
    Engine e = engine(ds);
    const Settings s = settings();
    e.evaluate(s);
    const auto ns = Engine::notes(e.detail(0, s, false), s);
    CHECK(ns.size() == 3 && ns[0].kind == kNoteDoor && ns[1].kind == kNoteSide && ns[2].kind == kNoteAllAccepted,
          "door, north side and all accepted from real passes");
    CHECK(ns.size() > 1 && ns[1].a == 0 && ns[1].b == 10 && e.network().names[ns[1].street] == "Main St",
          "... north side of Main St on 10 of 10 passes");
    const Detail full = e.detail(0, s), lite = e.detail(0, s, false);
    CHECK(lite.nominals.empty() && !full.nominals.empty() && lite.parts.size() == full.parts.size() &&
              lite.features.size() == full.features.size(),
          "detail without geometry has the same parts and features, and no drawing");
}

// ---- inspecting one run ----
static void test_runs_at() {
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(run(line(-300, 0, 300, 0), 2025));    // run 0 on the centreline
    ds.runs.push_back(run(line(-300, 40, 300, 40), 2024));  // run 1, 40 m north
    ds.cafes.push_back(cafe(0, 20));
    Engine e = engine(ds);
    auto hits = e.runs_at({0, 15}, 30, 0xffffffffu);
    CHECK(hits.size() == 2 && hits[0].run == 0 && hits[1].run == 1, "both runs within 30 m, nearest first");
    NEAR(hits.empty() ? 0 : hits[0].d, 15, 0.01, "... at their true distance");
    CHECK(e.runs_at({0, 15}, 10, 0xffffffffu).empty(), "nothing within 10 m");
    hits = e.runs_at({2.5, 0.5}, 1, 0xffffffffu);  // between two GPS points 5 m apart
    CHECK(hits.size() == 1 && hits[0].run == 0, "a click between two points still hits the run (segment test)");
    hits = e.runs_at({0, 15}, 30, 1u << (2025 - 2000));
    CHECK(hits.size() == 1 && hits[0].run == 0, "the year mask hides runs from other years");
}

static void test_run_report() {
    // one run along Main St at +4 m passes two cafes 300 m apart, and a third cafe it never reaches
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    auto pts = line(-300, 4, 300, 4);
    pts[pts.size() / 2 + 34] = ll(170, 24);  // one stray, 20 m off the line, just past the second cafe
    ds.runs.push_back(run(pts));
    ds.cafes.push_back(cafe(150, 10));   // passed second
    ds.cafes.push_back(cafe(-150, 10));  // passed first
    ds.cafes.push_back(cafe(0, 300));    // out of reach
    Engine e = engine(ds);
    const Settings s = settings();
    e.evaluate(s);
    const auto rows = e.run_report(0, s);
    CHECK(rows.size() == 2, "the report lists the two cafes within R, not the third");
    CHECK(rows.size() == 2 && rows[0].cafe == 1 && rows[1].cafe == 0, "... in the order the run passed them");
    NEAR(rows.size() == 2 ? rows[0].along : 0, 150, 1.0, "the first is 150 m into the run");
    NEAR(rows.size() == 2 ? rows[1].along : 0, 450, 1.0, "the second is 450 m into the run");
    NEAR(rows.empty() ? 0 : rows[0].part.d, 6, 0.01, "closest approach 6 m");
    CHECK(rows.size() == 2 && rows[0].part.pos && rows[0].part.pro, "the first cafe accepts the run");
    CHECK(rows.size() == 2 && rows[1].part.pos && !rows[1].part.pro, "the stray fails ⌒ at the second cafe");
    CHECK(rows.size() == 2 && e.network().names[rows[0].street] == "Main St", "rows carry the street of the closest pass");
    NEAR(e.run_length(0), 600 + 2 * std::hypot(5.0, 20.0) - 10, 1.0, "run length follows the track, stray included");

    const RunProfile pr = e.run_profile(0, 0);
    double max_off = 0, x0 = 0, x1 = 0;
    for (const auto& q : pr.pts) max_off = std::max(max_off, std::fabs(q.off));
    if (!pr.pts.empty()) { x0 = pr.pts.front().x; x1 = pr.pts.back().x; }
    CHECK(!pr.pts.empty() && e.network().names[pr.street] == "Main St", "a profile of the pass on Main St");
    NEAR(2 * max_off, pr.prof, 1e-6, "the profile's largest offset is half the ⌒ actual");
    CHECK(x0 < 0 && x1 > 0, "x runs west to east through the cafe (+ east on an E-W street)");
    CHECK(e.run_profile(2, 0).pts.empty(), "no profile at a cafe the run never reached");
}

static Run dated(std::vector<LatLon> pts, int year, int month) {
    Run r = run(std::move(pts), year);
    r.month = month;
    return r;
}

static void test_lot() {
    // two runs pass the cafe, one never comes near it; Dec 2024, Jun 2025 and Aug 2025
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(dated(line(-300, 4, 300, 4), 2025, 6));
    ds.runs.push_back(dated(line(-300, 500, 300, 500), 2025, 8));
    ds.runs.push_back(dated(line(-200, 4, 200, 4), 2024, 12));
    ds.cafes.push_back(cafe(0, 10));
    Engine e = engine(ds);
    Settings s = settings();
    e.evaluate(s);
    LotStats lot = e.lot();
    CHECK(lot.runs == 3, "the lot counts every run, not only those that passed a cafe");
    NEAR(lot.km, 1.6, 0.01, "total distance 1.6 km");
    NEAR(lot.longest_km, 0.6, 0.01, "longest run 0.6 km");
    NEAR(lot.median_km, 0.6, 0.01, "median run 0.6 km");
    CHECK(lot.parts == 2 && lot.accepted == 2, "two run-cafe parts, both accepted");
    NEAR(lot.median_cafes, 1, 1e-9, "the median run passed one cafe");
    CHECK(lot.most_cafes == 1 && lot.most_cafes_run == 0, "the first run to pass the most cafes is named");
    CHECK(lot.first_month == 2024 * 12 + 11, "months start at the earliest run (Dec 2024)");
    CHECK(lot.per_month.size() == 9 && lot.per_month[0] == 1 && lot.per_month[6] == 1 && lot.per_month[8] == 1,
          "runs per month, empty months included");
    s.tol_diam = 10;  // Ø12 no longer fits
    e.evaluate(s);
    CHECK(e.lot().parts == 2 && e.lot().accepted == 0, "the lot follows the callouts");
    s = settings();
    s.year_mask = 1u << (2025 - 2000);
    e.evaluate(s);
    lot = e.lot();
    CHECK(lot.runs == 2 && lot.parts == 1, "the lot follows the year mask");
    CHECK(lot.first_month == 2025 * 12 + 5 && lot.per_month.size() == 3, "... and so do its months");
    s.year_mask = 1u << (2020 - 2000);
    e.evaluate(s);
    CHECK(e.lot().runs == 0 && e.lot().per_month.empty() && e.lot().most_cafes_run == -1, "an empty lot is empty");
}

static void test_heat() {
    // a 800 m x 800 m box in 100 m cells, north edge at y = 700
    Dataset ds;
    ds.streets.push_back(street("Main St", -400, 0, 400, 0));
    ds.runs.push_back(run(line(-290, 4, 290, 4), 2025));                                       // row 6, cols 1-6
    ds.runs.push_back(run(line(-290, 4, 290, 4), 2025));                                       // row 6, cols 1-6 again
    ds.runs.push_back(run(concat(line(-290, 500, 290, 500), line(290, 500, -290, 500)), 2024)); // row 2, out and back
    ds.runs.push_back(run({ll(-350, 650), ll(350, 650)}, 2023));                               // row 0, two points only
    ds.cafes.push_back(cafe(0, 10));
    Engine e = engine(ds);
    const LatLon sw = ll(-400, -100), ne = ll(400, 700);
    auto h = e.heat(8, 8, sw, ne, 0xffffffffu);
    auto at = [&](int row, int col) { return h.empty() ? -1 : h[size_t(row * 8 + col)]; };
    CHECK(h.size() == 64, "one count per cell");
    CHECK(at(6, 1) == 2 && at(6, 4) == 2 && at(6, 6) == 2 && at(6, 0) == 0 && at(6, 7) == 0, "two runs share the Main St cells");
    CHECK(at(2, 1) == 1 && at(2, 4) == 1, "an out-and-back run counts once per cell");
    bool walked = true;
    for (int c = 0; c < 8; ++c) walked &= at(0, c) == 1;
    CHECK(walked, "a long segment marks every cell it crosses, not only its two points");
    h = e.heat(8, 8, sw, ne, 1u << (2025 - 2000));
    CHECK(at(6, 4) == 2 && at(2, 4) == 0 && at(0, 4) == 0, "the year mask hides the other runs");
    CHECK(e.heat(0, 8, sw, ne, 0xffffffffu).empty() && e.heat(8, 8, ne, sw, 0xffffffffu).empty(), "a bad box gives nothing");
}

static void put_u32(std::vector<uint8_t>& b, size_t at, uint32_t v) {
    for (int k = 0; k < 4; ++k) b[at + k] = uint8_t(v >> (8 * k));
}

static void test_dataset_versions() {
    Dataset ds;
    ds.runs.push_back(run(line(0, 0, 100, 0)));
    ds.version = 1;
    ds.meta = "ignored";
    auto v1 = encode(ds);
    Dataset back;
    std::string err;
    CHECK(decode(v1.data(), v1.size(), back, err) && back.version == 1 && back.meta.empty() && back.runs.size() == 1,
          "a v1 dataset (no metadata) still decodes");
    auto bad = v1;
    put_u32(bad, 8, 3);
    CHECK(!decode(bad.data(), bad.size(), back, err), "an unknown version is rejected");
    ds.version = kVersion;
    ds.meta = "{}";
    auto v2 = encode(ds);
    auto big = v2;
    put_u32(big, 16, kMaxMetaBytes + 1);
    CHECK(!decode(big.data(), big.size(), back, err), "metadata over the size cap is rejected");
    auto past = v2;
    put_u32(past, 16, kMaxMetaBytes);
    CHECK(!decode(past.data(), past.size(), back, err), "a metadata length past the end is rejected");
}

int main() {
    test_dataset_versions();
    test_roundtrip();
    test_position_diameter();
    test_out_and_back();
    test_sidewalks();
    test_stray_point();
    test_heading_at_intersection();
    test_turning_run_is_one_part();
    test_classify();
    test_combined_yield();
    test_notes_rules();
    test_notes_engine();
    test_runs_at();
    test_run_report();
    test_lot();
    test_heat();
    std::printf("%d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
