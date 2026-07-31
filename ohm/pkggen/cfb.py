"""Minimal MS-CFB (OLE2 compound file) writer — the container Altium libraries live in.

An Altium `.PcbLib`/`.SchLib` is not a text format and not a zip: it is a Microsoft
Compound File, a little filesystem-in-a-file of named storages (directories) and
streams (files). Nothing in the stdlib writes one and Ohm carries no runtime
dependency beyond lark, so this is the smallest conforming writer that covers what
the Altium emitters need:

  * version 3 (512-byte sectors), which is what every Altium file in the wild uses;
  * the mini stream, because an Altium library is dozens of sub-4 KB streams and the
    spec requires anything under `_MINI_CUTOFF` to live there — a reader looks for
    small streams in the mini stream and will not find them in the FAT;
  * no DIFAT sectors. Those are only needed past 109 FAT sectors ≈ 7 MB of container,
    which no generated library approaches. That is asserted, not assumed.

Output is byte-identical run to run: all timestamps and CLSIDs are zero, and sibling
order is the spec's own collation (length, then case-folded name), not dict order.

Reference: MS-CFB, [MS-CFB] rev 2.0. Verified against real Altium-authored libraries
and read back with an independent implementation (`olefile`) in tests/test_altium.py.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping

_SECTOR = 512
_MINI_SECTOR = 64
_MINI_CUTOFF = 4096

_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_MAXREGSECT = 0xFFFFFFFA
_DIFSECT = 0xFFFFFFFC
_FATSECT = 0xFFFFFFFD
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_NOSTREAM = 0xFFFFFFFF

_TYPE_STORAGE, _TYPE_STREAM, _TYPE_ROOT = 1, 2, 5
_BLACK = 1

# The spec caps a directory entry name at 32 UTF-16 code units including the
# terminator. Altium truncates longer footprint names rather than failing, but a
# silently truncated name is a library whose parts cannot be found by the name the
# netlist uses, so Ohm refuses instead.
MAX_NAME = 31


class CfbError(Exception):
    """A name or size the compound-file container cannot represent."""


def _order(name: str) -> tuple[int, str]:
    """Sibling collation: length first, then case-folded — [MS-CFB] 2.6.4."""
    return (len(name), name.upper())


class _Node:
    __slots__ = ("name", "data", "children", "did", "left", "right", "child", "start", "size")

    def __init__(self, name: str, data: bytes | None) -> None:
        self.name = name
        self.data = data  # None => storage
        self.children: dict[str, _Node] = {}
        self.did = _NOSTREAM
        self.left = self.right = self.child = _NOSTREAM
        self.start = _ENDOFCHAIN
        self.size = 0


def _balanced(nodes: list[_Node]) -> int:
    """Link a sorted sibling list into a balanced BST; return the root's id.

    A degenerate chain would still be a legal search tree, but a balanced one is
    what every writer produces and keeps depth logarithmic for readers that recurse.
    """
    if not nodes:
        return _NOSTREAM
    mid = len(nodes) // 2
    node = nodes[mid]
    node.left = _balanced(nodes[:mid])
    node.right = _balanced(nodes[mid + 1:])
    return node.did


def _build_tree(entries: Mapping[str, bytes]) -> _Node:
    root = _Node("Root Entry", None)
    for path, data in entries.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.children.setdefault(part, _Node(part, None))
            if node.data is not None:
                raise CfbError(f"{part!r} is both a stream and a storage in {path!r}")
        leaf = parts[-1]
        if leaf in node.children:
            raise CfbError(f"duplicate entry {path!r}")
        node.children[leaf] = _Node(leaf, data)
    for node in _walk(root):
        if node is not root and len(node.name) > MAX_NAME:
            raise CfbError(f"name {node.name!r} exceeds {MAX_NAME} characters")
    return root


def _walk(node: _Node):
    yield node
    for child in node.children.values():
        yield from _walk(child)


def _assign_ids(root: _Node) -> list[_Node]:
    """Breadth-first id assignment, siblings in collation order (deterministic)."""
    root.did = 0
    ordered = [root]
    queue = [root]
    while queue:
        node = queue.pop(0)
        kids = sorted(node.children.values(), key=lambda n: _order(n.name))
        for kid in kids:
            kid.did = len(ordered)
            ordered.append(kid)
            queue.append(kid)
        node.child = _balanced(kids)
    return ordered


class _Allocator:
    """Appends fixed-size sectors and chains them in the FAT."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.blocks: list[bytes] = []
        self.chain: list[int] = []

    def alloc(self, data: bytes) -> int:
        if not data:
            return _ENDOFCHAIN
        first = len(self.blocks)
        for offset in range(0, len(data), self.size):
            chunk = data[offset:offset + self.size]
            self.blocks.append(chunk.ljust(self.size, b"\x00"))
            self.chain.append(len(self.blocks))  # provisional: next sector
        self.chain[-1] = _ENDOFCHAIN
        return first

    def raw(self) -> bytes:
        return b"".join(self.blocks)


