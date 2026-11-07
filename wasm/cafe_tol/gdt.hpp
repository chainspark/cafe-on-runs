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

class Engine {
public:
    bool load(Dataset ds, std::string& err);
    void set_radius(double r);  // re-cuts passes for every cafe
    double radius() const { return radius_; }
    void evaluate(const Settings& s);
    Detail detail(int cafe, const Settings& s) const;

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

    Dataset ds_;
    Network net_;
    Track tr_;
    Grid pt_grid_{50.0};
    std::vector<P> cafe_xy_;
    std::vector<std::vector<Pass>> passes_;
    std::vector<CafeResult> results_;
    double radius_ = 0;
};

}  // namespace ct
