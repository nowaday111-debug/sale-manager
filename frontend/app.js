// Sale-manager – giao diện web nội bộ (React 18 + htm, không cần build).
import {
  React, html, useState, useEffect, useRef, useMemo, api, money, num, vnDate, today, thisPeriod, periodBounds,
  prevPeriod, Msg, Badge, NumInput, useLoad, PartnerSelect, AskReason, download, ASSET_TYPES, DIRECTIONS, STATUS,
  Icon, getIdentity, setIdentity, ROLES,
} from "./lib.js";
import { OrdersPage, DeliveryPage, ReconciliationPage, FlowTodo } from "./flow.js";

// [mã trang, nhãn, biểu tượng, nhóm]
const PAGES = [
  ["dash", "Tổng quan", "home", "Chung"],
  ["orders", "Đơn đặt hàng", "cart", "Đơn hàng → Công nợ"], ["delivery", "Biên bản giao nhận", "truck", "Đơn hàng → Công nợ"],
  ["reconcile", "Đối chiếu công nợ", "scale", "Đơn hàng → Công nợ"],
  ["slip", "Lập phiếu gas (F2)", "file", "Bán gas theo phiếu"], ["slips", "Danh sách phiếu", "list", "Bán gas theo phiếu"],
  ["ocr", "Đọc phiếu bằng AI", "scan", "Bán gas theo phiếu"], ["statement", "Bảng kê tháng", "sheet", "Bán gas theo phiếu"],
  ["customers", "Khách hàng & NCC", "users", "Danh mục & sổ sách"], ["cash", "Thu – Chi", "wallet", "Danh mục & sổ sách"],
  ["stock", "Kho", "box", "Danh mục & sổ sách"], ["import", "Nhập dữ liệu", "upload", "Danh mục & sổ sách"],
  ["settings", "Cài đặt", "settings", "Danh mục & sổ sách"],
];

function useTheme() {
  const initial = (() => { try { return localStorage.getItem("sm.theme"); } catch (e) { return null; } })()
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("sm.theme", theme); } catch (e) { /* bỏ qua */ }
  }, [theme]);
  return [theme, setTheme];
}

