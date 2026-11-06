// The dataset embedded in the build: runs, street centreline pieces (nominals) and cafes (datums).
// Binary format "CAFETOL1" - see cafe_blob.py, which writes it, and dataset.cpp, which reads it.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "geom.hpp"

namespace ct {

struct LatLon {
    double lat = 0, lon = 0;
};

struct Run {
    int64_t activity_id = 0;
    int year = 0, month = 0, day = 0;
    std::vector<std::vector<LatLon>> parts;  // split wherever the track has a gap (privacy holes)
};

struct Street {
    uint32_t centreline_id = 0;
    uint8_t cls = 0;
    std::string name;
    std::vector<LatLon> pts;
};

struct Cafe {
    int64_t osm_id = 0;
    std::string name;
    LatLon pos;
};

struct Dataset {
    uint32_t version = 1;
    uint32_t flags = 0;  // bit 0: public (privacy-scrubbed) build
    std::vector<Run> runs;
    std::vector<Street> streets;
    std::vector<Cafe> cafes;
};

constexpr char kMagic[8] = {'C', 'A', 'F', 'E', 'T', 'O', 'L', '1'};
constexpr uint32_t kVersion = 1;
constexpr double kCoordScale = 1e5;  // coordinates are stored as integer 1e-5 degrees

// Returns false and fills err on malformed input.
bool decode(const uint8_t* data, size_t size, Dataset& out, std::string& err);
// Inverse of decode (used by the tests; Python's cafe_blob.encode is the production writer).
std::vector<uint8_t> encode(const Dataset& ds);

}  // namespace ct
