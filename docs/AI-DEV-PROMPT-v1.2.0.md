# AI 开发任务书 · v1.2.0 发布闸门（Release Gate）

> **投喂方式**：把本文件从「一、任务总览」开始全部内容作为一次性 prompt 交给工程内的 AI 开发助手（或直接说「执行 `docs/AI-DEV-PROMPT-v1.2.0.md`」）。
> **本文件是自包含的**：所有决策、路径、行号、契约、验收命令均已给定，执行过程中**不需要也不能**再向需求方确认。
> 生成依据：`docs/QA-ACCEPTANCE.md`（缺陷证据）+ `docs/TODO-QA.md`（任务卡）+ 代码实测（124 项单测基线）。
> 生成日期：2026-11-07 ｜ 目标版本：`1.2.0`

---

## 一、任务总览

### 1.1 你的身份与唯一目标

你是本工程的 AI 开发工程师。本次任务**只有一个目标**：

> **把「个人器件库」从「能跑」推进到「账目可信、可对外发布」**——关闭 5 项 P0（会造成不可逆数据错误或应用不可用），并补 3 项让失败可被用户看见的 P1。

**成功判据**：`python -m pytest tests/ -q` 全绿且用例数 ≥ 142，5 项 P0 对应的反例断言全部由自动化测试覆盖，`docs/QA-ACCEPTANCE.md` 中 P0 条目状态改为「已修复」。

**不是本次目标**（不要做，做了算范围蔓延）：多盒支持、BOM 齐套、盘点模式、一键烧录、找料闭环、i18n、UI 大改版、性能优化、代码全量重构。详见 §1.5。

### 1.2 任务卡（按执行顺序，逐项交付）

| 序 | 任务 | 优先级 | 估时 | 对应缺陷 | 对应用例 | 主要改动面 |
|---|---|---|---|---|---|---|
| T1 | 备份版本化 + 恢复后迁移 + 失败回滚 | **P0** | 1.5d | QA-01 | TC-F8-03 | `pdl/api.py` / `pdl/db.py` |
| T2 | 流水撤销幂等（禁止二次撤销） | **P0** | 1.0d | QA-02 | TC-F7-03 | `pdl/services/devices.py` / `pdl/db.py` |
| T3 | 导入幂等补齐（CSV + 预览保存） | **P0** | 1.0d | QA-03 | TC-F2-02 | `pdl/services/importer.py` / `app.js` |
| T4 | 库存上限三处对齐（65535） | **P0** | 1.0d | QA-05 | TC-F6-03 | `pdl/security.py` / `codegen.py` / 固件 |
| T5 | 连接时库存差异检测（含固件 `stock` 命令） | **P0** | 2.0d | QA-04 | TC-F5-04 | `serial_bridge.py` / `protocol.cpp` / `api.py` / `app.js` |
| T6 | 同步失败可见（消灭静默 `except pass`） | P1 | 0.5d | QA-19 | TC-F5-06 | `boxmap.py` / `api.py` / `app.js` |
| T7 | 断连感知与状态真实化 | P1 | 1.0d | QA-06 | TC-F5-03 | `serial_bridge.py` / `app.js` |
| T8 | 映射名称随 sync 下发（OLED 不再显示旧名） | P1 | 2.0d | QA-07 | TC-F4-06 | `serial_bridge.py` / `protocol.cpp` / `box_ui.cpp` / `main.cpp` |

**合计 ≈ 10 人日。T1~T5 是发布闸门，必须全绿；T6~T8 若时间不足，允许只完成 T6，并在交付报告 §5「未验证项」注明 T7/T8 顺延（不得静默跳过）。**

### 1.3 依赖关系与执行顺序

```
T1 ─┐
T2 ─┼─ 账目可信（互不阻塞，可并行）
T3 ─┘
T4 ──→ 口径一致（T4 改 MAX_QTY 会影响 T3/T1 的测试数据构造，建议 T4 在 T1~T3 之后做）
T5 ──→ 依赖 T4（qty 范围一致性）+ T2（差异落账要写流水）
T6 ── 独立，可随时插入
T7 ── 依赖 T5（重连后必须先过差异确认，再恢复正常状态）
T8 ── 独立，最后做（固件 RAM 占用需在 T5 之后统一评估）
```

**每完成一项必须**：跑全量测试 → 通过 → 单独一次 commit → 在交付报告追加一行。**禁止把多个任务塞进一个 commit。**

### 1.4 验收硬规则（本工程强制，任何任务不得绕过）

1. **可断言**：验收标准必须能写成断言或 30 秒可复现的手工脚本，禁止「体验更好」「更稳定」。
2. **测上限**：任何可配置上限（格数、库存、文件数、批次），必须有「上限处仍可用」的用例。
3. **测失败态**：必须写明失败时用户看到什么、能否恢复、数据是否还在。
4. **测恢复**：涉及写入/覆盖的功能，必须验证「操作失败后原数据仍可用」。
5. **测口径一致**：同一数字在 界面 / 数据库 / 固件 三处必须能交叉核对。

> 上一轮 124 项单测全绿却漏掉 5 个 P0，根因就是用例只覆盖「正常路径的短数据」。本次新增用例必须至少覆盖一条上述规则，否则不算完成。

### 1.5 本期明确不做（Won't）

- ❌ 不引入任何新的第三方 Python 依赖（只用标准库 `sqlite3` / `hashlib` / `zipfile` / `json` / `threading`）。
- ❌ 不修改 `LICENSE`（GPL-3.0）、不调整许可证策略（另有独立决策待定）。
- ❌ 不改变已有 API 的**现有字段语义**（可新增字段，不可改名/删字段）。
- ❌ 不做多盒数据模型（`boxes` 表）、不做移动端、不做 OCR。
- ❌ 不重排/重命名与任务无关的函数，不对整个文件做格式化（会把 diff 淹没）。
- ❌ 不删除、不 `skip`、不 `xfail` 任何既有测试；不放宽既有断言。

---

## 二、项目背景与当前基线

### 2.1 产品是什么

一个**本地运行的桌面工具**：把「电阻电容等贴片器件」按格位装进一个 7×9（可配，硬上限 16×24=256 格）的实体器件盒，用 ESP32-S3 驱动 WS2812 灯阵定位某个格位，OLED 显示格位信息；PC 端（Flask + 原生 JS）管理器件库、映射、库存、流水、代码生成（把映射表编译进固件）。

核心价值链：**录入 → 入盒映射 → 找料点灯 → 取料扣减 → 库存可信 → 补货**。当前断在「库存可信」。

### 2.2 技术栈与目录

