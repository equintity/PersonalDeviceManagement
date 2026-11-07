# v1.2.0 发布闸门交付报告

## 1. 结论
- 完成：T1 T2 T3 T4 T5 T6 T7 T8（全部 8 项，无顺延）
- 测试：`python -m pytest tests/ -q` → **162 passed in 28.38s**（0 failed / 0 skipped / 0 xfailed）
- 发布建议：**通过**（唯一保留项见 §5「未上板验证」）

## 2. 逐项交付表

| 任务 | 状态 | commit | 改动文件 | 关键测试 | 反例是否覆盖 |
|---|---|---|---|---|---|
| T1 备份版本化+迁移+回滚 | ✅ | ff5d8ce | pdl/api.py, pdl/db.py, app.js | TestBackupMigration(7) | 是（任一失败分支后 GET /api/devices 仍 200 且条数不变） |
| T2 流水撤销幂等 | ✅ | 4eff095 | pdl/services/devices.py, pdl/db.py, pdl/security.py, pdl/__init__.py | TestUndoIdempotency(5) | 是（二次撤销 409、qty 不变、流水只增；并发 5 线程恰一次） |
| T3 导入幂等补齐 | ✅ | f9d39cb | pdl/services/importer.py, pdl/api.py, app.js | TestImportIdempotency(4) | 是（同内容不同文件名判重、双击保存不翻倍） |
| T4 库存上限 65535 | ✅ | 3af6f36 | pdl/security.py, pdl/services/{devices,importer,codegen}.py, pdl/api.py, app.js | TestQtyCeiling(7) | 是（脏数据拒绝生成点名器件；CSV 坏数量 400 带文件名行号原文） |
| T5 连接差异检测 | ✅ | 28ca89b | serial_bridge.py, protocol.cpp, storage.h/cpp, main.cpp, pdl/api.py, app.js | TestStockDiff(7) | 是（重连不覆盖离线取料；重复 apply applied:0） |
| T6 同步失败可见 | ✅ | 3486dbf | pdl/services/boxmap.py, pdl/api.py, app.js | TestSyncVisibility(3) | 是（未连接时 sent:false+原因；成功静默） |
| T7 断连感知 | ✅ | b08da44 | pdl/services/serial_bridge.py, app.js | TestConnectionState(3) | 是（connected 永真 >15s 即失败：心跳 15s 超时路径覆盖） |
| T8 名称随 sync 下发 | ✅ | 95c8645 | serial_bridge.py, protocol.cpp, main.cpp | TestSyncName(3) | 是（长名/UTF-8 截断不乱码；1024B 上限未放宽） |
| 文档 | ✅ | e69126e | docs/{FIRMWARE,USAGE,QA-ACCEPTANCE,TODO-QA}.md | — | — |

## 3. 实测证据（真实输出，未改写）

```
$ python -m pytest tests/test_release_gate.py -q
38 passed in 15.48s

$ python -m pytest tests/ -q
162 passed in 28.38s

$ # 出口验收 §7.2-3(a)（自动化等价）：
$ #   备份 → 清空映射 → 导入备份 → 列表/映射/流水复原  = TestD05Backup::test_export_import_roundtrip ✅
$ # 出口验收 §7.2-3(b)：
$ #   取料 3 次 → 连续撤销同一流水两次 → 第二次 409,库存只回退一次 = TestUndoIdempotency ✅
$ # 出口验收 §7.2-3(c)：
$ #   同一 CSV 连导两次 → 第二次 duplicate:true,库存不变    = TestImportIdempotency ✅
$ # 出口验收 §7.2-3(d)：
$ #   连接 → 取料 3 次 → 断开 → 重连 → 差异 3 条 → 以盒子为准
$ #   → 库存 -3 且流水 source=box_take                     = TestStockDiff::test_apply_box_writes_move_and_clears ✅
$ # 规模验收(TODO-QA 硬规则2)：
$ #   63 格满映射连接 → ready.mapped==63;差异 63 项分帧聚合无丢帧 = TestStockDiff::test_full_box_diff_aggregates ✅
```

## 4. 与需求不符/需产品决策的点
- **simulate 语义变更**：T5 规定 VirtualBox.simulate=离线按键(只改盒子不发事件)。
  原 v1.1 测试 `test_full_linkage_loop`/`test_box_take_move_records_cell` 依赖
  "simulate 即联动扣账",已按新语义**机制重写**(改为经差异确认产生 box_take 流水),
  断言未放宽;需产品确认该语义符合预期。
- **T3 CSV 入口**：CSV 实际是 `/api/import/files` 的多文件表单分支(按 `_is_full_field_csv`
  分流),已按任务书 §4-T3 指示在此分支接线,未新增独立 `/import/csv` 路由。
- **旧测试文件中 2 处机械性更新**:`test_sync_chunks_respect_line_limit` 按 T8 要求
  补 name 字段重算长度;`TestOrderXlsx::test_full_import_merge` 在 v1.1.1 已按幂等
  契约更新 —— 均非放宽断言。

## 5. 未验证项（诚实列出）
- **固件 stock/base/名称下发：仅虚拟盒验证,未上板**(无 ESP32 硬件)。
  上板步骤:① `pio run -t upload`(生成目录);② 串口监视器确认 hello;
  ③ 盒上按键取料 2 次后断电重连主机 → 界面应弹出 2 条差异;
  ④ OLED 名称应随软件映射变更(重新 sync 后)即时更新。
- 心跳 15s 超时路径已由虚拟盒等价验证(write_line 抛 OSError 触发同一 _teardown),
  真实串口拔插行为未实测。

## 6. 已知限制
- 单盒架构(boxes 表为 v2.0.0 范围);库存上限 65535(与固件 uint16 对齐);
  Windows 下串口独占;op_logs 超 2 万条自动裁剪至 1.5 万。
- 立创接口为非官方公开端点,字段变化仅影响"联网补详情/价格",不影响核心功能。

## 7. 下一步建议
- v1.2.1:快照恢复回收站 UI(T-M1-03 尾项)、库存阈值筛选(T-M1-05)、
  服务端搜索选择器(T-M3-02)、自动重连(T-M5-03 尾项)、SSE 推送(T-M6-06)。
- v1.3.0:BOM 配单、盘点模式、一键烧录(R-03/R-04/R-05)。
- v2.0.0 前:多盒数据模型设计评审(RK-05)与许可决策(T-M8-1)。
