"""软件 ↔ 器件盒 联动服务(需求 2)。

- 通过 USB 串口与 ESP32 交换“换行分隔 JSON”协议(与固件模板一致)
- 内置“虚拟器件盒”模拟器(virtual://box),无硬件也能完整演示联动:
  分配映射 → 自动同步 → 模拟按键取料 → 软件库存实时扣减
- 收发双方都做严格校验:命令动作用白名单,收到的事件先
  清洗字段再入库,杜绝把硬件数据当指令执行
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from . import boxmap, devices

BAUD = 115200
MAX_LINE = 1024           # 单条消息最大字节数(T-M4-01:512→1024,容纳分帧 sync)
SYNC_CHUNK = 8            # T-M3-01:sync 每批最多 8 个映射,分帧送达
EVENT_BUFFER = 200

# 软件 → 硬件 命令白名单
OUT_COMMANDS = {"ping", "info", "light", "off", "sync", "qty", "take", "stock"}
# 硬件 → 软件 事件白名单
IN_EVENTS = {"hello", "pong", "info", "ready", "cursor", "take", "return",
             "qty", "lit", "button", "log", "stock"}


class BridgeError(RuntimeError):
    pass


def _now() -> float:
    return round(time.time(), 3)


class VirtualBox:
    """虚拟器件盒:按固件协议应答,用于无硬件联调与演示。"""

    def __init__(self, on_event):
        self._on_event = on_event
        # RLock:事件回调会同步回到 write_line,必须可重入,否则死锁
        self._lock = threading.RLock()
        self._cursor = 0
        self._lit = -1
        self._items: dict[int, dict] = {}
        self._base: dict[int, int] = {}        # T5:sync 基线(host 下发值)
        self._sync_buf: dict[int, dict] = {}   # 分帧 sync 的累积缓冲
        self._open = True
        self._pending_diff: list[dict] | None = None   # T5:待确认的库存差异
        self._stock_wait: dict | None = None           # T5:request_stock 聚合器
        self._heartbeat: threading.Thread | None = None  # T7:5s ping 看门狗
        self._last_pong: float = 0.0                     # T7:最近 pong 时刻
        self._last_error: str | None = None            # T7:断连原因
        self._last_seen: float | None = None           # T7:最近收到数据时刻
        self.last_ready_mapped: int | None = None      # T6:最近一次 ready.mapped
        self._virtual_box: VirtualBox | None = None    # 虚拟盒实例跨连接持久(等价物理设备)

    def write_line(self, line: str) -> None:
        try:
            cmd = json.loads(line)
        except ValueError:
            self._emit("log", {"msg": "虚拟盒:收到无法解析的消息"})
            return
        action = cmd.get("cmd")
        with self._lock:
            if action == "ping":
                self._emit("pong", {"t": _now()})
            elif action == "info":
                self._emit("hello", {"device": "VirtualBox", "cells": len(self._items) or None,
                                     "version": "1.0.0-sim"})
            elif action == "light":
                self._lit = cmd.get("cell", -1)
                self._emit("lit", {"cell": self._lit})
            elif action == "off":
                self._lit = -1
                self._emit("lit", {"cell": -1})
            elif action == "sync":
                # 与固件同语义:seq/total 分帧累积,末批统一生效并回 ready
                seq = int(cmd.get("seq", 1) or 1)
                total = int(cmd.get("total", 1) or 1)
                if seq <= 1:
                    self._sync_buf = {}
                for i in cmd.get("items", []):
                    if isinstance(i, dict) and "cell" in i:
                        self._sync_buf[int(i["cell"])] = {
                            "qty": int(i.get("qty", 0)),
                            "name": str(i.get("name", ""))[:60]}
                if seq >= total:
                    self._items = dict(self._sync_buf)
                    self._sync_buf = {}
                    self._base = {cell: int(v["qty"])
                                  for cell, v in self._items.items()}   # T5:基线=host 值
                    self._emit("ready", {"mapped": len(self._items),
                                         "msg": "虚拟盒:映射已同步"})
            elif action == "qty":
                cell = cmd.get("cell")
                if cell in self._items:
                    self._items[cell]["qty"] = cmd.get("qty", 0)
            elif action == "stock":
                # T5/QA-04:返回 q != b 的项,8 项/帧;无差异也回一帧空 items
                diffs = [{"cell": cell, "qty": v["qty"]}
                         for cell, v in sorted(self._items.items())
                         if v["qty"] != self._base.get(cell, v["qty"])]
                total = max(1, (len(diffs) + 7) // 8)
                for seq in range(1, total + 1):
                    chunk = diffs[(seq - 1) * 8: seq * 8]
                    self._emit("stock", {"seq": seq, "total": total,
                                         "complete": seq == total,
                                         "items": chunk})

            elif action == "take":
                self.simulate("take", cmd.get("cell", self._cursor))
            else:
                self._emit("log", {"msg": f"虚拟盒:未知命令 {action!r}"})

    def simulate(self, action: str, cell: int | None = None) -> None:
        """模拟物理按键(T5 离线语义):只改盒子自身状态,不向主机发事件。

        take/return 只改 q(不动 b)—— 这正是"离线取料"的模型:
        主机重连后经 stock 命令发现 q != b 差异,用户确认后才落账。
        """
        with self._lock:
            if action == "move":
                cells = sorted(self._items) or [0]
                if cells:
                    cur = cell if cell is not None else self._cursor
                    pos = cells.index(cur) if cur in cells else 0
                    self._cursor = cells[(pos + 1) % len(cells)]
            elif action == "take":
                target = cell if cell is not None else self._cursor
                item = self._items.get(target)
                if item is not None:
                    item["qty"] = max(0, item.get("qty", 0) - 1)
            elif action == "return":
                target = cell if cell is not None else self._cursor
                item = self._items.get(target)
                if item is not None:
                    item["qty"] = item.get("qty", 0) + 1
            else:
                self._emit("log", {"msg": f"虚拟盒:不支持模拟 {action!r}"})

    def _emit(self, evt: str, data: dict) -> None:
        self._on_event({"evt": evt, **data})

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open


class SerialBridge:
    """管理唯一一条硬件连接,并把硬件事件翻译成软件动作(库存联动)。"""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._lock = threading.RLock()
        self._conn = None            # serial.Serial 或 VirtualBox
        self._port: str | None = None
        self._virtual = False
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._events: deque = deque(maxlen=EVENT_BUFFER)
        self._event_id = 0
        self._last_status: dict = {}
        self._lit_cell = -1
        self._heartbeat: threading.Thread | None = None  # T7:5s ping 看门狗
        self._last_pong: float = 0.0                     # T7:最近 pong 时刻
        self._pending_diff: list[dict] | None = None   # T5:待确认的库存差异
        self._stock_wait: dict | None = None           # T5:request_stock 聚合器
        self._heartbeat: threading.Thread | None = None  # T7:5s ping 看门狗
        self._last_pong: float = 0.0                     # T7:最近 pong 时刻
        self._last_error: str | None = None            # T7:断连原因
        self._last_seen: float | None = None           # T7:最近收到数据时刻
        self.last_ready_mapped: int | None = None      # T6:最近一次 ready.mapped
        self._virtual_box: VirtualBox | None = None    # 虚拟盒实例跨连接持久(等价物理设备)

    # -- 事件缓冲 -----------------------------------------------------------
    def _push_event(self, direction: str, data: dict) -> int:
        with self._lock:
            self._event_id += 1
            self._events.append({"id": self._event_id, "ts": _now(),
                                 "dir": direction, "data": data})
            return self._event_id

    def events_since(self, since: int) -> list[dict]:
        since = int(since) if isinstance(since, int) else 0
        with self._lock:
            return [e for e in self._events if e["id"] > since]

    # -- 连接管理 -----------------------------------------------------------
    @staticmethod
    def list_ports() -> list[dict]:
        ports = [{"port": "virtual://box", "description": "虚拟器件盒(无需硬件,用于演示)"}]
        try:
            from serial.tools import list_ports as _lp
            for p in _lp.comports():
                ports.append({"port": p.device,
                              "description": (p.description or "")[:80]})
        except ImportError:
            ports.append({"port": "", "description":
                          "未安装 pyserial,仅虚拟模式可用(pip install pyserial)"})
        return ports

    def connect(self, port: str, baud: int = BAUD) -> dict:
        with self._lock:
            if self._conn is not None:
                raise BridgeError("已有器件盒连接,请先断开")
            if not isinstance(port, str) or not port.strip():
                raise BridgeError("串口名不能为空")
            port = port.strip()
            if len(port) > 64 or "\n" in port or "\r" in port:
                raise BridgeError("串口名非法")
            baud = int(baud) if isinstance(baud, int) else BAUD
            if baud not in (9600, 57600, 115200, 230400, 460800, 921600):
                raise BridgeError(f"不支持的波特率:{baud}")

            if port == "virtual://box":
                # 虚拟盒实例跨连接复用:断开重连不丢"离线取料"(等价真机 NVS)
                if self._virtual_box is None:
                    self._virtual_box = VirtualBox(self._on_raw_event)
                self._conn = self._virtual_box
                self._virtual = True
            else:
                try:
                    import serial  # pyserial
                except ImportError as exc:
                    raise BridgeError("未安装 pyserial:pip install pyserial") from exc
                try:
                    self._conn = serial.Serial(port, baud, timeout=1)
                except Exception as exc:
                    raise BridgeError(f"打开串口失败:{exc}") from None
                self._virtual = False

            self._port = port
            self._stop.clear()
            with self._lock:
                self._last_error = None
                self._last_seen = time.time()
                self._last_pong = time.time()
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="pdl-serial-reader")
            self._reader.start()
            self._heartbeat = threading.Thread(target=self._heartbeat_loop,
                                               daemon=True, name="pdl-heartbeat")
            self._heartbeat.start()   # T7:5s ping 看门狗
            self._push_event("sys", {"msg": f"已连接 {port}"})
            self.send_command("info")            # 可不等回执
            # T5/QA-04:先读盒子差异,绝不先 push_sync(否则盒子真相被覆盖)
            stock = self.request_stock()
            if stock is not None:
                box = boxmap.get_box(self.project_root)
                db_by_cell = {m["cell"]: m["device"]
                              for m in box["mappings"] if m["device"]}
                pending = []
                for cell_s, box_qty in stock.items():
                    dev = db_by_cell.get(int(cell_s))
                    if not dev:
                        continue                 # 未映射格的差异忽略
                    if box_qty != dev["qty"]:
                        pending.append({
                            "cell": int(cell_s), "device_id": dev["id"],
                            "code": dev["lcsc_code"] or "", "name": dev["name"],
                            "box_qty": box_qty, "db_qty": dev["qty"],
                            "delta": box_qty - dev["qty"]})
                if pending:
                    with self._lock:
                        self._pending_diff = pending
                    self._push_event("sys", {
                        "msg": f"检测到 {len(pending)} 处库存差异,请在界面确认"})
                else:
                    try:
                        self.push_sync()         # 无差异:正常刷新基线
                    except Exception as exc:  # noqa: BLE001
                        self._push_event("sys", {"msg": f"已连接(映射同步待重试):{exc}"})
            else:
                # 读不到盒子状态(超时):保守不覆盖,提示手动同步
                self._push_event("sys", {"msg": "未获取到盒子库存状态,映射未自动同步,可手动同步"})
            return self.status()

    def _heartbeat_loop(self) -> None:
        """T7:每 5s ping,连续 15s 无 pong 判定断开(走统一清理路径)。"""
        while not self._stop.is_set():
            if self._stop.wait(5.0):
                return
            try:
                if not self.send_command("ping"):
                    self._teardown("心跳发送失败")
                    return
            except Exception as exc:  # 留痕而非静默
                self._teardown(f"心跳发送异常:{exc}")
                return
            with self._lock:
                last = self._last_pong
            if time.time() - last > 15.0:
                self._teardown("心跳超时(15s 无 pong)")
                return

    def _teardown(self, reason: str) -> None:
        """T7:异常断连的统一清理 —— 状态真实化并广播原因。"""
        with self._lock:
            if self._conn is None:
                return
            self._conn = None
            self._virtual = False
            self._lit_cell = -1
            self._last_error = reason
        self._push_event("sys", {"msg": f"连接已断开:{reason}"})

    def disconnect(self) -> dict:
        with self._lock:
            conn, self._conn = self._conn, None
            self._port = None
            self._lit_cell = -1
            self._last_error = "已主动断开" if conn is not None else self._last_error
            self._stop.set()
            if conn is not None:
                try:
                    if isinstance(conn, VirtualBox):
                        pass                    # 虚拟盒不断电,状态保留
                    else:
                        conn.close()
                except Exception:
                    pass
            if conn is not None:
                self._push_event("sys", {"msg": "已断开器件盒"})
        if self._heartbeat and self._heartbeat.is_alive():
            self._heartbeat.join(timeout=3)     # T7:心跳线程随连接生命周期
        return self.status()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def status(self) -> dict:
        with self._lock:
            return {"connected": self.connected, "port": self._port,
                    "virtual": self._virtual, "lit_cell": self._lit_cell,
                    "pending_diff": len(self._pending_diff or []),
                    "last_error": self._last_error,
                    "last_seen_at": self._last_seen,
                    "last_status": self._last_status}

    # -- 发送 ---------------------------------------------------------------
    def send_command(self, action: str, **payload) -> bool:
        """构造白名单命令并发送。返回是否成功。"""
        with self._lock:
            if self._conn is None:
                return False
            if action not in OUT_COMMANDS:
                raise BridgeError(f"禁止的命令:{action!r}")
            doc = {"cmd": action, **payload}
            try:
                line = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise BridgeError(f"命令序列化失败:{exc}") from None
            if len(line.encode("utf-8")) > MAX_LINE:
                raise BridgeError("命令超过协议长度限制")
            try:
                if self._virtual:
                    self._conn.write_line(line)
                else:
                    self._conn.write((line + "\n").encode("utf-8"))
            except Exception as exc:
                self._push_event("sys", {"msg": f"发送失败:{exc}"})
                return False
            self._push_event("out", doc)
            return True

    def request_stock(self, timeout: float = 2.0) -> dict | None:
        """T5:请求盒子差异清单,聚合 stock 事件直到 complete。

        返回 {cell: 盒子qty};超时返回 None(发 sys 事件,不抛异常)。
        """
        with self._lock:
            if self._conn is None:
                return None
            self._stock_wait = {"items": {}, "complete": False}
        if not self.send_command("stock"):
            with self._lock:
                self._stock_wait = None
            self._push_event("sys", {"msg": "stock 命令发送失败"})
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                w = self._stock_wait
                if w is not None and w["complete"]:
                    items = dict(w["items"])
                    self._stock_wait = None
                    return items
            time.sleep(0.05)
        with self._lock:
            self._stock_wait = None
        self._push_event("sys", {"msg": "读取盒子库存超时,可点击同步重试"})
        return None

    def stock_diff_view(self) -> dict:
        with self._lock:
            return {"pending": bool(self._pending_diff),
                    "items": list(self._pending_diff or [])}

    def apply_stock_diff(self, resolution: str, cells: list[int] | None = None) -> dict:
        """T5:差异确认。resolution=box(以盒子为准,写流水)/host(以软件为准,覆盖盒子)。

        幂等:处理完即清空对应 pending,重复 apply 返回 applied:0(T7 重连依赖此点)。
        """
        from . import devices as devices_mod
        if resolution not in ("box", "host"):
            raise BridgeError("resolution 必须是 box 或 host")
        with self._lock:
            pending = list(self._pending_diff or [])
        if not pending:
            return {"applied": 0, "resolution": resolution, "remaining": 0}
        sel = [p for p in pending if cells is None or p["cell"] in cells]
        if not sel:
            return {"applied": 0, "resolution": resolution,
                    "remaining": len(pending)}
        applied = 0
        if resolution == "box":
            for p in sel:
                delta = p["box_qty"] - p["db_qty"]
                if delta != 0 and p.get("device_id"):
                    devices_mod.adjust_qty(
                        self.project_root, p["device_id"], delta,
                        source="box_take" if delta < 0 else "box_return",
                        cell=p["cell"], note="连接差异确认(以盒子为准)")
                applied += 1
        else:  # host:以软件为准,push_sync 覆盖盒子,不写流水
            applied = len(sel)
        # host:直接 push_sync 覆盖盒子;box:刷新基线避免重复报
        if not self.push_sync():
            raise BridgeError("差异确认后同步失败,请重试")
        with self._lock:
            leftover = [p for p in pending
                        if resolution == "host"
                        or p["cell"] not in {s["cell"] for s in sel}]
            self._pending_diff = leftover if resolution == "box" else None
            remaining = len(self._pending_diff or [])
        return {"applied": applied, "resolution": resolution, "remaining": remaining}

    @staticmethod
    def _utf8_cut(s: str, limit: int) -> str:
        """T8:UTF-8 安全截断(按字节,不切断多字节字符,去控制字符)。"""
        from ..security import sanitize_text
        clean = sanitize_text(s or "", max_len=200)
        return clean.encode("utf-8")[:limit].decode("utf-8", "ignore")

    def push_sync(self) -> bool:
        """把当前全部映射+库存推送到硬件(联动核心)。

        T-M3-01:按每批 ≤SYNC_CHUNK 条分帧发送(seq/total),
        固件累计后在末批统一回 ready{mapped},63 格满映射不再超长。
        """
        box = boxmap.get_box(self.project_root)
        items = [{"cell": m["cell"], "qty": m["device"]["qty"],
                  "code": m["device"]["lcsc_code"] or "",
                  "name": self._utf8_cut(m["device"]["name"], 20)}
                 for m in box["mappings"] if m["device"]]
        rows, cols = box["config"]["rows"], box["config"]["cols"]
        if not items:
            return self.send_command("sync", rows=rows, cols=cols,
                                     seq=1, total=1, items=[])
        chunks = [items[i:i + SYNC_CHUNK] for i in range(0, len(items), SYNC_CHUNK)]
        total = len(chunks)
        ok_all = True
        for seq, chunk in enumerate(chunks, 1):
            if not self.send_command("sync", rows=rows, cols=cols,
                                     seq=seq, total=total, items=chunk):
                ok_all = False
                break
        if ok_all:
            with self._lock:
                self._pending_diff = None     # T5:基线已刷新,差异消除
        return ok_all

    def light_cell(self, cell: int, on: bool = True) -> bool:
        cell = int(cell)
        if on and self.send_command("light", cell=cell):
            with self._lock:
                self._lit_cell = cell
            return True
        if not on and self.send_command("off"):
            with self._lock:
                self._lit_cell = -1
            return True
        return False

    # -- 接收线程 -----------------------------------------------------------
    def _read_loop(self) -> None:
        conn = self._conn
        if conn is None or self._virtual:
            return
        buf = b""
        try:
            while not self._stop.is_set() and self._conn is not None:
                try:
                    chunk = conn.read(256)
                except (OSError, ValueError) as exc:
                    self._teardown(f"串口读取失败:{exc}")
                    return
                if not chunk:
                    time.sleep(0.05)
                    continue
                with self._lock:
                    self._last_seen = time.time()
                buf += chunk
                if len(buf) > MAX_LINE * 4:  # 异常保护:无换行的垃圾数据
                    buf = b""
                    continue
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._on_raw_line(line.decode("utf-8", "replace"))
        finally:
            # T7/QA-06:非主动断开 → 状态真实化(连接已断开 + 原因)
            if not self._stop.is_set():
                self._teardown("串口读取中断")

    def _on_raw_event(self, doc: dict) -> None:
        """虚拟盒事件入口(与真实硬件同一处理路径)。"""
        self._handle_event(doc)

    def _on_raw_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if len(line) > MAX_LINE:
            self._push_event("sys", {"msg": "丢弃超长消息"})
            return
        try:
            doc = json.loads(line)
        except ValueError:
            self._push_event("sys", {"msg": f"丢弃非 JSON 消息:{line[:60]!r}"})
            return
        if not isinstance(doc, dict):
            self._push_event("sys", {"msg": "丢弃非对象消息"})
            return
        self._handle_event(doc)

    def _handle_event(self, doc: dict) -> None:
        evt = doc.get("evt")
        if not isinstance(evt, str) or evt not in IN_EVENTS:
            self._push_event("sys", {"msg": f"未知事件:{str(evt)[:40]!r}"})
            return
        clean = {k: doc[k] for k in doc if k != "evt"
                 and isinstance(k, str) and len(k) <= 16}
        self._push_event("in", {"evt": evt, **_clip(clean)})
        if evt == "ready":
            self.last_ready_mapped = doc.get("mapped")
        if evt == "pong":
            with self._lock:
                self._last_pong = time.time()
        if evt == "stock" and self._stock_wait is not None:
            # T5:聚合分帧差异(仅投喂给等待中的 request_stock)
            for it in doc.get("items", []) or []:
                if isinstance(it, dict) and "cell" in it:
                    self._stock_wait["items"][int(it["cell"])] = int(it.get("qty", 0))
            if doc.get("complete"):
                self._stock_wait["complete"] = True

        # ---- 联动:硬件取料/退料 → 软件库存实时增减并回写硬件 ----
        if evt in ("take", "return") and isinstance(doc.get("cell"), int):
            self._on_stock_change(evt, doc["cell"])

    def _on_stock_change(self, evt: str, cell: int) -> None:
        try:
            box = boxmap.get_box(self.project_root)
            mapping = next((m for m in box["mappings"] if m["cell"] == cell), None)
            if not mapping or not mapping["device"]:
                self._push_event("sys", {"msg": f"格位 {cell} 未映射器件,忽略库存变更"})
                return
            device_id = mapping["device"]["id"]
            updated = devices.adjust_qty(
                self.project_root, device_id, -1 if evt == "take" else 1,
                source="box_take" if evt == "take" else "box_return",
                cell=cell, note="器件盒按键联动")
            self.send_command("qty", cell=cell, qty=updated["qty"])
            self._push_event("sys", {
                "msg": f"{'取料' if evt == 'take' else '退料'}联动:"
                       f"{updated['name']} → 库存 {updated['qty']}",
                "device_id": device_id, "qty": updated["qty"], "cell": cell})
        except Exception as exc:
            self._push_event("sys", {"msg": f"库存联动失败:{exc}"})

    # -- 模拟器操作 ---------------------------------------------------------
    def simulate(self, action: str, cell: int | None = None) -> dict:
        with self._lock:
            if not (self._virtual and isinstance(self._conn, VirtualBox)):
                raise BridgeError("仅虚拟器件盒支持模拟操作")
            if action not in ("move", "take", "return"):
                raise BridgeError("模拟动作只支持 move/take/return")
            self._conn.simulate(action, cell)
        return self.status()


def _clip(data: dict) -> dict:
    """限制事件字段大小,防止异常数据撑爆前端。"""
    out = {}
    for k, v in list(data.items())[:8]:
        if isinstance(v, (int, float, bool)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:120]
    return out
