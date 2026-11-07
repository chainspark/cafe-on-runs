#include "gdt.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>

namespace ct {
namespace {

std::vector<P> densify(const std::vector<P>& pts, double step) {
    std::vector<P> out;
    for (size_t k = 0; k + 1 < pts.size(); ++k) {
        const P& a = pts[k];
        const P& b = pts[k + 1];
        const double l = dist(a, b);
        const int n = std::max(1, int(std::ceil(l / step)));
        for (int i = 0; i < n; ++i) out.push_back({a.x + (b.x - a.x) * i / n, a.y + (b.y - a.y) * i / n});
    }
    if (!pts.empty()) out.push_back(pts.back());
    return out;
}

LatLon to_ll(const P& p) { return {unproject_lat(p), unproject_lon(p)}; }

double percentile(std::vector<double> v, double q) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, size_t(std::lround(q * (v.size() - 1))))];
}

}  // namespace

bool Engine::load(Dataset ds, std::string& err) {
    ds_ = std::move(ds);
    net_ = Network{};
    tr_ = Track{};
    pt_grid_ = Grid{50.0};
    build_network(ds_, net_);
    flatten_runs(ds_, tr_);
    match_points(net_, tr_);
    for (uint32_t i = 0; i < tr_.p.size(); ++i) pt_grid_.insert_point(i, tr_.p[i]);
    cafe_xy_.clear();
    for (const auto& c : ds_.cafes) cafe_xy_.push_back(project(c.pos.lat, c.pos.lon));
    passes_.assign(ds_.cafes.size(), {});
    results_.assign(ds_.cafes.size(), {});
    radius_ = 0;
    err.clear();
    return true;
}

void Engine::measure(Pass& ps, const P& cafe) const {
    const auto& p = tr_.p;
    if (ps.start == ps.end) {
        ps.d = dist(cafe, p[ps.start]);
        ps.q = p[ps.start];
    }
    for (int32_t k = ps.start; k < ps.end; ++k) {
        const SegHit h = seg_dist(cafe, p[k], p[k + 1]);
        if (h.d < ps.d) { ps.d = h.d; ps.q = h.q; }
    }
    double max_abs = 0, sum = 0, nxs = 0, nys = 0;
    int count = 0;
    std::set<int32_t> pieces;
    for (int32_t i = ps.start; i <= ps.end; ++i) {
        double off, nx, ny;
        if (tr_.name[i] == ps.name) {
            off = tr_.off[i];
            nx = tr_.nx[i];
            ny = tr_.ny[i];
            pieces.insert(tr_.piece[i]);
        } else {  // a stray (or unmatched) point still counts against this pass's street
            const OffsetHit h = offset_to_street(net_, p[i], ps.name, kStraySearchM);
            if (h.piece < 0) continue;
            off = h.off;
            nx = h.nx;
            ny = h.ny;
        }
        max_abs = std::max(max_abs, std::fabs(off));
        sum += off;
        nxs += nx;
        nys += ny;
        ++count;
    }
    ps.prof = 2 * max_abs;
    ps.mean_off = count ? sum / count : 0;
    const double nl = std::hypot(nxs, nys);
    if (nl > 1e-9) { ps.nx = nxs / nl; ps.ny = nys / nl; }
    ps.pieces.assign(pieces.begin(), pieces.end());
}

std::vector<Pass> Engine::cut_passes(const P& cafe, double r) const {
    std::vector<int32_t> idx;
    pt_grid_.query(cafe, r, [&](uint32_t id) {
        if (dist(tr_.p[id], cafe) <= r) idx.push_back(int32_t(id));
    });
    std::sort(idx.begin(), idx.end());
    std::vector<Pass> out;
    auto emit = [&](int32_t s, int32_t e, int32_t name) {
        if (name < 0 || e - s + 1 < 3) return;
        Pass ps;
        ps.run = tr_.part_run[tr_.pt_part[s]];
        ps.name = name;
        ps.start = s;
        ps.end = e;
        measure(ps, cafe);
        out.push_back(std::move(ps));
    };
    size_t a = 0;
    while (a < idx.size()) {
        size_t b = a;
        while (b + 1 < idx.size() && idx[b + 1] == idx[b] + 1 && tr_.pt_part[idx[b + 1]] == tr_.pt_part[idx[a]]) ++b;
        int32_t cur = -1, s = idx[a];
        for (int32_t i = idx[a]; i <= idx[b]; ++i) {
            const int32_t n = tr_.name[i];
            if (n >= 0 && cur >= 0 && n != cur) {  // the run turned onto another street: new pass
                emit(s, i - 1, cur);
                s = i;
                cur = n;
            } else if (cur < 0 && n >= 0) {
                cur = n;
            }
        }
        emit(s, idx[b], cur);
        a = b + 1;
    }
    return out;
}

