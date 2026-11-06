#include "dataset.hpp"

#include <cmath>
#include <cstring>

namespace ct {
namespace {

class Reader {
public:
    Reader(const uint8_t* d, size_t n) : d_(d), n_(n) {}
    bool ok() const { return ok_; }

    template <class T>
    T fixed() {
        T v{};
        if (pos_ + sizeof(T) > n_) { ok_ = false; return v; }
        std::memcpy(&v, d_ + pos_, sizeof(T));  // little-endian on wasm and x86
        pos_ += sizeof(T);
        return v;
    }
    uint64_t varint() {
        uint64_t v = 0;
        for (int shift = 0; shift < 64; shift += 7) {
            if (pos_ >= n_) { ok_ = false; return 0; }
            const uint8_t b = d_[pos_++];
            v |= uint64_t(b & 0x7f) << shift;
            if (!(b & 0x80)) return v;
        }
        ok_ = false;
        return 0;
    }
    int64_t zigzag() {
        const uint64_t u = varint();
        return static_cast<int64_t>(u >> 1) ^ -static_cast<int64_t>(u & 1);
    }
    std::string str(size_t len) {
        if (len > n_ - pos_) { ok_ = false; return {}; }  // no overflow on wasm32
        std::string s(reinterpret_cast<const char*>(d_ + pos_), len);
        pos_ += len;
        return s;
    }
    bool magic() {
        if (n_ < sizeof(kMagic) || std::memcmp(d_, kMagic, sizeof(kMagic)) != 0) return false;
        pos_ = sizeof(kMagic);
        return true;
    }

private:
    const uint8_t* d_;
    size_t n_, pos_ = 0;
    bool ok_ = true;
};

std::vector<LatLon> read_coords(Reader& r) {
    const uint32_t n = r.fixed<uint32_t>();
    std::vector<LatLon> pts;
    if (!r.ok() || n > 50'000'000) return pts;
    pts.reserve(n);
    int64_t lat = 0, lon = 0;
    for (uint32_t i = 0; i < n && r.ok(); ++i) {
        lat += r.zigzag();
        lon += r.zigzag();
        pts.push_back({lat / kCoordScale, lon / kCoordScale});
    }
    return pts;
}

void put_varint(std::vector<uint8_t>& out, uint64_t v) {
    while (v >= 0x80) { out.push_back(uint8_t(v) | 0x80); v >>= 7; }
    out.push_back(uint8_t(v));
}
void put_zigzag(std::vector<uint8_t>& out, int64_t v) { put_varint(out, (uint64_t(v) << 1) ^ uint64_t(v >> 63)); }
template <class T>
void put(std::vector<uint8_t>& out, T v) {
    const auto* b = reinterpret_cast<const uint8_t*>(&v);
    out.insert(out.end(), b, b + sizeof(T));
}
void put_coords(std::vector<uint8_t>& out, const std::vector<LatLon>& pts) {
    put<uint32_t>(out, uint32_t(pts.size()));
    int64_t plat = 0, plon = 0;
    for (const auto& p : pts) {
        const int64_t lat = std::llround(p.lat * kCoordScale), lon = std::llround(p.lon * kCoordScale);
        put_zigzag(out, lat - plat);
        put_zigzag(out, lon - plon);
        plat = lat;
        plon = lon;
    }
}
void put_str(std::vector<uint8_t>& out, const std::string& s) {
    put<uint16_t>(out, uint16_t(s.size()));
    out.insert(out.end(), s.begin(), s.end());
}

}  // namespace

bool decode(const uint8_t* data, size_t size, Dataset& out, std::string& err) {
    Reader r(data, size);
    if (!r.magic()) { err = "missing CAFETOL1 magic"; return false; }
    out.version = r.fixed<uint32_t>();
    out.flags = r.fixed<uint32_t>();
    if (!r.ok() || out.version < kMinVersion || out.version > kVersion) { err = "unsupported dataset version"; return false; }
    out.meta.clear();
    if (out.version >= 2) {
        const uint32_t n = r.fixed<uint32_t>();
        if (n > kMaxMetaBytes) { err = "dataset metadata too large"; return false; }
        out.meta = r.str(n);
    }

    const uint32_t n_runs = r.fixed<uint32_t>();
    for (uint32_t i = 0; i < n_runs && r.ok(); ++i) {
        Run run;
        run.activity_id = r.fixed<int64_t>();
        run.year = r.fixed<uint16_t>();
        run.month = r.fixed<uint8_t>();
        run.day = r.fixed<uint8_t>();
        const uint16_t n_parts = r.fixed<uint16_t>();
        for (uint16_t k = 0; k < n_parts && r.ok(); ++k) run.parts.push_back(read_coords(r));
        out.runs.push_back(std::move(run));
    }
    const uint32_t n_streets = r.fixed<uint32_t>();
    for (uint32_t i = 0; i < n_streets && r.ok(); ++i) {
        Street s;
        s.centreline_id = r.fixed<uint32_t>();
        s.cls = r.fixed<uint8_t>();
        s.name = r.str(r.fixed<uint16_t>());
        s.pts = read_coords(r);
        out.streets.push_back(std::move(s));
    }
    const uint32_t n_cafes = r.fixed<uint32_t>();
    for (uint32_t i = 0; i < n_cafes && r.ok(); ++i) {
        Cafe c;
        c.pos.lat = r.fixed<int32_t>() / kCoordScale;
        c.pos.lon = r.fixed<int32_t>() / kCoordScale;
        c.osm_id = r.fixed<int64_t>();
        c.name = r.str(r.fixed<uint16_t>());
        out.cafes.push_back(std::move(c));
    }
    if (!r.ok()) { err = "truncated dataset"; return false; }
    return true;
}

std::vector<uint8_t> encode(const Dataset& ds) {
    std::vector<uint8_t> out(kMagic, kMagic + sizeof(kMagic));
    put<uint32_t>(out, ds.version);
    put<uint32_t>(out, ds.flags);
    if (ds.version >= 2) {
        put<uint32_t>(out, uint32_t(ds.meta.size()));
        out.insert(out.end(), ds.meta.begin(), ds.meta.end());
    }
    put<uint32_t>(out, uint32_t(ds.runs.size()));
    for (const auto& run : ds.runs) {
        put<int64_t>(out, run.activity_id);
        put<uint16_t>(out, uint16_t(run.year));
        put<uint8_t>(out, uint8_t(run.month));
        put<uint8_t>(out, uint8_t(run.day));
        put<uint16_t>(out, uint16_t(run.parts.size()));
        for (const auto& part : run.parts) put_coords(out, part);
    }
    put<uint32_t>(out, uint32_t(ds.streets.size()));
    for (const auto& s : ds.streets) {
        put<uint32_t>(out, s.centreline_id);
        put<uint8_t>(out, s.cls);
        put_str(out, s.name);
        put_coords(out, s.pts);
    }
    put<uint32_t>(out, uint32_t(ds.cafes.size()));
    for (const auto& c : ds.cafes) {
        put<int32_t>(out, int32_t(std::lround(c.pos.lat * kCoordScale)));
        put<int32_t>(out, int32_t(std::lround(c.pos.lon * kCoordScale)));
        put<int64_t>(out, c.osm_id);
        put_str(out, c.name);
    }
    return out;
}

}  // namespace ct
