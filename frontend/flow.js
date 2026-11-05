// Quy trình Đơn đặt hàng -> Biên bản giao nhận -> Đối chiếu công nợ (màn hình cho kế toán nội bộ).
import {
  html, useState, useEffect, useRef, useMemo, api, money, num, vnDate, today, thisPeriod, periodBounds, prevPeriod,
  Msg, NumInput, useLoad, PartnerSelect, AskReason, Modal, Drawer, Icon, FlowBadge, ENTRY_TYPES, openPrint, download,
  readVietnameseCurrency,
} from "./lib.js";

const go = (page, params) => { location.hash = `#/${page}${params ? "?" + new URLSearchParams(params) : ""}`; };
const n = (v) => Number(v || 0);

function useFlash() {
  const [msg, setMsg] = useState(null);
  const run = async (fn, okText) => {
    try {
      const r = await fn();
      const warn = r && r.warnings && r.warnings.length ? r.warnings.join(" · ") : null;
      setMsg(warn ? { type: "warn", text: (okText ? okText + ". " : "") + warn } : okText ? { type: "ok", text: okText } : null);
      return r;
    } catch (e) { setMsg({ type: "err", text: e.message }); return null; }
  };
  return [msg, setMsg, run];
}

export function CreditBadge({ credit }) {
  if (!credit) return null;
  const label = { green: "Trong hạn mức", yellow: "Gần chạm hạn mức", red: "Vượt hạn mức / nợ quá hạn" }[credit.level];
  return html`<span class=${"credit " + credit.level} title=${(credit.messages || []).join("\n")}>
    <${Icon} name=${credit.level === "green" ? "check" : "alert"} size=${14} />${label}
    ${credit.credit_limit ? html`<span class="muted" style=${{ fontWeight: 500 }}>· ${num(credit.usage_percent)}% hạn mức</span>` : ""}</span>`;
}

function CreditPanel({ credit }) {
  if (!credit) return null;
  return html`<div class="panel">
    <div class="row" style=${{ alignItems: "center", marginBottom: 8 }}><h3 style=${{ margin: 0 }}>Kiểm soát công nợ khách hàng</h3>
      <span class="right"><${CreditBadge} credit=${credit} /></span></div>
    <dl class="kv">
      <dt>Dư nợ hiện tại</dt><dd><b>${money(credit.current_debt)}</b> <span class="muted small">(sổ cái đơn hàng ${money(credit.ledger_debt)} + bảng kê ${money(credit.statement_debt)})</span></dd>
      <dt>Hạn mức</dt><dd>${credit.credit_limit ? money(credit.credit_limit) : "Không giới hạn"} · được nợ ${credit.payment_term_days} ngày</dd>
      <dt>Đơn đã duyệt chưa giao</dt><dd>${money(credit.open_orders_amount)}</dd>
      ${credit.available !== null && html`<dt>Hạn mức còn lại</dt><dd>${money(credit.available)}</dd>`}
      <dt>Nợ quá hạn</dt><dd class=${n(credit.overdue_amount) > 0 ? "" : "muted"}>${money(credit.overdue_amount) || "0"}
        ${credit.overdue.length > 0 && html`<span class="small muted"> – ${credit.overdue.map((o) => `${o.reference_code} quá ${o.days_overdue} ngày`).join("; ")}</span>`}</dd>
    </dl>
    ${(credit.messages || []).map((m, i) => html`<div key=${i} class="msg warn small" style=${{ marginTop: 8, marginBottom: 0 }}><${Icon} name="alert" />${m}</div>`)}
  </div>`;
}

function Progress({ p }) {
  const pct = Math.min(100, n(p.percent));
  return html`<div>
    <div class="row small" style=${{ justifyContent: "space-between", marginBottom: 4 }}>
      <span>Đã giao <b>${p.items_completed}</b> / ${p.items_total} mặt hàng · ${num(p.delivered_qty, 3)} / ${num(p.ordered_qty, 3)} đơn vị</span>
      <b>${num(pct)}%</b></div>
    <div class=${"progress" + (pct >= 100 ? " done" : "")} role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct}>
      <div style=${{ width: pct + "%" }}></div></div></div>`;
}

// ================================================================ ĐƠN ĐẶT HÀNG
export function OrdersPage({ master, params }) {
  if (params.new) return html`<${OrderForm} master=${master} type=${params.type || "SALES"} />`;
  if (params.edit) return html`<${OrderForm} master=${master} id=${Number(params.edit)} />`;
  if (params.id) return html`<${OrderDetail} id=${Number(params.id)} master=${master} />`;
  return html`<${OrderList} master=${master} params=${params} />`;
}

function OrderList({ master, params }) {
  const [status, setStatus] = useState(params.status || "");
  const [type, setType] = useState(params.type || "SALES");
  const [partner, setPartner] = useState(null);
  const q = new URLSearchParams({ order_type: type, ...(status ? { status } : {}), ...(partner ? { partner_id: partner } : {}) });
  const list = useLoad(() => api(`/api/orders?${q}`), [status, type, partner]);
  const partners = type === "SALES" ? master.partners : master.suppliers;
  return html`<div>
    <div class="page-head"><h2>${type === "SALES" ? "Đơn đặt hàng bán (SO)" : "Đơn mua hàng (PO)"}</h2>
      <div class="seg"><button class=${type === "SALES" ? "on" : ""} onClick=${() => setType("SALES")}>Bán</button>
        <button class=${type === "PURCHASE" ? "on" : ""} onClick=${() => setType("PURCHASE")}>Mua</button></div>
      <button class="primary right" onClick=${() => go("orders", { new: 1, type })}><${Icon} name="plus" />Đơn mới</button></div>
    <div class="panel row">
      <${PartnerSelect} partners=${partners} value=${partner} onChange=${setPartner} all label=${type === "SALES" ? "Khách hàng" : "Nhà cung cấp"} />
      <label>Trạng thái<select value=${status} onChange=${(e) => setStatus(e.target.value)}>
        <option value="">— Tất cả —</option>
        ${["DRAFT", "PENDING_APPROVAL", "APPROVED", "PARTIALLY_DELIVERED", "COMPLETED", "CANCELLED"].map((s) =>
          html`<option key=${s} value=${s}>${{ DRAFT: "Nháp", PENDING_APPROVAL: "Chờ duyệt", APPROVED: "Đã duyệt", PARTIALLY_DELIVERED: "Giao từng phần", COMPLETED: "Hoàn tất", CANCELLED: "Đã hủy" }[s]}</option>`)}
      </select></label></div>
    ${list.error && html`<${Msg} msg=${{ type: "err", text: list.error }} />`}
    <div class="panel table-wrap"><table><thead><tr><th>Mã đơn</th><th>Ngày</th><th>Đối tác</th><th>Giá trị</th><th style=${{ width: 180 }}>Tiến độ giao</th><th>Trạng thái</th></tr></thead>
      <tbody>${(list.data || []).map((o) => html`<tr key=${o.id} class="click" onClick=${() => go("orders", { id: o.id })}>
        <td><b>${o.order_code}</b>${o.is_locked ? html` <${Icon} name="lock" size=${13} label="Đã khóa" />` : ""}</td><td>${vnDate(o.order_date)}</td>
        <td>${o.partner_name}</td><td class="r">${money(o.total_amount)}</td>
        <td><div class=${"progress" + (n(o.progress.percent) >= 100 ? " done" : "")}><div style=${{ width: Math.min(100, n(o.progress.percent)) + "%" }}></div></div>
          <span class="small muted">${o.progress.items_completed}/${o.progress.items_total} mặt hàng</span></td>
        <td><${FlowBadge} s=${o.status} /></td></tr>`)}
      ${list.data && list.data.length === 0 && html`<tr><td colspan="6" class="muted c">Chưa có đơn hàng</td></tr>`}</tbody></table></div></div>`;
}

const emptyItem = () => ({ product_id: null, ordered_qty: null, unit_price: null, discount_rate: "0", tax_rate: null });

function lineCalc(it) {
  const base = Math.round(n(it.ordered_qty) * n(it.unit_price));
  const disc = Math.round(base * n(it.discount_rate) / 100);
  const tax = Math.round((base - disc) * n(it.tax_rate) / 100);
  return { base, disc, tax, total: base - disc + tax };
}