void Engine::set_radius(double r) {
    radius_ = r;
    for (size_t c = 0; c < cafe_xy_.size(); ++c) passes_[c] = cut_passes(cafe_xy_[c], r);
}

bool Engine::in_years(int32_t run, uint32_t mask) const {
    const int bit = std::clamp(ds_.runs[run].year - 2000, 0, 31);
    return (mask >> bit) & 1u;
}

std::vector<Part> Engine::parts_of(const std::vector<Pass>& ps, const Settings& s) const {
    std::map<int32_t, Part> by_run;
    for (const auto& p : ps) {
        if (!in_years(p.run, s.year_mask)) continue;
        Part& part = by_run[p.run];
        part.run = p.run;
        part.d = std::min(part.d, p.d);            // ⌖: the run's closest approach
        part.prof = std::max(part.prof, p.prof);   // ⌒: the run's worst street near the cafe
    }
    std::vector<Part> out;
    for (auto& [run, part] : by_run) {
        part.pos = 2 * part.d <= s.tol_diam;
        part.pro = part.prof <= s.band;
        out.push_back(part);
    }
    return out;
}

int32_t Engine::classify(int runs, double pos, double pro, double both, double acceptance) {
    constexpr double eps = 1e-9;
    if (runs < kMinRuns) return kFew;
    if (both >= acceptance - eps) return kBoth;
    const bool P = pos >= acceptance - eps, Q = pro >= acceptance - eps;
    if (P && Q) return kApart;  // each callout passes alone, but not on the same runs
    if (P) return kPosOnly;
    if (Q) return kProfOnly;
    return kNeither;
}

void Engine::evaluate(const Settings& s) {
    for (size_t c = 0; c < passes_.size(); ++c) {
        const auto parts = parts_of(passes_[c], s);
        CafeResult r;
        r.runs = int32_t(parts.size());
        if (r.runs) {
            int pos = 0, pro = 0, both = 0;
            std::vector<double> diam;
            for (const auto& p : parts) {
                pos += p.pos;
                pro += p.pro;
                both += p.pos && p.pro;
                diam.push_back(2 * p.d);
            }
            r.pos_yield = double(pos) / r.runs;
            r.prof_yield = double(pro) / r.runs;
            r.both_yield = double(both) / r.runs;
            std::sort(diam.begin(), diam.end());
            r.best_diam = diam.front();
            r.median_diam = diam[diam.size() / 2];
        }
        r.status = classify(r.runs, r.pos_yield, r.prof_yield, r.both_yield, s.acceptance);
        results_[c] = r;
    }
}

std::vector<Note> Engine::notes(const Detail& d, const Settings& s) {
    std::vector<Note> out;
    const int n = int(d.parts.size());
    if (n < kMinRuns) return out;
    int pos = 0, pro = 0, both = 0, neither = 0;
    double closest = INFINITY;
    std::vector<double> prof;
    for (const auto& p : d.parts) {
        pos += p.pos;
        pro += p.pro;
        both += p.pos && p.pro;
        neither += !p.pos && !p.pro;
        closest = std::min(closest, p.d);
        prof.push_back(p.prof);
    }
    const bool many = n >= kNoteMinRuns;
    const int32_t status = classify(n, double(pos) / n, double(pro) / n, double(both) / n, s.acceptance);

    if (many && closest <= kNoteDoorM) out.push_back({kNoteDoor, closest});
    if (!d.features.empty() && d.features[0].passes >= kNoteSideMinPasses) {
        const Feature& f = d.features[0];
        int on = 0, m = 0;  // passes north (E-W street) or west (N-S street) of the centreline
        for (const auto& p : d.passes)
            if (p.name == f.name) { ++m; on += p.mean_off > 0; }
        const bool ew = std::fabs(f.ny) >= std::fabs(f.nx);
        if (m >= kNoteSideMinPasses && on >= kNoteSideShare * m)
            out.push_back({kNoteSide, ew ? 0.0 : 2.0, double(on), double(m), f.name});
        else if (m >= kNoteSideMinPasses && m - on >= kNoteSideShare * m)
            out.push_back({kNoteSide, ew ? 1.0 : 3.0, double(m - on), double(m), f.name});
    }
    if (status == kApart) out.push_back({kNoteApart});
    const double p95 = percentile(prof, 0.95);
    if (many && p95 > s.band && p95 <= s.band + kNoteNearBandM && pro >= kNoteNearBandHold * n)
        out.push_back({kNoteNearBand, p95, double(pro), double(n)});
    if (n >= kNoteDetourMinRuns && neither == 1) out.push_back({kNoteOneDetour, double(n)});
    if (many && both == n) out.push_back({kNoteAllAccepted, double(n)});
    if (out.size() > size_t(kNoteMax)) out.resize(kNoteMax);
    return out;
}

