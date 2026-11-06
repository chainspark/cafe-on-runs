// Matching GPS points to the drawing: each point is assigned to the nearest street centreline piece
// within kMatchM whose direction agrees with the run's heading. Runs are never merged with each other.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "dataset.hpp"
#include "geom.hpp"

namespace ct {

constexpr double kMatchM = 25.0;          // a point farther than this from every street is unmatched
constexpr double kHeadingDeg = 35.0;      // max angle between run heading and street (direction ignored)
constexpr int kMinStreetPts = 4;          // a street match shorter than this (~20 m) is a blip, not a pass
constexpr double kStraySearchM = 300.0;   // how far to look for the pass's street when measuring a stray

// Street centreline pieces, grouped by street name (the feature).
struct Network {
    std::vector<std::vector<P>> pieces;
    std::vector<int32_t> piece_name;      // index into names
    std::vector<std::string> names;
    std::vector<std::pair<uint32_t, uint32_t>> segs;  // (piece, first vertex) for each segment
    Grid seg_grid{25.0};
};

// All run points, flattened. Parts are contiguous ranges [part_start[k], part_start[k+1]).
struct Track {
    std::vector<P> p;
    std::vector<LatLon> ll;
    std::vector<int32_t> part_start;      // size n_parts + 1
    std::vector<int32_t> part_run;
    std::vector<int32_t> pt_part;
    std::vector<int32_t> name;            // matched street name per point, -1 if none
    std::vector<int32_t> piece;           // matched piece per point, -1 if none
    std::vector<float> off;               // signed offset (m) from the matched piece, canonical normal
    std::vector<float> nx, ny;            // canonical normal at the matched foot
};

struct OffsetHit {
    double d = INFINITY, off = 0, nx = 0, ny = 1;
    int32_t piece = -1;
};

void build_network(const Dataset& ds, Network& net);
void flatten_runs(const Dataset& ds, Track& tr);
void match_points(const Network& net, Track& tr);
// Nearest piece of street `name` within `radius` of p, with the signed (canonical) offset.
OffsetHit offset_to_street(const Network& net, const P& p, int32_t name, double radius);

}  // namespace ct