function OrderForm({ master, id, type }) {
  const existing = useLoad(() => (id ? api(`/api/orders/${id}`) : Promise.resolve(null)), [id]);
  const [f, setF] = useState(null);
  const [msg, setMsg, run] = useFlash();
  const [credit, setCredit] = useState(null);
  useEffect(() => {
    if (id && existing.data) {
      const o = existing.data;
      setF({ ...o, shipping_fee: o.shipping_fee, items: o.items.map((it) => ({ product_id: it.product_id, ordered_qty: it.ordered_qty,
        unit_price: it.unit_price, discount_rate: it.discount_rate, tax_rate: it.tax_rate })) });
    } else if (!id && !f) {
      setF({ order_type: type, partner_id: null, site_id: null, order_date: today(), expected_delivery_date: "", delivery_address: "",
        shipping_fee: "0", payment_terms: "Thanh toán chuyển khoản trong vòng 30 ngày kể từ ngày giao nhận", notes: "", items: [emptyItem()] });
    }
  }, [existing.data]);
  const locked = f && ["APPROVED", "PARTIALLY_DELIVERED", "COMPLETED"].includes(f.status);
  const totals = useMemo(() => {
    if (!f) return null;
    const rows = f.items.map(lineCalc);
    const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
    return { base: sum("base"), disc: sum("disc"), tax: sum("tax"), total: sum("total") + n(f.shipping_fee) };
  }, [f]);
  useEffect(() => {
    if (!f || !f.partner_id || f.order_type !== "SALES") { setCredit(null); return; }
    const t = setTimeout(() => api(`/api/partners/${f.partner_id}/credit?amount=${totals.total}`).then(setCredit).catch(() => setCredit(null)), 300);
    return () => clearTimeout(t);
  }, [f && f.partner_id, totals && totals.total]);
  if (!f) return html`<div class="muted">Đang tải…</div>`;
  const products = master.products || [];
  const setItem = (i, patch) => setF({ ...f, items: f.items.map((it, k) => (k === i ? { ...it, ...patch } : it)) });
  const pickProduct = (i, pid) => {
    const p = products.find((x) => x.id === pid);
    setItem(i, { product_id: pid, unit_price: p ? (f.order_type === "SALES" ? p.sale_price : p.cost_price) : null,
      tax_rate: p ? String(Math.round(n(p.vat_rate) * 10000) / 100) : null });
  };
  const partners = f.order_type === "SALES" ? master.partners : master.suppliers;
  const save = async (submit) => {
    const body = { partner_id: f.partner_id, order_date: f.order_date, order_type: f.order_type, site_id: f.site_id || null,
      expected_delivery_date: f.expected_delivery_date || null, delivery_address: f.delivery_address || null,
      shipping_fee: f.shipping_fee || "0", payment_terms: f.payment_terms || null, notes: f.notes || null, submit,
      items: f.items.filter((it) => it.product_id && n(it.ordered_qty) > 0).map((it) => ({ product_id: it.product_id,
        ordered_qty: it.ordered_qty, unit_price: it.unit_price, discount_rate: it.discount_rate || "0", tax_rate: it.tax_rate })) };
    const r = await run(() => (id ? api(`/api/orders/${id}`, { method: "PUT", json: body }) : api("/api/orders", { json: body })));
    if (r) go("orders", { id: r.id, ...(r.warnings && r.warnings.length ? { w: 1 } : {}) });
  };
  return html`<div>
    <div class="page-head"><button onClick=${() => history.back()}><${Icon} name="arrowLeft" />Quay lại</button>
      <h2>${id ? `Sửa đơn ${f.order_code}` : f.order_type === "SALES" ? "Đơn đặt hàng bán mới" : "Đơn mua hàng mới"}</h2>
      ${f.status && html`<${FlowBadge} s=${f.status} />`}</div>
    <${Msg} msg=${msg} />
    ${locked && html`<div class="msg warn"><${Icon} name="lock" />Đơn đã duyệt: khóa đơn giá, chiết khấu, thuế, số lượng và khách hàng. Chỉ sửa được thông tin giao hàng & ghi chú.</div>`}
    <div class="panel"><div class="row">
      <${PartnerSelect} partners=${partners} value=${f.partner_id} onChange=${(v) => !locked && setF({ ...f, partner_id: v })} label=${f.order_type === "SALES" ? "Khách hàng *" : "Nhà cung cấp *"} />
      <label>Ngày đặt *<input type="date" value=${f.order_date} disabled=${locked} onInput=${(e) => setF({ ...f, order_date: e.target.value })} /></label>
      <label>Ngày giao dự kiến<input type="date" value=${f.expected_delivery_date || ""} onInput=${(e) => setF({ ...f, expected_delivery_date: e.target.value })} /></label>
      ${credit && html`<div style=${{ alignSelf: "center" }}><${CreditBadge} credit=${credit} /></div>`}</div>
      <div class="row" style=${{ marginTop: 10 }}>
        <label class="grow">Địa điểm giao hàng<input value=${f.delivery_address || ""} onInput=${(e) => setF({ ...f, delivery_address: e.target.value })} /></label>
        <label class="grow">Điều khoản thanh toán<input value=${f.payment_terms || ""} onInput=${(e) => setF({ ...f, payment_terms: e.target.value })} /></label></div>
      ${credit && credit.messages.length > 0 && html`<div class="msg warn small" style=${{ marginTop: 10, marginBottom: 0 }}><${Icon} name="alert" />
        ${credit.messages.join(" · ")} – đơn sẽ chuyển <b> Chờ duyệt</b> (Quản lý / Kế toán trưởng).</div>`}</div>
    <div class="panel table-wrap"><table><thead><tr><th style=${{ width: 32 }}>#</th><th>Mặt hàng</th><th>SL đặt</th><th>Đơn giá (trước thuế)</th>
      <th>CK %</th><th>Thuế %</th><th>Thành tiền</th><th></th></tr></thead>
      <tbody>${f.items.map((it, i) => html`<tr key=${i}>
        <td class="c">${i + 1}</td>
        <td><select value=${it.product_id || ""} disabled=${locked} aria-label="Mặt hàng" onChange=${(e) => pickProduct(i, Number(e.target.value) || null)}>
          <option value="">— Chọn hàng —</option>${products.map((p) => html`<option key=${p.id} value=${p.id}>${p.sku} – ${p.name}</option>`)}</select></td>
        <td><${NumInput} value=${it.ordered_qty} width=${90} onChange=${(v) => !locked && setItem(i, { ordered_qty: v })} /></td>
        <td><${NumInput} value=${it.unit_price} width=${130} onChange=${(v) => !locked && setItem(i, { unit_price: v })} /></td>
        <td><${NumInput} value=${it.discount_rate} width=${60} onChange=${(v) => !locked && setItem(i, { discount_rate: v })} /></td>
        <td><${NumInput} value=${it.tax_rate} width=${60} onChange=${(v) => !locked && setItem(i, { tax_rate: v })} /></td>
        <td class="r">${money(lineCalc(it).total)}</td>
        <td>${!locked && html`<button class="icon-btn danger" aria-label="Xóa dòng" onClick=${() => setF({ ...f, items: f.items.filter((_, k) => k !== i) })}><${Icon} name="x" /></button>`}</td></tr>`)}
      <tr class="total"><td></td><td colspan="5">Tiền hàng ${money(totals.base)} · CK −${money(totals.disc)} · Thuế ${money(totals.tax)} · Phí VC
        <span style=${{ display: "inline-block", marginLeft: 6 }}><${NumInput} value=${f.shipping_fee} width=${110} onChange=${(v) => !locked && setF({ ...f, shipping_fee: v })} /></span></td>
        <td class="r">${money(totals.total)}</td><td></td></tr></tbody></table>
      ${!locked && html`<button style=${{ marginTop: 8 }} onClick=${() => setF({ ...f, items: [...f.items, emptyItem()] })}><${Icon} name="plus" />Thêm dòng</button>`}
      <div class="small muted" style=${{ marginTop: 6 }}>Bằng chữ: ${readVietnameseCurrency(totals.total)}</div></div>
    <div class="panel"><label class="row" style=${{ flexDirection: "column", alignItems: "stretch" }}><span class="small muted">Ghi chú</span>
      <textarea rows="2" value=${f.notes || ""} onInput=${(e) => setF({ ...f, notes: e.target.value })}></textarea></label></div>
    <div class="btn-bar">
      <button onClick=${() => save(false)} disabled=${!f.partner_id}>Lưu ${locked ? "" : "nháp"}</button>
      ${(!f.status || f.status === "DRAFT") && html`<button class="primary" disabled=${!f.partner_id} onClick=${() => save(true)}><${Icon} name="send" />Lưu & gửi duyệt</button>`}</div>
  </div>`;
}

