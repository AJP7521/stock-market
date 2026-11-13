"""Chart captures for decisions (Prompt 10A): check that a PNG is readable, and store it next to the database.

read_png() accepts a data:image/png;base64,... URL (or bare base64) and checks, with the standard library only:
base64 decoding, the PNG signature, every chunk's CRC, an IHDR first and an IEND last, the size limits, and that the
image data decompresses to exactly the bytes the header promises. Anything else raises ImageRejected, whose
`recapture` text tells the student what to do.

store_png() writes the bytes to <database folder>/captures/snapshot-<id>-<sha256 prefix>.png, so a disposable test
database keeps its captures beside it, and returns the path relative to the database folder.
"""

import base64
import binascii
import hashlib
import sqlite3
import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 8 * 1024 * 1024
MAX_SIDE = 4000
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}          # PNG colour type -> samples per pixel
RECAPTURE = "Recapture the chart with the Ask about chart button; the image must be a PNG exported from the chart."


class ImageRejected(Exception):
    def __init__(self, problem: str, recapture: str = RECAPTURE):
        super().__init__(problem)
        self.problem, self.recapture = problem, recapture


def decode_data_url(data: str) -> bytes:
    if not isinstance(data, str) or not data:
        raise ImageRejected("no image was sent")
    if data.startswith("data:"):
        header, _, payload = data.partition(",")
        if header != "data:image/png;base64":
            raise ImageRejected(f"expected a data:image/png;base64 URL, got {header[:40]!r}")
    else:
        payload = data
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageRejected(f"the image is not valid base64 ({exc})") from None
    if len(raw) > MAX_BYTES:
        raise ImageRejected(f"the image is {len(raw)} bytes; the limit is {MAX_BYTES}")
    return raw


def check_png(raw: bytes) -> dict:
    """Return {"width", "height", "bytes", "sha256"} for a readable PNG, else raise ImageRejected."""
    if not raw.startswith(PNG_SIGNATURE):
        raise ImageRejected("the file is not a PNG (wrong signature)")
    pos, chunks, idat = len(PNG_SIGNATURE), [], []
    while pos < len(raw):
        if pos + 8 > len(raw):
            raise ImageRejected("the PNG is truncated (incomplete chunk header)")
        length, ctype = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 8 + length + 4
        if end > len(raw):
            raise ImageRejected(f"the PNG is truncated (chunk {ctype!r} runs past the end)")
        data = raw[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", raw[end - 4:end])
        if zlib.crc32(ctype + data) != crc:
            raise ImageRejected(f"the PNG is damaged (bad checksum in chunk {ctype!r})")
        chunks.append(ctype)
        if ctype == b"IHDR":
            ihdr = data
        elif ctype == b"IDAT":
            idat.append(data)
        pos = end
        if ctype == b"IEND":
            break
    if not chunks or chunks[0] != b"IHDR" or len(ihdr) != 13:
        raise ImageRejected("the PNG has no valid IHDR header")
    if chunks[-1] != b"IEND":
        raise ImageRejected("the PNG is truncated (no IEND chunk)")
    width, height, depth, colour, _comp, _filter, interlace = struct.unpack(">IIBBBBB", ihdr)
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise ImageRejected(f"the PNG is {width}×{height}; each side must be 1–{MAX_SIDE} pixels")
    if colour not in CHANNELS or not idat:
        raise ImageRejected("the PNG has an unsupported colour type or no image data")
    try:
        pixels = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise ImageRejected(f"the PNG image data does not decompress ({exc})") from None
    if interlace == 0:
        row = (width * CHANNELS[colour] * depth + 7) // 8 + 1
        if len(pixels) != row * height:
            raise ImageRejected(f"the PNG image data has {len(pixels)} bytes; {row * height} were expected")
    return {"width": width, "height": height, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def read_png(data: str) -> tuple[bytes, dict]:
    raw = decode_data_url(data)
    return raw, check_png(raw)


def data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def database_folder(conn: sqlite3.Connection) -> Path:
    path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    return Path(path).resolve().parent


def store_png(conn: sqlite3.Connection, snapshot_id: int, raw: bytes, sha256: str) -> str:
    """Write the capture beside the database (once per content) and return its path relative to that folder."""
    rel = f"captures/snapshot-{snapshot_id}-{sha256[:16]}.png"
    path = database_folder(conn) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    return rel


def stored_path(conn: sqlite3.Connection, rel: str) -> Path:
    return database_folder(conn) / rel
