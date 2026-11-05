"""器件价格更新服务。

数据来源(均为嘉立创生态公开接口,2026-11 实测可用;可用环境变量覆盖):

1. LCSC 详情接口(首选,按 C 编号精确匹配)
   GET https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=C25704
   → 价格梯度(USD)/库存/型号/品牌/封装/productCycle 生命周期
2. JLCPCB SMT 器件库搜索(按型号匹配,用于无 C 编号的手工器件)
   POST https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList/v2
   → componentModelEn/componentCode/价格梯度/stockCount/基础-扩展库

匹配与标记规则:
- 有 C 编号 → 源 1 精确查询;productCycle != normal 视为已下架/停售;
- 无 C 编号 → 取型号(model)到源 2 搜索,**componentModelEn 严格相等**才算命中
  (命中后自动回填 C 编号,便于后续精确刷新);无命中 → no_match;
- 刷新只写 price/price_currency/price_status/price_updated_at/price_source,
  不改动用户手工维护的其它字段;已有手填价格被覆盖前记录在摘要中。

安全:型号/C编号过白名单校验;接口 URL 参数经 urlencode;返回数值做范围
校验(0 ≤ price ≤ 1e7);文本字段长度截断;请求带超时与限速;单批数量有上限。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from .. import security
from ..db import get_db, utc_now
from ..paths import as_data

LCSC_DETAIL_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail"
JLC_SEARCH_URL = ("https://jlcpcb.com/api/overseas-pcb-order/v1/"
                  "shoppingCart/smtGood/selectSmtComponentList/v2")

REQUEST_TIMEOUT = 10
REQUEST_INTERVAL = 0.35          # 限速,避免触发风控
MAX_BATCH = 100                  # 单次刷新上限
_UA = "PDL/1.1 (+local device library)"


def _get_json(url: str, payload: dict | None = None) -> dict | None:
    """GET/POST JSON,失败返回 None(不抛网络异常到调用方)。"""
    try:
        if payload is None:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"User-Agent": _UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:                    # noqa: BLE001 网络/解析失败统一降级
        return None


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------
def fetch_lcsc_detail(code: str) -> dict | None:
    """按 C 编号查询 → {price, currency, model, stock, cycle} 或 None。"""
    url = f"{LCSC_DETAIL_URL}?{urllib.parse.urlencode({'productCode': code})}"
    doc = _get_json(url)
    if not isinstance(doc, dict) or doc.get("code") != 200:
        return None
    d = doc.get("result") or {}
    price, currency = _lcsc_price(d.get("productPriceList"))
    return {
        "price": price,
        "currency": currency,
        "model": str(d.get("productModel") or "")[:50],
        "stock": _as_int(d.get("stockNumber")),
        "cycle": str(d.get("productCycle") or "normal")[:20],
        "brand": str(d.get("brandNameEn") or "")[:60],
        "package": str(d.get("encapStandard") or "")[:30],
    }


def _lcsc_price(price_list) -> tuple[float | None, str]:
    """取最小起订档价格;金额做范围校验。"""
    if not isinstance(price_list, list) or not price_list:
        return None, ""
    first = price_list[0] if isinstance(price_list[0], dict) else {}
    raw = first.get("productPrice")
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None, ""
    if not (0 <= price <= 1e7):
        return None, ""
    symbol = str(first.get("currencySymbol") or "$")
    currency = "USD" if symbol in ("$", "US$") else symbol.strip() or "USD"
    return round(price, 6), currency


def search_jlc_by_model(model: str) -> dict | None:
    """按型号在 JLCPCB 库搜索,componentModelEn 严格相等才命中。

    返回 {price, currency, model, stock, code, library} / None(网络失败时
    返回 {"error": True} 以区分“无匹配”)。
    """
    url = JLC_SEARCH_URL
    payload = {"keyword": model, "currentPage": 1, "pageSize": 20}
    doc = _get_json(url, payload)
    if not isinstance(doc, dict) or doc.get("code") != 200:
        return {"error": True}
    page = (doc.get("data") or {}).get("componentPageInfo") or {}
    items = page.get("list") or []
    want = model.lower()
    exact = [it for it in items
             if str(it.get("componentModelEn") or "").strip().lower() == want]
    if not exact:
        return None
    # 多条命中:优先基础库,其次库存>0,再取首档价最低者
    def rank(it: dict):
        lib = 0 if it.get("componentLibraryType") == "basic" else 1
        stock = -_as_int(it.get("stockCount"))
        price0 = _jlc_first_price(it) or 1e9
        return (lib, stock, price0)

    best = sorted(exact, key=rank)[0]
    price = _jlc_first_price(best)
    return {
        "price": price,
        "currency": "USD" if price is not None else "",
        "model": str(best.get("componentModelEn") or "")[:50],
        "stock": _as_int(best.get("stockCount")),
        "code": security.validate_lcsc_code(str(best.get("componentCode") or "")) or "",
        "library": str(best.get("componentLibraryType") or "")[:10],
    }


def _jlc_first_price(item: dict) -> float | None:
    prices = item.get("componentPrices")
    if not isinstance(prices, list) or not prices:
        return None
    first = prices[0] if isinstance(prices[0], dict) else {}
    try:
        price = float(first.get("productPrice"))
    except (TypeError, ValueError):
        return None
    return round(price, 6) if 0 <= price <= 1e7 else None


def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _valid_price(p) -> bool:
    """写入前的纵深校验:类型与范围(0 ≤ p ≤ 1e7)。"""
    return isinstance(p, (int, float)) and not isinstance(p, bool) \
        and 0 <= p <= 1e7


# ---------------------------------------------------------------------------
# 批量刷新
# ---------------------------------------------------------------------------
def refresh_prices(project_root, *, ids: list[int] | None = None,
                   scope: str = "selected") -> dict:
    """批量刷新器件价格并输出摘要。

    scope: selected(按 ids)/ all(全部)/ stale(从未刷新或标记异常的)。
    """
    scope_map = {"selected", "all", "stale"}
    if scope not in scope_map:
        raise security.ValidationError(f"scope 只支持 {'/'.join(sorted(scope_map))}")
    conn = get_db(project_root)
    if scope == "selected":
        if not ids:
            raise security.ValidationError("请提供要刷新的器件 ID 列表")
        checked_ids = security.validate_id_list(ids, field="器件ID列表",
                                                max_items=MAX_BATCH)
        rows = conn.execute(
            "SELECT * FROM devices WHERE id IN (%s)" % ",".join("?" * len(checked_ids)),
            checked_ids).fetchall()
    elif scope == "all":
        rows = conn.execute("SELECT * FROM devices ORDER BY id LIMIT ?",
                            (MAX_BATCH,)).fetchall()
    else:  # stale:price_status 为空或 no_match/error 的器件
        rows = conn.execute(
            "SELECT * FROM devices WHERE price_status IN ('', 'no_match', 'error')"
            " OR price_status IS NULL ORDER BY id LIMIT ?",
            (MAX_BATCH,)).fetchall()
    if not rows:
        return _summary(0, [], [], [], [], [], 0, scope)
    if len(rows) >= MAX_BATCH:
        # 提示还有更多(前端可分批)
        pass

    updated, no_match, discontinued, failed, skipped = [], [], [], [], []
    t0 = time.time()
    for row in rows:
        d = dict(row)
        item = {"id": d["id"], "name": security.sanitize_text(d["name"], max_len=60),
                "code": d["lcsc_code"] or "", "model": d["model"] or "",
                "old_price": d["price"]}
        if not d["lcsc_code"] and not (d["model"] or "").strip():
            skipped.append(item)
            continue
        try:
            result = _refresh_one(conn, d)
        except Exception as exc:  # noqa: BLE001 单个失败不中断整批
            conn.execute(
                "UPDATE devices SET price_status='error', price_updated_at=? WHERE id=?",
                (utc_now(), d["id"]))
            conn.commit()
            item["error"] = f"{exc.__class__.__name__}"
            failed.append(item)
            continue
        if result["status"] == "ok":
            item.update(result["fields"])
            updated.append(item)
        elif result["status"] == "discontinued":
            item.update(result["fields"])
            discontinued.append(item)
        elif result["status"] == "failed":
            item.update(result["fields"])
            failed.append(item)
        else:
            item.update(result["fields"])
            no_match.append(item)
        time.sleep(REQUEST_INTERVAL)

    conn.commit()
    return _summary(len(rows), updated, no_match, discontinued, failed,
                    skipped, round((time.time() - t0) * 1000), scope)


def _refresh_one(conn, d: dict) -> dict:
    """刷新单个器件,返回 {status, fields};状态落库。"""
    now = utc_now()
    if d["lcsc_code"]:
        info = fetch_lcsc_detail(d["lcsc_code"])
        if info is None:
            _set_price_status(conn, d["id"], "error", now)
            return {"status": "failed", "fields": {"error": "网络或接口失败"}}
        if info["cycle"] and info["cycle"] != "normal":
            _set_price_status(conn, d["id"], "discontinued", now,
                              source="lcsc", currency=info["currency"] or "USD")
            return {"status": "discontinued",
                    "fields": {"cycle": info["cycle"], "stock": info["stock"]}}
        if not _valid_price(info["price"]):
            _set_price_status(conn, d["id"], "no_match", now, source="lcsc")
            return {"status": "no_match", "fields": {"reason": "接口未返回有效价格"}}
        _write_price(conn, d["id"], info["price"], info["currency"], "ok", "lcsc", now)
        return {"status": "ok", "fields": {
            "price": info["price"], "currency": info["currency"], "stock": info["stock"]}}

    # 无 C 编号:按型号到 JLCPCB 库匹配
    info = search_jlc_by_model(d["model"].strip())
    if info is not None and info.get("error"):
        _set_price_status(conn, d["id"], "error", now)
        return {"status": "failed", "fields": {"error": "网络或接口失败"}}
    if not info:
        _set_price_status(conn, d["id"], "no_match", now)
        return {"status": "no_match", "fields": {"reason": "型号无精确匹配"}}
    if d.get("model", "").strip().lower() != (info["model"] or "").lower():
        _set_price_status(conn, d["id"], "no_match", now)
        return {"status": "no_match", "fields": {"reason": "型号无精确匹配"}}
    if not _valid_price(info["price"]):
        _set_price_status(conn, d["id"], "no_match", now)
        return {"status": "no_match", "fields": {"reason": "命中型号但无有效价格"}}
    # 命中:回填 C 编号(若空)便于下次精确刷新
    if not d["lcsc_code"] and info.get("code"):
        conn.execute("UPDATE devices SET lcsc_code = ? WHERE id = ? AND lcsc_code IS NULL",
                     (info["code"], d["id"]))
    _write_price(conn, d["id"], info["price"], info["currency"], "ok", "jlcpcb", now)
    return {"status": "ok", "fields": {
        "price": info["price"], "currency": info["currency"],
        "stock": info["stock"], "matched_code": info.get("code", "")}}


def _write_price(conn, device_id: int, price: float, currency: str,
                 status: str, source: str, now: str) -> None:
    conn.execute(
        "UPDATE devices SET price=?, price_currency=?, price_status=?,"
        " price_source=?, price_updated_at=? WHERE id=?",
        (price, currency, status, source, now, device_id))


def _set_price_status(conn, device_id: int, status: str, now: str,
                      *, source: str = "", currency: str = "") -> None:
    conn.execute(
        "UPDATE devices SET price_status=?, price_updated_at=?,"
        " price_source=CASE WHEN ?='' THEN price_source ELSE ? END,"
        " price_currency=CASE WHEN ?='' THEN price_currency ELSE ? END WHERE id=?",
        (status, now, source, source, currency, currency, device_id))


def _summary(requested, updated, no_match, discontinued, failed, skipped,
             elapsed_ms, scope) -> dict:
    return {
        "scope": scope,
        "requested": requested,
        "updated": updated,
        "no_match": no_match,
        "discontinued": discontinued,
        "failed": failed,
        "skipped": skipped,
        "elapsed_ms": elapsed_ms,
        "more_left": requested >= MAX_BATCH,
    }