| 层 | 技术 | 目录 |
|---|---|---|
| 后端 | Python 3.10+ / Flask（`threaded=True`）/ SQLite | `pdl/`（`api.py` 路由薄壳、`db.py` schema、`security.py` 校验、`services/*` 业务） |
| 前端 | 原生 JS（无框架）/ 单页多 Tab / CSS 变量 | `pdl/static/`（`index.html` 574 行、`js/app.js` 975 行、`css/style.css` 267 行） |
| 固件 | C++ / Arduino / PlatformIO / ESP32-S3 / NVS / U8g2 / FastLED | `firmware/template/` |
| 桌面壳 | pywebview / 单实例 | `desktop/launcher.py` |
| 测试 | pytest（`client`、`root`、`app_root` fixture） | `tests/`（9 个文件，124 用例） |

**启动与验证命令**

```bash
python app.py                       # http://127.0.0.1:8321
python -m pytest tests/ -q          # 当前基线：124 passed
# 若项目 python 缺依赖，用已装好的隔离环境：
#   C:/Users/13802/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -q
```

固件无硬件时用虚拟盒替代：`POST /api/serial/connect {"port":"virtual://box"}`（`VirtualBox` 已按固件协议应答，是本次 T5/T7 的测试基座）。

### 2.3 已确认的当前基线（不要重复排查，直接按此施工）

- 版本 `pdl/__init__.py:15 APP_VERSION = "1.1.1"`，HEAD `ea8cfe4`，`git status` 有 2 个未跟踪文档（`docs/QA-ACCEPTANCE.md`、`docs/TODO-QA.md`）。
- 已完成（**不要重做**）：63 格分帧 sync（`SYNC_CHUNK=8`，`serial_bridge.py:22`；固件 `protocol.cpp:85 kMaxLine=1024`）、订单文件 sha256 幂等（`importer.py:57-110`）、库存流水（`stock_moves`）、软删除（`devices.deleted_at`）、快照（`snapshots`）、操作审计（`op_logs` + `/api/ops`）、日备份（`daily_backup`）。
- **仍存在的 5 个 P0**（本次要修，逐条见 §4）：
  1. 旧备份恢复后应用瘫痪：`api.py:246-296` 只校验 3 张表就 `src.backup()` 覆盖，**不跑 `_migrate`**，manifest 无 `schema_version`。
  2. 撤销不幂等：`devices.py:238-248` 无冲销标记 → 二次撤销把 qty 从 10 变 11。
  3. CSV / 预览保存无幂等：`importer.py:405 import_csv`、`importer.py:549 save_preview` 未登记 `import_batches` → 同一文件连导 10→20→30。
  4. 离线取料被静默覆盖：`serial_bridge.py:271 push_sync` 无条件全量下发，固件 `persist=true` 落 NVS；连接流程（`connect()` L178-218）无差异检测。
  5. 库存上限三处不一致：`security.py:52 MAX_QTY = 9_999_999`，固件 `protocol.cpp` `qty` 上限 65535、`storage.cpp:11/17` 用 `getUShort/putUShort` → 超限静默截断。
- 另有 3 个 P1（T6/T7/T8）：`boxmap.py:43-48 _notify_sync` 用 `except: pass` 吞掉同步失败；`serial_bridge.py:236 connected` 只判 `_conn is not None`（读线程已死仍报在线）；`push_sync` 的 item 只有 `cell/qty/code`，OLED 名称来自编译期常量。

---

## 三、代码规范与架构约束（不可协商）

### 3.1 分层与调用方向

```
pdl/api.py          仅做:取参数 → security 校验 → 调 service → _ok()/_op() 包装
pdl/services/*.py   业务逻辑 + 事务边界;不得 import flask
pdl/db.py           schema / 迁移 / 连接池 / 备份;不得 import services
pdl/security.py     所有外部输入的校验与清洗;上限常量唯一来源
```

- **新增业务逻辑一律放 `pdl/services/`**，`api.py` 里的函数体超过 30 行即视为写错层。
- **所有外部输入必须过 `security.validate_int` / `sanitize_text`**，禁止裸 `int(request.json[...])`。
- **任何改动 `devices.qty` 的路径必须同事务写一条 `stock_moves`**（`_write_move`，`devices.py:251`），`before_qty` 必须等于上一条的 `after_qty`。
- **`stock_moves` 只增不删**，撤销 = 写一条反向记录，不允许 `DELETE`/`UPDATE` 原记录。
- **异常处理禁止 `except Exception: pass`**。若确实不能影响主流程（如审计日志），必须 `_op(..., ok=False)` 或写 `logs/` 留痕，并附注释说明为什么可忽略。
- 事务：写操作显式 `BEGIN IMMEDIATE`（`sqlite3` 默认隔离级别下容易丢更新），失败 `ROLLBACK`，`finally` 释放。

### 3.2 代码风格（与现有一致，评审按此执行）

- 中文 docstring，首行写做什么，其后用 `T-xx-yy:` 标注来源任务号（现有代码即此风格，如 `devices.py:239`）。
- 4 空格缩进，单行 ≤ 100 字符，`snake_case`；类型注解只加在新增函数的参数与返回上，不强制补旧函数。
- 前端：原生 JS，不引入框架/CDN；所有 DOM 文本用 `textContent` 或既有转义方式，**禁止拼接未转义的用户数据到 `innerHTML`**；请求统一走 `app.js:27` 的 `api()` 封装。
- 新增 UI 文案用中文，面向电子爱好者，不用「亲」「哦」等语气词；错误提示必须是「发生了什么 + 怎么办」。

### 3.3 schema 变更规则（本工程唯一合法的迁移方式）

1. 新表/新索引写进 `pdl/db.py` 的 `_SCHEMA`（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）。
2. 新列写进 `_MIGRATION_COLUMNS`（`db.py:122`），`init_db` 里的 `_migrate` 会自动幂等补列，**不要写 `ALTER TABLE ... ADD COLUMN` 到 `_SCHEMA`**。
3. 迁移必须幂等：连续调用 `init_db` 两次不得报错、不得改变数据。
4. 本次新增 `SCHEMA_VERSION` 后，`init_db` 必须 `PRAGMA user_version = SCHEMA_VERSION`（见 T1）。
5. **不允许 drop 表/列**，不允许在迁移里改已有列的类型。

### 3.4 协议约束（串口 JSON Lines，固件 ↔ 主机）

- 一帧 = 一行 JSON，以 `\n` 结尾，**UTF-8 编码后 ≤ 1024 字节**（`kMaxLine`）。主机侧发送前必须校验长度。
- 帧内不得有裸换行；字符串字段用 JSON 转义。
- 主机 → 盒：`{"cmd": "...", ...}`；盒 → 主机：`{"evt": "...", ...}`。
- 既有命令：`ping` `info` `light` `off` `qty` `sync`（分帧 `seq/total`）；既有事件：`hello` `pong` `ready{mapped}` `take` `return` `cursor` `lit` `log`。
- **新增字段只增不删**；固件对未知字段必须忽略而不是报错（`ArduinoJson` 默认行为，保持）。
- 任何新命令都**必须同时在 `VirtualBox`（`serial_bridge.py:40`）实现同语义应答**，否则无硬件环境下无法回归。

### 3.5 API 契约与错误信封

