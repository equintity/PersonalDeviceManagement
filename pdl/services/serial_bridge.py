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
OUT_COMMANDS = {"ping", "info", "light", "off", "sync", "qty", "take"}
# 硬件 → 软件 事件白名单
IN_EVENTS = {"hello", "pong", "info", "ready", "cursor", "take", "return",
             "qty", "lit", "button", "log"}


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
        self._sync_buf: dict[int, dict] = {}   # 分帧 sync 的累积缓冲
        self._open = True

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
                        self._sync_buf[int(i["cell"])] = {"qty": int(i.get("qty", 0))}
                if seq >= total:
                    self._items = dict(self._sync_buf)
                    self._sync_buf = {}
                    self._emit("ready", {"mapped": len(self._items),
                                         "msg": "虚拟盒:映射已同步"})
            elif action == "qty":
                cell = cmd.get("cell")
                if cell in self._items:
                    self._items[cell]["qty"] = cmd.get("qty", 0)
            elif action == "take":
                self.simulate("take", cmd.get("cell", self._cursor))
            else:
                self._emit("log", {"msg": f"虚拟盒:未知命令 {action!r}"})

    def simulate(self, action: str, cell: int | None = None) -> None:
        """模拟物理按键:move=移动光标 take=取料(短按) return=退料(长按)。"""
        with self._lock:
            if action == "move":
                cells = sorted(self._items) or [0]
                if cells:
                    cur = cell if cell is not None else self._cursor
                    pos = cells.index(cur) if cur in cells else 0
                    self._cursor = cells[(pos + 1) % len(cells)]
                    self._emit("cursor", {"cell": self._cursor})
            elif action == "take":
                target = cell if cell is not None else self._cursor
                item = self._items.get(target)
                qty = max(0, (item or {}).get("qty", 0) - 1) if item else 0
                if item is not None:
                    item["qty"] = qty
                self._emit("take", {"cell": target, "qty": qty})
                self._lit = target
                self._emit("lit", {"cell": target})
            elif action == "return":
                target = cell if cell is not None else self._cursor
                item = self._items.get(target)
                qty = ((item or {}).get("qty", 0) + 1) if item is not None else 1
                if item is not None:
                    item["qty"] = qty
                self._emit("return", {"cell": target, "qty": qty})
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
                self._conn = VirtualBox(self._on_raw_event)
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
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="pdl-serial-reader")
            self._reader.start()
            self._push_event("sys", {"msg": f"已连接 {port}"})
            # 连接后立即推送完整映射与库存(T-M3-01:失败不阻断连接,
            # 提示"映射同步待重试",可经「同步映射到硬件」按钮重试)
            try:
                self.push_sync()
            except Exception as exc:  # noqa: BLE001
                self._push_event("sys", {"msg": f"已连接(映射同步待重试):{exc}"})
            self.send_command("info")
            return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            conn, self._conn = self._conn, None
            self._port = None
            self._lit_cell = -1
            self._stop.set()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if self._port or conn is not None:
                self._push_event("sys", {"msg": "已断开器件盒"})
            return self.status()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def status(self) -> dict:
        with self._lock:
            return {"connected": self.connected, "port": self._port,
                    "virtual": self._virtual, "lit_cell": self._lit_cell,
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

    def push_sync(self) -> bool:
        """把当前全部映射+库存推送到硬件(联动核心)。

        T-M3-01:按每批 ≤SYNC_CHUNK 条分帧发送(seq/total),
        固件累计后在末批统一回 ready{mapped},63 格满映射不再超长。
        """
        box = boxmap.get_box(self.project_root)
        items = [{"cell": m["cell"], "qty": m["device"]["qty"],
                  "code": m["device"]["lcsc_code"] or ""}
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
        while not self._stop.is_set() and self._conn is not None:
            try:
                chunk = conn.read(256)
            except Exception:
                break
            if not chunk:
                time.sleep(0.05)
                continue
            buf += chunk
            if len(buf) > MAX_LINE * 4:  # 异常保护:无换行的垃圾数据
                buf = b""
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._on_raw_line(line.decode("utf-8", "replace"))

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
