/* PersonalDeviceManagement 前端逻辑(原生 JS,无外部依赖,CSP 兼容) */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    page: 1, pageSize: 50, q: "", source: "", total: 0, devices: [],
    box: null, selectedCell: -1, since: 0,
    status: { connected: false, virtual: false }, cellLit: false,
    selectedIds: new Set(),
  };

  /* ================= 基础设施 ================= */
  async function api(path, opts = {}) {
    // 带请求体(json/form)时默认 POST;GET/HEAD 不允许携带 body
    const init = {
      method: opts.method || (opts.json !== undefined || opts.form ? "POST" : "GET"),
      headers: {},
    };
    if (opts.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    }
    if (opts.form) init.body = opts.form;
    const resp = await fetch("/api" + path, init);
    let data = null;
    try { data = await resp.json(); } catch { throw new Error(`HTTP ${resp.status}`); }
    if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    return data.data;
  }

  function toast(msg, cls = "") {
    const el = document.createElement("div");
    el.className = `toast ${cls}`;
    el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 3800);
  }
  const ticker = (msg) => { $("eventTicker").textContent =
    `[${new Date().toLocaleTimeString()}] ${msg}`; };

  function openModal(html) {
    $("modalBox").innerHTML = html;
    $("modalMask").classList.remove("hidden");
    return $("modalBox");
  }
  const closeModal = () => {
    $("modalMask").classList.add("hidden");
    $("modalBox").innerHTML = "";
  };
  $("modalMask").addEventListener("click", (e) => {
    if (e.target === $("modalMask")) closeModal();
  });

  /* ================= 页签 ================= */
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    for (const b of $("tabs").querySelectorAll("button")) b.classList.toggle("active", b === btn);
    for (const t of document.querySelectorAll(".tab"))
      t.classList.toggle("active", t.id === `tab-${btn.dataset.tab}`);
    if (btn.dataset.tab === "codegen") loadPreview();
    if (btn.dataset.tab === "box") refreshPorts();
  });

  /* ================= 器件库 ================= */
  async function loadDevices() {
    const q = encodeURIComponent(state.q);
    const d = await api(`/devices?q=${q}&page=${state.page}&page_size=${state.pageSize}` +
      `&source=${state.source}`);
    state.total = d.total;
    state.devices = d.items;
    state.selectedIds.clear();
    renderDeviceTable();
  }

  function renderDeviceTable() {
    const tbody = $("deviceTbody");
    if (!state.devices.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--fg-dim);padding:30px">
        暂无器件。点击「批量导入」导入立创商城订单,或「＋新建器件」手工添加。</td></tr>`;
    } else {
      tbody.innerHTML = state.devices.map((d) => {
        const qtyCls = d.qty <= 0 ? "zero" : d.qty <= 5 ? "low" : "";
        return `<tr data-id="${d.id}">
          <td><input type="checkbox" class="row-check" data-id="${d.id}"></td>
          <td><span class="code-chip">${esc(d.lcsc_code || "—")}</span>
              <span class="src-tag">${d.source === "lcsc" ? "立创" : "手工"}</span></td>
          <td><span class="name" title="${esc(d.name)}">${esc(d.name)}</span></td>
          <td>${esc(d.package || "—")}</td>
          <td>${esc(d.manufacturer || "—")}</td>
          <td class="hide-sm"><span class="desc" title="${esc(d.description)}">${esc(d.description || "—")}</span></td>
          <td><span class="qty-badge ${qtyCls}" data-qty="${d.id}" title="点击修改库存">${d.qty}</span></td>
          <td class="hide-sm"><span class="desc">${esc(d.notes || "")}</span></td>
          <td>
            <button data-edit="${d.id}" style="padding:3px 9px">编辑</button>
            <button data-del="${d.id}" class="danger" style="padding:3px 9px">删</button>
          </td></tr>`;
      }).join("");
    }
    const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
    $("pageInfo").textContent = `共 ${state.total} 件 · 第 ${state.page}/${pages} 页`;
  }

  $("deviceTbody").addEventListener("click", async (e) => {
    const editId = e.target.dataset?.edit;
    const delId = e.target.dataset?.del;
    const qtyId = e.target.dataset?.qty;
    if (editId) return openDeviceModal(Number(editId));
    if (delId) {
      if (!confirm("确认删除该器件?其格位映射将被解除。")) return;
      await api("/devices/batch-delete", { json: { ids: [Number(delId)] } });
      toast("已删除", "ok");
      loadDevices(); loadBox();
      return;
    }
    if (qtyId) {
      const dev = state.devices.find((x) => x.id === Number(qtyId));
      const v = prompt(`调整「${dev.name}」的库存数量:`, String(dev.qty));
      if (v === null) return;
      const qty = Number(v);
      if (!Number.isInteger(qty) || qty < 0 || qty > 9999999)
        return toast("库存必须是非负整数", "err");
      await api(`/devices/${dev.id}`, { method: "PUT", json: { qty } });
      loadDevices(); loadBox();
    }
  });
  $("deviceTbody").addEventListener("change", (e) => {
    if (e.target.classList.contains("row-check")) {
      const id = Number(e.target.dataset.id);
      e.target.checked ? state.selectedIds.add(id) : state.selectedIds.delete(id);
    }
  });
  $("checkAll").addEventListener("change", (e) => {
    for (const cb of document.querySelectorAll(".row-check")) {
      cb.checked = e.target.checked;
      e.target.checked ? state.selectedIds.add(Number(cb.dataset.id))
                       : state.selectedIds.delete(Number(cb.dataset.id));
    }
  });
  $("btnBatchDelete").addEventListener("click", async () => {
    if (!state.selectedIds.size) return toast("请先勾选要删除的器件", "warn");
    if (!confirm(`确认删除选中的 ${state.selectedIds.size} 个器件?`)) return;
    const r = await api("/devices/batch-delete", { json: { ids: [...state.selectedIds] } });
    toast(`已删除 ${r.deleted} 个器件`, "ok");
    loadDevices(); loadBox();
  });

  let searchTimer = null;
  $("searchBox").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = $("searchBox").value.trim(); state.page = 1; loadDevices();
    }, 250);
  });
  $("sourceFilter").addEventListener("change", () => {
    state.source = $("sourceFilter").value; state.page = 1; loadDevices();
  });
  $("btnPrevPage").addEventListener("click", () => {
    if (state.page > 1) { state.page--; loadDevices(); }
  });
  $("btnNextPage").addEventListener("click", () => {
    if (state.page * state.pageSize < state.total) { state.page++; loadDevices(); }
  });
  $("btnExport").addEventListener("click", () => { location.href = "/api/export/devices.csv"; });

  /* ---- 新建/编辑器件弹窗 ---- */
  function openDeviceModal(id = null) {
    const dev = id ? state.devices.find((d) => d.id === id) : null;
    const m = openModal(`
      <h3>${dev ? "编辑器件" : "新建器件"}</h3>
      <div class="row2">
        <label>C编号(可选)<input id="mCode" value="${esc(dev?.lcsc_code || "")}" placeholder="C25704"></label>
        <label>库存数量<input id="mQty" type="number" min="0" value="${dev?.qty ?? 1}"></label>
      </div>
      <label>器件名称 *<input id="mName" value="${esc(dev?.name || "")}" maxlength="64"
        placeholder="如 100nF 50V X7R 0603 或 STM32F103C8T6"></label>
      <div class="row2">
        <label>分类<input id="mCat" value="${esc(dev?.category || "")}" placeholder="电容/MCU/连接器…"></label>
        <label>封装<input id="mPkg" value="${esc(dev?.package || "")}" placeholder="0603/LQFP48…"></label>
      </div>
      <div class="row2">
        <label>品牌/厂商<input id="mMfr" value="${esc(dev?.manufacturer || "")}"></label>
        <label>单价(元,可选)<input id="mPrice" type="number" step="0.0001" min="0" value="${dev?.price ?? ""}"></label>
      </div>
      <label>描述<input id="mDesc" value="${esc(dev?.description || "")}"></label>
      <label>备注<input id="mNotes" value="${esc(dev?.notes || "")}"></label>
      <label>自定义属性(JSON,可选)<textarea id="mAttrs"
        placeholder='{"耐压":"50V","精度":"±10%"}'>${esc(dev && Object.keys(dev.attrs || {}).length ? JSON.stringify(dev.attrs, null, 0) : "")}</textarea></label>
      <div class="actions">
        <button id="mCancel">取消</button>
        <button id="mSave" class="primary">保存</button>
      </div>`);
    m.querySelector("#mCancel").addEventListener("click", closeModal);
    m.querySelector("#mName").focus();
    m.querySelector("#mSave").addEventListener("click", async () => {
      let attrs = {};
      const attrsRaw = m.querySelector("#mAttrs").value.trim();
      if (attrsRaw) {
        try { attrs = JSON.parse(attrsRaw); }
        catch { return toast("自定义属性不是合法 JSON", "err"); }
      }
      const body = {
        lcsc_code: m.querySelector("#mCode").value.trim(),
        name: m.querySelector("#mName").value.trim(),
        qty: Number(m.querySelector("#mQty").value || 0),
        category: m.querySelector("#mCat").value.trim(),
        package: m.querySelector("#mPkg").value.trim(),
        manufacturer: m.querySelector("#mMfr").value.trim(),
        description: m.querySelector("#mDesc").value.trim(),
        notes: m.querySelector("#mNotes").value.trim(),
        attrs,
      };
      if (!body.name) return toast("器件名称不能为空", "err");
      const price = m.querySelector("#mPrice").value;
      if (price !== "") body.price = Number(price);
      try {
        if (dev) { await api(`/devices/${dev.id}`, { method: "PUT", json: body }); toast("已保存", "ok"); }
        else { await api("/devices", { json: body }); toast("已创建", "ok"); }
        closeModal(); loadDevices();
      } catch (err) { toast(err.message, "err"); }
    });
  }
  $("btnAddDevice").addEventListener("click", () => openDeviceModal());

  /* ---- 批量导入弹窗(需求 1) ---- */
  $("btnImport").addEventListener("click", () => {
    const m = openModal(`
      <h3>从立创商城批量导入</h3>
      <h4>方式一:订单文件(.xlsx / .xls / .csv,可多选)</h4>
      <input type="file" id="impFiles" multiple accept=".xlsx,.xls,.csv">
      <label class="muted" style="margin-top:4px">
        <input type="checkbox" id="impFetchFiles" checked style="width:auto">
        自动联网补充器件详情(名称/封装/品牌)</label>
      <h4 style="margin-top:14px">方式二:C 编号批量导入(每行一个,如 C25704)</h4>
      <textarea id="impCodes" placeholder="C25704&#10;C81588&#10;C31250"></textarea>
      <label class="muted" style="margin-top:4px">
        <input type="checkbox" id="impFetchCodes" checked style="width:auto">
        自动联网补充器件详情</label>
      <div class="actions">
        <button id="impCancel">取消</button>
        <button id="impGo" class="primary">开始导入</button>
      </div>
      <p class="muted">重复器件自动合并并累加数量;没有详情时仍会按 C 编号入库。</p>`);
    m.querySelector("#impCancel").addEventListener("click", closeModal);
    m.querySelector("#impGo").addEventListener("click", async () => {
      const btn = m.querySelector("#impGo");
      btn.disabled = true; btn.textContent = "导入中…";
      try {
        const files = m.querySelector("#impFiles").files;
        const codes = m.querySelector("#impCodes").value;
        let last = null;
        if (files.length) {
          const fd = new FormData();
          for (const f of files) fd.append("files", f);
          fd.append("fetch_details", m.querySelector("#impFetchFiles").checked ? "1" : "0");
          last = await api("/import/files", { method: "POST", form: fd });
        }
        if (codes.trim()) {
          last = await api("/import/codes", { json: {
            codes: codes.split(/[\s,;、]+/).filter(Boolean),
            fetch_details: m.querySelector("#impFetchCodes").checked } });
        }
        if (!last) { toast("请选择文件或输入 C 编号", "warn"); btn.disabled = false; btn.textContent = "开始导入"; return; }
        const msgs = [`新增 ${last.imported} 件,合并 ${last.merged} 件`];
        if (last.errors?.length) msgs.push(`警告:${last.errors.join(";")}`);
        toast(msgs.join("。"), last.errors?.length ? "warn" : "ok");
        closeModal(); loadDevices(); loadBox();
      } catch (err) { toast(`导入失败:${err.message}`, "err"); }
      btn.disabled = false; btn.textContent = "开始导入";
    });
  });

  /* ================= 器件盒映射(需求 2、3) ================= */
  async function loadBox() {
    state.box = await api("/box");
    const { config, mappings } = state.box;
    $("boxTitle").textContent = `器件盒 ${config.rows} × ${config.cols}(共 ${config.cells} 格)`;
    const map = {};
    for (const m of mappings) if (m.device) map[m.cell] = m;
    const grid = $("grid");
    grid.style.setProperty("--cols", config.cols);
    let html = "";
    for (let i = 0; i < config.cells; i++) {
      const m = map[i];
      const lit = i === state.status.lit_cell ? " lit" : "";
      const sel = i === state.selectedCell ? " selected" : "";
      html += `<div class="cell${m ? "" : " empty"}${sel}${lit}" data-cell="${i}"
        title="行 ${Math.floor(i / config.cols) + 1} 列 ${i % config.cols + 1}">
        <span class="num">${i}</span>
        <div class="nm">${m ? esc(m.device.name) : "(空)"}</div>
        <span class="q">${m ? "×" + m.device.qty : ""}</span></div>`;
    }
    grid.innerHTML = html;
    if (state.selectedCell >= config.cells) state.selectedCell = -1;
    fillCellPanel();
  }

  function fillConfigForm() {
    const c = state.box.config;
    $("cfgRows").value = c.rows; $("cfgCols").value = c.cols;
    $("cfgLedPin").value = c.led_pin; $("cfgBrightness").value = c.led_brightness;
    $("cfgLayout").value = c.led_layout; $("cfgDevName").value = c.device_name;
    $("cfgBtnUp").value = c.btn_up; $("cfgBtnDown").value = c.btn_down;
    $("cfgBtnLeft").value = c.btn_left; $("cfgBtnRight").value = c.btn_right;
    $("cfgBtnOk").value = c.btn_ok; $("cfgSda").value = c.oled_sda; $("cfgScl").value = c.oled_scl;
  }

  $("grid").addEventListener("click", (e) => {
    const cellEl = e.target.closest(".cell");
    if (!cellEl) return;
    state.selectedCell = Number(cellEl.dataset.cell);
    for (const el of document.querySelectorAll(".cell"))
      el.classList.toggle("selected", Number(el.dataset.cell) === state.selectedCell);
    fillCellPanel();
  });

  function currentCellMapping() {
    if (!state.box || state.selectedCell < 0) return null;
    return state.box.mappings.find((m) => m.cell === state.selectedCell) || null;
  }

  function fillCellPanel() {
    const m = currentCellMapping();
    const panel = $("cellPanel");
    if (state.selectedCell < 0) { panel.classList.add("hidden"); return; }
    panel.classList.remove("hidden");
    const cfg = state.box.config;
    $("cellTitle").textContent =
      `格位 #${state.selectedCell}(第 ${Math.floor(state.selectedCell / cfg.cols) + 1} 行 ` +
      `第 ${state.selectedCell % cfg.cols + 1} 列)`;
    $("cellInfo").innerHTML = m?.device
      ? `已映射:<b>${esc(m.device.name)}</b> ` +
        `<span class="code-chip">${esc(m.device.lcsc_code || "手工器件")}</span> ` +
        `库存 ${m.device.qty}`
      : `<span class="muted">空格位 —— 点击「分配器件」从器件库选择,或直接点击下方按钮。</span>`;
  }

  async function applyConfig(patch, okMsg) {
    try {
      const r = await api("/box/config", { method: "PUT", json: patch });
      if (r.removed_mappings?.length)
        toast(`尺寸已调整,${r.removed_mappings.length} 个超出边界的映射被移除`, "warn");
      else toast(okMsg || "配置已保存", "ok");
      await loadBox(); fillConfigForm();
    } catch (err) { toast(err.message, "err"); }
  }
  $("btnApplySize").addEventListener("click", () => {
    const rows = Number($("cfgRows").value), cols = Number($("cfgCols").value);
    if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 1 || cols < 1)
      return toast("行列数必须是正整数", "err");
    if (!confirm(`将器件盒调整为 ${rows}×${cols}?超出新边界的映射将被移除。`)) return;
    applyConfig({ rows, cols }, `格栅已调整为 ${rows}×${cols}`);
  });
  for (const chip of document.querySelectorAll("[data-preset]"))
    chip.addEventListener("click", () => {
      const [r, c] = chip.dataset.preset.split("x").map(Number);
      $("cfgRows").value = r; $("cfgCols").value = c;
    });
  $("btnApplyHw").addEventListener("click", () => {
    applyConfig({
      led_pin: Number($("cfgLedPin").value), led_brightness: Number($("cfgBrightness").value),
      led_layout: $("cfgLayout").value, device_name: $("cfgDevName").value.trim(),
      btn_up: Number($("cfgBtnUp").value), btn_down: Number($("cfgBtnDown").value),
      btn_left: Number($("cfgBtnLeft").value), btn_right: Number($("cfgBtnRight").value),
      btn_ok: Number($("cfgBtnOk").value), oled_sda: Number($("cfgSda").value),
      oled_scl: Number($("cfgScl").value),
    }, "硬件配置已保存(生成固件时生效)");
  });
  $("btnAutoArrange").addEventListener("click", async () => {
    if (!confirm("按器件库最新顺序一键填充全部格位?现有映射会被重排。")) return;
    const r = await api("/box/auto-arrange", { method: "POST" });
    toast(`已排布 ${r.assigned} 个器件`, "ok");
    loadBox();
  });
  $("btnClearBox").addEventListener("click", async () => {
    if (!confirm("确认清空全部格位映射?")) return;
    await api("/box/clear", { method: "POST" });
    toast("映射已清空", "ok");
    loadBox();
  });

  /* ---- 分配器件选择器 ---- */
  $("btnPickDevice").addEventListener("click", async () => {
    if (state.selectedCell < 0) return;
    let devices = [];
    try { devices = (await api("/devices?page=1&page_size=200")).items; }
    catch (err) { return toast(err.message, "err"); }
    const m = openModal(`
      <h3>为格位 #${state.selectedCell} 分配器件</h3>
      <input id="pickSearch" type="search" placeholder="输入名称 / C编号 过滤…">
      <div class="picker-list" id="pickList"></div>
      <div class="actions"><button id="pickCancel">取消</button></div>`);
    const render = (kw) => {
      const list = !kw ? devices : devices.filter((d) =>
        (d.name + (d.lcsc_code || "") + d.description).toLowerCase().includes(kw.toLowerCase()));
      $("pickList").innerHTML = list.length ? list.map((d) => `
        <div class="picker-item" data-id="${d.id}">
          <span class="code-chip">${esc(d.lcsc_code || "手工")}</span>
          <span class="d" title="${esc(d.description)}">${esc(d.name)}</span>
          <span class="qty-badge ${d.qty <= 0 ? "zero" : ""}">${d.qty}</span>
        </div>`).join("")
        : `<div class="picker-item muted">没有匹配的器件</div>`;
    };
    render("");
    $("pickSearch").addEventListener("input", () => render($("pickSearch").value.trim()));
    $("pickCancel").addEventListener("click", closeModal);
    $("pickList").addEventListener("click", async (e) => {
      const item = e.target.closest(".picker-item");
      if (!item?.dataset.id) return;
      await api("/box/mapping", { json: { cell: state.selectedCell, device_id: Number(item.dataset.id) } });
      toast(`已映射到格位 #${state.selectedCell}`, "ok");
      closeModal(); loadBox();
    });
  });

  $("btnClearCell").addEventListener("click", async () => {
    if (state.selectedCell < 0) return;
    await api(`/box/mapping/${state.selectedCell}`, { method: "DELETE" });
    toast("已解除映射", "ok");
    loadBox();
  });
  $("btnLightCell").addEventListener("click", async () => {
    if (state.selectedCell < 0) return;
    state.cellLit = !state.cellLit;
    try {
      await api("/serial/light", { json: { cell: state.selectedCell, on: state.cellLit } });
      if (!state.cellLit) ticker("已熄灭指示灯");
    } catch (err) {
      state.cellLit = false;
      toast(err.message, "warn");
    }
    loadBox();
  });
  $("btnTakeCell").addEventListener("click", async () => {
    const m = currentCellMapping();
    if (!m?.device) return toast("该格位没有映射器件", "warn");
    await api(`/devices/${m.device.id}/adjust-qty`, { json: { delta: -1 } });
    try { await api("/serial/light", { json: { cell: state.selectedCell, on: true } }); } catch {}
    toast(`取料 −1:${m.device.name}`, "ok");
    loadBox();
  });

  /* ================= 串口联动 ================= */
  function updateConnUI(st) {
    state.status = st;
    const dot = $("connDot");
    dot.className = "dot" + (st.connected ? (st.virtual ? " virt" : " on") : "");
    $("connText").textContent = st.connected
      ? (st.virtual ? "虚拟器件盒已连接" : `已连接 ${st.port}`) : "未连接器件盒";
    $("simPanel").classList.toggle("hidden", !(st.connected && st.virtual));
  }

  async function refreshPorts() {
    try {
      const ports = await api("/serial/ports");
      $("portSelect").innerHTML = ports.map((p) =>
        `<option value="${esc(p.port)}">${esc(p.port)} ${esc(p.description ? "· " + p.description : "")}</option>`
      ).join("");
    } catch (err) { ticker(`刷新串口失败:${err.message}`); }
  }
  $("btnRefreshPorts").addEventListener("click", refreshPorts);
  $("btnConnect").addEventListener("click", async () => {
    try {
      const st = await api("/serial/connect", { json: { port: $("portSelect").value } });
      updateConnUI(st);
      toast(st.connected ? "器件盒已连接,映射已同步" : "连接失败", st.connected ? "ok" : "err");
      loadBox();
    } catch (err) { toast(err.message, "err"); }
  });
  $("btnDisconnect").addEventListener("click", async () => {
    const st = await api("/serial/disconnect", { method: "POST" });
    updateConnUI(st); toast("已断开");
  });
  $("btnSyncNow").addEventListener("click", async () => {
    try {
      await api("/serial/sync", { method: "POST" });
      toast("映射与库存已推送到器件盒", "ok");
    } catch (err) { toast(err.message, "warn"); }
  });
  for (const btn of document.querySelectorAll("[data-sim]"))
    btn.addEventListener("click", async () => {
      try {
        await api("/serial/simulate", { json: { action: btn.dataset.sim } });
      } catch (err) { toast(err.message, "warn"); }
    });

  async function pollEvents() {
    try {
      const d = await api(`/serial/events?since=${state.since}`);
      updateConnUI(d.status);
      for (const ev of d.events) {
        state.since = ev.id;
        const { data } = ev;
        if (ev.dir === "in") {
          if (data.evt === "take" || data.evt === "return") {
            toast(`${data.evt === "take" ? "取料" : "退料"}联动:格位 #${data.cell}`, "ok");
            loadBox();
          } else if (data.evt === "lit") {
            if (state.status.lit_cell >= 0 || data.cell >= 0) loadBox();
          } else if (data.evt === "hello") {
            toast(`器件盒上线:${data.device || ""}`, "ok");
          } else if (data.evt === "cursor") {
            ticker(`硬件光标移动到格位 #${data.cell}`);
          }
        } else if (ev.dir === "sys") {
          if (data.msg) ticker(data.msg);
          if (String(data.msg).includes("联动")) { loadBox(); toast(data.msg, "ok"); }
        }
      }
    } catch { /* 轮询失败静默,下轮重试 */ }
  }
  setInterval(pollEvents, 1200);

  /* ================= 代码生成(需求 4、5) ================= */
  async function loadPreview() {
    try {
      const p = await api("/codegen/preview");
      $("codePreview").textContent = p.content;
      $("previewSha").textContent = `sha256:${p.sha256.slice(0, 16)}…`;
    } catch (err) {
      $("codePreview").textContent = `预览失败:${err.message}`;
    }
  }
  $("btnGenerate").addEventListener("click", async () => {
    const dir = $("outputDir").value.trim();
    if (!dir) return toast("请填写输出目录", "warn");
    const btn = $("btnGenerate");
    btn.disabled = true; btn.textContent = "生成中…";
    try {
      const r = await api("/codegen/generate", { json: { output_dir: dir } });
      $("genResult").innerHTML =
        `<div class="ok">✔ 已生成 ${r.count} 个文件 → <code>${esc(r.output_dir)}</code></div>
         <div class="muted" style="margin-top:6px">${r.files.map(esc).join(" · ")}</div>
         <div class="muted" style="margin-top:6px">下一步:用 VSCode + PlatformIO 打开该目录,点击 Upload 烧录。</div>`;
      toast("固件工程生成完成", "ok");
      loadPreview();
    } catch (err) {
      $("genResult").innerHTML = `<div class="err">✘ ${esc(err.message)}</div>`;
      toast(`生成失败:${err.message}`, "err");
    }
    btn.disabled = false; btn.textContent = "⚙ 生成固件工程";
  });

  /* ================= 初始化 ================= */
  (async () => {
    try {
      const info = await api("/system/info");
      $("appVer").textContent = `v${info.version}`;
    } catch { /* 忽略 */ }
    setupDesktopShell();
    await loadDevices();
    await loadBox();
    fillConfigForm();
    await refreshPorts();
    await pollEvents();
  })();

  /* ================= 桌面外壳集成 =================
   * 桌面模式由启动器以 ?shtoken=<随机令牌> 打开页面:
   * - 每 2s 上报心跳,所有页面关闭后由启动器看门狗退出应用;
   * - 顶栏显示「退出」按钮(POST /system/shutdown,令牌保护)。
   * 源码/服务器模式无令牌,两个端点均被后端拒绝,本段自动跳过。 */
  function setupDesktopShell() {
    const token = new URLSearchParams(location.search).get("shtoken") || "";
    if (!token) return;
    setInterval(() => {
      fetch("/api/system/heartbeat", { method: "POST",
        headers: { "X-PDL-Token": token } }).catch(() => {});
    }, 2000);
    const quit = document.createElement("button");
    quit.textContent = "退出程序";
    quit.style.marginLeft = "10px";
    quit.addEventListener("click", async () => {
      if (!confirm("退出个人器件库?")) return;
      try { await fetch("/api/system/shutdown", { method: "POST",
        headers: { "X-PDL-Token": token } }); } catch { /* 服务已停 */ }
      document.title = "已退出,可关闭此窗口";
      document.body.innerHTML = "";
      const tip = document.createElement("p");
      tip.textContent = "应用已退出,可以关闭本窗口。";
      tip.style.cssText = "font-size:20px;padding:60px;text-align:center";
      document.body.appendChild(tip);
    });
    document.querySelector(".conn").appendChild(quit);
  }
})();