Detail Engine::detail(int cafe, const Settings& s, bool geometry) const {
    Detail d;
    if (cafe < 0 || size_t(cafe) >= passes_.size()) return d;
    const P c = cafe_xy_[cafe];
    for (const auto& p : passes_[cafe])
        if (in_years(p.run, s.year_mask)) d.passes.push_back(p);
    d.parts = parts_of(d.passes, s);

    std::map<int32_t, std::vector<const Pass*>> by_name;
    std::set<int32_t> pieces;
    for (const auto& p : d.passes) {
        by_name[p.name].push_back(&p);
        pieces.insert(p.pieces.begin(), p.pieces.end());
    }
    for (const auto& [name, ps] : by_name) {
        Feature f;
        f.name = name;
        f.passes = int32_t(ps.size());
        std::vector<double> prof;
        double nxs = 0, nys = 0;
        for (const Pass* p : ps) {
            const bool pos = 2 * p->d <= s.tol_diam, pro = p->prof <= s.band;
            f.pos += pos;
            f.pro += pro;
            f.both += pos && pro;
            prof.push_back(p->prof);
            f.bias += p->mean_off / ps.size();
            nxs += p->nx;
            nys += p->ny;
        }
        f.p95 = percentile(prof, 0.95);
        const double nl = std::hypot(nxs, nys);
        if (nl > 1e-9) { f.nx = nxs / nl; f.ny = nys / nl; }
        d.features.push_back(f);
    }
    // most passes first; ties by name id, so "the main street" is the same on every call
    std::sort(d.features.begin(), d.features.end(), [](const Feature& a, const Feature& b) {
        return a.passes != b.passes ? a.passes > b.passes : a.name < b.name;
    });
    if (!geometry) return d;

    // Nominal centreline inside R and its equally disposed band, for drawing.
    const double half = s.band / 2;
    for (int32_t piece : pieces) {
        const auto pts = densify(net_.pieces[piece], 5.0);
        size_t i = 0;
        while (i < pts.size()) {
            if (dist(pts[i], c) > radius_) { ++i; continue; }
            size_t j = i;
            while (j + 1 < pts.size() && dist(pts[j + 1], c) <= radius_) ++j;
            if (j > i) {
                std::vector<LatLon> line, left, right;
                for (size_t k = i; k <= j; ++k) {
                    const P& a = pts[k == 0 ? 0 : k - 1];
                    const P& b = pts[std::min(pts.size() - 1, k + 1)];
                    const double l = std::max(1e-9, dist(a, b));
                    const double nx = -(b.y - a.y) / l, ny = (b.x - a.x) / l;
                    line.push_back(to_ll(pts[k]));
                    left.push_back(to_ll({pts[k].x + nx * half, pts[k].y + ny * half}));
                    right.push_back(to_ll({pts[k].x - nx * half, pts[k].y - ny * half}));
                }
                std::reverse(right.begin(), right.end());
                left.insert(left.end(), right.begin(), right.end());
                d.nominals.push_back(std::move(line));
                d.nominal_names.push_back(net_.piece_name[piece]);
                d.bands.push_back(std::move(left));
            }
            i = j + 1;
        }
    }
    return d;
}

}  // namespace ct