- 成功：`{"ok": true, "data": <payload>}`；失败：`{"ok": false, "error": "<中文可读原因>"}` + 4xx/5xx。
- 校验失败 400、状态冲突 409、不存在 404、未预期异常 500（全局 handler 统一转成信封，已有）。
- 变更类接口必须调 `_op(action, target, detail, ok)` 记审计；只读接口不记。
- **新增字段不得改动已有字段语义**；前端对未知字段必须容错。

---

## 四、任务详细规格（逐条施工）

> 每条包含：现状锚点 → 实现要求 → 数据结构/契约变更 → 验收（含反例）→ 测试要求。
> **行号是施工起点，不是终点**；若行号漂移，按函数名/内容定位。

### T1 · 备份版本化 + 恢复后迁移 + 失败回滚（P0，1.5d）

**现状（已实测）**
- `pdl/api.py:216-243` 导出：manifest 只有 `app/version/created_at/devices/mappings/moves`，**无 schema 版本**。
- `pdl/api.py:246-296` 导入：读 zip → 只校验 `{devices, mappings, box_config}` 三表存在 → `src.backup(当前库)` **直接覆盖** → 返回。全程不跑 `init_db`/`_migrate`，所以把 v1.0 备份（`devices` 无 `deleted_at` 列）恢复进来后，`GET /api/devices` 因 `WHERE deleted_at IS NULL` 抛 `no such column`，**列表永久 500，用户无法自救**。

**实现要求**
1. `pdl/db.py`：新增 `SCHEMA_VERSION = 4`（当前列迁移后的等价版本；v1.0=1，加价格列=2，加 `deleted_at`=3，本次 undo 列=4，与 T2 对齐）与 `def schema_version(conn) -> int`（读 `PRAGMA user_version`）；`init_db()` 末尾 `PRAGMA user_version = SCHEMA_VERSION`；`_migrate()` 返回 `(from_version, to_version)` 并写 `import_logs`（kind=`migrate`）。
2. manifest 增字段（导出时写）：
   ```json
   {"app":"PersonalDeviceManagement","app_version":"1.2.0","schema_version":4,
    "created_at":"2026-11-07T05:41:28Z",
    "counts":{"devices":832,"mappings":63,"moves":1204,"snapshots":3,"ops":88}}
   ```
   保留旧字段 `version/devices/mappings/moves` 以兼容旧备份（读侧优先 `counts`，回退旧字段）。
3. 导入流程改为**先落临时库、迁移、校验、再原子覆盖**：
   ```
   解包 → 校验 zip 结构 → 读 manifest（缺 schema_version 视为 1）
   → schema_version > SCHEMA_VERSION ? 400「备份来自更高版本（vX），请先升级程序」
   → 写 data/backups/restore-tmp.db → 用 init_db/_migrate 迁移到当前版本
   → 计数校验：devices/mappings 必须与 manifest 相等（差 1 即 400，附实际 vs 期望）
   → 迁移后仍缺关键列 → 400
   → 备份当前库为 pre-restore-<ts>.db（原子复制）
   → src.backup(当前连接) 覆盖 → 关闭临时连接 → 重查 SELECT COUNT(*) FROM devices WHERE deleted_at IS NULL 确认可用
   → 返回 {"restored":true,"from_schema":1,"to_schema":4,"devices":N,"safety_backup":"..."}
   ```
4. 任一步失败：**当前库不得被改动**，删除临时库，返回 400 + 中文原因（含失败步骤名，例如「迁移失败：devices.deleted_at 补列异常」），并 `_op("backup.import", ok=False)`。
5. `pre-restore-*.db` 只保留最近 3 份（启动/恢复时清理）。
6. 响应里带 `migrated: true/false` 与 `from_schema/to_schema`，前端在恢复成功后 toast 显示「已从 schema 1 迁移到 4」。

**验收（必须写成测试，`tests/test_release_gate.py::TestBackupMigration`）**
- [ ] 构造 v1.0 库（手工 `CREATE TABLE devices` 不含 `deleted_at`，插 2 行）+ manifest `schema_version:1` → 打包 → `POST /api/backup/import` → 200，`data.from_schema==1`，随后 `GET /api/devices` **200** 且 `total==2`（当前 bug：500）。
- [ ] manifest `schema_version: 999` → 400，`error` 含「更高版本」，且 `GET /api/devices` 仍 200、条数不变。
- [ ] 损坏 zip（`b"not a zip"`）→ 400，原库条数不变。
- [ ] manifest `counts.devices` 比实际多 1 → 400，原库条数不变。
- [ ] 恢复成功后 `PRAGMA user_version == SCHEMA_VERSION`。
- [ ] `pre-restore-*.db` 连续恢复 5 次后只剩 3 份。
- **反例断言**：任何失败分支后，`GET /api/devices` 必须 200 且条数等于操作前（断言「原数据仍可用」）。

---

### T2 · 流水撤销幂等（P0，1.0d）

**现状（已实测）**
`devices.py:238-248`：`undo_move` 查原流水 → `adjust_qty(-delta)` → 结束。没有「是否已撤销」的判断，也没有冲销关系，所以同一条流水连点两次撤销，qty 10 → 9 → 10 → **11**（第二次把 undo 记录又反了一次），且流水表出现两条 undo。

**实现要求**
1. `pdl/db.py`：
   - `stock_moves` 增列 `reversed_move_id INTEGER`（走 `_MIGRATION_COLUMNS`）。
   - 新增唯一索引（写进 `_SCHEMA`）：
     ```sql
     CREATE UNIQUE INDEX IF NOT EXISTS uq_moves_reversed
       ON stock_moves(reversed_move_id) WHERE reversed_move_id IS NOT NULL;
     ```
2. `devices.py:undo_move`：
   - 原流水 `source == "undo"` → `raise ConflictError("撤销记录不能再被撤销")`（409）。
   - 原流水已被冲销（`EXISTS(SELECT 1 FROM stock_moves WHERE reversed_move_id = ?)`）→ 409「该变动已撤销」。
   - 校验与写入同事务（`BEGIN IMMEDIATE`），插入 undo 记录时写 `reversed_move_id = 原 id`、`ref = f"undo:{id}"`、`source="undo"`；唯一索引兜底并发重复（捕获 `IntegrityError` → 409）。
   - 撤销成功后，若 `serial_bridge` 已连接且原流水有 `cell`，调 `send_command("qty", cell=<cell>, qty=<after>)` 让盒子同步回退；发送失败只记 `_op(ok=False)`，不回滚数据库（数据库是真相）。
3. 错误类型：`pdl/security.py` 目前只有 `ValidationError`（见 `security.py:41`）。本次**只新增一个** `class ConflictError(ValidationError)`；在 `pdl/__init__.py:134` 的 `errorhandler(ValidationError)` 旁注册 `@app.errorhandler(security.ConflictError)` 返回 **409**，并同样写 `log_op(..., ok=False)`（注意 Flask 会优先匹配更具体的异常类）。不要另造异常体系，也不要在路由里手写 `_fail(..., 409)` 绕过它。

