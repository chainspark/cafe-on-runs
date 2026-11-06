// Geometry helpers: local metric projection, point-to-segment distance, a uniform grid index.
#pragma once

#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <vector>

namespace ct {

constexpr double kPi = 3.14159265358979323846;
constexpr double kLat0 = 43.7;  // same reference latitude as the Python tolerance analyses
inline const double kKx = 111320.0 * std::cos(kLat0 * kPi / 180.0);
constexpr double kKy = 110540.0;

struct P {
    double x = 0, y = 0;
};

inline P project(double lat, double lon) { return {lon * kKx, lat * kKy}; }
inline double unproject_lat(const P& p) { return p.y / kKy; }
inline double unproject_lon(const P& p) { return p.x / kKx; }

inline double dist(const P& a, const P& b) { return std::hypot(a.x - b.x, a.y - b.y); }

// Closest point on segment ab to p: distance d, parameter t in [0,1], foot q, unit direction (tx,ty).
struct SegHit {
    double d = INFINITY, t = 0;
    P q;
    double tx = 1, ty = 0;
};

inline SegHit seg_dist(const P& p, const P& a, const P& b) {
    SegHit h;
    const double dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    double t = l2 > 1e-12 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0.0;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    h.t = t;
    h.q = {a.x + t * dx, a.y + t * dy};
    h.d = dist(p, h.q);
    const double l = std::sqrt(l2);
    if (l > 1e-9) { h.tx = dx / l; h.ty = dy / l; }
    return h;
}

// A normal to direction (tx,ty) in a canonical orientation, so offsets have a stable meaning no matter
// which way a centreline piece was digitised: for east-west-ish lines + points north, for
// north-south-ish lines + points west.
inline void canonical_normal(double tx, double ty, double& nx, double& ny) {
    nx = -ty;
    ny = tx;
    if (std::fabs(ny) >= std::fabs(nx)) {
        if (ny < 0) { nx = -nx; ny = -ny; }
    } else if (nx > 0) {
        nx = -nx; ny = -ny;
    }
}

// Uniform grid over items with a bounding box; query returns candidate item ids near a point.
class Grid {
public:
    explicit Grid(double cell = 25.0) : cell_(cell) {}

    void insert_box(uint32_t id, double x0, double y0, double x1, double y1) {
        const int64_t ix0 = idx(x0), ix1 = idx(x1), iy0 = idx(y0), iy1 = idx(y1);
        for (int64_t ix = ix0; ix <= ix1; ++ix)
            for (int64_t iy = iy0; iy <= iy1; ++iy) cells_[key(ix, iy)].push_back(id);
    }
    void insert_point(uint32_t id, const P& p) { cells_[key(idx(p.x), idx(p.y))].push_back(id); }

    template <class F>
    void query(const P& p, double r, F&& f) const {
        const int64_t ix0 = idx(p.x - r), ix1 = idx(p.x + r), iy0 = idx(p.y - r), iy1 = idx(p.y + r);
        for (int64_t ix = ix0; ix <= ix1; ++ix)
            for (int64_t iy = iy0; iy <= iy1; ++iy) {
                auto it = cells_.find(key(ix, iy));
                if (it == cells_.end()) continue;
                for (uint32_t id : it->second) f(id);
            }
    }

private:
    int64_t idx(double v) const { return static_cast<int64_t>(std::floor(v / cell_)); }
    static int64_t key(int64_t ix, int64_t iy) { return (ix << 32) ^ (iy & 0xffffffffLL); }
    double cell_;
    std::unordered_map<int64_t, std::vector<uint32_t>> cells_;
};

}  // namespace ct