function OrderDetail({ id, master }) {
  const d = useLoad(() => api(`/api/orders/${id}`), [id]);
  const [tab, setTab] = useState("items");
  const [msg, setMsg, run] = useFlash();
  const [ask, setAsk] = useState(null);
  const [dnForm, setDnForm] = useState(false);
  const [pay, setPay] = useState(false);
  const o = d.data;
  if (d.error) return html`<${Msg} msg=${{ type: "err", text: d.error }} />`;
  if (!o) return html`<div class="muted">Đang tải…</div>`;
  const act = async (path, body, text) => { if (await run(() => api(`/api/orders/${id}/${path}`, { json: body || {} }), text)) d.reload(); };
  return html`<div>
    <div class="page-head"><button onClick=${() => go("orders", { type: o.order_type })}><${Icon} name="arrowLeft" />Danh sách</button>
      <h2>${o.order_code}</h2><${FlowBadge} s=${o.status} />
      ${o.is_locked && html`<span class="pill neutral"><${Icon} name="lock" size=${12} />Đã khóa sổ</span>`}
      ${o.credit && html`<${CreditBadge} credit=${o.credit} />`}
      <div class="btn-bar right">
        <button onClick=${() => openPrint("order", o.id)}><${Icon} name="printer" />In đơn</button>
        ${o.status === "DRAFT" && html`<button onClick=${() => go("orders", { edit: o.id })}>Sửa</button>
          <button class="primary" onClick=${() => act("submit", null, "Đã gửi duyệt")}><${Icon} name="send" />Gửi duyệt</button>`}
        ${o.status === "PENDING_APPROVAL" && html`<button class="primary" onClick=${() => act("approve", {}, "Đã duyệt đơn")}><${Icon} name="check" />Duyệt đơn</button>`}
        ${["APPROVED", "PARTIALLY_DELIVERED"].includes(o.status) && !o.is_locked && html`<button onClick=${() => go("orders", { edit: o.id })}>Sửa thông tin giao</button>`}
        ${o.can_create_delivery && html`<button class="primary" onClick=${() => setDnForm(true)}><${Icon} name="truck" />Tạo Biên bản giao nhận</button>`}
        ${o.status === "PARTIALLY_DELIVERED" && html`<button onClick=${() => setAsk({ title: "Đóng phần còn lại chưa giao", path: "close" })}>Đóng phần còn lại</button>`}
        ${["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(o.status) && !o.is_locked && html`<button class="danger" onClick=${() => setAsk({ title: "Hủy đơn hàng", path: "cancel" })}>Hủy đơn</button>`}
      </div></div>
    <${Msg} msg=${msg} />
    ${o.status === "PENDING_APPROVAL" && o.approval_reason && html`<div class="msg warn"><${Icon} name="alert" />Chờ duyệt vì: ${o.approval_reason}</div>`}
    <div class="grid2">
      <div class="panel"><dl class="kv">
        <dt>${o.order_type === "SALES" ? "Khách hàng" : "Nhà cung cấp"}</dt><dd><b>${o.partner_code} – ${o.partner_name}</b></dd>
        <dt>Ngày đặt</dt><dd>${vnDate(o.order_date)}${o.expected_delivery_date ? ` · giao dự kiến ${vnDate(o.expected_delivery_date)}` : ""}</dd>
        <dt>Địa điểm giao</dt><dd>${o.delivery_address || o.site_name || "—"}</dd>
        <dt>Điều khoản</dt><dd>${o.payment_terms || "—"}</dd>
        <dt>Giá trị đơn</dt><dd><b>${money(o.total_amount)}</b> <span class="small muted">(${readVietnameseCurrency(o.total_amount)})</span></dd>
        <dt>Duyệt</dt><dd>${o.approved_by ? `${o.approved_by} · ${vnDate(o.approved_at)}` : "—"}</dd>
        ${o.notes && html`<dt>Ghi chú</dt><dd style=${{ whiteSpace: "pre-line" }}>${o.notes}</dd>`}
      </dl></div>
      <div class="panel"><h3>Tiến độ giao hàng</h3><${Progress} p=${o.progress} />
        <div class="small muted" style=${{ marginTop: 10 }}>Công nợ chỉ ghi nhận khi BBGN được kế toán xác nhận, theo số lượng khách <b>thực nhận</b>.</div>
        ${o.order_type === "SALES" && html`<button style=${{ marginTop: 10 }} onClick=${() => setPay(true)}><${Icon} name="wallet" />Ghi nhận khách thanh toán</button>`}</div>
    </div>
    <div class="tabs" role="tablist">${[["items", "Chi tiết hàng hóa"], ["history", `Lịch sử Giao nhận & Công nợ (${o.delivery_notes.length})`], ["credit", "Hạn mức công nợ"]].map(([k, v]) =>
      html`<button key=${k} role="tab" aria-selected=${tab === k} class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${v}</button>`)}</div>
    ${tab === "items" && html`<div class="panel table-wrap"><table><thead><tr><th>#</th><th>Mã</th><th>Tên hàng</th><th>ĐVT</th><th>SL đặt</th><th>Đã giao</th><th>Còn lại</th>
      <th>Đơn giá</th><th>CK %</th><th>Thuế %</th><th>Thành tiền</th></tr></thead>
      <tbody>${o.items.map((it) => html`<tr key=${it.id} class=${n(it.remaining_qty) > 0 && n(it.delivered_qty) > 0 ? "row-warn" : ""}>
        <td class="c">${it.line_no}</td><td>${it.product_code}</td><td>${it.product_name}</td><td class="c">${it.unit}</td>
        <td class="r">${num(it.ordered_qty, 3)}</td><td class="r">${num(it.delivered_qty, 3)}</td><td class="r"><b>${num(it.remaining_qty, 3)}</b></td>
        <td class="r">${money(it.unit_price)}</td><td class="r">${num(it.discount_rate, 2)}</td><td class="r">${num(it.tax_rate, 2)}</td><td class="r">${money(it.line_total)}</td></tr>`)}
      <tr class="total"><td colspan="10">Cộng (gồm phí vận chuyển ${money(o.shipping_fee)})</td><td class="r">${money(o.total_amount)}</td></tr></tbody></table></div>`}
    ${tab === "history" && html`<div class="panel table-wrap"><h3>Biên bản giao nhận</h3>
      <table><thead><tr><th>Mã BBGN</th><th>Ngày giao</th><th>Người nhận</th><th>Ảnh ký</th><th>Giá trị thực nhận</th><th>Trạng thái</th></tr></thead>
      <tbody>${o.delivery_notes.map((dn) => html`<tr key=${dn.id} class=${"click" + (dn.discrepancy_reason ? " row-warn" : "")} onClick=${() => go("delivery", { id: dn.id })}>
        <td><b>${dn.delivery_code}</b></td><td>${vnDate(dn.delivery_date)}</td><td>${dn.receiver_name || ""}</td>
        <td class="c">${dn.has_signature ? html`<${Icon} name="check" label="Có ảnh" />` : "—"}</td><td class="r">${money(dn.total_accepted_amount)}</td>
        <td><${FlowBadge} s=${dn.status} />${dn.discrepancy_reason ? html`<div class="small muted">${dn.discrepancy_reason}</div>` : ""}</td></tr>`)}
      ${o.delivery_notes.length === 0 && html`<tr><td colspan="6" class="muted c">Chưa có biên bản giao nhận</td></tr>`}</tbody></table>
      <h3>Bút toán công nợ của đơn</h3><${LedgerTable} rows=${o.ledger} /></div>`}
    ${tab === "credit" && html`<${CreditPanel} credit=${o.credit} />`}
    ${ask && html`<${AskReason} title=${ask.title} onClose=${() => setAsk(null)} onOk=${async (reason) => { setAsk(null); await act(ask.path, { reason }, "Đã cập nhật đơn"); }} />`}
    ${dnForm && html`<${CreateDeliveryModal} order=${o} onClose=${() => setDnForm(false)} onDone=${(note) => { setDnForm(false); go("delivery", { id: note.id }); }} />`}
    ${pay && html`<${PaymentModal} partnerId=${o.partner_id} orderId=${o.id} onClose=${() => setPay(false)} onDone=${() => { setPay(false); d.reload(); setMsg({ type: "ok", text: "Đã ghi nhận thanh toán" }); }} />`}
  </div>`;
}