**验收（`TestUndoIdempotency`）**
- [ ] 取料 −3（10→7）→ 撤销 → qty 10，流水 2 条（`source=undo`，`reversed_move_id=原id`）。
- [ ] 对同一条原流水再撤销 → **409**，`error` 含「已撤销」，qty 仍 10，流水仍 2 条。（当前 bug：qty 变 11）
- [ ] 对 undo 记录本身撤销 → 409。
- [ ] 并发：`threading` 起 5 个线程同时撤销同一流水 → 恰好 1 个 200，其余 409，qty 只回退一次、undo 记录恰好 1 条。
- [ ] 已连接虚拟盒时撤销带 `cell` 的流水 → 盒子 `_items[cell]` 的 qty 与 DB 一致（可用 `/api/serial/events` 断言收到 `qty` 命令）。

---

### T3 · 导入幂等补齐（P0，1.0d）

**现状（已实测）**
- `importer.py:53-110 import_files`（订单 xlsx）：算 `sha256(file bytes)` 写 `import_batches.idempotency_key`，重复默认拦截、`force` 才累加 —— **这一条是对的，作为范式复用**。
- `importer.py:405 import_csv`（全字段 CSV 导入）：**不查也不写** `import_batches` → 同一 CSV 连导三次，qty 10→20→30。
- `importer.py:549 save_preview`（预览页确认保存）：同样无幂等 → 用户双击「确认保存」或网络重试即翻倍。

**实现要求**
1. 抽出 `_register_batch(conn, key, kind, source_name, stats) -> dict|None` 与 `_lookup_batch(conn, key)` 两个私有函数（从 `import_files` 里搬，行为不变，`import_files` 改为调用它们，**保持其现有响应结构**）。
2. `import_csv`：键 `csv:<sha256(原始文件字节)>`；重复且未 `force` → 返回 `{"duplicate": true, "last": {...}, "imported": 0, "merged": 0}`。
   **入口已确认**：CSV 不是独立路由，而是 `POST /api/import/files` 的 `csv_files` 分支（`api.py:344-360`，用 `_is_full_field_csv()` 与订单 xlsx 分流）。当前该分支把 `force` **漏掉了**（`api.py:357` 只传两个参数），必须补齐；同时把 `api.py:351-352` 的「`duplicate` 立刻 `return _ok(stats)` 交前端确认」逻辑上移到分支汇总之后，使 CSV 判重与订单文件行为一致。
3. `save_preview`：键 `preview:<sha256(canonical_json)>`，canonical = 按 `code` 排序后的 `code:qty:name` 列表 + 总条数；重复且未 `force` → `{"duplicate": true, "created": 0, "merged": 0, "last": {...}}`。
4. 前端 `app.js`：复用 `app.js:357-365` 已有的「该文件已于 X 导入过」二次确认（`confirm`）逻辑，让 CSV 路径与预览保存路径也走同一弹窗；用户确认后带 `force=1` 重发。
5. `import_batches` 表已存在（`db.py:110`），不要新表。

**验收（`TestImportIdempotency` 扩展）**
- [ ] 同一 CSV 连导两次：`qty` 10 → **10**，第二次 `duplicate=true` 且 `last.created_at` 非空。（当前 bug：20）
- [ ] 相同内容、不同文件名的 CSV → 同样判定重复（按内容不按文件名）。
- [ ] 内容改 1 行 → 不判重复，正常合并。
- [ ] 同一 preview 连确认两次：第二次 `created==0`、`duplicate==true`，qty 不变；`force:true` 后才累加。
- [ ] `import_files` 行为回归：现有 `tests/test_v111.py::TestD02ImportIdempotency` 三条断言必须继续通过（不得改动该测试）。

> **CSV 测试数据配方**（照抄即可，否则会被 `_is_full_field_csv()` 判成订单文件）：
> 表头必须同时含 `lcsc_code` 与 `name` 两列（小写，`api.py:329-336` 判定），例如 `lcsc_code,name,qty,package`；
> 通过 `POST /api/import/files` 以 multipart 上传，字段名 `files`，重复导入时追加表单字段 `force=1`。

---

### T4 · 库存上限三处对齐（P0，1.0d）

**现状（已实测）**
`security.py:52 MAX_QTY = 9_999_999`；固件 `protocol.cpp` 的 `qty` / `sync` 只接受 `0..65535`；`storage.cpp:11,17` 用 `getUShort/putUShort`（uint16）→ 后端允许 100000，写进固件被**静默截断成 34464**，界面还显示 100000。**已决定采用「统一 65535」方案**（另一方案「固件改 uint32」需 NVS 格式变更、风险高，本期不做）。

**实现要求**
1. `security.py`：`MAX_QTY = 65535`，注释说明「与固件 uint16 NVS 对齐，改动需同步 firmware + 协议文档」。
2. 新增 `MAX_QTY_REASON = "固件单格上限 65535"`（或直接在 `validate_int` 的报错里拼），所有超限报错必须是「库存数量超出范围 0~65535（固件单格上限 65535）」。
3. `codegen.py` 生成的 `box_config.h` 增 `#define BOX_MAX_QTY 65535`，注释 `// 与主机 security.MAX_QTY 一致`；生成前若发现任何 `qty > 65535`（历史脏数据），**拒绝生成**并列出器件，不要静默夹值。
4. 前端：库存输入框 `max` 从 `/api/system/info` 新增字段 `max_qty` 读取，不写死。
5. `/api/system/info` 增 `{"max_qty": 65535, "max_cells": 256, "schema_version": 4}`。
6. 导入路径（`importer.py` 各处 `min(security.MAX_QTY, ...)`）：超限**拒绝并报错**（不要静默夹），错误文案带器件名与原始值。
7. **顺手必修的静默点**：`importer.py:426-429 import_csv` 里 `qty` 用 `re.search(r"\d{1,7}")` 解析，解析不到就 `qty = 1`——这是第二条静默通道（用户明明写了 `1,000` 却入库 1）。改为：解析失败 → 抛 `ValidationError(f"{文件名} 第 N 行数量无法识别：{原文}")`；`1,000` → 1000；小数四舍五入取整并把原文写进 `notes`。本条与 T4 同一次提交，验收见下。

**验收（`TestQtyCeiling`）**
- [ ] `POST /api/devices {"qty":65536}` → 400，`error` 含「65535」。
- [ ] `qty=65535` 保存成功；`GET /api/devices` 读到 65535；`/api/codegen/preview` 生成的 header 里该值为 65535；`#define BOX_MAX_QTY 65535` 存在。
- [ ] 手工把 DB 改成 70000 后调 `/api/codegen/generate` → 400 且错误里点名该器件（不允许生成 34464）。
- [ ] 导入数量 70000 → 400（或按 §4 决定拒绝），错误含原始值；**不得**出现「导入成功但库存是 65535」的静默路径。
- [ ] CSV 数量列写 `1,000` → 入库 1000；写 `abc` 或 `-5` → 400，错误含文件名+行号+原文；**不得**出现静默入库 1（当前 `importer.py:426-429` 的 `qty = 1` 兜底必须删除）。
- [ ] 回归：63 格全映射连接后 `ready.mapped == 63`（`test_v111.py::TestD01ProtocolScale` 继续通过）。
- **反例断言**：全链路任一处出现「界面数字 ≠ DB 数字 ≠ 固件数字」即失败。