function useHashRoute() {
  const parse = () => {
    const [page, q] = (location.hash.slice(2) || "dash").split("?");
    return { page, params: Object.fromEntries(new URLSearchParams(q || "")) };
  };
  const [r, setR] = useState(parse);
  useEffect(() => { const f = () => setR(parse()); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  return r;
}
export const go = (page, params) => { location.hash = `#/${page}${params ? "?" + new URLSearchParams(params) : ""}`; };

function useMaster() {
  const [m, setM] = useState({ partners: [], suppliers: [], products: [], reload: () => {} });
  const load = () => Promise.all([api("/api/partners"), api("/api/products")]).then(([ps, prods]) => setM({
    partners: ps.filter((p) => p.partner_type !== "SUPPLIER" && p.is_active),
    suppliers: ps.filter((p) => p.partner_type !== "CUSTOMER"),
    allPartners: ps, products: prods, reload: load,
  })).catch(() => {});
  useEffect(() => { load(); }, []);
  return m;
}

// ============================================================ Tổng quan
function Dashboard({ master }) {
  const [period, setPeriod] = useState(thisPeriod());
  const [from, to] = periodBounds(period);
  const d = useLoad(() => api(`/api/reports/dashboard?date_from=${from}&date_to=${to}`), [period]);
  const debts = useLoad(() => api(`/api/debts/summary?side=RECEIVABLE&date_from=${from}&date_to=${to}`), [period]);
  const k = d.data;
  return html`<div>
    <div class="row" style=${{ marginBottom: 12 }}><h2 style=${{ margin: 0 }}>Tổng quan</h2>
      <label class="right">Tháng <input type="month" value=${period} onInput=${(e) => e.target.value && setPeriod(e.target.value)} /></label></div>
    ${d.error && html`<${Msg} msg=${{ type: "err", text: d.error }} />`}
    <${FlowTodo} />
    ${k && html`<div class="kpis">
      <div class="kpi"><div class="t">Doanh thu (gồm VAT)</div><div class="v">${money(k.revenue)}</div></div>
      <div class="kpi"><div class="t">Lãi gộp</div><div class="v">${money(k.gross_profit)}</div></div>
      <div class="kpi"><div class="t">Thực thu (TM ${money(k.cash_received)} + CK ${money(k.bank_received)})</div><div class="v">${money(k.total_received)}</div></div>
      <div class="kpi"><div class="t">Công nợ tăng trong tháng</div><div class="v">${money(k.debt_increase)}</div></div>
    </div>`}
    <div class="panel"><h3 style=${{ marginTop: 0 }}>Công nợ phải thu theo khách</h3>
      <table><thead><tr><th>Khách hàng</th><th>Nợ đầu tháng</th><th>Phát sinh</th><th>Đã trả</th><th>Còn nợ</th></tr></thead>
      <tbody>${(debts.data || []).map((r) => html`<tr key=${r.partner_id} class="click" onClick=${() => go("customers", { id: r.partner_id })}>
        <td>${r.code} – ${r.name}</td><td class="r">${money(r.opening)}</td><td class="r">${money(r.increase)}</td>
        <td class="r">${money(r.decrease)}</td><td class="r"><b>${money(r.closing)}</b></td></tr>`)}
      ${debts.data && html`<tr class="total"><td>Cộng</td>${["opening", "increase", "decrease", "closing"].map((f) =>
        html`<td class="r" key=${f}>${money(debts.data.reduce((a, r) => a + Number(r[f]), 0))}</td>`)}</tr>`}
      </tbody></table></div>
    ${k && k.low_stock.length > 0 && html`<div class="panel"><h3 style=${{ marginTop: 0 }}>Hàng sắp hết</h3>
      ${k.low_stock.map((s) => html`<div key=${s.sku}>${s.sku} – ${s.name}: còn ${num(s.current_stock)} (tối thiểu ${num(s.min_stock)})</div>`)}</div>`}
  </div>`;
}

// ============================================================ Lập phiếu
const emptyLine = () => ({ product_id: null, qty: null, unit_price: null, price_basis: "KG", description: "" });
const emptySlip = () => ({
  partner_id: null, site_id: null, slip_date: today(), slip_no: "", vat_invoice_no: "", staff: "",
  items: [emptyLine()], gas_residual_kg: null, other_deduction: null, deduction_note: "", note: "",
  assets: [], paid_amount: null, paid_fund: "CASH", doc_type: "SALE",
});

function lineAmount(line, prod) {
  const q = Number(line.qty || 0), p = Number(line.unit_price || 0);
  if (prod && prod.kind === "GAS_CYLINDER" && line.price_basis === "KG") return q * Number(prod.capacity_kg || 0) * p;
  return q * p;
}

function SlipForm({ master, params }) {
  const [slip, setSlip] = useState(emptySlip);
  const [editId, setEditId] = useState(null);
  const [sites, setSites] = useState([]);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const firstRef = useRef(null);
  const prodById = useMemo(() => Object.fromEntries(master.products.map((p) => [p.id, p])), [master.products]);
  const set = (patch) => setSlip((s) => ({ ...s, ...patch }));

  useEffect(() => {
    if (!params.id) { setEditId(null); return; }
    api(`/api/slips/${params.id}`).then((s) => {
      setEditId(s.id);
      setSlip({
        ...emptySlip(), ...Object.fromEntries(Object.entries(s).filter(([k]) => k in emptySlip())),
        slip_no: s.slip_no || "", vat_invoice_no: s.vat_invoice_no || "", staff: s.staff || "", note: s.note || "",
        deduction_note: s.deduction_note || "",
        items: s.items.map((i) => ({ product_id: i.product_id, qty: i.qty, unit_price: i.unit_price,
          price_basis: i.price_basis, description: i.description || "" })),
        assets: s.assets || [], paid_amount: null,
      });
    }).catch((e) => setMsg({ type: "err", text: e.message }));
  }, [params.id]);

  useEffect(() => {
    if (!slip.partner_id) { setSites([]); return; }
    api(`/api/partners/${slip.partner_id}/sites`).then(setSites).catch(() => setSites([]));
  }, [slip.partner_id]);

  // tự lấy đơn giá theo bảng giá của khách
  const lookup = async (idx, line, partnerId, onDate) => {
    if (!line.product_id || !partnerId || line.unit_price) return;
    try {
      const r = await api(`/api/prices/lookup?product_id=${line.product_id}&on_date=${onDate}&partner_id=${partnerId}`);
      setSlip((s) => { const items = [...s.items]; if (!items[idx].unit_price) items[idx] = { ...items[idx], unit_price: r.price, price_basis: r.price_basis }; return { ...s, items }; });
    } catch { /* chưa có giá – nhập tay */ }
  };
  const setLine = (idx, patch) => setSlip((s) => {
    const items = s.items.map((l, i) => (i === idx ? { ...l, ...patch } : l));
    if (patch.product_id) {
      const p = prodById[patch.product_id];
      items[idx].price_basis = p && p.kind === "GAS_CYLINDER" ? (p.default_price_basis || "KG") : "UNIT";
      items[idx].unit_price = null;
      setTimeout(() => lookup(idx, items[idx], s.partner_id, s.slip_date));
    }
    return { ...s, items };
  });
  const addLine = () => setSlip((s) => ({ ...s, items: [...s.items, emptyLine()] }));

  const calc = useMemo(() => {
    let gross = 0, kg = 0, resPrice = null;
    for (const l of slip.items) {
      const p = prodById[l.product_id];
      gross += lineAmount(l, p);
      if (p && p.kind === "GAS_CYLINDER") {
        kg += Number(l.qty || 0) * Number(p.capacity_kg || 0);
        if (resPrice === null && l.unit_price) resPrice = l.price_basis === "KG" ? Number(l.unit_price) : Number(l.unit_price) / Number(p.capacity_kg || 1);
      }
    }
    const res = Number(slip.gas_residual_kg || 0);
    const total = Math.round(gross - res * (resPrice || 0) - Number(slip.other_deduction || 0));
    return { gross, kg, net: kg - res, total };
  }, [slip, prodById]);

  const save = async (status) => {
    setBusy(true); setMsg(null);
    try {
      const body = {
        ...slip, status,
        slip_no: slip.slip_no || null, vat_invoice_no: slip.vat_invoice_no || null, staff: slip.staff || null,
        note: slip.note || null, deduction_note: slip.deduction_note || null,
        gas_residual_kg: slip.gas_residual_kg || "0", other_deduction: slip.other_deduction || "0",
        paid_amount: slip.paid_amount || "0",
        items: slip.items.filter((l) => l.product_id && l.qty).map((l) => ({
          product_id: l.product_id, qty: l.qty, unit_price: l.unit_price, price_basis: l.price_basis,
          description: l.description || null })),
        assets: slip.assets.filter((a) => a.asset_type && (a.qty || a.description)).map((a) => ({
          asset_type: a.asset_type, direction: a.direction, qty: a.qty || "0", product_id: a.product_id || null,
          description: a.description || null, deposit_amount: a.deposit_amount || "0" })),
      };
      if (!body.partner_id) throw new Error("Chưa chọn khách hàng");
      if (!body.items.length) throw new Error("Phiếu chưa có dòng hàng");
      const r = editId ? await api(`/api/slips/${editId}`, { method: "PUT", json: body }) : await api("/api/slips", { json: body });
      const w = r.warnings && r.warnings.length ? " – Lưu ý: " + r.warnings.join("; ") : "";
      setMsg({ type: w ? "warn" : "ok", text: `${editId ? "Đã sửa" : "Đã lưu"} phiếu #${r.slip.id} – ${money(r.slip.total)} đ (${STATUS[r.slip.status]})${w}` });
      if (!editId) {
        setSlip((s) => ({ ...emptySlip(), partner_id: s.partner_id, site_id: s.site_id, slip_date: s.slip_date }));
        setTimeout(() => firstRef.current && firstRef.current.focus(), 50);
      }
    } catch (e) { setMsg({ type: "err", text: e.message }); }
    setBusy(false);
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "F2") { e.preventDefault(); go("slip"); setEditId(null); setSlip(emptySlip()); setMsg(null); }
      if (e.key === "F4") { e.preventDefault(); addLine(); }
      if (e.key === "F8") { e.preventDefault(); save("DRAFT"); }
      if (e.key === "F9") { e.preventDefault(); save("POSTED"); }
    };
    addEventListener("keydown", onKey); return () => removeEventListener("keydown", onKey);
  });

  const gasProducts = master.products.filter((p) => p.is_active);
  return html`<div>
    <h2>${editId ? `Sửa phiếu #${editId}` : "Lập phiếu giao hàng / Hóa đơn bán lẻ"}</h2>
    <${Msg} msg=${msg} />
    <div class="panel">
      <div class="row">
        <${PartnerSelect} partners=${master.partners} value=${slip.partner_id} onChange=${(v) => set({ partner_id: v, site_id: null })} />
        <label class="grow">Bếp / điểm giao
          <select value=${slip.site_id || ""} onChange=${(e) => set({ site_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">— Không chọn —</option>
            ${sites.map((s) => html`<option key=${s.id} value=${s.id}>${s.name}</option>`)}</select></label>
        <label>Ngày <input type="date" value=${slip.slip_date} onInput=${(e) => set({ slip_date: e.target.value })} /></label>
        <label>Loại
          <select value=${slip.doc_type} onChange=${(e) => set({ doc_type: e.target.value })}>
            <option value="SALE">Bán hàng</option><option value="RETURN_SALE">Khách trả hàng</option></select></label>
      </div>
      <div class="row" style=${{ marginTop: 8 }}>
        <label>Seri phiếu giấy <input ref=${firstRef} value=${slip.slip_no} onInput=${(e) => set({ slip_no: e.target.value })} style=${{ width: 110 }} /></label>
        <label>Số HĐ GTGT <input value=${slip.vat_invoice_no} onInput=${(e) => set({ vat_invoice_no: e.target.value })} style=${{ width: 100 }} /></label>
        <label class="grow">Nhân viên giao <input value=${slip.staff} onInput=${(e) => set({ staff: e.target.value })} /></label>
      </div>
    </div>

    <div class="panel">
      <table><thead><tr><th style=${{ width: 32 }}>#</th><th>Hàng</th><th>SL</th><th>Tính giá theo</th><th>Đơn giá</th><th>Kg</th><th>Thành tiền</th><th>Ghi chú dòng</th><th></th></tr></thead>
      <tbody>${slip.items.map((l, i) => {
        const p = prodById[l.product_id];
        return html`<tr key=${i}><td class="c">${i + 1}</td>
          <td><select value=${l.product_id || ""} onChange=${(e) => setLine(i, { product_id: Number(e.target.value) || null })}>
            <option value="">— Chọn —</option>${gasProducts.map((x) => html`<option key=${x.id} value=${x.id}>${x.sku} – ${x.name}</option>`)}</select></td>
          <td><${NumInput} value=${l.qty} width=${70} onChange=${(v) => setLine(i, { qty: v })} /></td>
          <td><select value=${l.price_basis} onChange=${(e) => setLine(i, { price_basis: e.target.value })}>
            <option value="KG">kg</option><option value="UNIT">bình/cái</option></select></td>
          <td><${NumInput} value=${l.unit_price} width=${100} onChange=${(v) => setLine(i, { unit_price: v })} /></td>
          <td class="r">${p && p.kind === "GAS_CYLINDER" ? num(Number(l.qty || 0) * Number(p.capacity_kg || 0)) : ""}</td>
          <td class="r">${money(lineAmount(l, p))}</td>
          <td><input value=${l.description} onInput=${(e) => setLine(i, { description: e.target.value })} /></td>
          <td><button class="link danger" onClick=${() => set({ items: slip.items.filter((_, j) => j !== i) })}>✕</button></td></tr>`;
      })}</tbody></table>
      <div class="row" style=${{ marginTop: 8 }}>
        <button onClick=${addLine}>+ Thêm dòng (F4)</button>
        <label>Gas dư (kg) <${NumInput} value=${slip.gas_residual_kg} width=${90} onChange=${(v) => set({ gas_residual_kg: v })} /></label>
        <label>Trừ khác (đ) <${NumInput} value=${slip.other_deduction} width=${110} onChange=${(v) => set({ other_deduction: v })} /></label>
        <label class="grow">Lý do trừ <input value=${slip.deduction_note} onInput=${(e) => set({ deduction_note: e.target.value })} /></label>
      </div>
      <div class="row" style=${{ marginTop: 10, fontSize: 15 }}>
        <span>Tổng kg: <b>${num(calc.kg)}</b></span><span>Thực giao: <b>${num(calc.net)}</b> kg</span>
        <span class="right">Thành tiền (tạm tính): <b style=${{ fontSize: 18 }}>${money(calc.total)}</b> đ</span>
      </div>
    </div>

    <div class="panel">
      <h3 style=${{ marginTop: 0 }}>Vỏ bình mượn / thiết bị đầu tư / quà tặng kèm phiếu</h3>
      ${slip.assets.map((a, i) => html`<div class="row" key=${i} style=${{ marginBottom: 6 }}>
        <select value=${a.asset_type} onChange=${(e) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, asset_type: e.target.value } : x)) })}>
          ${Object.entries(ASSET_TYPES).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}</select>
        <select value=${a.direction} onChange=${(e) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, direction: e.target.value } : x)) })}>
          ${Object.entries(DIRECTIONS).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}</select>
        <${NumInput} value=${a.qty} width=${60} placeholder="SL" onChange=${(v) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, qty: v } : x)) })} />
        ${a.asset_type === "EMPTY_CYLINDER" && html`<select value=${a.product_id || ""} onChange=${(e) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, product_id: Number(e.target.value) || null } : x)) })}>
          <option value="">Loại vỏ</option>${master.products.filter((p) => p.kind === "GAS_CYLINDER").map((p) => html`<option key=${p.id} value=${p.id}>${p.sku}</option>`)}</select>`}
        <input class="grow" placeholder="Mô tả (VD: Mượn 02 vỏ pc48)" value=${a.description || ""} onInput=${(e) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)) })} />
        <${NumInput} value=${a.deposit_amount} width=${100} placeholder="Tiền cược" onChange=${(v) => set({ assets: slip.assets.map((x, j) => (j === i ? { ...x, deposit_amount: v } : x)) })} />
        <button class="link danger" onClick=${() => set({ assets: slip.assets.filter((_, j) => j !== i) })}>✕</button></div>`)}
      <button onClick=${() => set({ assets: [...slip.assets, { asset_type: "EMPTY_CYLINDER", direction: "LEND", qty: null, description: "" }] })}>+ Thêm vỏ/thiết bị</button>
    </div>

    <div class="panel"><div class="row">
      <label class="grow">Ghi chú (hiện ở cột Ghi chú bảng kê) <input value=${slip.note} onInput=${(e) => set({ note: e.target.value })} /></label>
      ${!editId && html`<label>Khách trả ngay (đ) <${NumInput} value=${slip.paid_amount} width=${120} onChange=${(v) => set({ paid_amount: v })} /></label>
      <label>Quỹ <select value=${slip.paid_fund} onChange=${(e) => set({ paid_fund: e.target.value })}><option value="CASH">Tiền mặt</option><option value="BANK">Chuyển khoản</option></select></label>`}
    </div>
    <div class="row" style=${{ marginTop: 12 }}>
      <button class="primary" disabled=${busy} onClick=${() => save("POSTED")}>Ghi sổ (F9)</button>
      ${!editId && html`<button disabled=${busy} onClick=${() => save("DRAFT")}>Lưu nháp (F8)</button>`}
      <button onClick=${() => { go("slip"); setEditId(null); setSlip(emptySlip()); setMsg(null); }}>Phiếu mới (F2)</button>
    </div></div>
  </div>`;
}

// ============================================================ Danh sách phiếu
function SlipList({ master }) {
  const [f, setF] = useState({ partner_id: null, period: thisPeriod(), status: "" });
  const [from, to] = periodBounds(f.period);
  const [ask, setAsk] = useState(null);
  const [msg, setMsg] = useState(null);
  const q = new URLSearchParams({ date_from: from, date_to: to, ...(f.partner_id ? { partner_id: f.partner_id } : {}), ...(f.status ? { status: f.status } : {}) });
  const list = useLoad(() => api(`/api/slips?${q}`), [q.toString()]);
  const act = async (fn) => { setMsg(null); try { await fn(); list.reload(); } catch (e) { setMsg({ type: "err", text: e.message }); } };
  const rows = list.data || [];
  return html`<div><h2>Danh sách phiếu</h2><${Msg} msg=${msg} />
    <div class="panel row">
      <${PartnerSelect} partners=${master.partners} value=${f.partner_id} all onChange=${(v) => setF({ ...f, partner_id: v })} />
      <label>Tháng <input type="month" value=${f.period} onInput=${(e) => e.target.value && setF({ ...f, period: e.target.value })} /></label>
      <label>Trạng thái <select value=${f.status} onChange=${(e) => setF({ ...f, status: e.target.value })}>
        <option value="">Tất cả</option><option value="POSTED">Đã ghi sổ</option><option value="DRAFT">Nháp</option><option value="CANCELLED">Đã hủy</option></select></label>
    </div>
    <div class="panel"><table><thead><tr><th>Ngày</th><th>Seri</th><th>Số HĐ</th><th>Khách / bếp</th><th>Hàng</th><th>Gas dư</th><th>Thành tiền</th><th>Trạng thái</th><th></th></tr></thead>
      <tbody>${rows.map((s) => html`<tr key=${s.id}>
        <td>${vnDate(s.slip_date)}</td><td>${s.slip_no}</td><td>${s.vat_invoice_no}</td>
        <td>${s.partner_name}<div class="small muted">${s.site_name}</div></td>
        <td>${s.items.map((i) => `${num(i.qty)} ${i.sku}`).join(", ")}</td><td class="r">${num(s.gas_residual_kg)}</td>
        <td class="r">${s.doc_type === "RETURN_SALE" ? "−" : ""}${money(s.total)}</td><td><${Badge} s=${s.status} /></td>
        <td style=${{ whiteSpace: "nowrap" }}>
          ${s.status !== "CANCELLED" && html`<button class="link" onClick=${() => go("slip", { id: s.id })}>Sửa</button>`}
          ${s.status === "DRAFT" && html`<button class="link" onClick=${() => act(() => api(`/api/slips/${s.id}/approve`, { method: "POST" }))}>Duyệt</button>`}
          ${s.status !== "CANCELLED" && html`<button class="link danger" onClick=${() => setAsk(s)}>Hủy</button>`}
          ${s.image_path && html`<a class="small" href=${`/api/ocr/jobs/${(JSON.parse(s.ocr_json || "{}") || {}).ocr_job_id}/image`} target="_blank">Ảnh</a>`}
        </td></tr>`)}
      <tr class="total"><td colspan="6">${rows.length} phiếu</td><td class="r">${money(rows.filter((s) => s.status === "POSTED").reduce((a, s) => a + (s.doc_type === "RETURN_SALE" ? -1 : 1) * Number(s.total), 0))}</td><td colspan="2"></td></tr>
      </tbody></table></div>
    ${ask && html`<${AskReason} title=${`Hủy phiếu #${ask.id}`} onClose=${() => setAsk(null)}
      onOk=${(reason) => { const id = ask.id; setAsk(null); act(() => api(`/api/slips/${id}/cancel`, { json: { reason } })); }} />`}
  </div>`;
}

