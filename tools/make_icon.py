"""Generate resources/app.ico with the standard library only.

A small 32x32 icon: dark rounded tile, white page, yellow marker.
Keeping this in source means the icon can be regenerated without Pillow.
"""

from __future__ import annotations

import struct
from pathlib import Path

SIZE = 32


def pixel(x: int, y: int) -> tuple[int, int, int, int]:
    """Return (R, G, B, A) for one icon pixel."""
    # Rounded dark tile.
    corners = ((6, 6), (SIZE - 7, 6), (6, SIZE - 7), (SIZE - 7, SIZE - 7))
    outside_corner = any(
        (x - cx) ** 2 + (y - cy) ** 2 > 36
        and (x < cx if cx < SIZE // 2 else x > cx)
        and (y < cy if cy < SIZE // 2 else y > cy)
        for cx, cy in corners
    )
    if outside_corner or x < 2 or y < 2 or x > SIZE - 3 or y > SIZE - 3:
        return (0, 0, 0, 0)

    if 8 <= x <= 23 and 5 <= y <= 27:
        # White page with a subtle border.
        if x in (8, 23) or y in (5, 27):
            return (150, 155, 165, 255)
        for line_y in (11, 15, 19):
            if line_y <= y <= line_y + 1 and 11 <= x <= 20:
                return (105, 110, 120, 255)
        if 12 <= y <= 24 and 11 <= x <= 13:
            return (255, 214, 51, 255)  # yellow marker
        return (245, 246, 250, 255)

    return (31, 31, 35, 255)


def build_ico() -> bytes:
    rows: list[bytes] = []
    for y in range(SIZE - 1, -1, -1):  # BMP rows are bottom-up
        row = bytearray()
        for x in range(SIZE):
            r, g, b, a = pixel(x, y)
            row += bytes((b, g, r, a))
        rows.append(bytes(row))

    header = struct.pack("<IiiHHIIiiII", 40, SIZE, SIZE * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = b"".join(rows)
    and_mask = b"\x00" * (SIZE // 8 * SIZE)
    image = header + xor + and_mask

    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack(
        "<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(image), 6 + 16
    )
    return ico + image


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "resources" / "app.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_ico())
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