def _dir_entry(node: _Node, kind: int) -> bytes:
    name = node.name.encode("utf-16-le") + b"\x00\x00"
    return (
        name.ljust(64, b"\x00")
        + struct.pack("<HBB", len(name), kind, _BLACK)
        + struct.pack("<III", node.left, node.right, node.child)
        + b"\x00" * 16  # CLSID
        + b"\x00" * 4  # state bits
        + b"\x00" * 16  # creation + modification time: zero keeps output reproducible
        + struct.pack("<IQ", node.start, node.size)
    )


def write_cfb(entries: Mapping[str, bytes]) -> bytes:
    """Pack `{"Storage/Stream": data}` into a compound file.

    Intermediate storages are created from the path; an empty storage is requested
    by mapping its path to an empty stream is NOT supported — pass the streams you
    want and the storages follow.
    """
    root = _build_tree(entries)
    ordered = _assign_ids(root)

    main = _Allocator(_SECTOR)
    mini = _Allocator(_MINI_SECTOR)

    # Large streams go straight into the FAT; small ones into the mini stream. The
    # cutoff is not a preference — a reader picks where to look purely from the size.
    for node in ordered:
        if node.data is None:
            node.start = 0  # a storage owns no sectors ([MS-CFB] 2.6.3)
            continue
        node.size = len(node.data)
        if node.size >= _MINI_CUTOFF:
            node.start = main.alloc(node.data)
        else:
            node.start = mini.alloc(node.data)

    root.start = main.alloc(mini.raw())
    root.size = len(mini.raw())

    minifat = b"".join(struct.pack("<I", v) for v in mini.chain)
    minifat_start = main.alloc(minifat)
    minifat_count = len(minifat) // _SECTOR + (1 if len(minifat) % _SECTOR else 0)

    directory = b"".join(
        _dir_entry(n, _TYPE_ROOT if n is root else _TYPE_STORAGE if n.data is None
                   else _TYPE_STREAM)
        for n in ordered
    )
    # Unused entries in the last directory sector are all-zero, which is object
    # type 0 (unallocated) — a reader must not walk into them.
    directory += b"\x00" * ((-len(directory)) % _SECTOR)
    dir_start = main.alloc(directory)
    dir_count = len(directory) // _SECTOR

    # The FAT describes itself, so its own sector count is a fixed point.
    data_sectors = len(main.blocks)
    fat_count = 1
    while (data_sectors + fat_count) * 4 > fat_count * _SECTOR:
        fat_count += 1
    if fat_count > 109:
        raise CfbError("library too large for a header-only DIFAT")

    fat = list(main.chain) + [_FATSECT] * fat_count
    fat += [_FREESECT] * (fat_count * (_SECTOR // 4) - len(fat))
    assert all(v <= _MAXREGSECT or v >= _DIFSECT for v in fat)

    difat = [data_sectors + i for i in range(fat_count)]
    difat += [_FREESECT] * (109 - fat_count)

    header = (
        _SIGNATURE
        + b"\x00" * 16  # CLSID
        + struct.pack("<HHH", 0x003E, 3, 0xFFFE)  # minor, major, little-endian
        + struct.pack("<HH", 9, 6)  # sector shift (512), mini sector shift (64)
        + b"\x00" * 6  # reserved
        + struct.pack("<I", 0)  # directory sector count: 0 for version 3
        + struct.pack("<I", fat_count)
        + struct.pack("<I", dir_start)
        + struct.pack("<I", 0)  # transaction signature
        + struct.pack("<I", _MINI_CUTOFF)
        + struct.pack("<I", minifat_start)
        + struct.pack("<I", minifat_count)
        + struct.pack("<II", _ENDOFCHAIN, 0)  # no DIFAT sectors
        + b"".join(struct.pack("<I", v) for v in difat)
    )
    assert len(header) == _SECTOR, len(header)
    assert dir_count >= 1
    return header + main.raw() + b"".join(struct.pack("<I", v) for v in fat)