// ============================================================ Đọc phiếu AI
function OcrPage({ master }) {
  const jobs = useLoad(() => api("/api/ocr/jobs"), []);
  const prov = useLoad(() => api("/api/ocr/providers"), []);
  const [sel, setSel] = useState(null);
  const [uploading, setUploading] = useState(0);
  const [msg, setMsg] = useState(null);
  const upload = async (files) => {
    setMsg(null);
    for (const f of files) {
      setUploading((n) => n + 1);
      try {
        const fd = new FormData(); fd.append("file", f);
        const j = await api("/api/ocr/jobs", { method: "POST", body: fd });
        if (j.status === "FAILED") setMsg({ type: "err", text: `${f.name}: ${j.error}` });
      } catch (e) { setMsg({ type: "err", text: `${f.name}: ${e.message}` }); }
      setUploading((n) => n - 1);
      jobs.reload();
    }
  };
  const pending = (jobs.data || []).filter((j) => j.status === "PENDING_REVIEW");
  return html`<div><h2>Đọc phiếu "Hóa đơn bán lẻ" bằng AI</h2><${Msg} msg=${msg} />
    ${prov.data && prov.data.configured.length === 0 && html`<div class="msg warn">Chưa có khóa API cho AI đọc ảnh. Điền <b>DEEPSEEK_API_KEY</b> (hoặc khóa hãng khác) vào file backend/.env rồi khởi động lại.</div>`}
    <div class="panel row">
      <label class="grow">Chọn ảnh phiếu (chọn được nhiều ảnh, .jpg .png .pdf)
        <input type="file" multiple accept="image/*,.pdf" onChange=${(e) => upload([...e.target.files])} /></label>
      ${uploading > 0 && html`<span>Đang đọc ${uploading} ảnh…</span>`}
      <span class="right muted">${pending.length} phiếu chờ duyệt</span>
    </div>
    ${sel ? html`<${OcrReview} key=${sel} id=${sel} master=${master} onDone=${(t) => { setMsg(t); setSel(null); jobs.reload(); }} onBack=${() => setSel(null)} />`
      : html`<div class="panel"><table><thead><tr><th>#</th><th>Thời gian</th><th>Ảnh</th><th>Hãng AI</th><th>Seri</th><th>Bếp</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead>
      <tbody>${(jobs.data || []).map((j) => {
        const p = j.result && j.result.proposal;
        return html`<tr key=${j.id} class="click" onClick=${() => setSel(j.id)}><td>${j.id}</td><td>${(j.created_at || "").replace("T", " ").slice(0, 16)}</td>
          <td>${j.filename}</td><td>${j.provider || ""}</td><td>${p && p.slip_no}</td><td>${p && p.site_name}</td>
          <td><${Badge} s=${j.status} />${j.needs_review && j.status === "PENDING_REVIEW" ? html` <span class="badge DRAFT">cần xem kỹ</span>` : ""}</td><td class="small">${j.error}</td></tr>`;
      })}</tbody></table></div>`}
  </div>`;
}

