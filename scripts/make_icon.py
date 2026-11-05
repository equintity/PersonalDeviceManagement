"""生成应用图标 assets/icon.ico —— 纯 Python 标准库,无 PIL 依赖。

图案:深色圆角底 + 3x3 格栅(器件盒意象),右下格高亮(取料意象)。
采用经典 BMP 帧 ICO(BITMAPINFOHEADER + 32bpp BGRA XOR + AND 掩码),
兼容性最好(Windows 全版本及各类工具的 ICO 解析器)。

用法:python scripts/make_icon.py
"""

import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"

BG = (15, 20, 26, 255)        # 深色底
GRID = (61, 166, 245, 255)    # 主题蓝格
LIT = (255, 215, 94, 255)     # 高亮黄格


def make_pixels(size: int) -> list[list[tuple[int, int, int, int]]]:
    """绘制 size×size RGBA 像素矩阵(顶行在前)。"""
    px = [[BG for _ in range(size)] for _ in range(size)]
    r = size * 0.18  # 圆角半径
    for y in range(size):
        for x in range(size):
            rx = min(x, size - 1 - x)
            ry = min(y, size - 1 - y)
            if rx < r and ry < r and (r - rx) ** 2 + (r - ry) ** 2 > r * r:
                px[y][x] = (0, 0, 0, 0)  # 圆角外透明
    pad = size * 0.14
    cell = (size - pad * 2) / 3.0
    gap = cell * 0.14
    for row in range(3):
        for col in range(3):
            x0 = int(pad + col * cell + gap)
            x1 = int(pad + (col + 1) * cell - gap)
            y0 = int(pad + row * cell + gap)
            y1 = int(pad + (row + 1) * cell - gap)
            color = LIT if (row == 2 and col == 2) else GRID
            for y in range(max(0, y0), min(size, y1)):
                for x in range(max(0, x0), min(size, x1)):
                    px[y][x] = color
    return px


def bmp_frame(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    """像素矩阵 → 经典 BMP 帧的 ICO 图像数据。"""
    size = len(px)
    # XOR(BGRA,自底向上)+ AND 掩码(1bpp,行按 4 字节对齐,全 0 = 用 alpha)
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            rr, gg, bb, aa = px[y][x]
            xor += struct.pack("BBBB", bb, gg, rr, aa)
    and_stride = ((size + 31) // 32) * 4
    and_mask = b"\x00" * (and_stride * size)
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
        len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 256]
    frames = [bmp_frame(make_pixels(s)) for s in sizes]
    # ICO 全部字段为小端:ICONDIR(reserved=0, type=1, count=n)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    offset = 6 + 16 * len(sizes)
    for s, frame in zip(sizes, frames):
        b = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(frame), offset)
        offset += len(frame)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + entries + b"".join(frames))
    print(f"图标已生成:{OUT}({OUT.stat().st_size} 字节,{len(sizes)} 个尺寸)")


if __name__ == "__main__":
    main()
