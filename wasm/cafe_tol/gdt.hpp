// GD&T on the run map.
//   Datum A ............ the cafe
//   Drawing nominal .... the City street centreline
//   Part / lot ......... one run / all runs within R of the cafe
//   ⌖ true position .... Ø = 2 x the run's closest approach to the cafe, conforms if Ø <= tol
//   ⌒ profile of line .. 2 x max |offset| from the centreline (equally disposed), worst pass of the run
//   accepted run ....... meets both callouts; a cafe conforms to both when accepted runs >= acceptance
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "dataset.hpp"
#include "match.hpp"

namespace ct {

constexpr int kMinRuns = 3;  // a status needs at least this many runs

enum Status : int32_t { kBoth = 0, kPosOnly = 1, kProfOnly = 2, kApart = 3, kNeither = 4, kFew = 5 };

// One run's contiguous stretch inside R of a cafe, on one street.
struct Pass {
    int32_t run = -1, name = -1;
    int32_t start = 0, end = 0;   // inclusive point indices into Track
    double d = INFINITY;          // closest approach to the cafe (m)
    P q;                          // where it happened
    int32_t qi = -1;              // ... on the segment starting at this point index
    double prof = 0;              // 2 x max |offset| from the centreline (m)
    double mean_off = 0;          // bias: + = north side (E-W streets) / west side (N-S streets)
    double nx = 0, ny = 1;        // mean canonical normal, to name the side
    std::vector<int32_t> pieces;  // centreline pieces this pass was measured against
};

// One run past a cafe = one part.
struct Part {
    int32_t run = -1;
    double d = INFINITY, prof = 0;
    bool pos = false, pro = false;
};

struct CafeResult {
    int32_t runs = 0, status = kFew;
    double pos_yield = 0, prof_yield = 0, both_yield = 0;
    double best_diam = NAN, median_diam = NAN;
};

struct Feature {
    int32_t name = -1, passes = 0, pos = 0, pro = 0, both = 0;
    double p95 = 0, bias = 0, nx = 0, ny = 1;
};

struct Detail {
    std::vector<Pass> passes;  // year-filtered
    std::vector<Part> parts;
    std::vector<Feature> features;
    std::vector<std::vector<LatLon>> nominals, bands;  // centreline inside R, and its ±band/2 polygon
    std::vector<int32_t> nominal_names;                // street name id of each nominal / band
};

struct Settings {
    double tol_diam = 50, band = 30, acceptance = 0.75;
    uint32_t year_mask = 0xffffffffu;  // bit (year - 2000)
};

// Checker's notes: facts worth pointing out about a cafe's runs. The engine owns the rules, their
// thresholds and the numbers; the page owns the wording. Thresholds keep notes rare (about one cafe in
// eight on the real data). At most kNoteMax per cafe, in this order (most striking first):
enum NoteKind : int32_t {
    kNoteDoor = 1,          // a run came within kNoteDoorM of the cafe. a = closest approach (m)
    kNoteSide = 2,          // >= kNoteSideShare of the main street's passes on one side.
                            //   a = side (0 north, 1 south, 2 west, 3 east), b = passes on it, c = passes, street
    kNoteApart = 3,         // status "each alone, not together"
    kNoteNearBand = 4,      // ⌒ p95 just over the band while most runs hold it. a = p95 (m), b = runs holding, c = runs
    kNoteOneDetour = 5,     // exactly one run fails both callouts. a = runs
    kNoteAllAccepted = 6,   // every run accepted. a = runs
};
constexpr int kNoteMax = 3;
constexpr int kNoteMinRuns = 10;           // door, near-band and all-accepted need this many runs
constexpr double kNoteDoorM = 2.0;
constexpr int kNoteSideMinPasses = 10;
constexpr double kNoteSideShare = 0.9;
constexpr double kNoteNearBandM = 8.0;     // p95 at most this far over the band...
constexpr double kNoteNearBandHold = 0.8;  // ...while at least this share of runs hold the band
constexpr int kNoteDetourMinRuns = 15;

struct Note {
    int32_t kind = 0;
    double a = 0, b = 0, c = 0;
    int32_t street = -1;  // street name id, for kNoteSide
};

// ---- inspecting one run (a part) across every cafe ----
struct RunHit {
    int32_t run = -1;
    double d = INFINITY;  // distance from the query point to the run's track (m)
};
// One cafe a run passed: the run's part there, and where along the run it happened.
struct RunRow {
    int32_t cafe = -1;
    Part part;
    double along = 0;     // distance along the run to its closest approach (m)
    int32_t street = -1;  // street name id of the pass that gave the closest approach
    P q;                  // the closest approach
};
struct ProfilePt {
    double x = 0;    // position along the street relative to the cafe (m), + east / north
    double off = 0;  // signed offset from the centreline (m), canonical normal
};
// A run's offsets at one cafe: the pass that gave its closest approach, point by point.
struct RunProfile {
    int32_t street = -1;
    double d = INFINITY, prof = 0;
    std::vector<ProfilePt> pts;
};

class Engine {
public:
    bool load(Dataset ds, std::string& err);
    void set_radius(double r);  // re-cuts passes for every cafe
    double radius() const { return radius_; }
    void evaluate(const Settings& s);
    // geometry = false skips the nominal lines and bands (drawing only), for notes over every cafe.
    Detail detail(int cafe, const Settings& s, bool geometry = true) const;
    // The checker's notes for one cafe's detail (see NoteKind).
    static std::vector<Note> notes(const Detail& d, const Settings& s);

    // Runs (in the year mask) whose track passes within r of p, nearest first.
    std::vector<RunHit> runs_at(const P& p, double r, uint32_t year_mask) const;
    // Every cafe the run passed within R, in the order it passed them, with the run's part at each.
    std::vector<RunRow> run_report(int run, const Settings& s) const;
    // The run's offset profile at one cafe (empty if it didn't pass within R).
    RunProfile run_profile(int cafe, int run) const;
    double run_length(int run) const { return run >= 0 && size_t(run) < run_len_.size() ? run_len_[run] : 0; }

    const Dataset& data() const { return ds_; }
    const Track& track() const { return tr_; }
    const Network& network() const { return net_; }
    const std::vector<CafeResult>& results() const { return results_; }
    const std::vector<Pass>& passes(int cafe) const { return passes_[cafe]; }

    // Exposed for tests.
    static int32_t classify(int runs, double pos, double pro, double both, double acceptance);
    std::vector<Pass> cut_passes(const P& cafe, double r) const;

private:
    bool in_years(int32_t run, uint32_t mask) const;
    std::vector<Part> parts_of(const std::vector<Pass>& ps, const Settings& s) const;
    void measure(Pass& ps, const P& cafe) const;
    // Signed offset of point i from street `name`: its own match, or a stray measured against the street.
    bool offset_of(int32_t i, int32_t name, double& off, double& nx, double& ny) const;

    Dataset ds_;
    Network net_;
    Track tr_;
    Grid pt_grid_{50.0};
    std::vector<P> cafe_xy_;
    std::vector<std::vector<Pass>> passes_;
    std::vector<CafeResult> results_;
    std::vector<float> pt_along_;   // distance along its run to each point (m); gaps count as straight lines
    std::vector<double> run_len_;
    double radius_ = 0;
};

}  // namespace ct