function OcrReview({ id, master, onDone, onBack }) {
  const [job, setJob] = useState(null);
  const [p, setP] = useState(null);
  const [sites, setSites] = useState([]);
  const [err, setErr] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [ask, setAsk] = useState(false);
  const [alias, setAlias] = useState(true);
  useEffect(() => { api(`/api/ocr/jobs/${id}`).then((j) => { setJob(j); setP(j.result ? { ...j.result.proposal } : null); }).catch((e) => setErr(e.message)); }, [id]);
  useEffect(() => { if (p && p.partner_id) api(`/api/partners/${p.partner_id}/sites`).then(setSites); }, [p && p.partner_id]);
  if (err) return html`<${Msg} msg=${{ type: "err", text: err }} />`;
  if (!job) return html`<div>Đang tải…</div>`;
  const set = (patch) => setP((x) => ({ ...x, ...patch }));
  const setItem = (i, patch) => set({ items: p.items.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const editable = job.status === "PENDING_REVIEW";
  const norm = job.result && job.result.normalized;
  const apply = async () => {
    setErr(null);
    try {
      const raw = norm ? [norm.customer_text, norm.address_text].filter(Boolean).join(" ").trim() : "";
      if (alias && p.site_id && raw && !p.site_name_matched) {
        await api(`/api/sites/${p.site_id}/aliases`, { json: { alias: raw } }).catch(() => {});
      }
      const payload = { ...p, items: p.items.filter((i) => i.sku && i.qty) };
      const j = await api(`/api/ocr/jobs/${id}/apply`, { json: payload });
      onDone({ type: "ok", text: `Đã ghi sổ phiếu ${p.slip_no || ""} (lượt đọc #${j.id}${j.slip_id ? `, phiếu #${j.slip_id}` : ""})` });
    } catch (e) { setErr(e.message); }
  };
  const skus = master.products.map((x) => x.sku);
  return html`<div>
    <div class="row" style=${{ marginBottom: 10 }}><button onClick=${onBack}>← Danh sách</button>
      <b>Lượt đọc #${job.id}</b> <${Badge} s=${job.status} /> <span class="muted small">${job.provider} ${job.model} · ${num(job.seconds)} giây</span></div>
    <${Msg} msg=${err && { type: "err", text: err }} />
    <div class="split">
      <div class="imgbox"><div class="row" style=${{ padding: 6, background: "#333" }}>
        <button onClick=${() => setZoom((z) => Math.max(0.5, z - 0.25))}>−</button><button onClick=${() => setZoom((z) => z + 0.25)}>+</button></div>
        <img src=${`/api/ocr/jobs/${id}/image`} style=${{ width: `${zoom * 100}%` }} /></div>
      <div>${!p ? html`<div class="panel">${job.error || "Không có kết quả"}</div>` : html`
        ${(p.flags || []).length > 0 && html`<div class="panel">${p.flags.map((f, i) => html`<div key=${i} class=${"msg " + (f.level === "warning" ? "warn" : "ok")} style=${{ marginBottom: 4 }}>${f.message}</div>`)}</div>`}
        <div class="panel">
          ${norm && html`<div class="small muted" style=${{ marginBottom: 6 }}>Trên phiếu: “${norm.customer_text || ""}” / “${norm.address_text || ""}”</div>`}
          <div class="row">
            <${PartnerSelect} partners=${master.partners} value=${p.partner_id} onChange=${(v) => set({ partner_id: v, site_id: null })} />
            <label class="grow">Bếp <select disabled=${!editable} value=${p.site_id || ""} onChange=${(e) => set({ site_id: Number(e.target.value) || null })}>
              <option value="">— Chọn —</option>${sites.map((s) => html`<option key=${s.id} value=${s.id}>${s.name}</option>`)}</select></label>
          </div>
          <div class="row" style=${{ marginTop: 8 }}>
            <label>Ngày <input type="date" value=${p.slip_date || ""} onInput=${(e) => set({ slip_date: e.target.value })} /></label>
            <label>Seri <input value=${p.slip_no || ""} style=${{ width: 100 }} onInput=${(e) => set({ slip_no: e.target.value })} /></label>
            <label>Số HĐ GTGT <input value=${p.vat_invoice_no || ""} style=${{ width: 90 }} onInput=${(e) => set({ vat_invoice_no: e.target.value })} /></label>
            <label>Gas dư (kg) <${NumInput} value=${p.gas_residual_kg} width=${80} onChange=${(v) => set({ gas_residual_kg: v })} /></label>
          </div>
          <table style=${{ marginTop: 10 }}><thead><tr><th>Mã</th><th>SL</th><th>Theo</th><th>Đơn giá</th><th>Mô tả</th><th></th></tr></thead>
            <tbody>${(p.items || []).map((it, i) => html`<tr key=${i}>
              <td><select value=${it.sku || ""} onChange=${(e) => setItem(i, { sku: e.target.value })}><option value="">—</option>${skus.map((s) => html`<option key=${s}>${s}</option>`)}</select></td>
              <td><${NumInput} value=${it.qty} width=${60} onChange=${(v) => setItem(i, { qty: v })} /></td>
              <td><select value=${it.price_basis || "KG"} onChange=${(e) => setItem(i, { price_basis: e.target.value })}><option value="KG">kg</option><option value="UNIT">bình</option></select></td>
              <td><${NumInput} value=${it.unit_price} width=${90} onChange=${(v) => setItem(i, { unit_price: v })} /></td>
              <td><input value=${it.description || ""} onInput=${(e) => setItem(i, { description: e.target.value })} /></td>
              <td><button class="link danger" onClick=${() => set({ items: p.items.filter((_, j) => j !== i) })}>✕</button></td></tr>`)}</tbody></table>
          <button style=${{ marginTop: 6 }} onClick=${() => set({ items: [...(p.items || []), { sku: skus[0], qty: null, unit_price: null, price_basis: "KG" }] })}>+ Dòng</button>
          <div class="small" style=${{ marginTop: 8 }}>Tổng tiền viết trên phiếu: <b>${money(p.total_written)}</b> · Phần mềm tính: <b>${money(p.total_calc)}</b></div>
          <h3>Vỏ / thiết bị</h3>
          ${(p.assets || []).map((a, i) => html`<div class="row small" key=${i}>${ASSET_TYPES[a.asset_type]} · ${DIRECTIONS[a.direction]} · ${num(a.qty)} ${a.sku || ""} — ${a.description}
            <button class="link danger" onClick=${() => set({ assets: p.assets.filter((_, j) => j !== i) })}>bỏ</button></div>`)}
          <label class="row" style=${{ marginTop: 8 }}><input class="grow" value=${p.note || ""} placeholder="Ghi chú" onInput=${(e) => set({ note: e.target.value })} /></label>
          ${editable && html`<label class="small chk"><input type="checkbox" checked=${alias} onChange=${(e) => setAlias(e.target.checked)} /> Nhớ chữ viết tay này là tên gọi khác của bếp đã chọn</label>`}
          ${editable && html`<div class="row" style=${{ marginTop: 12 }}><button class="primary" onClick=${apply}>Ghi sổ phiếu</button>
            <button class="danger" onClick=${() => setAsk(true)}>Từ chối</button></div>`}
        </div>`}</div>
    </div>
    ${ask && html`<${AskReason} title="Từ chối lượt đọc" onClose=${() => setAsk(false)} onOk=${async (reason) => {
      setAsk(false); try { await api(`/api/ocr/jobs/${id}/reject`, { json: { reason } }); onDone({ type: "ok", text: "Đã từ chối" }); } catch (e) { setErr(e.message); } }} />`}
  </div>`;
}

// ============================================================ Khách hàng
function Customers({ master, params }) {
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const id = params.id ? Number(params.id) : null;
  if (id) return html`<${CustomerDetail} id=${id} master=${master} />`;
  const list = (master.allPartners || []).filter((p) => !q || `${p.code} ${p.name} ${p.tax_code || ""}`.toLowerCase().includes(q.toLowerCase()));
  return html`<div><h2>Khách hàng & nhà cung cấp</h2>
    <div class="panel row"><input class="grow" placeholder="Tìm theo mã, tên, MST…" value=${q} onInput=${(e) => setQ(e.target.value)} />
      <button class="primary" onClick=${() => setAdding(true)}>+ Thêm</button></div>
    ${adding && html`<${PartnerForm} onDone=${() => { setAdding(false); master.reload(); }} />`}
    <div class="panel"><table><thead><tr><th>Mã</th><th>Tên</th><th>MST</th><th>Điện thoại</th><th>Loại</th></tr></thead>
      <tbody>${list.map((p) => html`<tr key=${p.id} class="click" onClick=${() => go("customers", { id: p.id })}>
        <td>${p.code}</td><td>${p.name}</td><td>${p.tax_code}</td><td>${p.phone}</td><td>${{ CUSTOMER: "Khách", SUPPLIER: "NCC", BOTH: "Cả hai" }[p.partner_type]}</td></tr>`)}</tbody></table></div>
  </div>`;
}

function PartnerForm({ partner, onDone }) {
  const [f, setF] = useState(partner || { code: "", name: "", partner_type: "CUSTOMER", tax_code: "", address: "", phone: "", contact_name: "", workbook_path: "", credit_limit: null,
    payment_term_days: 30, email: "", representative: "", representative_title: "" });
  const [err, setErr] = useState(null);
  const field = (k, label, w) => html`<label class=${w ? "" : "grow"}>${label}<input value=${f[k] || ""} style=${w ? { width: w } : {}} onInput=${(e) => setF({ ...f, [k]: e.target.value })} /></label>`;
  const save = async () => {
    try {
      const body = { code: f.code, name: f.name, partner_type: f.partner_type, tax_code: f.tax_code || null, address: f.address || null,
        phone: f.phone || null, contact_name: f.contact_name || null, workbook_path: f.workbook_path || null, credit_limit: f.credit_limit || null,
        payment_term_days: Number(f.payment_term_days || 0), email: f.email || null, representative: f.representative || null,
        representative_title: f.representative_title || null };
      if (partner) await api(`/api/partners/${partner.id}`, { method: "PATCH", json: body }); else await api("/api/partners", { json: body });
      onDone();
    } catch (e) { setErr(e.message); }
  };
  return html`<div class="panel"><${Msg} msg=${err && { type: "err", text: err }} />
    <div class="row">${field("code", "Mã", 100)}${field("name", "Tên")}
      <label>Loại <select value=${f.partner_type} onChange=${(e) => setF({ ...f, partner_type: e.target.value })}>
        <option value="CUSTOMER">Khách hàng</option><option value="SUPPLIER">Nhà cung cấp</option><option value="BOTH">Cả hai</option></select></label></div>
    <div class="row" style=${{ marginTop: 8 }}>${field("tax_code", "MST", 130)}${field("address", "Địa chỉ")}</div>
    <div class="row" style=${{ marginTop: 8 }}>${field("contact_name", "Người liên hệ", 160)}${field("phone", "Điện thoại", 180)}
      <label>Hạn mức nợ (để trống = không giới hạn)<${NumInput} value=${f.credit_limit} width=${150} onChange=${(v) => setF({ ...f, credit_limit: v })} /></label>
      <label>Số ngày được nợ<${NumInput} value=${f.payment_term_days} width=${90} onChange=${(v) => setF({ ...f, payment_term_days: v })} /></label></div>
    <div class="row" style=${{ marginTop: 8 }}>${field("email", "Email", 200)}${field("representative", "Người đại diện (ký BB đối chiếu)")}${field("representative_title", "Chức vụ", 160)}</div>
    <div class="row" style=${{ marginTop: 8 }}>${field("workbook_path", "File Excel bảng kê của khách (đường dẫn đầy đủ, VD: D:\\BangKe\\Stargate.xlsx)")}</div>
    <div class="row" style=${{ marginTop: 10 }}><button class="primary" onClick=${save}>Lưu</button><button onClick=${() => onDone()}>Thôi</button></div></div>`;
}

function CustomerDetail({ id, master }) {
  const [period, setPeriod] = useState(thisPeriod());
  const ov = useLoad(() => api(`/api/reports/customers/${id}/overview?period=${period}&months=12`), [id, period]);
  const partner = useLoad(() => api(`/api/partners/${id}`), [id]);
  const [edit, setEdit] = useState(false);
  const [tab, setTab] = useState("overview");
  const d = ov.data;
  const maxAmt = d ? Math.max(1, ...d.monthly.map((m) => Number(m.amount))) : 1;
  return html`<div>
    <div class="row" style=${{ marginBottom: 10 }}><button onClick=${() => go("customers")}>← Danh sách</button>
      <h2 style=${{ margin: 0 }}>${d ? d.partner.name : "…"}</h2>
      <label class="right">Đến tháng <input type="month" value=${period} onInput=${(e) => e.target.value && setPeriod(e.target.value)} /></label></div>
    ${ov.error && html`<${Msg} msg=${{ type: "err", text: ov.error }} />`}
    <div class="tabs">${[["overview", "Tổng quan"], ["sites", "Bếp & tên gọi khác"], ["prices", "Bảng giá"], ["info", "Thông tin"]].map(([k, v]) =>
      html`<button key=${k} class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${v}</button>`)}
      <button class="right" onClick=${() => go("reconcile", { partner: id })}>Đối chiếu công nợ →</button>
      <button onClick=${() => go("statement", { partner: id, period })}>Bảng kê tháng này →</button></div>
    ${tab === "overview" && d && html`<div>
      <div class="kpis">
        <div class="kpi"><div class="t">Còn nợ đến ${vnDate(d.as_of)}</div><div class="v">${money(d.balance)}</div></div>
        <div class="kpi"><div class="t">Vỏ / thiết bị đang mượn</div><div class="v" style=${{ fontSize: 15 }}>${d.total_loans || "—"}</div></div>
        <div class="kpi"><div class="t">Hạn mức nợ</div><div class="v">${d.partner.credit_limit ? money(d.partner.credit_limit) : "—"}</div>
          ${d.partner.credit_limit && Number(d.balance) > Number(d.partner.credit_limit) ? html`<div class="msg err small" style=${{ margin: "6px 0 0" }}>Vượt hạn mức</div>` : ""}</div>
      </div>
      <div class="panel"><h3 style=${{ marginTop: 0 }}>Nợ còn lại theo tháng phát sinh</h3>
        ${d.outstanding_by_period.length === 0 ? html`<div class="muted">Không còn nợ</div>` :
          html`<table><tbody>${d.outstanding_by_period.map((o) => html`<tr key=${o.period}><td>Tháng ${o.period.slice(5)}/${o.period.slice(0, 4)}</td><td class="r">${money(o.amount)}</td></tr>`)}</tbody></table>`}</div>
      <div class="panel"><h3 style=${{ marginTop: 0 }}>12 tháng gần nhất</h3>
        <table><thead><tr><th>Tháng</th><th>Số phiếu</th><th>Kg</th><th>Tiền hàng</th><th style=${{ width: "22%" }}></th><th>Đã trả</th><th>Nợ cuối tháng</th></tr></thead>
        <tbody>${d.monthly.map((m) => html`<tr key=${m.period} class="click" onClick=${() => go("statement", { partner: id, period: m.period })}>
          <td>${m.period.slice(5)}/${m.period.slice(0, 4)}</td><td class="r">${m.slips}</td><td class="r">${num(m.kg)}</td><td class="r">${money(m.amount)}</td>
          <td><div class="bar" style=${{ width: `${(100 * Number(m.amount)) / maxAmt}%` }}></div></td>
          <td class="r">${money(m.paid)}</td><td class="r">${money(m.closing_debt)}</td></tr>`)}</tbody></table></div>
      <div class="panel"><h3 style=${{ marginTop: 0 }}>Theo bếp (12 tháng)</h3>
        <table><thead><tr><th>Bếp</th><th>Số phiếu</th><th>Kg</th><th>Tiền</th><th>Giao gần nhất</th><th>Đang mượn</th></tr></thead>
        <tbody>${d.sites.map((s) => html`<tr key=${s.site_id}><td>${s.name}${s.is_active ? "" : " (ngừng)"}</td><td class="r">${s.slips}</td><td class="r">${num(s.kg)}</td>
          <td class="r">${money(s.amount)}</td><td>${vnDate(s.last_delivery)}</td><td>${s.loans}</td></tr>`)}</tbody></table></div>
    </div>`}
    ${tab === "sites" && html`<${SitesTab} partnerId=${id} />`}
    ${tab === "prices" && html`<${PricesTab} partnerId=${id} master=${master} />`}
    ${tab === "info" && partner.data && (edit ? html`<${PartnerForm} partner=${partner.data} onDone=${() => { setEdit(false); partner.reload(); master.reload(); }} />`
      : html`<div class="panel"><table><tbody>${[["Mã", "code"], ["Tên", "name"], ["MST", "tax_code"], ["Địa chỉ", "address"], ["Liên hệ", "contact_name"], ["Điện thoại", "phone"], ["Email", "email"],
        ["Người đại diện", "representative"], ["Chức vụ", "representative_title"], ["Hạn mức nợ", "credit_limit"], ["Số ngày được nợ", "payment_term_days"],
        ["Dư nợ sổ cái đơn hàng", "current_debt"], ["Phải trả NCC (sổ cái)", "current_payable"], ["File bảng kê", "workbook_path"]].map(([l, k]) =>
        html`<tr key=${k}><th style=${{ width: 160, textAlign: "left" }}>${l}</th><td>${partner.data[k]}</td></tr>`)}</tbody></table>
        <button style=${{ marginTop: 10 }} onClick=${() => setEdit(true)}>Sửa</button></div>`)}
  </div>`;
}

function SitesTab({ partnerId }) {
  const sites = useLoad(() => api(`/api/partners/${partnerId}/sites`), [partnerId]);
  const [f, setF] = useState({ name: "", investment_note: "", aliases: "" });
  const [alias, setAlias] = useState({});
  const [msg, setMsg] = useState(null);
  const add = async () => {
    try {
      await api(`/api/partners/${partnerId}/sites`, { json: { name: f.name, investment_note: f.investment_note || null, aliases: f.aliases.split(";").map((x) => x.trim()).filter(Boolean) } });
      setF({ name: "", investment_note: "", aliases: "" }); sites.reload(); setMsg({ type: "ok", text: "Đã thêm bếp" });
    } catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  const addAlias = async (sid) => {
    try { await api(`/api/sites/${sid}/aliases`, { json: { alias: alias[sid] } }); setAlias({ ...alias, [sid]: "" }); setMsg({ type: "ok", text: "Đã lưu tên gọi khác" }); }
    catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  return html`<div><${Msg} msg=${msg} />
    <div class="panel"><table><thead><tr><th>Thứ tự</th><th>Bếp</th><th>Lịch sử đầu tư (dòng đầu khối trên bảng kê)</th><th>Thêm tên gọi khác (chữ viết tay trên phiếu)</th></tr></thead>
      <tbody>${(sites.data || []).map((s) => html`<tr key=${s.id}><td class="c">${s.sort_order}</td><td>${s.name}</td><td class="small" style=${{ whiteSpace: "pre-line" }}>${s.investment_note}</td>
        <td><div class="row"><input value=${alias[s.id] || ""} placeholder="VD: Stagate HP" onInput=${(e) => setAlias({ ...alias, [s.id]: e.target.value })} />
          <button disabled=${!alias[s.id]} onClick=${() => addAlias(s.id)}>Lưu</button></div></td></tr>`)}</tbody></table></div>
    <div class="panel"><h3 style=${{ marginTop: 0 }}>Thêm bếp</h3>
      <div class="row"><label class="grow">Tên bếp<input value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })} /></label>
        <label class="grow">Tên gọi khác (cách nhau bằng ;)<input value=${f.aliases} onInput=${(e) => setF({ ...f, aliases: e.target.value })} /></label></div>
      <label class="row" style=${{ marginTop: 8 }}><textarea class="grow" rows="2" placeholder="Lịch sử đầu tư" value=${f.investment_note} onInput=${(e) => setF({ ...f, investment_note: e.target.value })}></textarea></label>
      <button class="primary" style=${{ marginTop: 8 }} disabled=${!f.name} onClick=${add}>Thêm bếp</button></div></div>`;
}

function PricesTab({ partnerId, master }) {
  const [f, setF] = useState({ product_id: null, price: null, valid_from: today(), price_basis: "KG" });
  const [msg, setMsg] = useState(null);
  const [cur, setCur] = useState({});
  const gas = master.products.filter((p) => p.is_active);
  useEffect(() => {
    Promise.all(gas.map((p) => api(`/api/prices/lookup?product_id=${p.id}&on_date=${today()}&partner_id=${partnerId}`).then((r) => [p.id, r]).catch(() => [p.id, null])))
      .then((rows) => setCur(Object.fromEntries(rows)));
  }, [partnerId, master.products, msg]);
  const save = async () => {
    try { await api("/api/prices", { json: { ...f, partner_id: partnerId } }); setMsg({ type: "ok", text: "Đã lưu giá" }); }
    catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  return html`<div><${Msg} msg=${msg} />
    <div class="panel"><table><thead><tr><th>Mặt hàng</th><th>Giá đang áp dụng hôm nay</th></tr></thead><tbody>
      ${gas.map((p) => html`<tr key=${p.id}><td>${p.sku} – ${p.name}</td><td class="r">${cur[p.id] ? `${money(cur[p.id].price)} đ/${cur[p.id].price_basis === "KG" ? "kg" : "bình"}` : html`<span class="muted">chưa có</span>`}</td></tr>`)}</tbody></table></div>
    <div class="panel row">
      <label>Mặt hàng <select value=${f.product_id || ""} onChange=${(e) => setF({ ...f, product_id: Number(e.target.value) || null })}><option value="">—</option>
        ${gas.map((p) => html`<option key=${p.id} value=${p.id}>${p.sku}</option>`)}</select></label>
      <label>Giá (gồm VAT) <${NumInput} value=${f.price} onChange=${(v) => setF({ ...f, price: v })} /></label>
      <label>Theo <select value=${f.price_basis} onChange=${(e) => setF({ ...f, price_basis: e.target.value })}><option value="KG">kg</option><option value="UNIT">bình/cái</option></select></label>
      <label>Áp dụng từ <input type="date" value=${f.valid_from} onInput=${(e) => setF({ ...f, valid_from: e.target.value })} /></label>
      <button class="primary" disabled=${!f.product_id || !f.price} onClick=${save}>Lưu giá</button></div></div>`;
}

// ============================================================ Bảng kê
function StatementPage({ master, params }) {
  const [partnerId, setPartnerId] = useState(params.partner ? Number(params.partner) : null);
  const [period, setPeriod] = useState(params.period || prevPeriod(thisPeriod()));
  const [msg, setMsg] = useState(null);
  const [opt, setOpt] = useState({ replace: false, hide_old_months: true });
  const st = useLoad(() => (partnerId ? api(`/api/statements/preview/${partnerId}/${period}`) : null), [partnerId, period]);
  const saved = useLoad(() => (partnerId ? api(`/api/statements?partner_id=${partnerId}`) : []), [partnerId, msg]);
  const cur = (saved.data || []).find((s) => s.period === period);
  const run = async (fn) => { setMsg(null); try { setMsg(await fn()); } catch (e) { setMsg({ type: "err", text: e.message }); } };
  const toWorkbook = () => run(async () => {
    const r = await api("/api/statements/export-to-workbook", { json: { partner_id: partnerId, period, ...opt } });
    return { type: "ok", text: `Đã thêm sheet ${r.sheet} vào ${r.path}${r.backup ? ` (bản sao lưu: ${r.backup})` : ""}` };
  });
  const d = st.data;
  return html`<div><h2>Bảng kê chi tiết công nợ</h2><${Msg} msg=${msg} />
    <div class="panel row">
      <${PartnerSelect} partners=${master.partners} value=${partnerId} onChange=${setPartnerId} />
      <label>Tháng <input type="month" value=${period} onInput=${(e) => e.target.value && setPeriod(e.target.value)} /></label>
      ${cur && html`<span>Đã lưu: <${Badge} s=${cur.status} /></span>`}
    </div>
    ${partnerId && html`<div class="panel">
      <div class="row">
        <button class="primary" onClick=${() => download(`/api/statements/export/${partnerId}/${period}`)}>Tải file Excel</button>
        <button onClick=${toWorkbook}>Thêm sheet vào file bảng kê của khách</button>
        <label class="small chk"><input type="checkbox" checked=${opt.replace} onChange=${(e) => setOpt({ ...opt, replace: e.target.checked })} /> Ghi đè nếu đã có sheet tháng này</label>
        <label class="small chk"><input type="checkbox" checked=${opt.hide_old_months} onChange=${(e) => setOpt({ ...opt, hide_old_months: e.target.checked })} /> Ẩn sheet tháng cũ</label>
      </div>
      <div class="row" style=${{ marginTop: 8 }}>
        <button disabled=${cur && cur.status === "CONFIRMED"} onClick=${() => run(async () => { await api("/api/statements", { json: { partner_id: partnerId, period } }); return { type: "ok", text: "Đã lưu bảng kê (nháp)" }; })}>Lưu bảng kê</button>
        ${cur && cur.status === "DRAFT" && html`<button onClick=${() => run(async () => { await api(`/api/statements/${cur.id}/status`, { json: { status: "SENT" } }); return { type: "ok", text: "Đã đánh dấu gửi khách" }; })}>Đã gửi khách</button>`}
        ${cur && cur.status !== "CONFIRMED" && html`<button onClick=${() => run(async () => { await api(`/api/statements/${cur.id}/status`, { json: { status: "CONFIRMED" } }); return { type: "ok", text: "Khách đã xác nhận – kỳ này đã khóa sổ" }; })}>Khách xác nhận (khóa sổ)</button>`}
        ${cur && cur.status === "CONFIRMED" && html`<button class="danger" onClick=${() => run(async () => { await api(`/api/statements/${cur.id}/status`, { json: { status: "SENT" } }); return { type: "warn", text: "Đã mở khóa kỳ" }; })}>Mở khóa</button>`}
      </div></div>`}
    ${st.error && html`<${Msg} msg=${{ type: "err", text: st.error }} />`}
    ${d && html`<${StatementView} d=${d} />`}
  </div>`;
}

function StatementView({ d }) {
  const cols = d.cylinder_columns.length ? d.cylinder_columns : ["PC48"];
  const span = 2 + cols.length + 4;
  return html`<div class="panel bk" style=${{ overflowX: "auto" }}>
    <div style=${{ textAlign: "center", fontSize: 18, fontWeight: 700 }}>BẢNG KÊ CHI TIẾT</div>
    <div style=${{ textAlign: "center", fontStyle: "italic", fontWeight: 700, marginBottom: 8 }}>${d.period_text}</div>
    <div>Đơn vị mua hàng: <b>${d.buyer.name}</b></div><div class="small">MST ${d.buyer.tax_code || ""} · ${d.buyer.address || ""}</div>
    <table style=${{ marginTop: 8 }}><thead>
      <tr><th rowspan="2">Ngày tháng</th><th rowspan="2">Số hóa đơn</th><th colspan=${cols.length}>Loại bình</th><th rowspan="2">Gas dư</th><th rowspan="2">Thực giao</th>
        <th rowspan="2">Đơn giá</th><th rowspan="2">Thành tiền</th><th rowspan="2">Ghi chú</th><th rowspan="2"></th></tr>
      <tr>${cols.map((c) => html`<th key=${c}>${c}</th>`)}</tr>
      <tr class="total"><td colspan=${span}></td><td colspan="2" class="c">${d.opening_loans_text}</td></tr></thead>
    <tbody>${d.blocks.map((b, bi) => {
      const rows = [];
      if (b.investment_note) rows.push(html`<tr key="inv"><td colspan=${span + 1} class="c" style=${{ fontWeight: 700, whiteSpace: "pre-line" }}>${b.investment_note}</td></tr>`);
      b.rows.forEach((r, i) => rows.push(r.kind === "slip" ? html`<tr key=${i}><td>${vnDate(r.date)}</td><td class="c"><b>${r.vat_invoice_no}</b></td>
        ${cols.map((c) => html`<td key=${c} class="c">${num(r.qty_by_sku[c])}</td>`)}
        <td class="c">${num(r.gas_residual_kg)}</td><td class="c">${num(r.net_kg)}</td><td class="r">${money(r.unit_price)}</td><td class="r">${money(r.amount)}</td>
        <td class="c">${[r.note, r.qty_by_sku && Object.keys(r.qty_by_sku).length ? r.description : null].filter(Boolean).join("; ") || (!Object.keys(r.qty_by_sku).length ? r.description : "")}</td></tr>`
        : html`<tr key=${i}><td></td><td></td><td colspan=${cols.length + 4} class="c">${r.text}</td><td></td></tr>`));
      rows.push(html`<tr key="t" class="total"><td>Tổng</td><td></td>${cols.map((c) => html`<td key=${c} class="c">${num(b.totals.qty_by_sku[c] || 0)}</td>`)}
        <td class="c">${num(b.totals.gas_residual_kg)}</td><td class="c">${num(b.totals.net_kg)}</td><td class="c">X</td><td class="r">${money(b.totals.amount)}</td><td></td></tr>`);
      // cột tên bếp gộp dọc
      const first = rows[0];
      rows[0] = React.cloneElement(first, {}, [...React.Children.toArray(first.props.children),
        html`<td key="site" class="site" rowSpan=${rows.length}>${b.site_label}</td>`]);
      return html`<${React.Fragment} key=${bi}>${rows}<//>`;
    })}
    <tr class="total"><td>Tổng cộng</td><td></td>${cols.map((c) => html`<td key=${c} class="c">${num(d.grand_total.qty_by_sku[c] || 0)}</td>`)}
      <td class="c">${num(d.grand_total.gas_residual_kg)}</td><td class="c">${num(d.grand_total.net_kg)}</td><td class="c">X</td>
      <td class="r">${money(d.grand_total.amount)}</td><td colspan="2" class="c">${d.closing_loans_text}</td></tr>
    ${d.prior_debts.map((r) => html`<tr key=${r.period}><td colspan=${span - 2} class="c"><b>${r.label}</b></td><td class="r"><b>${money(r.amount)}</b></td><td colspan="2"></td></tr>`)}
    <tr class="debt"><td colspan=${span - 2} class="c">Tổng công nợ</td><td class="r">${money(d.total_debt)}</td><td colspan="2"></td></tr>
    ${d.payments.map((p, i) => html`<tr key=${i}><td colspan=${span - 2} class="c"><b>${p.label}</b></td><td class="r"><b>${money(p.amount)}</b></td><td colspan="2" class="c"><b>${p.note}</b></td></tr>`)}
    <tr class="debt"><td colspan=${span - 2} class="c">Tổng đã thanh toán</td><td class="r">${money(d.total_paid)}</td><td colspan="2"></td></tr>
    <tr><td colspan=${span - 2} class="c"><b>Công nợ còn lại phải thanh toán là:</b></td><td class="r"><b>${money(d.closing_debt)}</b></td><td colspan="2"></td></tr>
    </tbody></table>
    <p><b>Bằng chữ:</b> ${d.amount_in_words}</p>
    ${!d.balanced && html`<div class="msg warn">Lưu ý: số còn nợ trên bảng kê (${money(d.closing_debt)}) khác sổ công nợ (${money(d.ledger_balance)}) – kiểm tra tiền trả trước/sau kỳ.</div>`}
  </div>`;
}

// ============================================================ Thu chi
function CashPage({ master }) {
  const [f, setF] = useState({ voucher_type: "RECEIPT", voucher_date: today(), amount: null, fund_type: "BANK", partner_id: null, description: "", category: "" });
  const [msg, setMsg] = useState(null);
  const [fund, setFund] = useState("BANK");
  const [period, setPeriod] = useState(thisPeriod());
  const [from, to] = periodBounds(period);
  const book = useLoad(() => api(`/api/cash/book?fund_type=${fund}&date_from=${from}&date_to=${to}`), [fund, period, msg]);
  const out = useLoad(() => (f.partner_id ? api(`/api/debts/outstanding/${f.partner_id}`) : null), [f.partner_id, msg]);
  const save = async () => {
    setMsg(null);
    try {
      const v = await api("/api/cash/vouchers", { json: { ...f, partner_id: f.partner_id || null, description: f.description || null, category: f.category || null } });
      const al = (v.allocations || []).map((a) => `${a.period}: ${money(a.amount)}`).join(", ");
      setMsg({ type: "ok", text: `Đã lập ${v.voucher_no} – ${money(v.amount)} đ${al ? ` (trừ nợ ${al})` : ""}` });
      setF({ ...f, amount: null, description: "" });
    } catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  return html`<div><h2>Thu – Chi</h2><${Msg} msg=${msg} />
    <div class="panel"><div class="row">
      <label>Loại <select value=${f.voucher_type} onChange=${(e) => setF({ ...f, voucher_type: e.target.value })}><option value="RECEIPT">Phiếu thu</option><option value="PAYMENT">Phiếu chi</option></select></label>
      <label>Ngày <input type="date" value=${f.voucher_date} onInput=${(e) => setF({ ...f, voucher_date: e.target.value })} /></label>
      <label>Quỹ <select value=${f.fund_type} onChange=${(e) => setF({ ...f, fund_type: e.target.value })}><option value="BANK">Ngân hàng</option><option value="CASH">Tiền mặt</option></select></label>
      <label>Số tiền <${NumInput} value=${f.amount} width=${140} onChange=${(v) => setF({ ...f, amount: v })} /></label>
      <${PartnerSelect} partners=${master.allPartners} value=${f.partner_id} label="Khách hàng / NCC (bỏ trống = thu chi nội bộ)" onChange=${(v) => setF({ ...f, partner_id: v })} />
    </div><div class="row" style=${{ marginTop: 8 }}>
      <label class="grow">Diễn giải <input value=${f.description} onInput=${(e) => setF({ ...f, description: e.target.value })} /></label>
      ${!f.partner_id && html`<label>Khoản mục <input value=${f.category} placeholder="VD: Xăng xe, lương" onInput=${(e) => setF({ ...f, category: e.target.value })} /></label>`}
      <button class="primary" disabled=${!f.amount} onClick=${save}>Lưu phiếu</button></div>
    ${out.data && html`<div class="small" style=${{ marginTop: 8 }}>Đang nợ ${money(out.data.balance)} đ: ${out.data.periods.map((p) => `T${p.period.slice(5)}/${p.period.slice(0, 4)} ${money(p.amount)}`).join(" · ")} – tiền thu sẽ trừ vào tháng cũ nhất trước.</div>`}
    </div>
    <div class="panel"><div class="row" style=${{ marginBottom: 8 }}><h3 style=${{ margin: 0 }}>Sổ quỹ</h3>
      <select value=${fund} onChange=${(e) => setFund(e.target.value)}><option value="BANK">Tiền gửi ngân hàng</option><option value="CASH">Tiền mặt</option></select>
      <input type="month" value=${period} onInput=${(e) => e.target.value && setPeriod(e.target.value)} /></div>
      ${book.data && html`<table><thead><tr><th>Ngày</th><th>Số phiếu</th><th>Đối tượng</th><th>Diễn giải</th><th>Thu</th><th>Chi</th><th>Tồn</th></tr></thead><tbody>
        <tr class="total"><td colspan="6">Tồn đầu kỳ</td><td class="r">${money(book.data.opening)}</td></tr>
        ${book.data.lines.map((l, i) => html`<tr key=${i}><td>${vnDate(l.date)}</td><td>${l.voucher_no}</td><td>${l.partner}</td><td>${l.description || l.category}</td>
          <td class="r">${money(l.receipt)}</td><td class="r">${money(l.payment)}</td><td class="r">${money(l.balance)}</td></tr>`)}
        <tr class="total"><td colspan="4">Cộng / Tồn cuối kỳ</td><td class="r">${money(book.data.total_receipt)}</td><td class="r">${money(book.data.total_payment)}</td><td class="r">${money(book.data.closing)}</td></tr>
      </tbody></table>`}</div></div>`;
}

// ============================================================ Kho
function StockPage({ master }) {
  const [period, setPeriod] = useState(thisPeriod());
  const [from, to] = periodBounds(period);
  const [msg, setMsg] = useState(null);
  const nxt = useLoad(() => api(`/api/inventory/nxt?date_from=${from}&date_to=${to}`), [period, msg]);
  const [op, setOp] = useState({ product_id: null, qty: null, unit_cost: null, on_date: today() });
  const [count, setCount] = useState({});
  const saveOpening = async () => { try { await api("/api/inventory/opening", { json: op }); setMsg({ type: "ok", text: "Đã ghi tồn đầu" }); } catch (e) { setMsg({ type: "err", text: e.message }); } };
  const stocktake = async () => {
    try {
      const counted = Object.fromEntries(Object.entries(count).filter(([, v]) => v !== null && v !== ""));
      const r = await api("/api/inventory/stocktakes", { json: { on_date: today(), counted } });
      setMsg({ type: "ok", text: `Đã kiểm kê – phiếu cân bằng kho #${r.id || ""}` }); setCount({});
    } catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  return html`<div><h2>Kho</h2><${Msg} msg=${msg} />
    <div class="panel"><div class="row" style=${{ marginBottom: 8 }}><h3 style=${{ margin: 0 }}>Nhập – Xuất – Tồn</h3>
      <input type="month" value=${period} onInput=${(e) => e.target.value && setPeriod(e.target.value)} /></div>
      <table><thead><tr><th>Mã</th><th>Tên</th><th>Tồn đầu</th><th>Nhập</th><th>Xuất</th><th>Tồn cuối</th><th>Giá trị tồn</th><th>Đếm thực tế (kiểm kê)</th></tr></thead>
      <tbody>${(nxt.data || []).map((r) => html`<tr key=${r.product_id}><td>${r.sku}</td><td>${r.name}</td><td class="r">${num(r.opening_qty)}</td>
        <td class="r">${num(r.in_qty)}</td><td class="r">${num(r.out_qty)}</td><td class="r"><b>${num(r.closing_qty)}</b>${r.below_min ? html` <span class="badge CANCELLED">dưới mức tối thiểu</span>` : ""}</td>
        <td class="r">${money(r.closing_value)}</td><td><${NumInput} value=${count[r.product_id]} width=${80} onChange=${(v) => setCount({ ...count, [r.product_id]: v })} /></td></tr>`)}</tbody></table>
      <button style=${{ marginTop: 8 }} disabled=${!Object.values(count).some((v) => v)} onClick=${stocktake}>Lưu kiểm kê hôm nay (tự sinh phiếu cân bằng kho)</button></div>
    <div class="panel"><h3 style=${{ marginTop: 0 }}>Khai báo tồn kho đầu kỳ</h3><div class="row">
      <label>Mặt hàng <select value=${op.product_id || ""} onChange=${(e) => setOp({ ...op, product_id: Number(e.target.value) || null })}><option value="">—</option>
        ${master.products.map((p) => html`<option key=${p.id} value=${p.id}>${p.sku}</option>`)}</select></label>
      <label>Số lượng <${NumInput} value=${op.qty} onChange=${(v) => setOp({ ...op, qty: v })} /></label>
      <label>Giá vốn / đơn vị <${NumInput} value=${op.unit_cost} onChange=${(v) => setOp({ ...op, unit_cost: v })} /></label>
      <label>Ngày <input type="date" value=${op.on_date} onInput=${(e) => setOp({ ...op, on_date: e.target.value })} /></label>
      <button class="primary" disabled=${!op.product_id || !op.qty} onClick=${saveOpening}>Lưu</button></div></div></div>`;
}

// ============================================================ Nhập dữ liệu
function ImportPage({ master }) {
  const [msg, setMsg] = useState(null);
  const [rep, setRep] = useState(null);
  const [xml, setXml] = useState(null);
  const [mapping, setMapping] = useState({});
  const [busy, setBusy] = useState(false);
  const upExcel = async (file) => {
    setBusy(true); setMsg(null); setRep(null);
    try { const fd = new FormData(); fd.append("file", file); const r = await api("/api/import/excel", { method: "POST", body: fd }); setRep(r); master.reload(); }
    catch (e) { setMsg({ type: "err", text: e.message }); }
    setBusy(false);
  };
  const upXml = async (file, mp) => {
    setBusy(true); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", file); if (mp) fd.append("mapping", JSON.stringify(mp));
      const r = await api("/api/import/xml", { method: "POST", body: fd }); setXml({ file, r });
      if (r.status === "IMPORTED") setMsg({ type: "ok", text: `Đã nhập hóa đơn ${r.invoice.symbol} số ${r.invoice.number} (${r.direction === "PURCHASE" ? "mua vào" : "bán ra"})` });
      if (r.status === "DUPLICATE") setMsg({ type: "warn", text: "Hóa đơn này đã nhập trước đó" });
    } catch (e) { setMsg({ type: "err", text: e.message }); }
    setBusy(false);
  };
  return html`<div><h2>Nhập dữ liệu</h2><${Msg} msg=${msg} />
    <div class="panel"><h3 style=${{ marginTop: 0 }}>1. Lịch sử từ file Bảng kê Excel cũ</h3>
      <input type="file" accept=".xlsx,.xlsm" disabled=${busy} onChange=${(e) => e.target.files[0] && upExcel(e.target.files[0])} />
      ${busy && html` <span>Đang xử lý…</span>`}
      ${rep && html`<div style=${{ marginTop: 10 }}>
        <div><b>${rep.partner_name}</b> – ${rep.sheets} sheet · phiếu mới ${rep.slips_created} (bỏ qua trùng ${rep.slips_skipped_duplicate}) · thanh toán mới ${rep.vouchers_created}</div>
        ${rep.assumptions.map((a, i) => html`<div key=${i} class="small muted">* ${a}</div>`)}
        <table style=${{ marginTop: 8 }}><thead><tr><th>Sheet</th><th>Tổng trên file</th><th>Phần mềm</th><th>Còn nợ trên file</th><th>Phần mềm</th><th></th></tr></thead><tbody>
          ${rep.sheet_checks.map((c) => html`<tr key=${c.sheet}><td>${c.sheet}</td><td class="r">${money(c.file_total)}</td><td class="r">${money(c.app_total)}</td>
            <td class="r">${money(c.file_closing)}</td><td class="r">${money(c.app_closing)}</td><td>${c.ok ? html`<span class="badge POSTED">khớp</span>` : html`<span class="badge CANCELLED">lệch</span>`}</td></tr>`)}</tbody></table>
        ${rep.warnings.map((w, i) => html`<div key=${i} class="small" style=${{ color: "var(--warn)" }}>! ${w}</div>`)}</div>`}
    </div>
    <div class="panel"><h3 style=${{ marginTop: 0 }}>2. Hóa đơn điện tử XML (TT78)</h3>
      <input type="file" accept=".xml" disabled=${busy} onChange=${(e) => e.target.files[0] && upXml(e.target.files[0])} />
      ${xml && xml.r.status === "NEEDS_MAPPING" && html`<div style=${{ marginTop: 10 }}>
        <div class="msg warn">Có mã hàng của nhà cung cấp chưa nối với danh mục. Chọn mã tương ứng (phần mềm sẽ nhớ cho lần sau):</div>
        <table><thead><tr><th>Dòng</th><th>Mã NCC</th><th>Tên hàng</th><th>ĐVT</th><th>SL</th><th>Mã của mình</th><th>Hệ số quy đổi</th></tr></thead><tbody>
          ${xml.r.unmapped.map((u) => html`<tr key=${u.line}><td>${u.line}</td><td>${u.code}</td><td>${u.name}</td><td>${u.unit}</td><td class="r">${num(u.qty)}</td>
            <td><select value=${(mapping[u.line] || {}).sku || ""} onChange=${(e) => setMapping({ ...mapping, [u.line]: { ...(mapping[u.line] || { qty_factor: "1" }), sku: e.target.value } })}>
              <option value="">—</option>${master.products.map((p) => html`<option key=${p.id}>${p.sku}</option>`)}</select></td>
            <td><input style=${{ width: 70 }} value=${(mapping[u.line] || {}).qty_factor || "1"} onInput=${(e) => setMapping({ ...mapping, [u.line]: { ...(mapping[u.line] || {}), qty_factor: e.target.value } })} /></td></tr>`)}</tbody></table>
        <button class="primary" style=${{ marginTop: 8 }} onClick=${() => upXml(xml.file, mapping)}>Nhập lại với mã đã chọn</button></div>`}
      ${xml && xml.r.warnings.map((w, i) => html`<div key=${i} class="small" style=${{ color: "var(--warn)" }}>! ${w}</div>`)}
    </div></div>`;
}

// ============================================================ Cài đặt
function SettingsPage() {
  const c = useLoad(() => api("/api/company").catch(() => ({})), []);
  const prov = useLoad(() => api("/api/ocr/providers"), []);
  const [f, setF] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => { if (c.data) setF({ name: "", sign_city: "Hà Nội", ...c.data }); }, [c.data]);
  const save = async () => {
    try {
      const keys = ["name", "parent_name", "tax_code", "address", "phones", "email", "bank_account", "bank_name", "contact_line", "sign_city"];
      await api("/api/company", { method: "PUT", json: Object.fromEntries(keys.map((k) => [k, f[k] || (k === "name" || k === "sign_city" ? f[k] : null)])) });
      setMsg({ type: "ok", text: "Đã lưu thông tin công ty" });
    } catch (e) { setMsg({ type: "err", text: e.message }); }
  };
  const fields = [["parent_name", "Tổng công ty (dòng 1 bảng kê)"], ["name", "Tên công ty"], ["tax_code", "MST"], ["address", "Địa chỉ"], ["phones", "Dòng điện thoại (VD: ĐT: 0968...)"],
    ["bank_account", "Số tài khoản"], ["bank_name", "Ngân hàng"], ["contact_line", "Người liên hệ (VD: Mr. Khánh: 0968.97.2011)"], ["email", "Email"], ["sign_city", "Nơi ký (Hà Nội)"]];
  const [who, setWho] = useState(getIdentity());
  const me = useLoad(() => api("/api/me"), []);
  return html`<div><h2>Cài đặt</h2><${Msg} msg=${msg} />
    ${me.data && me.data.auth_enabled ? html`<div class="panel"><h3>Tài khoản đăng nhập</h3>
      <p>Đang đăng nhập: <b>${me.data.user}</b> – vai trò <b>${ROLES[me.data.role] || me.data.role}</b>.</p>
      <p class="small muted">Tài khoản & vai trò quản lý trong biến <code>SALE_AUTH_USERS</code> của file backend/.env trên máy chủ.</p></div>`
    : html`<div class="panel"><h3>Người dùng trên máy này</h3>
      <div class="row"><label class="grow">Tên người dùng (ghi vào nhật ký, người duyệt / xác nhận)<input value=${who.user} onInput=${(e) => setWho({ ...who, user: e.target.value })} /></label>
        <label>Vai trò<select value=${who.role} onChange=${(e) => setWho({ ...who, role: e.target.value })}>
          <option value="">— Theo cấu hình máy chủ —</option>${Object.entries(ROLES).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}</select></label>
        <button class="primary" onClick=${() => { setIdentity(who.user, who.role); setMsg({ type: "ok", text: "Đã lưu người dùng – tải lại trang để áp dụng" }); }}>Lưu</button></div>
      <p class="small muted">Chỉ <b>Quản lý / Kế toán trưởng / Quản trị</b> được duyệt đơn vượt hạn mức hoặc có nợ quá hạn. Vai trò mặc định của máy chủ đặt bằng <code>SALE_DEFAULT_ROLE</code> trong backend/.env.</p></div>`}
    <div class="panel"><h3 style=${{ marginTop: 0 }}>Thông tin công ty (in trên bảng kê)</h3>
      ${f && fields.map(([k, l]) => html`<label key=${k} class="row" style=${{ marginBottom: 6 }}><span style=${{ width: 260 }} class="small muted">${l}</span>
        <input class="grow" value=${f[k] || ""} onInput=${(e) => setF({ ...f, [k]: e.target.value })} /></label>`)}
      <button class="primary" onClick=${save}>Lưu</button></div>
    <div class="panel"><h3 style=${{ marginTop: 0 }}>Sao lưu dữ liệu</h3>
      <p class="small muted">Phần mềm tự sao lưu mỗi ngày vào thư mục data/backups (giữ 30 bản). Nên chép thêm thư mục này sang USB / Google Drive hằng tuần.</p>
      <button onClick=${async () => { try { const r = await api("/api/backup", { method: "POST" }); setMsg({ type: "ok", text: "Đã sao lưu: " + r.path }); } catch (e) { setMsg({ type: "err", text: e.message }); } }}>Sao lưu ngay</button></div>
    <div class="panel"><h3 style=${{ marginTop: 0 }}>AI đọc ảnh phiếu</h3>
      ${prov.data && html`<table><thead><tr><th>Hãng</th><th>Mô hình mặc định</th><th>Biến khóa trong .env</th><th>Trạng thái</th></tr></thead><tbody>
        ${Object.entries(prov.data.all).map(([k, v]) => html`<tr key=${k}><td>${k}</td><td>${v.default_model}</td><td><code>${v.key_env}</code></td>
          <td>${prov.data.configured.includes(k) ? html`<span class="badge POSTED">đã có khóa</span>` : html`<span class="muted">chưa có</span>`}</td></tr>`)}</tbody></table>`}
      <p class="small muted">Đổi hãng mặc định: thêm dòng <code>OCR_PROVIDER=deepseek</code> (hoặc gemini, qwen, openai, anthropic) vào file backend/.env rồi khởi động lại.</p></div></div>`;
}

// ============================================================ Khung
function App() {
  const { page, params } = useHashRoute();
  const master = useMaster();
  const pages = { dash: Dashboard, slip: SlipForm, slips: SlipList, ocr: OcrPage, customers: Customers, statement: StatementPage, cash: CashPage, stock: StockPage, import: ImportPage, settings: SettingsPage,
    orders: OrdersPage, delivery: DeliveryPage, reconcile: ReconciliationPage };
  const [theme, setTheme] = useTheme();
  const [me, setMe] = useState(null);
  useEffect(() => { api("/api/me").then(setMe).catch(() => {}); }, []);
  const local = getIdentity();
  const who = me && me.auth_enabled ? { user: me.user, role: me.role } : local;
  let lastGroup = null;
  const Page = pages[page] || Dashboard;
  useEffect(() => {
    const onKey = (e) => { if (e.key === "F2" && page !== "slip") { e.preventDefault(); go("slip"); } };
    addEventListener("keydown", onKey); return () => removeEventListener("keydown", onKey);
  }, [page]);
  return html`<div class="app">
    <nav class="side" aria-label="Chức năng"><h1><span class="logo">SM</span>Sale-manager</h1>
      ${PAGES.map(([k, v, icon, group]) => {
        const head = group !== lastGroup ? html`<div class="group" key=${"g" + group}>${group}</div>` : null;
        lastGroup = group;
        return html`${head}<a key=${k} href=${"#/" + k} class=${page === k ? "on" : ""} aria-current=${page === k ? "page" : undefined}><${Icon} name=${icon} />${v}</a>`;
      })}
      <div class="kbd">Phím tắt khi lập phiếu:<br />F2 phiếu mới · F4 thêm dòng<br />F8 lưu nháp · F9 ghi sổ</div>
      <div class="who"><${Icon} name="users" size=${14} /><span>${who.user || "admin"}${who.role ? " · " + (ROLES[who.role] || who.role) : ""}</span>
        <button class="icon-btn right" style=${{ background: "transparent", color: "inherit", borderColor: "#334155", minHeight: 28, minWidth: 28, padding: 4 }}
          aria-label=${theme === "dark" ? "Chế độ sáng" : "Chế độ tối"} onClick=${() => setTheme(theme === "dark" ? "light" : "dark")}>
          <${Icon} name=${theme === "dark" ? "sun" : "moon"} size=${14} /></button></div></nav>
    <main><${Page} key=${page + JSON.stringify(params)} master=${master} params=${params} /></main></div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