---

### T5 · 连接时库存差异检测（P0，2.0d，含固件）

**现状（已实测）**
`serial_bridge.py:178-218 connect()`：连上即 `push_sync()`（全量覆盖盒子，固件 `persist=true` 写 NVS）→ 再 `send_command("info")`。**结果**：软件关闭时在盒子上按键取料 3 次，重连瞬间被主机旧值覆盖，3 次扣减**无任何提示地消失**。

**关键设计前提（顺序不改，功能必不成立）**

连接时**禁止先 `push_sync`**——那是把盒子的真相先抹掉再读，`stock` 永远读不出差异。必须改成：

```
打开串口 → send info（可不等回执）
        → request_stock()            # 先读盒子：q 与基线 b 的差异
        → 与 DB 逐格比对
           ├─ 无差异 → push_sync()   # 正常刷新，基线=DB
           └─ 有差异 → 不 push，缓存 pending，等用户确认（apply 后才 push）
```

**协议新增（已定稿，照此实现）**

| 方向 | 帧 | 语义 |
|---|---|---|
| 主机→盒 | `{"cmd":"stock"}` | 请求差异清单（相对"上次 sync 基线"） |
| 盒→主机 | `{"evt":"stock","seq":1,"total":2,"complete":false,"items":[{"cell":5,"qty":7,"code":"C25704"}]}` | 分帧返回，`items` 每帧 ≤8 项；末帧 `complete:true`；无差异时也必须回一帧 `{"items":[],"complete":true}` |

**基线定义（实现按此，不要用纯 RAM）**

- 基线 = 固件 NVS 中每格的 `b<cell>`（uint16），存"上一次 sync 从主机收到的值"。
- `sync` 末批：`q` 与 `b` 同批写入（一次批量落盘，而不是每格一次）。
- 物理按键 `take/return` 与 `qty` 命令只改 `q`，**不动 `b`** → 离线改动天然表现为 `q != b`。
- 首次烧录 / 新格位没有 `b` → 启动时 `b := q`（视为无差异），避免首次连接全员误报。
- `stock` 只返回 `q != b` 的已映射格。**不用纯 RAM 基线**：断电后 RAM 丢失会导致差异全部漏报，正好丢掉本任务要救的那批数据。

**实现要求**
1. **固件**
   - `storage.h/cpp`：新增 `load_base(cell, default)` / `save_base(cell, qty)`，key = `b<cell>`（NVS key ≤15 字符，合规），类型 `uint16`，与 `q<cell>` 同量级。
   - `main.cpp`：新增 `base_of(cell)` / `set_base(cell, qty)`；`load_qty` 时若 `b<cell>` 缺失则初始化为当前 `q`。
   - `protocol.cpp`：新增 `cmd == "stock"` 分支 → 遍历 `0..BOX_CELLS-1`，跳过未映射格，收集 `q != b` 的 `{cell, qty, code}`，按 8 项/帧发送（`seq/total/complete`）；无差异也发一帧空 `items` + `complete:true`。
   - `sync` 末批（`protocol.cpp:150-155` 附近）：写 `b` 后回 `ready{mapped}`（保持现有语义）。
   - 未知命令仍回 `log`，不要改成静默。
2. **虚拟盒**（`serial_bridge.py:40 VirtualBox`）——**先做这个**，它是无硬件环境的唯一测试基座：
   - 增 `self._base: dict[int,int]`；`sync` 末帧 `_base = {cell: qty}`；`simulate("take"/"return")` 只改 `_items`（这就是"离线改动"）。
   - `write_line` 增 `"stock"` 分支：返回 `_items` 中 `qty != _base.get(cell)` 的项，8 项/帧，末帧 `complete:true`。
3. **主机** `serial_bridge.py`：
   - `request_stock(timeout=2.0)`：发 `stock`，聚合 `stock` 事件直到 `complete`，返回 `{cell: qty}`；超时返回 `None` 并发 `sys` 事件（不抛异常）。
   - `connect()` 按上面的新顺序改写；有差异时 `self._pending_diff = [{cell, code, name, box_qty, db_qty, delta}]`，并只发一条 `sys` 事件「检测到 N 处库存差异，请在界面确认」（不要新增自定义事件类型，结构化数据走 REST：`GET /api/serial/stock-diff`）。
   - `push_sync()` 成功后清空 `_pending_diff`。
4. **API**（`pdl/api.py`）：
   - `GET /api/serial/stock-diff` → `{"pending":bool, "items":[...]}`。
   - `POST /api/serial/stock-diff/apply` body `{"resolution":"box"|"host","cells":[...]}`：
     - `box`：逐格 `adjust_qty(delta = box_qty - db_qty, source = "box_take" if delta<0 else "box_return", cell=cell, note="连接差异确认(以盒子为准)")`，随后 `push_sync()`（把基线刷成新值，避免下次再报），返回 `{"applied":N, "remaining":0}`。
     - `host`：先 `push_sync()` 覆盖盒子（用户明确选择软件为准），`applied` = 被覆盖格数，`remaining` 同理。
     - `cells` 省略 = 全部；给了只处理这些格，其余留在 pending。
     - 每次写 `_op("serial.stock-diff", f"resolution={resolution}", f"处理 {N} 格")`。
     - **幂等**：处理完即清空对应 pending，重复 apply 返回 `{"applied":0}`（T7 重连不得重复扣减依赖此点）。
5. **前端**（`app.js`）：连接成功后调 `GET /api/serial/stock-diff`，`pending==true` → 弹窗表格（格位 / 器件 / 盒子值 / 软件值 / 差值）+ 三按钮 `[以盒子为准] [以软件为准] [逐格选择]`；未处理时状态栏常驻「待确认差异 N 项」，点击可重开。

