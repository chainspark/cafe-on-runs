"""The CAFETOL1 dataset: runs, street centreline pieces and cafes, compiled into the WASM build.

Little-endian layout (read by wasm/cafe_tol/dataset.cpp):

    "CAFETOL1" | u32 version | u32 flags (bit0 = public)
    u32 meta_len; utf8 JSON provenance (version >= 2): build info and data sources, never coordinates
    u32 n_runs;    per run:   i64 activity_id, u16 year, u8 month, u8 day, u16 n_parts,
                              per part: coords
    u32 n_streets; per piece: u32 centreline_id, u8 class, u16 name_len, utf8 name, coords
    u32 n_cafes;   per cafe:  i32 lat_e5, i32 lon_e5, i64 osm_id, u16 name_len, utf8 name
    coords = u32 n, then n x (zigzag-varint dlat, zigzag-varint dlon) in 1e-5 degrees, first absolute

Also used by privacy.check to read the dataset back out of a built page and scan every coordinate.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"CAFETOL1"  # a fixed name: the 1 is not the format version
VERSION = 2
SUPPORTED_VERSIONS = (1, 2)  # v1 had no provenance meta
MAX_META_BYTES = 64 * 1024
SCALE = 1e5
FLAG_PUBLIC = 1


@dataclass
class Run:
    activity_id: int
    year: int
    month: int
    day: int
    parts: list[np.ndarray] = field(default_factory=list)  # each (n, 2) lat, lon


@dataclass
class Street:
    centreline_id: int
    cls: int
    name: str
    pts: np.ndarray  # (n, 2) lat, lon


@dataclass
class Cafe:
    lat: float
    lon: float
    osm_id: int
    name: str


@dataclass
class Dataset:
    flags: int
    runs: list[Run]
    streets: list[Street]
    cafes: list[Cafe]
    meta: dict = field(default_factory=dict)  # provenance: build info, data sources

    def all_points(self) -> np.ndarray:
        """Every coordinate in the dataset, (n, 2) lat, lon - for the privacy scan."""
        arrs = [p for r in self.runs for p in r.parts] + [s.pts for s in self.streets]
        arrs.append(np.array([[c.lat, c.lon] for c in self.cafes]).reshape(-1, 2))
        return np.vstack([a for a in arrs if len(a)]) if any(len(a) for a in arrs) else np.empty((0, 2))

    def names(self) -> list[str]:
        return [c.name for c in self.cafes] + sorted({s.name for s in self.streets})


# ---------- encoding ----------
def _varints(values: np.ndarray) -> bytes:
    """Zigzag + LEB128 varint encoding of an int64 array."""
    u = ((values.astype(np.int64) << 1) ^ (values.astype(np.int64) >> 63)).astype(np.uint64)
    out = bytearray()
    for v in u.tolist():
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)
    return bytes(out)


def _coords(pts: np.ndarray) -> bytes:
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    q = np.rint(pts * SCALE).astype(np.int64)
    d = np.diff(q, axis=0, prepend=np.zeros((1, 2), np.int64))
    return struct.pack("<I", len(q)) + _varints(d.reshape(-1))


def _str(s: str) -> bytes:
    b = s.encode("utf-8")[:65535]
    return struct.pack("<H", len(b)) + b


def encode(ds: Dataset) -> bytes:
    meta = json.dumps(ds.meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if ds.meta else b""
    if len(meta) > MAX_META_BYTES:
        raise ValueError("dataset metadata too large")
    out = [MAGIC, struct.pack("<II", VERSION, ds.flags), struct.pack("<I", len(meta)), meta,
           struct.pack("<I", len(ds.runs))]
    for r in ds.runs:
        out.append(struct.pack("<qHBBH", r.activity_id, r.year, r.month, r.day, len(r.parts)))
        out += [_coords(p) for p in r.parts]
    out.append(struct.pack("<I", len(ds.streets)))
    for s in ds.streets:
        out += [struct.pack("<IB", s.centreline_id, s.cls), _str(s.name), _coords(s.pts)]
    out.append(struct.pack("<I", len(ds.cafes)))
    for c in ds.cafes:
        out += [struct.pack("<iiq", round(c.lat * SCALE), round(c.lon * SCALE), c.osm_id), _str(c.name)]
    return b"".join(out)


def write_inc(blob: bytes, path) -> None:
    """A comma-separated byte list for `#include` inside a C array initialiser."""
    lines = (",".join(map(str, blob[i:i + 40])) for i in range(0, len(blob), 40))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",\n".join(lines) + "\n", encoding="ascii")


# ---------- decoding ----------
class _Reader:
    def __init__(self, b: bytes, pos: int = 0):
        self.b, self.pos = b, pos

    def fixed(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.b, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def coords(self) -> np.ndarray:
        n = self.fixed("I")
        vals = np.empty(2 * n, np.int64)
        b, pos = self.b, self.pos
        for i in range(2 * n):
            v = shift = 0
            while True:
                byte = b[pos]
                pos += 1
                v |= (byte & 0x7F) << shift
                if byte < 0x80:
                    break
                shift += 7
            vals[i] = (v >> 1) ^ -(v & 1)
        self.pos = pos
        return np.cumsum(vals.reshape(-1, 2), axis=0) / SCALE

    def str(self) -> str:
        n = self.fixed("H")
        if self.pos + n > len(self.b):
            raise ValueError("truncated string")
        s = self.b[self.pos:self.pos + n].decode("utf-8")
        self.pos += n
        return s


def decode(b: bytes, offset: int = 0) -> Dataset:
    if b[offset:offset + len(MAGIC)] != MAGIC:
        raise ValueError("not a CAFETOL1 dataset")
    r = _Reader(b, offset + len(MAGIC))
    version, flags = r.fixed("II")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported dataset version {version}")
    meta = {}
    if version >= 2:
        n = r.fixed("I")
        if n > MAX_META_BYTES or r.pos + n > len(r.b):
            raise ValueError("dataset metadata length out of range")
        meta = json.loads(r.b[r.pos:r.pos + n].decode("utf-8")) if n else {}
        r.pos += n
    runs = []
    for _ in range(r.fixed("I")):
        aid, year, month, day, n_parts = r.fixed("qHBBH")
        runs.append(Run(aid, year, month, day, [r.coords() for _ in range(n_parts)]))
    streets = []
    for _ in range(r.fixed("I")):
        cid, cls = r.fixed("IB")
        name = r.str()
        streets.append(Street(cid, cls, name, r.coords()))
    cafes = []
    for _ in range(r.fixed("I")):
        lat, lon, osm = r.fixed("iiq")
        cafes.append(Cafe(lat / SCALE, lon / SCALE, osm, r.str()))
    return Dataset(flags, runs, streets, cafes, meta)


# ---------- reading a dataset back out of a compiled .wasm ----------
def _leb(b: bytes, pos: int, signed: bool = False) -> tuple[int, int]:
    v = shift = 0
    while True:
        byte = b[pos]
        pos += 1
        v |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            if signed and byte & 0x40:
                v -= 1 << shift
            return v, pos


def wasm_memory_image(wasm: bytes) -> bytes:
    """Initial linear memory of a module, rebuilt from its data section.

    The optimiser may split a static array into several data segments (it drops long runs of zero
    bytes), so the dataset is searched for in the rebuilt memory, not in the raw file.
    """
    if wasm[:4] != b"\0asm":
        raise ValueError("not a wasm module")
    pos, segs = 8, []
    while pos < len(wasm):
        sec, size = wasm[pos], None
        size, pos = _leb(wasm, pos + 1)
        end = pos + size
        if sec == 11:  # data section
            count, p = _leb(wasm, pos)
            for _ in range(count):
                flags, p = _leb(wasm, p)
                if flags == 2:
                    _, p = _leb(wasm, p)  # memory index
                offset = None
                if flags in (0, 2):
                    if wasm[p] != 0x41:  # i32.const
                        raise ValueError("unsupported data segment offset expression")
                    offset, p = _leb(wasm, p + 1, signed=True)
                    if wasm[p] != 0x0B:
                        raise ValueError("unsupported data segment offset expression")
                    p += 1
                n, p = _leb(wasm, p)
                if offset is not None:
                    segs.append((offset, wasm[p:p + n]))
                p += n
        pos = end
    if not segs:
        return b""
    top = max(o + len(d) for o, d in segs)
    mem = bytearray(top)
    for o, d in segs:
        mem[o:o + len(d)] = d
    return bytes(mem)


def find_in_wasm(wasm: bytes) -> list[Dataset]:
    """Every dataset in the module's initial memory.

    The decoder's own 8-byte magic constant also lives in memory; an occurrence without a
    supported version header is skipped (the engine would reject it too). Any other decoding error raises,
    so a damaged dataset can never pass the privacy scan unread.
    """
    mem = wasm_memory_image(wasm)
    found, i = [], mem.find(MAGIC)
    while i >= 0:
        j = i + len(MAGIC)
        if len(mem) >= j + 4 and struct.unpack_from("<I", mem, j)[0] in SUPPORTED_VERSIONS:
            found.append(decode(mem, i))
        i = mem.find(MAGIC, i + 1)
    return found