export function LedgerTable({ rows }) {
  return html`<table><thead><tr><th>Ngày</th><th>Chứng từ</th><th>Loại</th><th>Diễn giải</th><th>Tăng</th><th>Giảm</th></tr></thead>
    <tbody>${(rows || []).map((e) => html`<tr key=${e.id}><td>${vnDate(e.transaction_date)}</td><td>${e.reference_code}${e.is_locked ? html` <${Icon} name="lock" size=${12} label="Đã khóa" />` : ""}</td>
      <td>${ENTRY_TYPES[e.entry_type] || e.entry_type}</td><td class="small">${e.description}</td>
      <td class="r">${n(e.debit_amount) ? money(e.debit_amount) : ""}</td><td class="r">${n(e.credit_amount) ? money(e.credit_amount) : ""}</td></tr>`)}
    ${(rows || []).length === 0 && html`<tr><td colspan="6" class="muted c">Chưa phát sinh công nợ</td></tr>`}</tbody></table>`;
}

function CreateDeliveryModal({ order, onClose, onDone }) {
  const pv = useLoad(() => api(`/api/orders/${order.id}/delivery-preview`), [order.id]);
  const [qty, setQty] = useState({});
  const [h, setH] = useState({ delivery_date: today(), deliverer_name: "", deliverer_phone: "", receiver_name: "", warehouse_name: "" });
  const [err, setErr] = useState(null);
  useEffect(() => { if (pv.data) setQty(Object.fromEntries(pv.data.map((r) => [r.order_item_id, r.remaining_qty]))); }, [pv.data]);
  const submit = async () => {
    try {
      const lines = (pv.data || []).filter((r) => n(qty[r.order_item_id]) > 0).map((r) => ({ order_item_id: r.order_item_id, dispatched_qty: qty[r.order_item_id] }));
      const note = await api("/api/delivery-notes", { json: { order_id: order.id, lines, ...h, deliverer_name: h.deliverer_name || null,
        deliverer_phone: h.deliverer_phone || null, receiver_name: h.receiver_name || null, warehouse_name: h.warehouse_name || null } });
      onDone(note);
    } catch (e) { setErr(e.message); }
  };
  const field = (k, l, type = "text") => html`<label class="grow">${l}<input type=${type} value=${h[k]} onInput=${(e) => setH({ ...h, [k]: e.target.value })} /></label>`;
  return html`<div class="modal" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="box wide" role="dialog" aria-modal="true">
    <h3 style=${{ marginTop: 0 }}>Tạo Biên bản giao nhận – căn cứ đơn ${order.order_code}</h3>
    ${err && html`<${Msg} msg=${{ type: "err", text: err }} />`}
    <div class="row">${field("delivery_date", "Ngày giao *", "date")}${field("warehouse_name", "Kho xuất")}${field("deliverer_name", "Người giao")}${field("deliverer_phone", "SĐT người giao")}${field("receiver_name", "Người nhận (dự kiến)")}</div>
    <div class="table-wrap" style=${{ marginTop: 12 }}><table><thead><tr><th>Mặt hàng</th><th>ĐVT</th><th>SL đặt</th><th>Đã nhận</th><th>Đang giao</th><th>Còn lại</th><th>SL xuất lần này</th><th>Đơn giá</th></tr></thead>
      <tbody>${(pv.data || []).map((r) => html`<tr key=${r.order_item_id}>
        <td>${r.product_code} – ${r.product_name}</td><td class="c">${r.unit}</td><td class="r">${num(r.ordered_qty, 3)}</td><td class="r">${num(r.delivered_qty, 3)}</td>
        <td class="r">${num(r.reserved_qty, 3)}</td><td class="r"><b>${num(r.remaining_qty, 3)}</b></td>
        <td><${NumInput} value=${qty[r.order_item_id]} width=${100} onChange=${(v) => setQty({ ...qty, [r.order_item_id]: v })} />
          ${n(qty[r.order_item_id]) > n(r.remaining_qty) && html`<div class="small" style=${{ color: "var(--bad)" }}>Vượt SL còn lại</div>`}</td>
        <td class="r muted">${money(r.unit_price)}</td></tr>`)}</tbody></table></div>
    <div class="small muted" style=${{ marginTop: 6 }}>Đơn giá, chiết khấu, thuế kế thừa từ đơn hàng đã duyệt (không sửa được ở BBGN).</div>
    <div class="btn-bar" style=${{ marginTop: 12 }}><button class="primary" onClick=${submit}><${Icon} name="truck" />Tạo BBGN</button><button onClick=${onClose}>Thôi</button></div>
  </div></div>`;
}

export function PaymentModal({ partnerId, orderId, onClose, onDone }) {
  const [f, setF] = useState({ payment_date: today(), amount: null, fund_type: "BANK", description: "" });
  const [err, setErr] = useState(null);
  const submit = async () => {
    try {
      await api("/api/ledger/payments", { json: { partner_id: partnerId, order_id: orderId || null, payment_date: f.payment_date,
        amount: f.amount, fund_type: f.fund_type, description: f.description || null } });
      onDone();
    } catch (e) { setErr(e.message); }
  };
  return html`<${Modal} title="Ghi nhận thanh toán (phiếu thu)" onClose=${onClose}>
    ${err && html`<${Msg} msg=${{ type: "err", text: err }} />`}
    <div class="row"><label>Ngày<input type="date" value=${f.payment_date} onInput=${(e) => setF({ ...f, payment_date: e.target.value })} /></label>
      <label>Số tiền<${NumInput} value=${f.amount} width=${150} onChange=${(v) => setF({ ...f, amount: v })} /></label>
      <label>Hình thức<select value=${f.fund_type} onChange=${(e) => setF({ ...f, fund_type: e.target.value })}><option value="BANK">Chuyển khoản</option><option value="CASH">Tiền mặt</option></select></label></div>
    <label class="row" style=${{ marginTop: 8, flexDirection: "column", alignItems: "stretch" }}><span class="small muted">Diễn giải</span>
      <input value=${f.description} onInput=${(e) => setF({ ...f, description: e.target.value })} /></label>
    ${f.amount && html`<div class="small muted" style=${{ marginTop: 6 }}>${readVietnameseCurrency(f.amount)}</div>`}
    <div class="btn-bar" style=${{ marginTop: 12 }}><button class="primary" disabled=${!n(f.amount)} onClick=${submit}>Ghi sổ</button><button onClick=${onClose}>Thôi</button></div>
  <//>`;
}

// ================================================================ BIÊN BẢN GIAO NHẬN
export function DeliveryPage({ master, params }) {
  if (params.id) return html`<${DeliveryDetail} id=${Number(params.id)} />`;
  const [status, setStatus] = useState(params.status || "PREPARING,IN_TRANSIT");
  const list = useLoad(() => api(`/api/delivery-notes${status ? "?status=" + status : ""}`), [status]);
  return html`<div>
    <div class="page-head"><h2>Biên bản giao nhận (BBGN)</h2>
      <div class="seg right">${[["PREPARING,IN_TRANSIT", "Đang xử lý"], ["CONFIRMED", "Đã xác nhận"], ["CANCELLED", "Đã hủy"], ["", "Tất cả"]].map(([k, v]) =>
        html`<button key=${k} class=${status === k ? "on" : ""} onClick=${() => setStatus(k)}>${v}</button>`)}</div></div>
    <div class="panel table-wrap"><table><thead><tr><th>Mã BBGN</th><th>Đơn hàng</th><th>Ngày giao</th><th>Đối tác</th><th>Giá trị thực nhận</th><th>Ảnh ký</th><th>Trạng thái</th></tr></thead>
      <tbody>${(list.data || []).map((d) => html`<tr key=${d.id} class=${"click" + (d.has_discrepancy && d.status !== "PREPARING" ? " row-warn" : "")} onClick=${() => go("delivery", { id: d.id })}>
        <td><b>${d.delivery_code}</b></td><td>${d.order_code}</td><td>${vnDate(d.delivery_date)}</td><td>${d.partner_name}</td>
        <td class="r">${money(d.total_accepted_amount)}</td><td class="c">${d.receiver_signature_url ? html`<${Icon} name="check" label="Có ảnh" />` : "—"}</td>
        <td><${FlowBadge} s=${d.status} /></td></tr>`)}
      ${list.data && list.data.length === 0 && html`<tr><td colspan="7" class="muted c">Không có biên bản</td></tr>`}</tbody></table></div></div>`;
}