**验收（`TestStockDiff`，全部用虚拟盒）**
- [ ] 映射 1 格（qty=10）→ 连接（此时无差异，自动 push_sync，盒子基线=10）→ 取料 3 次（`POST /api/serial/simulate {"action":"take","cell":0}` ×3，盒子 q=7、b=10）→ 断开 → 重连 → `GET /api/serial/stock-diff` 返回 1 条，`box_qty==7`、`db_qty==10`、`delta==-3`。（当前 bug：无差异接口，且重连先 push 导致盒子被打回 10、DB 仍 10）
- [ ] `POST .../apply {"resolution":"box"}` → DB qty 7，`stock_moves` 新增 1 条 `delta=-3`、`source="box_take"`、`cell=0`；再断开重连 → 无差异（基线已刷新）。
- [ ] 同上场景改选 `host` → 盒子 q 被覆盖为 10；再断开重连 → 无差异（`b` 已随 sync 更新）；DB 仍 10，`stock_moves` 条数不增加。
- [ ] `cells:[0]` 逐格选择：多格差异时只处理指定格，其余仍在 pending；重复 apply 同格返回 `applied:0`。
- [ ] 无差异场景（连接后不做任何操作就断开重连）→ `pending==false`、不弹窗。
- [ ] 63 格全映射：`stock` 分帧 ≤8 项/帧、每帧 ≤1024B，最终聚合 63 项，无丢帧；无差异时也收到空帧 `complete:true`（不靠超时结束）。
- **反例断言**：任何"重连后差异消失且无提示"的路径即失败（这是本次 P0 的原始现象）。

---

### T6 · 同步失败可见（P1，0.5d）

**现状**：`boxmap.py:43-48 _notify_sync` 遍历 hook 后 `except Exception: pass` → 改完映射同步失败，前端毫无反馈，用户以为已生效。

**实现要求**
1. `_notify_sync` 返回 `{"sent": bool, "reason": str}`，收集**第一个**失败原因（不吞异常），但整体仍不阻断配置保存（配置已落库，失败只影响硬件）。
2. `set_mapping` / `clear_box` / `auto_arrange` / `set_config` 的返回值统一带 `sync` 字段（新增字段，兼容前端）。
3. `POST /api/serial/sync` 返回 `{"sent":..., "reason":..., "mapped":...}`；失败时 `_op("serial.sync", ok=False, detail=reason)`。
4. 前端：写操作成功后若 `data.sync && !data.sync.sent` → toast「已保存，但同步到盒子失败：<reason>」，附「重试」按钮调 `/api/serial/sync`。

**验收**
- [ ] 未连接盒子时改映射 → 响应 200 且 `data.sync.sent==false`、`reason` 非空（当前：无 `sync` 字段）。
- [ ] 前端出现含原因的提示与重试按钮（人工 30 秒验收，附截图到交付报告）。
- [ ] 连接虚拟盒后改映射 → `sync.sent==true`，无错误提示。
- [ ] 重试成功后错误提示消失。

---

### T7 · 断连感知与状态真实化（P1，1.0d）

**现状**：`serial_bridge.py:236-237 connected` 只看 `self._conn is not None`；`_read_loop` 异常退出后没人清空 `_conn` → 拔掉 USB 后 `GET /api/serial/status` 永远 `connected:true`，前端绿灯常亮，点同步全部失败但无解释。

**实现要求**
1. `_read_loop` 用 `try/finally`：退出时（非主动 disconnect）清 `_conn=None`、`_virtual=False`、`_last_error=<异常摘要>`，发 `sys` 事件「连接已断开：<原因>」。
2. 心跳：连接后启用 5s `ping`；连续 3 次无 `pong`（15s）→ 判定断开，走同一清理路径。心跳线程随连接生命周期启停，`disconnect()` 必须 join。
3. `status()` 增 `last_error`、`last_seen_at`；`/api/serial/status` 透传。
4. 前端：状态区在 `connected==false && last_error` 时显示「连接已断开：<原因>」+「重新连接」按钮；重连成功自动 `push_sync` 并按 T5 走差异确认。
5. 重连期间不得重复写流水（T5 的 apply 有幂等性由 `stock_moves` 与 `import_batches` 之外的 `cells` 去重保证；同一差异 apply 两次要返回 `applied:0`，用「pending 清空」实现）。

**验收**
- [ ] 断开虚拟盒（`POST /api/serial/disconnect` 后读线程退出）→ 3s 内 `GET /api/serial/status` `connected:false`、`last_error` 非空。
- [ ] 强制让 `_read_loop` 抛异常（monkeypatch `conn.read` 抛 `OSError`）→ 同上，且前端提示出现。
- [ ] 重连后 8s 内 `connected:true` 且 `ready.mapped` 等于映射数。
- [ ] 重连过程中 `stock_moves` 条数不增加（无重复扣减）。
- **反例断言**：`connected==true` 但 `ping` 无响应的状态持续 >15s 即失败。

---

### T8 · 映射名称随 sync 下发（P1，2.0d）

**现状**：`push_sync`（`serial_bridge.py:278-280`）的 item 只有 `{cell, qty, code}`；固件 OLED（`box_ui.cpp`）的名称来自编译期 `BOX_MAPPINGS[i].name`。用户在软件里把格位 5 从「100nF」换成「10uF」，**不重新烧录就永远显示旧名**。

**实现要求**
1. `push_sync` 的 item 增 `"name"`：`name[:20]` 的 **UTF-8 安全截断**（按字节截，不切断多字节字符），去掉控制字符。
2. 固件：`main.cpp` 增 `static char s_name[BOX_CELLS][21]`（RAM 1323B，文档注明）；`sync` 应用时拷贝 `name`；`box_ui` 显示时优先 `s_name[cell]`，为空则回退编译期常量；**不写 NVS**（名称是易变展示数据）。
3. 单帧字节数复核：`8 项 × (cell+qty+code 约 30B + name 20B) ≈ 400B < 1024B`，保持 `SYNC_CHUNK=8` 不变；若实测超限，降到 6 并写注释说明原因。

**验收**
- [ ] 格位 5 映射器件 A → 连接（`ready.mapped==1`）→ 改映射为器件 B → **不断开重烧**（`POST /api/serial/sync`）→ 盒子 RAM 中 `s_name[5]` 为 B 的名称（虚拟盒断言 `_items[5]["name"]`）。
- [ ] `ready.mapped` 不因名称下发而变化。
- [ ] 名称含中文/emoji/60 字符 → 不崩、不截断出乱码（截断后仍是合法 UTF-8），OLED 不越界。
- [ ] 63 格满映射 sync 仍分 8 帧、每帧 ≤1024B（`test_v111.py::test_sync_chunks_respect_line_limit` 需按新字段更新 length 计算，**只改这一处断言，不得放宽上限**）。

---

## 五、输入输出约定（汇总）

### 5.1 数据模型变更（本次全部）

```sql
-- T2:冲销关系
ALTER TABLE stock_moves ADD COLUMN reversed_move_id INTEGER;          -- 走 _MIGRATION_COLUMNS
CREATE UNIQUE INDEX IF NOT EXISTS uq_moves_reversed
  ON stock_moves(reversed_move_id) WHERE reversed_move_id IS NOT NULL;

-- T1:版本号(非表结构)
PRAGMA user_version = 4;

-- T3/T5 复用既有表:import_batches(idempotency_key, kind, source_name, stats_json, created_at)
--              op_logs(action, target, detail, ok, created_at)
```

固件 NVS 侧（不涉及 SQL，但属于本次数据契约）：

