# 设计说明

## 需求 → 模块映射

| 原始需求 | 模块 | 关键决策 |
|---|---|---|
| ① 立创商城批量导入全部商品数据 + 手工维护 | `pdl/services/importer.py` + `devices.py` | 三通道导入:订单 xlsx/xls/csv(表头关键词 + 全表正则兜底)、C编号批量接口查询、全字段 CSV 往返;合并策略 = 按 C 编号累加数量、缺失字段补全;手工器件不强制 C 编号,自定义属性以 JSON 字段存储 |
| ② 与自定义器件盒联动(7×9) | `boxmap.py` + `serial_bridge.py` | 默认 7×9=63 格;联动走"换行分隔 JSON"串口协议,连接即全量 sync,硬件取料/退料事件回调改库存并回写 qty;VirtualBox 模拟器实现同一协议,无硬件可演示全闭环 |
| ③ 动态配置映射适配不同盒型 | `boxmap.py` | 映射按 (row,col) 存储,与尺寸解耦;改尺寸保留界内映射、移除越界并报告;预设常用盒型;上限 16×24 且 ≤256 格(限制固件数组规模) |
| ④ 仅配置即生成 ESP32 代码 | `codegen.py` + `firmware/template/` | "配置层(box_config.h 生成) + 代码层(固定模板)"分离;生成 = 复制模板 + 渲染配置 + 扫描 + 落盘;PlatformIO 工程开箱即烧;LED 蛇形/逐行排布可选以适配走线 |
| ⑤ 软件层与代码层安全管理 | `security.py`(全链路) | 五道防线:入口白名单 → C 转义 → 生成后扫描 → 输出目录防覆盖 → Web/协议层加固;详见 docs/SECURITY.md |

## 技术选型

- **Python 3.10+ / Flask / SQLite(WAL)**:零编译依赖、单机即用;线程安全按
  (线程, 数据库路径) 维度建连;
- **原生 HTML/CSS/JS 前端**:无构建链、无 CDN,配合严格 CSP;
- **PlatformIO + Arduino 框架固件**:与参考工程一致,库依赖自动解析;
  中文显示用 U8g2 中文字库,器件名保留 UTF-8 原文。

## 数据模型

```
devices(id, lcsc_code UNIQUE?, name, category, package, manufacturer,
        description, qty, price?, notes, attrs JSON, source, created_at, updated_at)
box_config(id=1, rows, cols, led_pin, led_brightness, led_layout,
           device_name, btn_up..btn_ok, oled_sda, oled_scl, updated_at)
mappings(row, col, device_id→devices ON DELETE SET NULL)   -- PK(row,col)
import_logs(kind, detail, imported, merged, skipped, errors, created_at)
```

要点:
- `mappings` 外键 ON DELETE SET NULL:删除器件不删格位,改为显示空格;
- 同一器件可占多个格位(大用量分格存放),`set_mapping` 只增改不搬移;
- `attrs` JSON 字段承载任意自定义属性(键值 32 项内,标量值)。

## 交互闭环(无硬件验证路径)

```
分配器件 → PUT /api/box/mapping → boxmap 同步钩子 → bridge.push_sync()
模拟取料 → VirtualBox.emit(take) → bridge._on_stock_change
        → devices.adjust_qty(-1) → 回写 qty 命令 → 前端轮询刷新
```

真实硬件仅替换 VirtualBox 为 pyserial 连接,处理路径完全一致(测试
`test_full_linkage_loop` 覆盖)。

## 已知取舍

- 映射烧录在固件配置头里,改映射需重新生成 + 烧录(换来"零手写代码");
  库存则运行期双向同步、NVS 持久化;
- BLE 与 WiFi 通道预留为后续扩展(协议本身与传输层无关);
- 立创公开接口非官方契约,解析失败时降级为仅编号导入,不影响可用性。