function SignatureUpload({ note, onUploaded, disabled }) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const input = useRef(null);
  const camera = useRef(null);
  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await api(`/api/delivery-notes/${note.id}/signature`, { method: "POST", body: fd });
      onUploaded(r);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const src = note.signature_endpoint ? `${note.signature_endpoint}?v=${encodeURIComponent(note.receiver_signature_url || "")}` : null;
  const isPdf = (note.receiver_signature_url || "").toLowerCase().endsWith(".pdf");
  return html`<div>
    ${err && html`<${Msg} msg=${{ type: "err", text: err }} />`}
    <div class=${"dropzone" + (over ? " over" : "")} role="button" tabIndex="0" aria-label="Tải ảnh biên bản có chữ ký"
      onClick=${() => !disabled && input.current.click()} onKeyDown=${(e) => (e.key === "Enter" || e.key === " ") && !disabled && input.current.click()}
      onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${(e) => { e.preventDefault(); setOver(false); !disabled && upload(e.dataTransfer.files[0]); }}>
      ${src && !isPdf && html`<img src=${src} alt="Ảnh BBGN có chữ ký" />`}
      ${src && isPdf && html`<div><a href=${src} target="_blank" rel="noopener" onClick=${(e) => e.stopPropagation()}>Xem file PDF đã tải</a></div>`}
      ${busy ? "Đang tải lên…" : disabled ? (src ? "" : "Chưa có ảnh ký nhận") : html`<div><${Icon} name="upload" size=${20} /></div>
        <div><b>Kéo-thả ảnh BBGN đã ký</b> vào đây hoặc bấm để chọn file</div><div class="small">JPG, PNG, WEBP, HEIC hoặc PDF – tối đa 15 MB</div>`}
    </div>
    ${!disabled && html`<button style=${{ marginTop: 8 }} onClick=${() => camera.current.click()}><${Icon} name="camera" />Chụp bằng điện thoại</button>`}
    <input ref=${input} type="file" accept="image/*,application/pdf" hidden onChange=${(e) => upload(e.target.files[0])} />
    <input ref=${camera} type="file" accept="image/*" capture="environment" hidden onChange=${(e) => upload(e.target.files[0])} />
  </div>`;
}

