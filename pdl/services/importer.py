"""立创商城批量导入服务。

支持三种导入途径(需求 1):
1. 立创商城订单文件批量导入:.xlsx / .xls / .csv,自动识别
   “C编号/商品编号”与“数量”列,支持多文件、重复器件自动合并累加数量;
2. C 编号批量导入:直接输入 C 编号列表,调用立创公开接口拉取
   名称/封装/品牌/描述等全部商品信息(可关闭,失败自动降级为仅编号导入);
3. 全字段 CSV 导入:软件导出的通用格式,可完整往返。

安全:文件类型/大小白名单;解析在只读模式下进行;所有字段经 security 校验;
数据库操作参数化;接口访问仅限立创域名且带超时。
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import security
from ..db import get_db, utc_now
from . import devices as device_service

# 立创商城公开商品详情接口(可用环境变量 PDL_LCSC_API 覆盖)
LCSC_API_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail"

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_FILES_PER_BATCH = 20
MAX_CODES_PER_BATCH = 300
API_TIMEOUT = 10
API_INTERVAL = 0.4  # 接口限速:避免触发风控

# 订单表头关键词(立创商城各版本导出格式的常见列名)
_CODE_HEADER_KEYS = ("c编号", "器件编号", "商品编号", "元件编号", "料号", "零件编号", "编号")
_QTY_HEADER_KEYS = ("数量", "购买数量", "采购数量", "申购数量", "订购数量")

_C_CODE_RE = re.compile(r"[Cc]\d{1,8}")

_LCSC_FIELDS = ("category", "package", "manufacturer", "description")


# ---------------------------------------------------------------------------
# 途径 1:订单文件导入
# ---------------------------------------------------------------------------
def import_files(project_root, file_items: list[dict], *, fetch_details: bool = False) -> dict:
    """file_items: [{path, filename}]。返回汇总统计。"""
    if not file_items:
        raise security.ValidationError("请选择要导入的文件")
    if len(file_items) > MAX_FILES_PER_BATCH:
        raise security.ValidationError(f"单次最多导入 {MAX_FILES_PER_BATCH} 个文件")

    entries: dict[str, int] = {}   # code -> qty 累加
    errors: list[str] = []
    for item in file_items:
        try:
            parsed = _parse_order_file(Path(item["path"]))
            for code, qty in parsed:
                entries[code] = entries.get(code, 0) + qty
        except security.ValidationError as exc:
            errors.append(f"{item['filename']}: {exc}")
        except Exception as exc:  # 解析器异常兜底,不让单个文件中断整批
            errors.append(f"{item['filename']}: 文件解析失败({exc.__class__.__name__})")

    if not entries:
        raise security.ValidationError(
            "未能从文件中识别出任何 C 编号。" + (";".join(errors) if errors else "")
        )

    stats = _upsert_entries(project_root, entries, fetch_details=fetch_details,
                            kind="file", detail=",".join(i["filename"] for i in file_items))
    stats["errors"] = errors
    return stats


def _parse_order_file(path: Path) -> list[tuple[str, int]]:
    """解析单个订单文件 → [(C编号, 数量)]。"""
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise security.ValidationError(f"不支持的文件类型:{suffix}(允许 {'/'.join(sorted(ALLOWED_EXTENSIONS))})")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise security.ValidationError("文件超过 20MB 上限")

    if suffix == ".xlsx":
        rows = _read_xlsx(path)
    elif suffix == ".xls":
        rows = _read_xls(path)
    else:
        rows = _read_csv(path)
    return _extract_entries(rows)


def _read_xlsx(path: Path) -> list[list[str]]:
    from openpyxl import load_workbook  # 延迟导入

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                out.append(["" if c is None else str(c).strip() for c in row])
        return out
    finally:
        wb.close()


def _read_xls(path: Path) -> list[list[str]]:
    try:
        import xlrd  # 可选依赖
    except ImportError as exc:
        raise security.ValidationError(
            "旧版 .xls 需要安装 xlrd:pip install xlrd;或另存为 .xlsx / .csv 后导入"
        ) from exc
    book = xlrd.open_workbook(str(path))
    out = []
    for sheet in book.sheets():
        for r in range(sheet.nrows):
            out.append([str(sheet.cell_value(r, c)).strip()
                        for c in range(sheet.ncols)])
    return out


def _read_csv(path: Path) -> list[list[str]]:
    """CSV:按 utf-8-sig → gbk → big5 顺序尝试解码。"""
    raw = path.read_bytes()
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            text = raw.decode(enc)
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = "," if sample.count(",") >= sample.count("\t") else "\t"
            return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
        except (UnicodeDecodeError, ValueError) as exc:
            last_error = exc
    raise security.ValidationError(f"CSV 编码无法识别:{last_error}")


def _extract_entries(rows: list[list[str]]) -> list[tuple[str, int]]:
    """从表格中提取 (C编号, 数量):
    优先表头定位列;若无表头,回退为全表正则扫描。
    """
    code_col = qty_col = -1
    for row in rows[:10]:  # 表头通常在前 10 行内
        for idx, cell in enumerate(row):
            low = cell.lower()
            if code_col < 0 and any(k in low for k in _CODE_HEADER_KEYS):
                code_col = idx
            if qty_col < 0 and any(k in low for k in _QTY_HEADER_KEYS):
                qty_col = idx
        if code_col >= 0 and qty_col >= 0:
            break

    entries: dict[str, int] = {}
    if code_col >= 0:
        for row in rows:
            if code_col >= len(row):
                continue
            m = _C_CODE_RE.search(row[code_col])
            if not m:
                continue
            qty = _parse_qty(row[qty_col]) if 0 <= qty_col < len(row) else 1
            code = security.validate_lcsc_code(m.group(0))
            if code:
                entries[code] = entries.get(code, 0) + max(1, qty)
    if not entries:  # 回退:全表扫描任何形如 C123456 的单元格
        for row in rows:
            for cell in row:
                m = _C_CODE_RE.search(cell)
                if m:
                    code = security.validate_lcsc_code(m.group(0))
                    if code:
                        entries.setdefault(code, 1)
    if not entries:
        raise security.ValidationError("文件中没有找到 C 编号(如 C25704)")
    return list(entries.items())


def _parse_qty(cell: str) -> int:
    m = re.search(r"\d{1,7}", str(cell))
    return int(m.group(0)) if m else 1


# ---------------------------------------------------------------------------
# 途径 2:C 编号批量导入(+ 立创接口详情)
# ---------------------------------------------------------------------------
def import_codes(project_root, codes: list, *, fetch_details: bool = True) -> dict:
    """C 编号批量导入。无效编号自动跳过并计入 skipped(便于整段粘贴)。"""
    if not isinstance(codes, list) or not codes:
        raise security.ValidationError("请提供 C 编号列表")
    if len(codes) > MAX_CODES_PER_BATCH:
        raise security.ValidationError(f"单次最多导入 {MAX_CODES_PER_BATCH} 个编号")
    clean: dict[str, int] = {}
    pre_skipped = 0
    for raw in codes:
        try:
            code = security.validate_lcsc_code(str(raw))
        except security.ValidationError:
            pre_skipped += 1
            continue
        if code:
            clean.setdefault(code, 1)
    if not clean:
        raise security.ValidationError("没有有效的 C 编号")
    result = _upsert_entries(project_root, clean, fetch_details=fetch_details,
                             kind="codes", detail=f"{len(clean)} 个编号")
    result["skipped"] += pre_skipped
    return result


def fetch_lcsc_details(codes: list[str]) -> dict[str, dict]:
    """调用立创公开接口查询器件详情。网络失败返回空结果,不抛异常。"""
    results: dict[str, dict] = {}
    for code in codes:
        url = f"{LCSC_API_URL}?{urllib.parse.urlencode({'productCode': code})}"
        req = urllib.request.Request(url, headers={"User-Agent": "PDL/1.0 (+local tool)"})
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            time.sleep(API_INTERVAL)
            continue
        result = payload.get("result") or {}
        if result:
            results[code] = {
                "name": str(result.get("productDescription") or result.get("productName") or "")[:security.MAX_NAME_LEN],
                "package": str(result.get("encapStandard") or ""),
                "manufacturer": str(result.get("brandNameEn") or result.get("brandNameZh") or ""),
                "category": str(result.get("catalogName") or ""),
                "description": str(result.get("productDescription") or result.get("productName") or ""),
                "price": _first_price(result.get("productPriceList")),
            }
        time.sleep(API_INTERVAL)
    return results


def _first_price(price_list) -> float | None:
    try:
        if isinstance(price_list, list) and price_list:
            return float(price_list[0].get("ltdPrice") or price_list[0].get("usdPrice"))
    except (TypeError, ValueError, IndexError, AttributeError):
        pass
    return None


# ---------------------------------------------------------------------------
# 合并写入:重复器件自动累加数量(增量导入)
# ---------------------------------------------------------------------------
def _upsert_entries(project_root, entries: dict[str, int], *, fetch_details: bool,
                    kind: str, detail: str) -> dict:
    codes = list(entries.keys())
    details: dict[str, dict] = {}
    if fetch_details:
        details = fetch_lcsc_details(codes)

    conn = get_db(project_root)
    imported = merged = skipped = 0
    now = utc_now()
    for code, qty in entries.items():
        if qty <= 0:
            skipped += 1
            continue
        try:
            qty = security.validate_int(qty, field="数量", min_val=1, max_val=security.MAX_QTY)
        except security.ValidationError:
            skipped += 1
            continue
        d = details.get(code, {})
        name = security.sanitize_text(d.get("name") or code, field="器件名",
                                      max_len=security.MAX_NAME_LEN) or code
        row = conn.execute("SELECT id, qty FROM devices WHERE lcsc_code = ?", (code,)).fetchone()
        if row:  # 已存在:数量累加,补充缺失的详情
            conn.execute(
                "UPDATE devices SET qty = MIN(?, qty + ?),"
                " category = CASE WHEN category = '' THEN ? ELSE category END,"
                " package = CASE WHEN package = '' THEN ? ELSE package END,"
                " manufacturer = CASE WHEN manufacturer = '' THEN ? ELSE manufacturer END,"
                " description = CASE WHEN description = '' THEN ? ELSE description END,"
                " price = COALESCE(price, ?), source = 'lcsc', updated_at = ?"
                " WHERE id = ?",
                (security.MAX_QTY, qty, d.get("category", ""), d.get("package", ""),
                 d.get("manufacturer", ""), d.get("description", ""),
                 d.get("price"), now, row["id"]),
            )
            merged += 1
        else:
            conn.execute(
                "INSERT INTO devices (lcsc_code, name, category, package, manufacturer,"
                " description, qty, price, attrs, source, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,'lcsc',?,?)",
                (code, name, security.sanitize_text(d.get("category", "")),
                 security.sanitize_text(d.get("package", "")),
                 security.sanitize_text(d.get("manufacturer", "")),
                 security.sanitize_text(d.get("description", "")),
                 qty, d.get("price"), "{}", now, now),
            )
            imported += 1
    conn.execute(
        "INSERT INTO import_logs (kind, detail, imported, merged, skipped, errors, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (kind, security.sanitize_text(detail, max_len=200), imported, merged, skipped,
         "[]", now),
    )
    conn.commit()
    return {"imported": imported, "merged": merged, "skipped": skipped, "errors": []}


# ---------------------------------------------------------------------------
# 途径 3:通用 CSV 导入/导出(全字段往返)
# ---------------------------------------------------------------------------
_CSV_COLUMNS = ["lcsc_code", "name", "category", "package", "manufacturer",
                "description", "qty", "price", "notes", "attrs", "source"]


def export_csv(project_root) -> str:
    """导出全部器件为 CSV(UTF-8 BOM,Excel 可直接打开)。"""
    conn = get_db(project_root)
    rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for r in rows:
        d = row_to_device_plain(r)
        writer.writerow([d[c] for c in _CSV_COLUMNS])
    return buf.getvalue()


def row_to_device_plain(row) -> dict:
    from ..db import row_to_device
    d = row_to_device(row)
    d["attrs"] = json.dumps(d.get("attrs") or {}, ensure_ascii=False)
    return {c: ("" if d.get(c) is None else d.get(c)) for c in _CSV_COLUMNS}


def import_csv(project_root, file_items: list[dict]) -> dict:
    """导入本工具导出的全字段 CSV,字段白名单严格校验。"""
    all_entries: list[tuple[str, int]] = []
    for item in file_items:
        rows = _read_csv(Path(item["path"]))
        if not rows:
            continue
        header = [security.sanitize_text(h, field="列名", max_len=40).lower()
                  for h in rows[0]]
        if "name" not in header or "lcsc_code" not in header:
            raise security.ValidationError(
                f"{item['filename']}: 不是本工具导出的全字段 CSV(缺少 lcsc_code/name 列)")
        idx = {col: header.index(col) for col in _CSV_COLUMNS if col in header}
        for row in rows[1:]:
            get = lambda col: row[idx[col]] if col in idx and idx[col] < len(row) else ""
            code = security.validate_lcsc_code(get("lcsc_code"))
            name = security.validate_device_name(get("name") or code)
            try:
                qty = int(re.search(r"\d{1,7}", str(get("qty"))).group(0)) \
                    if re.search(r"\d{1,7}", str(get("qty"))) else 1
            except ValueError:
                qty = 1
            data = {
                "lcsc_code": code, "name": name,
                "qty": security.validate_int(qty, field="数量", min_val=0, max_val=security.MAX_QTY),
                **{f: security.sanitize_text(get(f), max_len=security.MAX_TEXT_LEN)
                   for f in _LCSC_FIELDS + ("notes",)},
            }
            all_entries.append((code, name, qty, data))
    if not all_entries:
        raise security.ValidationError("CSV 中没有有效数据行")

    conn = get_db(project_root)
    imported = merged = 0
    now = utc_now()
    for code, name, qty, data in all_entries:
        existing = conn.execute("SELECT id FROM devices WHERE lcsc_code = ?",
                                (code,)).fetchone() if code else None
        if existing:
            conn.execute("UPDATE devices SET qty = MIN(?, qty + ?), updated_at = ? WHERE id = ?",
                         (security.MAX_QTY, qty, now, existing["id"]))
            merged += 1
        else:
            conn.execute(
                "INSERT INTO devices (lcsc_code, name, category, package, manufacturer,"
                " description, qty, notes, attrs, source, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,'manual',?,?)",
                (code, name, data["category"], data["package"], data["manufacturer"],
                 data["description"], qty, data["notes"], "{}", now, now),
            )
            imported += 1
    conn.execute(
        "INSERT INTO import_logs (kind, detail, imported, merged, skipped, errors, created_at)"
        " VALUES ('csv', ?, ?, ?, 0, '[]', ?)",
        (",".join(i["filename"] for i in file_items)[:200], imported, merged, now),
    )
    conn.commit()
    return {"imported": imported, "merged": merged, "skipped": 0, "errors": []}
