"""生成 examples/ 示例数据:立创商城订单样例(xlsx)与 C编号清单。

用法:python scripts/make_examples.py
"""

import csv
import sys
from pathlib import Path

from openpyxl import Workbook

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

SAMPLE_ITEMS = [
    # 序号, 订单号, 商品编号, 商品名称, 数量, 单价
    ("1", "1122334455", "C25704", "100nF 50V ±10% X7R 0603 多层陶瓷电容器", 50, 0.0085),
    ("2", "1122334455", "C81588", "1uF 25V ±10% X7R 0603 多层陶瓷电容器", 40, 0.0132),
    ("3", "1122334455", "C25804", "10kΩ ±1% 1/10W 0603 厚膜电阻", 60, 0.0049),
    ("4", "1122334455", "C31250", "AMS1117-3.3 线性稳压器 SOT-223", 5, 0.45),
    ("5", "1122334455", "C8304",   "0603 贴片红色 LED 指示灯", 30, 0.015),
    ("6", "1122334456", "C7763",   "STM32F103C8T6 LQFP-48 微控制器", 2, 7.8),
    ("7", "1122334456", "C25704", "100nF 50V ±10% X7R 0603 多层陶瓷电容器", 20, 0.0085),
    ("8", "1122334456", "C466247", "SS8050 NPN 三极管 SOT-23", 30, 0.031),
    ("9", "1122334456", "C69872",  "2.54mm 1x4P 排针连接器 直插", 10, 0.06),
]


def make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "订单明细"
    ws.append(["序号", "订单号", "商品编号", "商品名称", "数量", "单价(元)"])
    for row in SAMPLE_ITEMS:
        ws.append(list(row))
    ws.column_dimensions["D"].width = 46
    wb.save(path)


def make_csv(path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["商品编号", "数量"])
        for _, _, code, _, qty, _ in SAMPLE_ITEMS:
            w.writerow([code, qty])


def make_codes_txt(path: Path) -> None:
    codes = sorted({code for _, _, code, _, _, _ in SAMPLE_ITEMS})
    path.write_text("\n".join(codes) + "\n", encoding="utf-8")


if __name__ == "__main__":
    EXAMPLES.mkdir(exist_ok=True)
    make_xlsx(EXAMPLES / "lcsc_order_sample.xlsx")
    make_csv(EXAMPLES / "lcsc_order_sample.csv")
    make_codes_txt(EXAMPLES / "lcsc_codes_sample.txt")
    print("示例已生成:", *[p.name for p in sorted(EXAMPLES.iterdir())], sep="\n  ")
    sys.exit(0)
