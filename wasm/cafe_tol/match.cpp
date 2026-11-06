#include "match.hpp"

#include <algorithm>
#include <cmath>
#include <unordered_map>

namespace ct {

void build_network(const Dataset& ds, Network& net) {
    std::unordered_map<std::string, int32_t> name_ids;
    for (const auto& s : ds.streets) {
        if (s.pts.size() < 2) continue;
        auto [it, added] = name_ids.try_emplace(s.name, int32_t(net.names.size()));
        if (added) net.names.push_back(s.name);
        std::vector<P> pts;
        pts.reserve(s.pts.size());
        for (const auto& q : s.pts) pts.push_back(project(q.lat, q.lon));
        const uint32_t piece = uint32_t(net.pieces.size());
        for (uint32_t k = 0; k + 1 < pts.size(); ++k) {
            const uint32_t seg = uint32_t(net.segs.size());
            net.segs.emplace_back(piece, k);
            const P& a = pts[k];
            const P& b = pts[k + 1];
            net.seg_grid.insert_box(seg, std::min(a.x, b.x), std::min(a.y, b.y), std::max(a.x, b.x), std::max(a.y, b.y));
        }
        net.pieces.push_back(std::move(pts));
        net.piece_name.push_back(it->second);
    }
}

void flatten_runs(const Dataset& ds, Track& tr) {
    tr.part_start.push_back(0);
    for (size_t r = 0; r < ds.runs.size(); ++r) {
        for (const auto& part : ds.runs[r].parts) {
            if (part.empty()) continue;
            const int32_t k = int32_t(tr.part_run.size());
            for (const auto& q : part) {
                tr.p.push_back(project(q.lat, q.lon));
                tr.ll.push_back(q);
                tr.pt_part.push_back(k);
            }
            tr.part_run.push_back(int32_t(r));
            tr.part_start.push_back(int32_t(tr.p.size()));
        }
    }
    const size_t n = tr.p.size();
    tr.name.assign(n, -1);
    tr.piece.assign(n, -1);
    tr.off.assign(n, NAN);
    tr.nx.assign(n, 0.f);
    tr.ny.assign(n, 1.f);
}

void match_points(const Network& net, Track& tr) {
    const double cos_h = std::cos(kHeadingDeg * kPi / 180.0);
    const size_t n_parts = tr.part_run.size();
    for (size_t k = 0; k < n_parts; ++k) {
        const int32_t s = tr.part_start[k], e = tr.part_start[k + 1];
        for (int32_t i = s; i < e; ++i) {
            const P& a = tr.p[std::max(s, i - 1)];
            const P& b = tr.p[std::min(e - 1, i + 1)];
            const double hl = dist(a, b);
            if (hl < 1e-6) continue;
            const double hx = (b.x - a.x) / hl, hy = (b.y - a.y) / hl;
            SegHit best;
            int32_t best_piece = -1;
            net.seg_grid.query(tr.p[i], kMatchM, [&](uint32_t seg) {
                const auto [piece, v] = net.segs[seg];
                const auto& pts = net.pieces[piece];
                const SegHit h = seg_dist(tr.p[i], pts[v], pts[v + 1]);
                if (h.d <= kMatchM && h.d < best.d && std::fabs(h.tx * hx + h.ty * hy) >= cos_h) {
                    best = h;
                    best_piece = int32_t(piece);
                }
            });
            if (best_piece < 0) continue;
            double nx, ny;
            canonical_normal(best.tx, best.ty, nx, ny);
            tr.piece[i] = best_piece;
            tr.name[i] = net.piece_name[best_piece];
            tr.off[i] = float((tr.p[i].x - best.q.x) * nx + (tr.p[i].y - best.q.y) * ny);
            tr.nx[i] = float(nx);
            tr.ny[i] = float(ny);
        }
        // A short match to a different street (e.g. clipping a cross street at an intersection) is a
        // blip: leave those points unmatched so they are measured against the surrounding street.
        int32_t i = s;
        while (i < e) {
            int32_t j = i;
            while (j + 1 < e && tr.name[j + 1] == tr.name[i]) ++j;
            const int32_t v = tr.name[i];
            if (v >= 0 && j - i + 1 < kMinStreetPts) {
                const int32_t prev = i > s ? tr.name[i - 1] : -2, next = j + 1 < e ? tr.name[j + 1] : -2;
                if (prev != v && next != v)
                    for (int32_t t = i; t <= j; ++t) { tr.name[t] = -1; tr.piece[t] = -1; tr.off[t] = NAN; }
            }
            i = j + 1;
        }
    }
}

OffsetHit offset_to_street(const Network& net, const P& p, int32_t name, double radius) {
    OffsetHit out;
    net.seg_grid.query(p, radius, [&](uint32_t seg) {
        const auto [piece, v] = net.segs[seg];
        if (net.piece_name[piece] != name) return;
        const auto& pts = net.pieces[piece];
        const SegHit h = seg_dist(p, pts[v], pts[v + 1]);
        if (h.d < out.d) {
            double nx, ny;
            canonical_normal(h.tx, h.ty, nx, ny);
            out.d = h.d;
            out.off = (p.x - h.q.x) * nx + (p.y - h.q.y) * ny;
            out.nx = nx;
            out.ny = ny;
            out.piece = int32_t(piece);
        }
    });
    return out;
}

}  // namespace ct