| key | 类型 | 含义 | 写入时机 |
|---|---|---|---|
| `q<cell>` | uint16 | 当前格位库存（已有） | 取料/退料/`qty` 命令/`sync` 末批 |
| `b<cell>` | uint16 | **新增**：上次 sync 收到的基线值 | 仅 `sync` 末批；首次启动时初始化为当前 `q` |
| `s_name[cell][21]` | RAM only | **新增**：格位器件名缓存（T8） | 每次 `sync`；掉电即失，回退编译期常量 |

### 5.2 新增/变更 API

| 方法 | 路径 | 变更 | 请求 | 响应 data |
|---|---|---|---|---|
| GET | `/api/system/info` | 增字段 | — | `{version, max_qty:65535, max_cells:256, schema_version:4}` |
| GET | `/api/backup/export` | manifest 增字段 | — | zip（`pdl.db` + `manifest.json`） |
| POST | `/api/backup/import` | 流程重写 | multipart `file` | `{restored, from_schema, to_schema, migrated, devices, safety_backup}` |
| POST | `/api/moves/<id>/undo` | 幂等 | — | 200 `{move_id, device}` / **409** `{ok:false,error}` |
| POST | `/api/import/save-preview` | 幂等 | `{items, force?}` | `{created, merged, duplicate?, last?}` |
| POST | `/api/import/files`（CSV 走此入口时） | 幂等 | `force?` | 同上 |
| POST | `/api/serial/stock-diff/apply` | **新增** | `{resolution:"box"\|"host", cells?}` | `{applied, resolution, remaining}` |
| GET | `/api/serial/stock-diff` | **新增** | — | `{pending, items:[{cell,code,name,box_qty,db_qty,delta}]}` |
| POST | `/api/serial/sync` | 增字段 | — | `{sent, reason, mapped}` |

### 5.3 协议帧样例（可直接用于固件/虚拟盒对照）

```jsonc
// 主机→盒:请求差异(连接后第一步,必须先于 sync)
{"cmd":"stock"}
// 盒→主机:第 1/2 帧
{"evt":"stock","seq":1,"total":2,"complete":false,"items":[{"cell":5,"qty":7,"code":"C25704"}]}
// 盒→主机:末帧
{"evt":"stock","seq":2,"total":2,"complete":true,"items":[{"cell":9,"qty":0,"code":"C88001"}]}
// 盒→主机:无差异也必须回一帧(否则主机只能等超时)
{"evt":"stock","seq":1,"total":1,"complete":true,"items":[]}
// 主机→盒:名称下发(sync 分帧,每帧 ≤8 项;同时把基线 b 更新为该值)
{"cmd":"sync","seq":1,"total":1,"rows":7,"cols":9,
 "items":[{"cell":5,"qty":7,"code":"C25704","name":"10uF 0805 25V"}]}
```

### 5.4 文件与命名

| 产物 | 路径/命名 | 规则 |
|---|---|---|
| 备份 | `pdl-backup-<UTC yyyymmdd-HHMMSS>.zip` | 内含 `pdl.db` + `manifest.json` |
| 恢复保险备份 | `data/backups/pre-restore-<ts>.db` | 保留最近 3 份 |
| 临时库 | `data/backups/restore-tmp.db` | 无论成败必须删除 |
| 新增测试 | `tests/test_release_gate.py` | 按 `TestBackupMigration` / `TestUndoIdempotency` / `TestImportIdempotency` / `TestQtyCeiling` / `TestStockDiff` / `TestSyncVisibility` / `TestConnectionState` / `TestSyncName` 分 8 个 class |
| 交付报告 | `docs/AI-DEV-REPORT.md` | 按 §8 模板 |

---

## 六、可用上下文（开工前必读，不要重复探索）

### 6.1 关键文件与职责

| 文件 | 职责 | 本次涉及 |
|---|---|---|
| `pdl/__init__.py` | `create_app()`、`APP_VERSION`、全局错误 handler | 版本号 1.2.0、`/system/info` |
| `pdl/api.py` | 全部路由与 `_ok/_fail/_body/_op` 信封 | T1 T4 T5 T6 T7 |
| `pdl/db.py` | `_SCHEMA`、`_MIGRATION_COLUMNS`、`init_db`、`_migrate`、`backup_db`、`daily_backup`、`utc_now` | T1 T2 T4 |
| `pdl/security.py` | `MAX_*` 常量、`validate_int`、`sanitize_text` | T2 T4 |
| `pdl/services/devices.py` | 器件 CRUD、`adjust_qty`、`undo_move`、`_write_move`、`list_moves` | T2 T5 |
| `pdl/services/importer.py` | `import_files`(有幂等)、`import_codes`、`import_csv`、`save_preview` | T3 T4 |
| `pdl/services/boxmap.py` | 映射/配置/快照、`_notify_sync` | T5 T6 |
| `pdl/services/serial_bridge.py` | `VirtualBox`、`connect`、`push_sync`、`_read_loop`、`send_command` | T5 T7 T8 |
| `pdl/services/codegen.py` | 生成 `box_config.h` | T4 |
| `pdl/static/js/app.js` | 前端全部交互（`api()` 在 L27，导入确认在 L357） | T3 T4 T5 T6 T7 |
| `firmware/template/src/protocol.cpp` | 行协议解析、`sync` 分帧、事件发送 | T5 T8 |
| `firmware/template/src/main.cpp` | `set_qty` / `qty_of` / 光标 | T5 T8 |
| `firmware/template/src/storage.cpp` | NVS `getUShort/putUShort` | **T5（新增 `load_base/save_base`）** |
| `firmware/template/src/box_ui.cpp` | OLED 渲染 | T8 |
| `tests/conftest.py` | `client` / `root` / `app_root` fixture | 新测试复用 |

### 6.2 测试基座用法

```python
def test_xxx(client):                       # Flask test_client，数据目录临时隔离
    r = client.post("/api/serial/connect", json={"port": "virtual://box"})
    assert r.get_json()["data"]["connected"] is True
    client.post("/api/serial/simulate", json={"action": "take", "cell": 0})

def test_yyy(root):                         # 数据根路径，直接调 service
    from pdl.services import devices as device_service
```

- 虚拟盒不支持的操作（如拔 USB）用 `monkeypatch` 让 `conn.read` 抛 `OSError` 模拟。
- 涉及网络（立创商城价格）必须 `monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: None)`，**测试绝不允许联网**。

### 6.3 已知坑（会直接浪费你半天，先看）

| 坑 | 规避方式 |
|---|---|
| `sqlite3` 连接被缓存（`db.py:_connections`），测试间可能串数据 | 新测试用 `client`/`root` fixture，不要自己 `sqlite3.connect` 生产库 |
| `PRAGMA user_version` 在旧库为 0 | 视为 1 处理，不要当异常 |
| Windows 路径含空格/中文 | 一律 `pathlib.Path`，不要拼字符串 |
| Flask `MAX_CONTENT_LENGTH` 25MB | 备份/导入测试用小文件，别造 30MB |
| 固件 NVS key 长度限制 15 字符 | `q<cell>` 已合规，不要加前缀 |
| `serial_bridge` 用 `RLock`（事件回调可重入） | 改 `_read_loop`/`send_command` 时不要换普通 `Lock`，会死锁 |
| 前端无构建步骤 | 改完直接刷新页面，不要引入 bundler |

