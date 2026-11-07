"""一次性补丁:T7 断连感知收尾(读循环 try/finally + _teardown + 心跳)。用后即删。"""
from pathlib import Path

p = Path("pdl/services/serial_bridge.py")
src = p.read_text(encoding="utf-8")

# 1) _read_loop:try/finally(异常退出 → 状态真实化)
old = '''    # -- 接收线程 -----------------------------------------------------------
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
            while b"\\n" in buf:
                line, buf = buf.split(b"\\n", 1)
                self._on_raw_line(line.decode("utf-8", "replace"))'''
new = '''    # -- 接收线程 -----------------------------------------------------------
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
                while b"\\n" in buf:
                    line, buf = buf.split(b"\\n", 1)
                    self._on_raw_line(line.decode("utf-8", "replace"))
        finally:
            # T7/QA-06:非主动断开 → 状态真实化(连接已断开 + 原因)
            if not self._stop.is_set():
                self._teardown("串口读取中断")'''
assert old in src, "read_loop 未命中"
src = src.replace(old, new, 1)

# 2) 心跳循环 + _teardown(插在 disconnect 之前)
old = '''    def disconnect(self) -> dict:'''
new = '''    def _heartbeat_loop(self) -> None:
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

    def disconnect(self) -> dict:'''
assert old in src, "disconnect 锚点未命中"
src = src.replace(old, new, 1)

# 3) 字段(_heartbeat/_last_pong,挂在 SerialBridge 的 lit_cell 后)
old = '''        self._last_status: dict = {}
        self._lit_cell = -1

    # -- 事件缓冲 -----------------------------------------------------------'''
new = '''        self._last_status: dict = {}
        self._lit_cell = -1
        self._heartbeat: threading.Thread | None = None  # T7:5s ping 看门狗
        self._last_pong: float = 0.0                     # T7:最近 pong 时刻

    # -- 事件缓冲 -----------------------------------------------------------'''
assert old in src, "字段锚点未命中"
src = src.replace(old, new, 1)

# 4) connect:重置错误/时间戳 + 启动心跳
old = '''            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="pdl-serial-reader")
            self._reader.start()'''
new = '''            self._stop.clear()
            with self._lock:
                self._last_error = None
                self._last_seen = time.time()
                self._last_pong = time.time()
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="pdl-serial-reader")
            self._reader.start()
            self._heartbeat = threading.Thread(target=self._heartbeat_loop,
                                               daemon=True, name="pdl-heartbeat")
            self._heartbeat.start()   # T7:5s ping 看门狗'''
assert old in src, "connect 心跳启动未命中"
src = src.replace(old, new, 1)

# 5) pong 时间戳
old = '''        if evt == "stock" and self._stock_wait is not None:'''
new = '''        if evt == "pong":
            with self._lock:
                self._last_pong = time.time()
        if evt == "stock" and self._stock_wait is not None:'''
assert old in src, "pong 锚点未命中"
src = src.replace(old, new, 1)

# 6) 清理 VirtualBox 误加字段
old = '''        self._open = True
        self._last_error: str | None = None            # T7:断连原因
        self._last_seen: float | None = None           # T7:最近收到数据时刻
        self.last_ready_mapped'''
new = '''        self._open = True
        self.last_ready_mapped'''
assert old in src, "VirtualBox 清理未命中"
src = src.replace(old, new, 1)

p.write_text(src, encoding="utf-8", newline="\n")
print("T7 全部落地")
