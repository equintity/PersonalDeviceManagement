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
    // 完成反馈至少完整停留 1.5s(总驻留 3.4s)后才开始淡出
    setTimeout(() => el.classList.add("out"), 3400);
    setTimeout(() => el.remove(), 3800);
  }

  /* 完成态闪烁:格位高亮保持 hold 毫秒(≥1.5s)后才回退,
     确保"操作 → 反馈"因果可感知。
     flashMap 登记未过期的闪烁,防止轮询重渲染把完成态提前抹掉。 */
  const flashMap = new Map();
  function flashCell(cell, cls = "flash-ok", hold = 1600) {
    const until = Date.now() + hold;
    flashMap.set(cell, { cls, until });
    const el = document.querySelector(`#grid .cell[data-cell="${cell}"]`);
    if (el) el.classList.add(cls);
    setTimeout(() => {
      flashMap.delete(cell);
      document.querySelector(`#grid .cell[data-cell="${cell}"]`)?.classList.remove(cls);
    }, hold);
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
          <td><span class="name" title="${esc(d.model ? "型号:" + d.model + " · " : "")}${esc(d.name)}">${esc(d.name)}</span></td>
          <td>${esc(d.package || "—")}</td>
          <td>${esc(d.manufacturer || "—")}</td>
          <td class="hide-sm"><span class="desc" title="${esc(d.description)}">${esc(d.description || "—")}</span></td>
          <td>${renderPrice(d)}</td>
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

  function renderPrice(d) {
    const badges = {
      ok: "已更新", no_match: "无匹配", discontinued: "已下架", error: "失败",
    };
    let html = '<span class="price-cell">';
    if (d.price !== null && d.price !== undefined && d.price !== "") {
      const cur = d.price_currency || "";
      const sym = cur === "USD" ? "$" : cur === "CNY" ? "¥" : "";
      html += `<span class="cur">${sym || esc(cur)}</span>${d.price}`;
    } else {
      html += '<span class="cur">—</span>';
    }
    if (d.price_status && badges[d.price_status]) {
      const when = d.price_updated_at ? ` · ${d.price_updated_at.slice(0, 16).replace("T", " ")} UTC` : "";
      const src = d.price_source === "lcsc" ? "立创" : d.price_source === "jlcpcb" ? "JLC" : "";
      html += `<span class="p-badge ${d.price_status}" title="${esc(badges[d.price_status])}${when}${src ? " · 来源:" + src : ""}">${badges[d.price_status]}</span>`;
    }
    return html + "</span>";
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
      <label>型号 MPN(可选,价格更新时用于匹配)
        <input id="mModel" value="${esc(dev?.model || "")}" maxlength="50"
        placeholder="如 NTMFS5C430NLT1G"></label>
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
        model: m.querySelector("#mModel").value.trim(),
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
      <div id="impEntry">
        <h4>方式一:订单文件(.xlsx / .xls / .csv,可多选)</h4>
        <input type="file" id="impFiles" multiple accept=".xlsx,.xls,.csv">
        <label class="muted" style="margin-top:4px">
          <input type="checkbox" id="impFetchFiles" checked style="width:auto">
          自动联网补充器件详情(名称/封装/品牌)</label>
        <div class="actions">
          <button id="impCancel">取消</button>
          <button id="impGo" class="primary">导入文件</button>
        </div>
        <h4 style="margin-top:16px">方式二:C 编号一键加载(推荐)</h4>
        <p class="muted" style="margin:4px 0">手动录入或批量粘贴 C 编号(每行一个),
          系统自动补全名称/型号/封装/品牌/价格/库存,可在保存前逐项修改。</p>
        <textarea id="impCodes" placeholder="C25704&#10;C2040&#10;C8734"></textarea>
        <div id="impProgress" class="muted" style="margin-top:6px"></div>
        <div class="actions">
          <button id="impCancel2">取消</button>
          <button id="impLoad" class="primary">🚀 一键加载</button>
        </div>
      </div>
      <div id="impPreviewWrap" class="hidden"></div>`);
    m.classList.add("wide");
    m.querySelector("#impCancel").addEventListener("click", closeModal);
    m.querySelector("#impCancel2").addEventListener("click", closeModal);

    // ---- 方式一:文件导入 ----
    m.querySelector("#impGo").addEventListener("click", async () => {
      const btn = m.querySelector("#impGo");
      const files = m.querySelector("#impFiles").files;
      if (!files.length) return toast("请选择订单文件", "warn");
      btn.disabled = true; btn.textContent = "导入中…";
      try {
        const fd = new FormData();
        for (const f of files) fd.append("files", f);
        fd.append("fetch_details",
          m.querySelector("#impFetchFiles").checked ? "1" : "0");
        const r = await api("/import/files", { method: "POST", form: fd });
        const msgs = [`新增 ${r.imported} 件,合并 ${r.merged} 件`];
        if (r.errors?.length) msgs.push(`警告:${r.errors.join(";")}`);
        toast(msgs.join("。"), r.errors?.length ? "warn" : "ok");
        closeModal(); loadDevices(); loadBox();
      } catch (err) { toast(`导入失败:${err.message}`, "err"); }
      btn.disabled = false; btn.textContent = "导入文件";
    });

    // ---- 方式二:C 编号一键加载(预览-编辑-确认) ----
    const wrap = m.querySelector("#impPreviewWrap");
    const entry = m.querySelector("#impEntry");
    const prog = m.querySelector("#impProgress");
    let rows = [];          // 预览行状态(含用户编辑)
    let invalids = [];      // 格式无效行

    const esc2 = esc;
    const statusChip = (s) => ({
      new: '<span class="p-badge ok">新器件</span>',
      exists: '<span class="p-badge" style="color:var(--pri-d);border-color:var(--pri)">已存在·合并</span>',
      not_found: '<span class="p-badge no_match">无返回</span>',
      invalid: '<span class="p-badge error">格式无效</span>',
    }[s] || "");

    function renderEntry() {
      wrap.classList.add("hidden");
      entry.classList.remove("hidden");
      m.querySelector("#impLoad").disabled = false;
      m.querySelector("#impLoad").textContent = "🚀 一键加载";
    }

    function renderPreview() {
      entry.classList.add("hidden");
      wrap.classList.remove("hidden");
      const editable = (idx, field, val, disabled) =>
        `<input class="pv-input" data-idx="${idx}" data-field="${field}"
          value="${esc2(val ?? "")}" ${disabled ? "disabled" : ""}>`;
      const body = rows.map((r, idx) => {
        const locked = r.status === "exists";   // 已存在:人工字段不覆盖,锁定编辑
        const off = r.status !== "new" && r.status !== "exists";
        return `<tr class="${off ? "pv-off" : ""}">
          <td><span class="code-chip">${esc2(r.code)}</span>${statusChip(r.status)}
            ${r.status === "exists" ? `<div class="muted" style="font-size:10px">库内 ${r.exists_qty} 件,保存时合并</div>` : ""}
            ${r.cycle && r.cycle !== "normal" ? '<div class="muted" style="font-size:10px;color:var(--err)">已下架</div>' : ""}</td>
          <td>${editable(idx, "name", r.name, off)}</td>
          <td>${editable(idx, "model", r.model, off)}</td>
          <td>${editable(idx, "package", r.package, off)}</td>
          <td>${editable(idx, "manufacturer", r.manufacturer, off)}</td>
          <td>${off ? "—" : editable(idx, "price", r.price ?? "", false)}</td>
          <td>${off ? "—" : editable(idx, "qty", r.qty, false)}</td>
          <td><button class="pv-del" data-idx="${idx}" title="移除此行">✕</button></td></tr>`;
      }).join("");
      const invalidHtml = invalids.length
        ? `<div class="sum-sec"><h4 style="color:var(--err)">无效编号(${invalids.length})</h4>
           <ul class="sum-list">${invalids.map((v) =>
             `<li><span class="code-chip">${esc2(v.input || "(空)")}</span> —— ${esc2(v.reason)}</li>`).join("")}</ul></div>`
        : "";
      const savable = rows.filter((r) => r.status === "new" || r.status === "exists").length;
      wrap.innerHTML = `
        <h4>加载结果预览(可编辑,保存前请核对)</h4>
        ${invalidHtml}
        <div class="pv-wrap"><table class="pv-table"><thead><tr>
          <th>C编号</th><th>名称</th><th>型号</th><th>封装</th>
          <th>品牌</th><th>价格$</th><th>库存</th><th></th>
        </tr></thead><tbody>${body || ""}</tbody></table></div>
        <p class="muted">已存在器件保存时仅合并数量并刷新价格,人工字段不会覆盖(输入框已锁定)。
          价格为立创接口 USD 价。</p>
        <div class="actions">
          <button id="impBack">⬅ 返回修改编号</button>
          <button id="impSave" class="primary" ${savable ? "" : "disabled"}>
            ✔ 确认保存(${savable} 件)</button>
        </div>`;
      // 编辑绑定:输入即写回 rows(保存前可自由修改)
      wrap.querySelectorAll(".pv-input").forEach((inp) => {
        inp.addEventListener("input", () => {
          const idx = Number(inp.dataset.idx);
          const field = inp.dataset.field;
          let v = inp.value;
          if (field === "price" || field === "qty") v = v.replace(/[^\d.]/g, "");
          rows[idx][field] = v;
        });
      });
      wrap.querySelectorAll(".pv-del").forEach((btn) => {
        btn.addEventListener("click", () => {
          rows.splice(Number(btn.dataset.idx), 1);
          renderPreview();
        });
      });
      wrap.querySelector("#impBack").addEventListener("click", renderEntry);
      wrap.querySelector("#impSave").addEventListener("click", async () => {
        const btn = wrap.querySelector("#impSave");
        const items = rows
          .filter((r) => r.status === "new" || r.status === "exists")
          .map((r) => ({ ...r, qty: Number(r.qty) || 0,
                         price: r.price === "" || r.price === null ? null : Number(r.price) }));
        if (!items.length) return;
        btn.disabled = true; btn.textContent = "保存中…";
        try {
          const r = await api("/import/save-preview", { json: { items } });
          toast(`已保存:新增 ${r.created} 件,合并 ${r.merged} 件` +
                (r.failed.length ? `,失败 ${r.failed.length} 件` : ""),
                r.failed.length ? "warn" : "ok");
          closeModal(); loadDevices(); loadBox();
        } catch (err) {
          toast(`保存失败:${err.message}`, "err");
          btn.disabled = false; btn.textContent = "✔ 确认保存";
        }
      });
    }

    m.querySelector("#impLoad").addEventListener("click", async () => {
      const btn = m.querySelector("#impLoad");
      const codes = m.querySelector("#impCodes").value
        .split(/[\s,;、]+/).filter(Boolean);
      if (!codes.length) return toast("请先录入或粘贴 C 编号", "warn");
      btn.disabled = true; btn.textContent = "加载中…";
      rows = []; invalids = [];
      entry.querySelector("#impCodes").disabled = true;
      // 分块请求(每块 20 个),进度可见;后端再限速 0.35s/个
      const CHUNK = 20;
      try {
        for (let i = 0; i < codes.length; i += CHUNK) {
          const chunk = codes.slice(i, i + CHUNK);
          prog.textContent = `加载中 ${Math.min(i + CHUNK, codes.length)}/${codes.length} …(含接口限速)`;
          const r = await api("/import/preview", { json: { codes: chunk } });
          rows = rows.concat(r.items);
          invalids = invalids.concat(r.invalid);
        }
        prog.textContent = "";
        if (!rows.length && !invalids.length) {
          toast("没有识别到有效编号", "warn");
        } else {
          renderPreview();
        }
      } catch (err) {
        toast(`加载失败:${err.message}`, "err");
      }
      entry.querySelector("#impCodes").disabled = false;
      btn.disabled = false; btn.textContent = "🚀 一键加载";
    });
  });

  /* ---- 价格批量刷新(需求:型号匹配/批量刷新/无匹配与下架标记/摘要) ---- */
  $("btnRefreshPrices").addEventListener("click", () => {
    const selCount = state.selectedIds.size;
    const m = openModal(`
      <h3>⟳ 批量刷新器件价格</h3>
      <label>刷新范围
        <select id="prScope">
          <option value="all">全部器件(单批最多 100,可多次执行)</option>
          ${selCount ? `<option value="selected">仅选中的 ${selCount} 件</option>` : ""}
          <option value="stale">仅未刷新 / 无匹配 / 失败的器件</option>
        </select>
      </label>
      <p class="muted">数据来源:有 C 编号 → 立创接口精确查询(含下架状态);
        无 C 编号 → 按型号在 JLCPCB 库<b>严格相等</b>匹配,命中自动回填 C 编号。
        无匹配与已下架器件会标记在价格列。价格为接口币种(通常 USD)。</p>
      <div class="actions">
        <button id="prCancel">取消</button>
        <button id="prGo" class="primary">开始刷新</button>
      </div>`);
    m.querySelector("#prCancel").addEventListener("click", closeModal);
    m.querySelector("#prGo").addEventListener("click", async () => {
      const scope = m.querySelector("#prScope").value;
      const btn = m.querySelector("#prGo");
      btn.disabled = true; btn.textContent = "刷新中(含限速,请稍候)…";
      try {
        const ids = scope === "selected" ? [...state.selectedIds] : undefined;
        const r = await api("/prices/refresh", { json: { scope, ids } });
        closeModal();
        showPriceSummary(r);
        loadDevices();
      } catch (err) {
        toast(`刷新失败:${err.message}`, "err");
        btn.disabled = false; btn.textContent = "开始刷新";
      }
    });
  });

  function showPriceSummary(r) {
    const li = (items, fmt) => items.length
      ? `<ul class="sum-list">${items.map((x) => `<li>${fmt(x)}</li>`).join("")}</ul>`
      : `<p class="muted" style="margin:2px 0">无</p>`;
    const money = (x) => x.currency === "USD" ? `$${x.price}` :
      x.currency === "CNY" ? `¥${x.price}` : `${x.price} ${x.currency || ""}`;
    const html = `
      <h3>价格刷新结果摘要</h3>
      <p>范围 <b>${esc(r.scope)}</b> · 共处理 <b>${r.requested}</b> 件 · 耗时 ${(r.elapsed_ms / 1000).toFixed(1)}s
        ${r.more_left ? '<span class="muted">(还有更多,可再次执行)</span>' : ""}</p>
      <div class="sum-sec"><h4 style="color:var(--ok)">✔ 已更新价格(${r.updated.length})</h4>
        ${li(r.updated, (x) => `${esc(x.name)}:${money(x)}${x.matched_code ? ` <span class="code-chip">${esc(x.matched_code)}</span>` : ""}`)}</div>
      <div class="sum-sec"><h4 style="color:var(--warn)">⚠ 无匹配(${r.no_match.length})</h4>
        ${li(r.no_match, (x) => `${esc(x.name)}${x.reason ? ` —— ${esc(x.reason)}` : ""}`)}</div>
      <div class="sum-sec"><h4 style="color:var(--err)">⛔ 已下架 / 停售(${r.discontinued.length})</h4>
        ${li(r.discontinued, (x) => `${esc(x.name)}${x.cycle ? ` —— 状态:${esc(x.cycle)}` : ""}`)}</div>
      <div class="sum-sec"><h4 style="color:var(--err)">✘ 失败(${r.failed.length})</h4>
        ${li(r.failed, (x) => `${esc(x.name)} —— ${esc(x.error || "网络异常")}`)}</div>
      <div class="sum-sec"><h4>– 跳过(无 C 编号且无型号,${r.skipped.length})</h4>
        ${li(r.skipped, (x) => esc(x.name))}</div>
      <div class="actions"><button id="sumClose" class="primary">知道了</button></div>`;
    const box = openModal(html);
    box.querySelector("#sumClose").addEventListener("click", closeModal);
  }

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
    const now = Date.now();
    for (let i = 0; i < config.cells; i++) {
      const m = map[i];
      const lit = i === state.status.lit_cell ? " lit" : "";
      const sel = i === state.selectedCell ? " selected" : "";
      const fl = flashMap.get(i);
      const flash = fl && fl.until > now ? ` ${fl.cls}` : "";
      html += `<div class="cell${m ? "" : " empty"}${sel}${lit}${flash}" data-cell="${i}"
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
      const cell = state.selectedCell;
      await api("/box/mapping", { json: { cell, device_id: Number(item.dataset.id) } });
      toast(`已映射到格位 #${cell}`, "ok");
      closeModal();
      await loadBox();
      flashCell(cell, "flash-ok");   // 分配完成态停留 ≥1.5s
    });
  });

  $("btnClearCell").addEventListener("click", async () => {
    if (state.selectedCell < 0) return;
    await api(`/box/mapping/${state.selectedCell}`, { method: "DELETE" });
    toast("已解除映射", "ok");
    await loadBox();
    flashCell(state.selectedCell, "flash-err");   // 解除=橙红闪烁 ≥1.5s
  });
  $("btnLightCell").addEventListener("click", async () => {
    if (state.selectedCell < 0) return;
    state.cellLit = !state.cellLit;
    try {
      await api("/serial/light", { json: { cell: state.selectedCell, on: state.cellLit } });
      toast(state.cellLit ? "已点亮格位(硬件同步)" : "已熄灭", "ok");
    } catch (err) {
      state.cellLit = false;
      toast(err.message, "warn");
    }
    await loadBox();
  });
  $("btnTakeCell").addEventListener("click", async () => {
    const m = currentCellMapping();
    if (!m?.device) return toast("该格位没有映射器件", "warn");
    await api(`/devices/${m.device.id}/adjust-qty`, { json: { delta: -1 } });
    try { await api("/serial/light", { json: { cell: state.selectedCell, on: true } }); } catch {}
    toast(`取料 −1:${m.device.name}`, "ok");
    await loadBox();
    flashCell(state.selectedCell, "flash-ok");    // 完成态停留 ≥1.5s
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
            await loadBox();
            flashCell(data.cell, "flash-ok");   // 硬件动作的可见回执 ≥1.5s
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
      // 完成态驻留按钮 ≥1.5s,再恢复可点击
      btn.textContent = "✔ 已生成";
      setTimeout(() => {
        btn.textContent = "⚙ 生成固件工程";
        btn.disabled = false;
      }, 1600);
      loadPreview();
    } catch (err) {
      $("genResult").innerHTML = `<div class="err">✘ ${esc(err.message)}</div>`;
      toast(`生成失败:${err.message}`, "err");
      btn.disabled = false; btn.textContent = "⚙ 生成固件工程";
    }
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