---

## 七、交付成果（Definition of Done）

### 7.1 必须产出

1. **代码**：T1~T5 全部实现（T6 必做；T7/T8 按时间，未做则在报告注明顺延）。
2. **测试**：新增 `tests/test_release_gate.py`，**≥ 18 个用例**，覆盖 §4 每条验收与反例；全量测试 **≥ 142 passed、0 failed、0 skipped、0 xfailed**。
3. **文档更新**（与代码同一次提交）：
   - `docs/FIRMWARE.md`：补 `stock` 命令、`stock` 事件分帧语义、`sync` 的 `name` 字段、错误事件表（`json error` / `line too long` / `bad cell` / `bad qty cmd`）、单帧上限 1024B。
   - `docs/USAGE.md`：补「连接差异确认」流程、库存上限 65535、重复导入确认、恢复备份会自动迁移。
   - `docs/QA-ACCEPTANCE.md`：把 QA-01~QA-07 状态改为「已修复（commit: xxx）」，追加一列「回归用例」。
   - `docs/TODO-QA.md`：勾选完成项，在 §13 进度记录写实际耗时。
4. **交付报告** `docs/AI-DEV-REPORT.md`（模板见 §8）。
5. **版本号**：`pdl/__init__.py APP_VERSION = "1.2.0"`；`PersonalDeviceManagement.spec` 若有版本字段同步。

### 7.2 验收命令（评审会逐条执行）

```bash
# 1. 全量测试（必须 0 failed / 0 skipped）
python -m pytest tests/ -q

# 2. 新增用例单独跑
python -m pytest tests/test_release_gate.py -q

# 3. 起服务，手工走 4 条主链路
python app.py
#  a) 导出备份 → 清空映射 → 导入备份 → 列表/映射/流水复原
#  b) 取料 3 次 → 连续撤销同一流水两次 → 第二次报 409，库存只回退一次
#  c) 同一 CSV 连导两次 → 第二次提示「已导入过」，库存不变
#  d) 连接虚拟盒 → 取料 3 次 → 断开 → 重连 → 弹出 3 条差异 → 选「以盒子为准」→ 库存 −3 且流水来源为 box_take
```

### 7.3 提交规范

- 分支：`feat/v1.2.0-release-gate`
- 一个任务一个 commit，Conventional Commits + 中文说明：
  ```
  fix(p0): 备份恢复后执行 schema 迁移,失败不覆盖当前库 (T1/QA-01)
  fix(p0): 流水撤销幂等,重复撤销返回 409 (T2/QA-02)
  fix(p0): CSV 与预览保存接入导入幂等 (T3/QA-03)
  fix(p0): 库存上限统一 65535,三处口径一致 (T4/QA-05)
  feat(p0): 连接时库存差异检测与确认 (T5/QA-04)
  fix(p1): 同步失败回传原因并在界面提示 (T6/QA-19)
  fix(p1): 断连感知与状态真实化 (T7/QA-06)
  feat(p1): 映射名称随 sync 下发 (T8/QA-07)
  ```
- **禁止**：一个 commit 混多个任务；提交里夹带无关格式化；`--no-verify` 跳过钩子。

---

## 八、交付报告模板（`docs/AI-DEV-REPORT.md`）

```markdown
# v1.2.0 发布闸门交付报告

## 1. 结论
- 完成：T1 T2 T3 T4 T5 T6（T7/T8：顺延/完成）
- 测试：`python -m pytest tests/ -q` → 142 passed in 14.2s（0 failed / 0 skipped）
- 发布建议：通过 / 有条件通过（条件：xxx）

## 2. 逐项交付表
| 任务 | 状态 | commit | 改动文件 | 关键测试 | 反例是否覆盖 |
|---|---|---|---|---|---|
| T1 | ✅ | abc1234 | pdl/api.py, pdl/db.py, tests/test_release_gate.py | TestBackupMigration(6) | 是（失败后原库可用） |
| ... |

## 3. 实测证据（粘贴真实输出，不要改写）
```
$ python -m pytest tests/test_release_gate.py -q
................  (18 passed)
```

## 4. 与需求不符/需产品决策的点
- （如：T3 的 CSV 入口实际是 `/import/files` 的多文件表单，已按此接线，需产品确认是否要独立 `/import/csv`）

## 5. 未验证项（必须诚实列出）
- T5 固件 `stock` 命令：仅虚拟盒验证，**未上板**（无硬件）。上板验收步骤见 docs/FIRMWARE.md §x。

## 6. 已知限制
- 单盒架构；库存上限 65535；Windows 下串口独占。

## 7. 下一步建议
- P1 剩余：T-M1-01 阈值筛选、T-M3-02 选择器分页、T-M9-02 统一错误提示
```

---

## 九、阻塞与异常上报（不得静默降级）

遇到以下情况，**停下该项、做其余项**，并在报告 §5 写明：

| 情况 | 处理 |
|---|---|
| 需要真机（上板）才能验证 | 用虚拟盒完成等价验证，标注「未上板」，并写出上板步骤（3~5 条命令/操作） |
| 发现需求本身有歧义 | 按本文件给定方案执行；若确实冲突，选**保守方案（宁拒绝不静默）**并在报告 §4 记录，不要向需求方反复确认 |
| 发现新的 P0（会造成静默数据错误） | 立即停止新功能，写进报告 §6 并给出复现步骤；若 ≤0.5 人日可一并修复 |
| 某条测试无法通过 | **禁止** `skip`/`xfail`/放宽断言/改测试去迁就实现。要么修实现，要么在报告 §4 说明为什么不修 |
| 时间不够 | 按 T1→T2→T3→T4→T5→T6→T7→T8 顺序交付，未做的明确列在报告，**不允许"半成品 + 绿测试"** |

---

## 十、开工第一件事（照做即可）

```bash
git checkout -b feat/v1.2.0-release-gate
python -m pytest tests/ -q          # 确认 124 passed 基线，任何偏差先报告
# 然后从 T1 开始：先写失败的测试（红），再改实现（绿），再跑全量
```

> 提示：T1 的测试最难构造（要手工造 v1.0 库）。先写这段：
> `conn.executescript("CREATE TABLE devices (id INTEGER PRIMARY KEY, lcsc_code TEXT UNIQUE, name TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT);")` + 插 2 行 → `PRAGMA user_version = 1` → 打包进 zip → 断言恢复后 `GET /api/devices` 为 200。这个用例在当前代码上必须**先失败**，再修。

---

*本任务书为产品与质量视角的施工规格，实施时如遇安全、合规、许可相关问题（GPL-3.0 分发义务、立创商城接口抓取合规），按现有 `docs/SECURITY.md` 与 `LICENSE` 执行，不在本次改动范围内。*