function DeliveryDetail({ id }) {
  const d = useLoad(() => api(`/api/delivery-notes/${id}`), [id]);
  const [rows, setRows] = useState([]);
  const [h, setH] = useState({});
  const [msg, setMsg, run] = useFlash();
  const [ask, setAsk] = useState(false);
  const note = d.data;
  useEffect(() => {
    if (!note) return;
    setRows(note.items.map((it) => ({ ...it })));
    setH({ receiver_name: note.receiver_name || "", discrepancy_reason: note.discrepancy_reason || "", deliverer_name: note.deliverer_name || "" });
  }, [note]);
  if (d.error) return html`<${Msg} msg=${{ type: "err", text: d.error }} />`;
  if (!note) return html`<div class="muted">Đang tải…</div>`;
  const st = note.status;
  const editableQty = st === "PREPARING" && !note.is_locked;
  const editableRecv = st === "IN_TRANSIT" && !note.is_locked;
  const setRow = (i, patch) => setRows(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const lineTotal = (r) => {
    const base = Math.round(n(r.accepted_qty) * n(r.unit_price)); const disc = Math.round(base * n(r.discount_rate) / 100);
    return base - disc + Math.round((base - disc) * n(r.tax_rate) / 100);
  };
  const total = rows.reduce((a, r) => a + lineTotal(r), 0) + n(note.shipping_fee);
  const discrepancy = rows.some((r) => n(r.rejected_qty) > 0 || n(r.accepted_qty) < n(r.dispatched_qty));
  const mismatch = rows.some((r) => Math.abs(n(r.accepted_qty) + n(r.rejected_qty) - n(r.dispatched_qty)) > 1e-9);
  const missingNote = rows.some((r) => (n(r.rejected_qty) > 0 || n(r.accepted_qty) < n(r.dispatched_qty)) && !(r.item_condition_note || "").trim());
  const lines = () => rows.map((r) => ({ order_item_id: r.order_item_id, ...(editableQty ? { dispatched_qty: r.dispatched_qty } : {}),
    ...(editableRecv ? { accepted_qty: r.accepted_qty, rejected_qty: r.rejected_qty } : {}), item_condition_note: r.item_condition_note || "" }));
  const save = async () => {
    const r = await run(() => api(`/api/delivery-notes/${id}`, { method: "PUT", json: { lines: lines(), receiver_name: h.receiver_name || null,
      deliverer_name: h.deliverer_name || null, ...(editableRecv ? { discrepancy_reason: h.discrepancy_reason || null } : {}) } }), "Đã lưu");
    if (r) d.reload();
    return r;
  };
  const dispatch = async () => { if (await save() && await run(() => api(`/api/delivery-notes/${id}/dispatch`, { method: "POST" }), "Đã xuất kho – BBGN đang đi giao")) d.reload(); };
  const confirm = async () => {
    const r = await run(() => api(`/api/delivery-notes/${id}/confirm`, { json: { lines: lines(), discrepancy_reason: h.discrepancy_reason || null,
      receiver_name: h.receiver_name || null } }), "Đã xác nhận BBGN và ghi nhận công nợ");
    if (r) d.reload();
  };
  const rowClass = (r) => (n(r.rejected_qty) > 0 ? "row-bad" : n(r.accepted_qty) < n(r.dispatched_qty) ? "row-warn" : "");
  const canConfirm = editableRecv && note.receiver_signature_url && !mismatch && !(discrepancy && (!h.discrepancy_reason.trim() || missingNote));
  return html`<div>
    <div class="page-head"><button onClick=${() => go("orders", { id: note.order_id })}><${Icon} name="arrowLeft" />Đơn ${note.order_code}</button>
      <h2>BBGN ${note.delivery_code}</h2><${FlowBadge} s=${st} />
      ${note.is_locked && html`<span class="pill neutral"><${Icon} name="lock" size=${12} />Đã khóa sổ</span>`}
      <div class="btn-bar right">
        <button onClick=${() => openPrint("dn", note.id)}><${Icon} name="printer" />In BBGN</button>
        <button onClick=${() => openPrint("dn", note.id, "&hide_price=1")}>In cho kho (ẩn giá)</button>
        ${["PREPARING", "IN_TRANSIT"].includes(st) && !note.is_locked && html`<button class="danger" onClick=${() => setAsk(true)}>Hủy BBGN</button>`}</div></div>
    <${Msg} msg=${msg} />
    <div class="grid2">
      <div class="panel"><dl class="kv">
        <dt>Căn cứ đơn hàng</dt><dd><b>${note.order_code}</b> ngày ${vnDate(note.order_date)}</dd>
        <dt>Đối tác</dt><dd>${note.partner_code} – ${note.partner_name}</dd>
        <dt>Địa điểm giao</dt><dd>${note.delivery_address || "—"}</dd>
        <dt>Ngày giao</dt><dd>${vnDate(note.delivery_date)}</dd>
        <dt>Người giao</dt><dd>${editableQty || editableRecv ? html`<input value=${h.deliverer_name} onInput=${(e) => setH({ ...h, deliverer_name: e.target.value })} />` : (note.deliverer_name || "—")}</dd>
        <dt>Người nhận</dt><dd>${editableQty || editableRecv ? html`<input value=${h.receiver_name} placeholder="Họ tên người ký nhận" onInput=${(e) => setH({ ...h, receiver_name: e.target.value })} />` : (note.receiver_name || "—")}</dd>
        ${note.confirmed_by && html`<dt>Kế toán xác nhận</dt><dd>${note.confirmed_by} · ${vnDate(note.confirmed_at)}</dd>`}
      </dl></div>
      <div class="panel"><h3>Ảnh BBGN có chữ ký người nhận</h3>
        <${SignatureUpload} note=${note} disabled=${!(editableQty || editableRecv)} onUploaded=${() => { d.reload(); setMsg({ type: "ok", text: "Đã tải ảnh ký nhận" }); }} /></div>
    </div>
    <div class="panel table-wrap"><h3>Đối chiếu số lượng</h3>
      <table><thead><tr><th>#</th><th>Mặt hàng</th><th>ĐVT</th><th>SL Đặt</th><th>SL Xuất kho</th><th>SL Thực nhận</th><th>SL Lỗi/Trả lại</th>
        <th>Tình trạng / ghi chú</th><th>Đơn giá</th><th>Thành tiền (thực nhận)</th></tr></thead>
      <tbody>${rows.map((r, i) => html`<tr key=${r.id} class=${rowClass(r)}>
        <td class="c">${r.line_no}</td><td>${r.product_code} – ${r.product_name}</td><td class="c">${r.unit}</td>
        <td class="r">${num(r.ordered_qty_snapshot, 3)}</td>
        <td class="r">${editableQty ? html`<${NumInput} value=${r.dispatched_qty} width=${90} onChange=${(v) => setRow(i, { dispatched_qty: v, accepted_qty: v, rejected_qty: "0" })} />` : num(r.dispatched_qty, 3)}</td>
        <td class="r">${editableRecv ? html`<${NumInput} value=${r.accepted_qty} width=${90} onChange=${(v) => setRow(i, { accepted_qty: v, rejected_qty: String(Math.max(0, n(r.dispatched_qty) - n(v))) })} />` : num(r.accepted_qty, 3)}</td>
        <td class="r">${editableRecv ? html`<${NumInput} value=${r.rejected_qty} width=${90} onChange=${(v) => setRow(i, { rejected_qty: v })} />` : num(r.rejected_qty, 3)}</td>
        <td>${editableRecv || editableQty ? html`<input value=${r.item_condition_note || ""} aria-label="Tình trạng hàng" placeholder=${rowClass(r) ? "Bắt buộc khi có chênh lệch" : ""}
          class=${rowClass(r) && !(r.item_condition_note || "").trim() ? "invalid" : ""} onInput=${(e) => setRow(i, { item_condition_note: e.target.value })} />` : (r.item_condition_note || "")}</td>
        <td class="r muted">${money(r.unit_price)}</td><td class="r">${money(lineTotal(r))}</td></tr>`)}
      ${n(note.shipping_fee) > 0 && html`<tr><td colspan="9">Phí vận chuyển của đơn</td><td class="r">${money(note.shipping_fee)}</td></tr>`}
      <tr class="total"><td colspan="9">Tổng giá trị ghi nhận công nợ (theo SL thực nhận)</td><td class="r">${money(total)}</td></tr></tbody></table>
      <div class="small muted" style=${{ marginTop: 6 }}>Dòng vàng: thực nhận ít hơn xuất kho · Dòng đỏ: có hàng lỗi / trả lại. Đơn giá kế thừa từ đơn hàng đã duyệt.</div>
      ${mismatch && editableRecv && html`<div class="msg err small" style=${{ marginTop: 8 }}><${Icon} name="alert" />SL thực nhận + SL lỗi phải bằng SL xuất kho trên từng dòng.</div>`}
      ${(editableRecv || note.discrepancy_reason) && html`<label class="row" style=${{ marginTop: 10, flexDirection: "column", alignItems: "stretch" }}>
        <span class="small muted">Lý do chênh lệch ${discrepancy ? "(bắt buộc)" : ""}</span>
        <textarea rows="2" disabled=${!editableRecv} class=${discrepancy && !(h.discrepancy_reason || "").trim() ? "invalid" : ""} value=${h.discrepancy_reason}
          onInput=${(e) => setH({ ...h, discrepancy_reason: e.target.value })}></textarea></label>`}
    </div>
    <div class="btn-bar">
      ${(editableQty || editableRecv) && html`<button onClick=${save}>Lưu</button>`}
      ${editableQty && html`<button class="primary" onClick=${dispatch}><${Icon} name="truck" />Xuất kho & đi giao</button>`}
      ${editableRecv && html`<button class="success" disabled=${!canConfirm} onClick=${confirm}
        title=${!note.receiver_signature_url ? "Tải ảnh BBGN có chữ ký trước" : ""}><${Icon} name="check" />Kế toán Xác nhận & Ghi nhận Công nợ</button>`}
      ${editableRecv && !note.receiver_signature_url && html`<span class="small muted">Cần tải ảnh BBGN có chữ ký trước khi xác nhận.</span>`}</div>
    ${ask && html`<${AskReason} title="Hủy biên bản giao nhận" onClose=${() => setAsk(false)} onOk=${async (reason) => {
      setAsk(false); if (await run(() => api(`/api/delivery-notes/${id}/cancel`, { json: { reason } }), "Đã hủy BBGN")) d.reload(); }} />`}
  </div>`;
}

// ================================================================ ĐỐI CHIẾU CÔNG NỢ
function quarterBounds(d) {
  const y = Number(d.slice(0, 4)), q = Math.floor((Number(d.slice(5, 7)) - 1) / 3);
  const from = `${y}-${String(q * 3 + 1).padStart(2, "0")}-01`;
  const last = new Date(y, q * 3 + 3, 0).getDate();
  return [from, `${y}-${String(q * 3 + 3).padStart(2, "0")}-${last}`];
}

export function ReconciliationPage({ master, params }) {
  if (params.id) return html`<${ReconciliationDetail} id=${Number(params.id)} />`;
  const [preset, setPreset] = useState("this");
  const [range, setRange] = useState(periodBounds(thisPeriod()));
  const [partner, setPartner] = useState(params.partner ? Number(params.partner) : null);
  const [activeOnly, setActiveOnly] = useState(true);
  const [msg, setMsg, run] = useFlash();
  useEffect(() => {
    if (preset === "this") setRange(periodBounds(thisPeriod()));
    if (preset === "prev") setRange(periodBounds(prevPeriod(thisPeriod())));
    if (preset === "quarter") setRange(quarterBounds(today()));
  }, [preset]);
  const [from, to] = range;
  const active = useLoad(() => api(`/api/reconciliations/active-partners?date_from=${from}&date_to=${to}`), [from, to]);
  const all = master.allPartners || [];
  const options = activeOnly && active.data ? all.filter((p) => active.data.some((a) => a.partner_id === p.id)) : all;
  const pv = useLoad(() => (partner ? api(`/api/reconciliations/preview?partner_id=${partner}&period_from=${from}&period_to=${to}`) : Promise.resolve(null)), [partner, from, to]);
  const recs = useLoad(() => api(`/api/reconciliations${partner ? "?partner_id=" + partner : ""}`), [partner]);
  const create = async () => {
    const r = await run(() => api("/api/reconciliations", { json: { partner_id: partner, period_from: from, period_to: to } }));
    if (r) go("reconcile", { id: r.id });
  };
  return html`<div>
    <div class="page-head"><h2>Đối chiếu công nợ</h2></div>
    <${Msg} msg=${msg} />
    <div class="panel"><div class="row">
      <label>Kỳ đối chiếu<div class="seg">${[["this", "Tháng này"], ["prev", "Tháng trước"], ["quarter", "Quý này"], ["custom", "Tùy chỉnh"]].map(([k, v]) =>
        html`<button key=${k} class=${preset === k ? "on" : ""} onClick=${() => setPreset(k)}>${v}</button>`)}</div></label>
      <label>Từ ngày<input type="date" value=${from} disabled=${preset !== "custom"} onInput=${(e) => setRange([e.target.value, to])} /></label>
      <label>Đến ngày<input type="date" value=${to} disabled=${preset !== "custom"} onInput=${(e) => setRange([from, e.target.value])} /></label>
      <${PartnerSelect} partners=${options} value=${partner} onChange=${setPartner} label="Khách hàng / NCC" />
      <label class="chk"><input type="checkbox" checked=${activeOnly} onChange=${(e) => setActiveOnly(e.target.checked)} />Chỉ đối tác có phát sinh trong kỳ (${(active.data || []).length})</label></div></div>
    ${pv.data && html`<${RecKpis} r=${pv.data} />
      <div class="panel table-wrap"><div class="row" style=${{ alignItems: "center", marginBottom: 8 }}><h3 style=${{ margin: 0 }}>Xem trước phát sinh trong kỳ</h3>
        <button class="primary right" onClick=${create}><${Icon} name="file" />Lập biên bản đối chiếu</button></div>
        <table><thead><tr><th>Ngày</th><th>Mã ĐĐH</th><th>Chứng từ</th><th>Diễn giải</th><th>Tăng</th><th>Giảm</th><th>Số dư</th></tr></thead>
        <tbody>${pv.data.lines.map((l) => html`<tr key=${l.id}><td>${vnDate(l.transaction_date)}</td><td>${l.order_code || ""}</td><td>${l.reference_code}</td>
          <td class="small">${l.description}</td><td class="r">${n(l.debit_amount) ? money(l.debit_amount) : ""}</td><td class="r">${n(l.credit_amount) ? money(l.credit_amount) : ""}</td><td class="r">${money(l.line_balance)}</td></tr>`)}
        ${pv.data.lines.length === 0 && html`<tr><td colspan="7" class="muted c">Không có phát sinh trong kỳ</td></tr>`}</tbody></table></div>`}
    <div class="panel table-wrap"><h3>Biên bản đã lập</h3>
      <table><thead><tr><th>Mã</th><th>Đối tác</th><th>Kỳ</th><th>Đầu kỳ</th><th>Tăng</th><th>Giảm</th><th>Cuối kỳ</th><th>Trạng thái</th></tr></thead>
      <tbody>${(recs.data || []).map((r) => html`<tr key=${r.id} class="click" onClick=${() => go("reconcile", { id: r.id })}>
        <td><b>${r.reconciliation_code}</b></td><td>${r.partner_name}</td><td>${vnDate(r.period_from)} – ${vnDate(r.period_to)}</td>
        <td class="r">${money(r.opening_balance)}</td><td class="r">${money(r.total_increase)}</td><td class="r">${money(r.total_decrease)}</td>
        <td class="r"><b>${money(r.closing_balance)}</b></td><td><${FlowBadge} s=${r.status} /></td></tr>`)}
      ${recs.data && recs.data.length === 0 && html`<tr><td colspan="8" class="muted c">Chưa có biên bản</td></tr>`}</tbody></table></div></div>`;
}

function RecKpis({ r }) {
  return html`<div class="kpis">
    <div class="kpi accent"><div class="t">Dư nợ đầu kỳ</div><div class="v">${money(r.opening_balance) || "0"}</div></div>
    <div class="kpi warn"><div class="t">Phát sinh tăng (BBGN)</div><div class="v">${money(r.total_increase) || "0"}</div></div>
    <div class="kpi ok"><div class="t">Đã thanh toán / Giảm trừ</div><div class="v">${money(r.total_decrease) || "0"}</div></div>
    <div class="kpi bad"><div class="t">Dư nợ cuối kỳ</div><div class="v">${money(r.closing_balance) || "0"}</div>
      <div class="s">${readVietnameseCurrency(r.closing_balance)}</div></div></div>`;
}

function QuickPreview({ kind, id, onClose }) {
  const d = useLoad(() => api(kind === "order" ? `/api/orders/${id}` : `/api/delivery-notes/${id}`), [kind, id]);
  const x = d.data;
  return html`<${Drawer} title=${x ? (kind === "order" ? `Đơn hàng ${x.order_code}` : `BBGN ${x.delivery_code}`) : "Đang tải…"} onClose=${onClose}>
    ${d.error && html`<${Msg} msg=${{ type: "err", text: d.error }} />`}
    ${x && kind === "order" && html`<div>
      <div class="row" style=${{ marginBottom: 10 }}><${FlowBadge} s=${x.status} /><span class="right"><button onClick=${() => openPrint("order", x.id)}><${Icon} name="printer" />In</button>
        <button onClick=${() => { onClose(); go("orders", { id: x.id }); }}>Mở đầy đủ</button></span></div>
      <dl class="kv"><dt>Ngày đặt</dt><dd>${vnDate(x.order_date)}</dd><dt>Giá trị</dt><dd><b>${money(x.total_amount)}</b></dd>
        <dt>Duyệt</dt><dd>${x.approved_by || "—"}</dd></dl>
      <div style=${{ margin: "10px 0" }}><${Progress} p=${x.progress} /></div>
      <table><thead><tr><th>Hàng</th><th>Đặt</th><th>Đã giao</th><th>Đơn giá</th><th>Thành tiền</th></tr></thead>
        <tbody>${x.items.map((it) => html`<tr key=${it.id}><td>${it.product_code}</td><td class="r">${num(it.ordered_qty, 3)}</td><td class="r">${num(it.delivered_qty, 3)}</td>
          <td class="r">${money(it.unit_price)}</td><td class="r">${money(it.line_total)}</td></tr>`)}</tbody></table>
      <h3>BBGN của đơn</h3>${x.delivery_notes.map((dn) => html`<div key=${dn.id} class="small">${dn.delivery_code} · ${vnDate(dn.delivery_date)} · ${money(dn.total_accepted_amount)} · <${FlowBadge} s=${dn.status} /></div>`)}</div>`}
    ${x && kind === "dn" && html`<div>
      <div class="row" style=${{ marginBottom: 10 }}><${FlowBadge} s=${x.status} /><span class="right"><button onClick=${() => openPrint("dn", x.id)}><${Icon} name="printer" />In</button>
        <button onClick=${() => { onClose(); go("delivery", { id: x.id }); }}>Mở đầy đủ</button></span></div>
      <dl class="kv"><dt>Căn cứ đơn</dt><dd>${x.order_code}</dd><dt>Ngày giao</dt><dd>${vnDate(x.delivery_date)}</dd>
        <dt>Người nhận</dt><dd>${x.receiver_name || "—"}</dd><dt>Xác nhận</dt><dd>${x.confirmed_by || "—"}</dd>
        ${x.discrepancy_reason && html`<dt>Chênh lệch</dt><dd>${x.discrepancy_reason}</dd>`}</dl>
      <table style=${{ marginTop: 10 }}><thead><tr><th>Hàng</th><th>Đặt</th><th>Xuất</th><th>Thực nhận</th><th>Lỗi</th></tr></thead>
        <tbody>${x.items.map((it) => html`<tr key=${it.id} class=${n(it.rejected_qty) > 0 ? "row-bad" : n(it.accepted_qty) < n(it.dispatched_qty) ? "row-warn" : ""}>
          <td>${it.product_code}</td><td class="r">${num(it.ordered_qty_snapshot, 3)}</td><td class="r">${num(it.dispatched_qty, 3)}</td>
          <td class="r">${num(it.accepted_qty, 3)}</td><td class="r">${num(it.rejected_qty, 3)}</td></tr>`)}</tbody></table>
      <h3>Ảnh chữ ký giao nhận</h3>
      ${x.signature_endpoint ? ((x.receiver_signature_url || "").toLowerCase().endsWith(".pdf")
        ? html`<a href=${x.signature_endpoint} target="_blank" rel="noopener">Mở file PDF</a>`
        : html`<img src=${x.signature_endpoint} alt="Ảnh BBGN có chữ ký" style=${{ maxWidth: "100%", borderRadius: 6, border: "1px solid var(--line)" }} />`)
        : html`<div class="muted">Không có ảnh</div>`}</div>`}
  <//>`;
}

function ReconciliationDetail({ id }) {
  const d = useLoad(() => api(`/api/reconciliations/${id}`), [id]);
  const [msg, setMsg, run] = useFlash();
  const [preview, setPreview] = useState(null);
  const [dispute, setDispute] = useState(null);
  const [lock, setLock] = useState(false);
  const [confirmer, setConfirmer] = useState("");
  const [pay, setPay] = useState(false);
  const r = d.data;
  if (d.error) return html`<${Msg} msg=${{ type: "err", text: d.error }} />`;
  if (!r) return html`<div class="muted">Đang tải…</div>`;
  const done = r.status === "RECONCILED";
  const act = async (path, body, text) => { if (await run(() => api(`/api/reconciliations/${id}/${path}`, { json: body || {} }), text)) d.reload(); };
  return html`<div>
    <div class="page-head"><button onClick=${() => go("reconcile")}><${Icon} name="arrowLeft" />Đối chiếu</button>
      <h2>${r.reconciliation_code}</h2><${FlowBadge} s=${r.status} />
      <span class="muted">${r.partner_code} – ${r.partner_name} · ${vnDate(r.period_from)} – ${vnDate(r.period_to)}</span>
      <div class="btn-bar right">
        <button onClick=${() => openPrint("dr", r.id)}><${Icon} name="printer" />In biên bản</button>
        <button onClick=${() => download(`/api/reconciliations/${r.id}/export.xlsx`)}><${Icon} name="download" />Xuất Excel</button>
        ${!done && html`<button onClick=${() => act("refresh", null, "Đã làm mới số liệu")}><${Icon} name="refresh" />Làm mới</button>`}
        ${["DRAFT"].includes(r.status) && html`<button onClick=${() => act("send", null, "Đã đánh dấu gửi đối tác")}><${Icon} name="send" />Đã gửi đối tác</button>`}
        ${!done && r.side === "RECEIVABLE" && html`<button onClick=${() => setPay(true)}><${Icon} name="wallet" />Ghi thanh toán</button>`}
        ${!done && html`<button class="success" disabled=${!r.can_reconcile} title=${r.disputed_count ? "Còn dòng chênh lệch" : ""} onClick=${() => setLock(true)}>
          <${Icon} name="lock" />Xác nhận khớp công nợ & Khóa sổ</button>`}</div></div>
    <${Msg} msg=${msg} />
    ${r.status === "DISPUTED" && html`<div class="msg err"><${Icon} name="alert" />Có ${r.disputed_count} dòng chênh lệch – xử lý (điều chỉnh / làm mới) rồi bấm "Đã xử lý" để mở lại nút khóa sổ.</div>`}
    ${done && html`<div class="msg ok"><${Icon} name="lock" />Đã khớp ngày ${vnDate(r.reconciled_at)}${r.partner_confirmed_by ? ` – đối tác xác nhận: ${r.partner_confirmed_by}` : ""}. Toàn bộ chứng từ trong kỳ đã khóa; điều chỉnh phát sinh sau hạch toán ở kỳ tiếp theo.</div>`}
    <${RecKpis} r=${r} />
    <div class="panel table-wrap"><h3>Bảng kê chi tiết chứng từ</h3>
      <table><thead><tr><th>#</th><th>Ngày</th><th>Mã ĐĐH</th><th>Mã BBGN / Phiếu</th><th>Diễn giải</th><th>Phát sinh tăng</th><th>Phát sinh giảm</th><th>Số dư</th><th></th></tr></thead>
      <tbody><tr class="debt"><td></td><td colspan="6">Số dư đầu kỳ</td><td class="r">${money(r.opening_balance) || "0"}</td><td></td></tr>
      ${r.lines.map((l) => html`<tr key=${l.id} class=${l.is_disputed ? "row-disputed" : ""}>
        <td class="c">${l.line_no}</td><td>${vnDate(l.transaction_date)}</td>
        <td>${l.order_code ? html`<button class="link" onClick=${() => setPreview({ kind: "order", id: l.order_id })}>${l.order_code}</button>` : ""}</td>
        <td>${l.delivery_note_id ? html`<button class="link" onClick=${() => setPreview({ kind: "dn", id: l.delivery_note_id })}>${l.document_code}</button>` : l.document_code}</td>
        <td class="small">${l.description}${l.dispute_comment ? html`<div style=${{ color: l.is_disputed ? "var(--bad)" : "var(--ok)" }}><b>${l.is_disputed ? "Chênh lệch: " : ""}</b>${l.dispute_comment}</div>` : ""}</td>
        <td class="r">${n(l.increase_amount) ? money(l.increase_amount) : ""}</td><td class="r">${n(l.decrease_amount) ? money(l.decrease_amount) : ""}</td>
        <td class="r">${money(l.line_balance)}</td>
        <td>${!done && (l.is_disputed ? html`<button class="link" onClick=${() => act(`lines/${l.id}/resolve`, { note: null }, "Đã xử lý chênh lệch")}>Đã xử lý</button>`
          : html`<button class="link" style=${{ color: "var(--bad)" }} onClick=${() => setDispute(l)}>Báo chênh lệch</button>`)}</td></tr>`)}
      <tr class="total"><td></td><td colspan="4">Cộng phát sinh</td><td class="r">${money(r.total_increase)}</td><td class="r">${money(r.total_decrease)}</td><td></td><td></td></tr>
      <tr class="debt"><td></td><td colspan="6">Số dư cuối kỳ phải thanh toán</td><td class="r">${money(r.closing_balance)}</td><td></td></tr></tbody></table>
      <div class="small muted" style=${{ marginTop: 6 }}>Bấm vào Mã ĐĐH / Mã BBGN để xem nhanh chứng từ gốc và ảnh chữ ký giao nhận.</div></div>
    ${preview && html`<${QuickPreview} kind=${preview.kind} id=${preview.id} onClose=${() => setPreview(null)} />`}
    ${dispute && html`<${AskReason} title=${`Báo chênh lệch: ${dispute.document_code}`} onClose=${() => setDispute(null)}
      onOk=${async (comment) => { const l = dispute; setDispute(null); await act(`lines/${l.id}/dispute`, { comment }, "Đã ghi nhận chênh lệch"); }} />`}
    ${lock && html`<${Modal} title="Xác nhận khớp công nợ & khóa sổ" onClose=${() => setLock(false)}>
      <p class="small">Sau khi khóa: mọi BBGN, phiếu thu/chi, bút toán và đơn hàng của <b>${r.partner_name}</b> trong kỳ ${vnDate(r.period_from)} – ${vnDate(r.period_to)} sẽ không sửa/xóa được.</p>
      <label class="row" style=${{ flexDirection: "column", alignItems: "stretch" }}><span class="small muted">Người xác nhận phía đối tác (họ tên – chức vụ)</span>
        <input value=${confirmer} onInput=${(e) => setConfirmer(e.target.value)} /></label>
      <div class="btn-bar" style=${{ marginTop: 12 }}><button class="success" onClick=${async () => { setLock(false); await act("reconcile", { partner_confirmed_by: confirmer || null }, "Đã khớp & khóa sổ kỳ đối chiếu"); }}>
        <${Icon} name="lock" />Khóa sổ</button><button onClick=${() => setLock(false)}>Thôi</button></div><//>`}
    ${pay && html`<${PaymentModal} partnerId=${r.partner_id} onClose=${() => setPay(false)} onDone=${() => { setPay(false); setMsg({ type: "ok", text: "Đã ghi thanh toán – bấm Làm mới để cập nhật biên bản" }); }} />`}
  </div>`;
}

// Ô "Việc cần xử lý" trên Tổng quan
export function FlowTodo() {
  const d = useLoad(() => Promise.all([
    api("/api/orders?status=PENDING_APPROVAL"), api("/api/delivery-notes?status=PREPARING,IN_TRANSIT"),
    api("/api/reconciliations?status=DRAFT,SENT,DISPUTED"),
  ]), []);
  if (!d.data) return null;
  const [pending, notes, recs] = d.data;
  const disputed = recs.filter((r) => r.status === "DISPUTED").length;
  return html`<div class="kpis">
    <div class="kpi warn click" role="link" tabIndex="0" style=${{ cursor: "pointer" }} onClick=${() => go("orders", { status: "PENDING_APPROVAL" })}>
      <div class="t">Đơn chờ duyệt (vượt hạn mức / nợ quá hạn)</div><div class="v">${pending.length}</div></div>
    <div class="kpi accent" role="link" tabIndex="0" style=${{ cursor: "pointer" }} onClick=${() => go("delivery")}>
      <div class="t">BBGN chờ xác nhận công nợ</div><div class="v">${notes.length}</div>
      <div class="s">${notes.filter((x) => x.status === "IN_TRANSIT").length} đang đi giao</div></div>
    <div class=${"kpi " + (disputed ? "bad" : "ok")} role="link" tabIndex="0" style=${{ cursor: "pointer" }} onClick=${() => go("reconcile")}>
      <div class="t">Biên bản đối chiếu đang mở</div><div class="v">${recs.length}</div>
      <div class="s">${disputed ? `${disputed} biên bản có chênh lệch` : "Không có chênh lệch"}</div></div></div>`;
}
