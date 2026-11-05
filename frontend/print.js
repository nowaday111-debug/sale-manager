// Mẫu in A4: Đơn đặt hàng (PO/SO) · Biên bản giao nhận (BBGN) · Biên bản đối chiếu công nợ (BBĐCCN).
// Mở: print.html?type=order|dn|dr&id=..[&hide_price=1]
import { html, useState, useEffect, api, money, num, vnDate, readVietnameseCurrency } from "./lib.js";

const qs = new URLSearchParams(location.search);
const TYPE = qs.get("type");
const ID = qs.get("id");
const n = (v) => Number(v || 0);
const dt = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")} ${vnDate(iso)}`;
};
const longDate = (iso, city) => {
  const [y, m, d] = String(iso).slice(0, 10).split("-");
  return `${city || "Hà Nội"}, ngày ${d} tháng ${m} năm ${y}`;
};

function CompanyHead({ p }) {
  const c = p.company;
  return html`<div class="head">
    <div class="co"><b>${c.name}</b>
      ${c.tax_code && html`<div>Mã số thuế: ${c.tax_code}</div>`}${c.address && html`<div>Địa chỉ: ${c.address}</div>`}
      ${c.phones && html`<div>${c.phones}</div>`}${c.bank_account && html`<div>TK: ${c.bank_account} – ${c.bank_name || ""}</div>`}</div>
    <div class="qr"><img src=${`/api/print/${p.type}/${ID}/qr.svg`} alt=${"QR " + p.code} /><div>${p.code}</div></div></div>`;
}

function Footer({ p }) {
  return html`<div class="foot"><span>In lúc ${dt(p.printed_at)} – Sale-manager</span><span>Mã chứng từ: ${p.code}</span></div>`;
}

function Sign({ cols }) {
  return html`<div class=${"sign c" + cols.length}>${cols.map(([t, s]) => html`<div key=${t}><b>${t}</b><i>${s || "(Ký, ghi rõ họ tên)"}</i><div class="space"></div></div>`)}</div>`;
}

// ---------------------------------------------------------------- Mẫu 1: Đơn đặt hàng
function OrderSheet({ p }) {
  const o = p.doc, pt = o.partner, isSale = o.order_type === "SALES";
  return html`<div class="sheet">
    <${CompanyHead} p=${p} />
    <h1>${isSale ? "ĐƠN ĐẶT HÀNG (BÁN HÀNG)" : "ĐƠN ĐẶT HÀNG (MUA HÀNG)"}</h1>
    <div class="sub">Số: ${o.order_code} – Ngày ${vnDate(o.order_date)}</div>
    <table class="info"><tbody>
      <tr><td class="l">${isSale ? "Khách hàng" : "Nhà cung cấp"}:</td><td><b>${pt.name}</b></td></tr>
      <tr><td class="l">Mã số thuế:</td><td>${pt.tax_code || ""}</td></tr>
      <tr><td class="l">Địa chỉ:</td><td>${pt.address || ""}</td></tr>
      <tr><td class="l">Người liên hệ:</td><td>${[pt.contact_name, pt.phone].filter(Boolean).join(" – ")}</td></tr>
      <tr><td class="l">Địa điểm giao hàng:</td><td>${o.delivery_address || o.site_name || pt.address || ""}</td></tr>
      <tr><td class="l">Hạn giao hàng:</td><td>${o.expected_delivery_date ? vnDate(o.expected_delivery_date) : "Theo thỏa thuận"}</td></tr>
    </tbody></table>
    <table class="grid"><thead><tr><th>STT</th><th>Mã hàng</th><th>Tên hàng & quy cách</th><th>ĐVT</th><th>Số lượng</th><th>Đơn giá</th>
      <th>Chiết khấu</th><th>Thuế</th><th>Thành tiền</th></tr></thead>
      <tbody>${o.items.map((it) => html`<tr key=${it.id}><td class="c">${it.line_no}</td><td class="c">${it.product_code}</td><td>${it.product_name}</td>
        <td class="c">${it.unit}</td><td class="r">${num(it.ordered_qty, 3)}</td><td class="r">${money(it.unit_price)}</td>
        <td class="r">${n(it.discount_rate) ? num(it.discount_rate, 2) + "%" : ""}</td><td class="r">${num(it.tax_rate, 2)}%</td><td class="r">${money(it.line_total)}</td></tr>`)}
      <tr><td colspan="8">Cộng tiền hàng (trước chiết khấu, thuế)</td><td class="r">${money(o.subtotal_amount)}</td></tr>
      <tr><td colspan="8">Chiết khấu</td><td class="r">${n(o.discount_amount) ? "−" + money(o.discount_amount) : "0"}</td></tr>
      <tr><td colspan="8">Tiền thuế GTGT</td><td class="r">${money(o.tax_amount)}</td></tr>
      ${n(o.shipping_fee) > 0 && html`<tr><td colspan="8">Phí vận chuyển</td><td class="r">${money(o.shipping_fee)}</td></tr>`}
      <tr class="total"><td colspan="8">TỔNG GIÁ TRỊ ĐƠN HÀNG</td><td class="r">${money(o.total_amount)}</td></tr></tbody></table>
    <div class="words"><b>Số tiền viết bằng chữ:</b> <i>${readVietnameseCurrency(o.total_amount)}</i></div>
    <div class="terms"><b>Điều khoản thương mại:</b><ol>
      <li>Thanh toán: ${o.payment_terms || `Chuyển khoản trong vòng ${pt.payment_term_days || 30} ngày kể từ ngày giao nhận`}.</li>
      <li>Giá trên đã bao gồm thuế GTGT theo thuế suất từng mặt hàng; đơn giá được giữ cố định khi đơn đã được phê duyệt.</li>
      <li>Công nợ được ghi nhận theo số lượng thực nhận trên Biên bản giao nhận có chữ ký của hai bên.</li>
      <li>Hàng lỗi, thiếu phải được ghi rõ trên Biên bản giao nhận tại thời điểm nhận hàng.</li>
      ${o.notes && html`<li>${o.notes}</li>`}</ol></div>
    <div class="ref small">Phê duyệt: ${o.approved_by ? `${o.approved_by} – ${vnDate(o.approved_at)}` : "Chưa phê duyệt"}</div>
    <div style=${{ textAlign: "right", fontStyle: "italic", marginTop: 8 }}>${longDate(o.order_date, p.company.sign_city)}</div>
    <${Sign} cols=${[["NGƯỜI LẬP ĐƠN"], ["KẾ TOÁN KIỂM SOÁT CÔNG NỢ"], ["QUẢN LÝ PHÊ DUYỆT"], ["XÁC NHẬN CỦA ĐỐI TÁC", "(Ký, đóng dấu)"]]} />
    <${Footer} p=${p} /></div>`;
}

// ---------------------------------------------------------------- Mẫu 2: Biên bản giao nhận
function DeliverySheet({ p, hidePrice }) {
  const d = p.doc, pt = d.partner, isSale = d.order_type === "SALES";
  const cols = hidePrice ? 8 : 10;
  return html`<div class=${"sheet" + (hidePrice ? " hidden-price" : "")}>
    <${CompanyHead} p=${p} />
    <h1>BIÊN BẢN GIAO NHẬN HÀNG HÓA</h1>
    <div class="sub">Số: ${d.delivery_code} – Ngày ${vnDate(d.delivery_date)}</div>
    <div class="ref"><b>Căn cứ theo Đơn đặt hàng số: ${d.order_code} ngày ${vnDate(d.order_date)}</b></div>
    <table class="info"><tbody>
      <tr><td class="l">Bên giao:</td><td>${isSale ? p.company.name : pt.name}${d.deliverer_name ? ` – Người giao: ${d.deliverer_name}` : ""}${d.deliverer_phone ? ` (${d.deliverer_phone})` : ""}</td></tr>
      <tr><td class="l">Bên nhận:</td><td><b>${isSale ? pt.name : p.company.name}</b>${d.receiver_name ? ` – Người nhận: ${d.receiver_name}` : ""}</td></tr>
      <tr><td class="l">Địa điểm giao nhận:</td><td>${d.delivery_address || ""}</td></tr>
      ${d.warehouse_name && html`<tr><td class="l">Kho xuất:</td><td>${d.warehouse_name}</td></tr>`}
    </tbody></table>
    <table class="grid"><thead><tr><th>STT</th><th>Mã hàng</th><th>Tên hàng</th><th>ĐVT</th><th>SL theo Đơn</th><th>SL Thực giao</th><th>SL Thực nhận</th>
      <th>Tình trạng / Ghi chú</th><th class="price">Đơn giá</th><th class="price">Thành tiền</th></tr></thead>
      <tbody>${d.items.map((it) => html`<tr key=${it.id}><td class="c">${it.line_no}</td><td class="c">${it.product_code}</td><td>${it.product_name}</td>
        <td class="c">${it.unit}</td><td class="r">${num(it.ordered_qty_snapshot, 3)}</td><td class="r">${num(it.dispatched_qty, 3)}</td>
        <td class="r">${d.status === "CONFIRMED" ? num(it.accepted_qty, 3) : ""}</td>
        <td>${n(it.rejected_qty) > 0 ? `Lỗi/trả lại: ${num(it.rejected_qty, 3)}. ` : ""}${it.item_condition_note || ""}</td>
        <td class="r price">${money(it.unit_price)}</td><td class="r price">${d.status === "CONFIRMED" ? money(it.accepted_line_total) : ""}</td></tr>`)}
      ${!hidePrice && n(d.shipping_fee) > 0 && html`<tr><td colspan="9">Phí vận chuyển</td><td class="r">${money(d.shipping_fee)}</td></tr>`}
      ${!hidePrice && d.status === "CONFIRMED" && html`<tr class="total"><td colspan=${cols - 1}>Tổng giá trị thực nhận</td><td class="r">${money(d.total_accepted_amount)}</td></tr>`}
      </tbody></table>
    ${!hidePrice && d.status === "CONFIRMED" && html`<div class="words"><b>Số tiền viết bằng chữ:</b> <i>${readVietnameseCurrency(d.total_accepted_amount)}</i></div>`}
    ${d.discrepancy_reason && html`<div class="ref"><b>Lý do chênh lệch:</b> ${d.discrepancy_reason}</div>`}
    <div class="terms small">Hai bên đã kiểm đếm, giao nhận đủ số lượng và tình trạng hàng hóa như trên. Biên bản lập thành 02 bản, mỗi bên giữ 01 bản.</div>
    <div style=${{ textAlign: "right", fontStyle: "italic", marginTop: 6 }}>${longDate(d.delivery_date, p.company.sign_city)}</div>
    <${Sign} cols=${[["NGƯỜI LẬP PHIẾU"], ["THỦ KHO XUẤT"], ["NHÂN VIÊN GIAO HÀNG"], ["ĐẠI DIỆN BÊN NHẬN HÀNG", "(Ký, ghi rõ họ tên)"]]} />
    <${Footer} p=${p} /></div>`;
}

// ---------------------------------------------------------------- Mẫu 3: Biên bản đối chiếu công nợ
function ReconciliationSheet({ p }) {
  const r = p.doc, pt = r.partner, c = p.company;
  const usSeller = r.side === "RECEIVABLE";
  const us = { name: c.name, tax: c.tax_code, address: c.address, phone: c.phones, rep: "", title: "" };
  const them = { name: pt.name, tax: pt.tax_code, address: pt.address, phone: pt.phone, rep: pt.representative, title: pt.representative_title };
  const [A, B] = usSeller ? [us, them] : [them, us];
  const party = (label, x) => html`<div><h4>${label}</h4>
    <div><b>${x.name}</b></div><div>Mã số thuế: ${x.tax || ""}</div><div>Địa chỉ: ${x.address || ""}</div>
    <div>Điện thoại: ${x.phone || ""}</div><div>Đại diện: ${x.rep || "………………………"} Chức vụ: ${x.title || "…………"}</div></div>`;
  const closing = n(r.closing_balance);
  return html`<div class="sheet">
    <div class="head" style=${{ gridTemplateColumns: "1fr 1.2fr auto" }}>
      <div class="co"><b>${c.name}</b><div>Số: ${r.reconciliation_code}</div></div>
      <div class="nation"><b>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</b><div class="motto">Độc lập – Tự do – Hạnh phúc</div></div>
      <div class="qr"><img src=${`/api/print/dr/${ID}/qr.svg`} alt=${"QR " + p.code} /></div></div>
    <h1>BIÊN BẢN ĐỐI CHIẾU CÔNG NỢ</h1>
    <div class="sub">Từ ngày ${vnDate(r.period_from)} đến ngày ${vnDate(r.period_to)}</div>
    <div class="terms">Hôm nay, ${longDate(r.period_to, c.sign_city).replace(/^.*?, /, "")}, chúng tôi gồm:</div>
    <div class="parties">${party("BÊN A (Bên bán / cung cấp)", A)}${party("BÊN B (Bên mua / tiêu thụ)", B)}</div>
    <div class="terms">Cùng nhau đối chiếu công nợ phát sinh trong kỳ, kết quả như sau:</div>
    <table class="grid summary"><tbody>
      <tr><td class="l">(1) Số dư đầu kỳ</td><td class="r">${money(r.opening_balance) || "0"}</td></tr>
      <tr><td class="l">(2) Phát sinh tăng trong kỳ (theo Biên bản giao nhận)</td><td class="r">${money(r.total_increase) || "0"}</td></tr>
      <tr><td class="l">(3) Đã thanh toán / giảm trừ trong kỳ</td><td class="r">${money(r.total_decrease) || "0"}</td></tr>
      <tr class="debt"><td class="l">(4) Số dư cuối kỳ phải thanh toán = (1) + (2) − (3)</td><td class="r">${money(r.closing_balance) || "0"}</td></tr></tbody></table>
    <div class="words"><b>Kết luận:</b> ${closing >= 0 ? "Bên B còn phải thanh toán cho Bên A" : "Bên A còn phải trả lại Bên B"} số tiền <b>${money(Math.abs(closing))} đồng</b>.<br />
      <b>Bằng chữ:</b> <i>${readVietnameseCurrency(Math.abs(closing))}</i></div>
    <h4 style=${{ margin: "12px 0 2px" }}>Bảng kê chi tiết chứng từ</h4>
    <table class="grid"><thead><tr><th>STT</th><th>Ngày</th><th>Số ĐĐH</th><th>Số BBGN / Phiếu</th><th>Diễn giải</th><th>Phát sinh tăng</th><th>Phát sinh giảm</th><th>Số dư</th></tr></thead>
      <tbody><tr><td colspan="7"><i>Số dư đầu kỳ</i></td><td class="r">${money(r.opening_balance) || "0"}</td></tr>
      ${r.lines.map((l) => html`<tr key=${l.id}><td class="c">${l.line_no}</td><td class="c">${vnDate(l.transaction_date)}</td><td class="c">${l.order_code || ""}</td>
        <td class="c">${l.document_code}</td><td>${l.description || ""}${l.is_disputed ? html`<br /><b>[Chênh lệch: ${l.dispute_comment}]</b>` : ""}</td>
        <td class="r">${n(l.increase_amount) ? money(l.increase_amount) : ""}</td><td class="r">${n(l.decrease_amount) ? money(l.decrease_amount) : ""}</td><td class="r">${money(l.line_balance)}</td></tr>`)}
      <tr class="total"><td colspan="5">Cộng phát sinh</td><td class="r">${money(r.total_increase)}</td><td class="r">${money(r.total_decrease)}</td><td class="r">${money(r.closing_balance)}</td></tr></tbody></table>
    ${r.appendix.length > 0 && html`<div class="appendix"><h4 style=${{ margin: "12px 0 2px" }}>Phụ lục: chi tiết từng lần giao nhận</h4>
      <table class="grid"><thead><tr><th>Ngày</th><th>Số ĐĐH</th><th>Số BBGN</th><th>Mặt hàng</th><th>SL thực nhận</th><th>Đơn giá</th><th>Thành tiền</th></tr></thead>
      <tbody>${r.appendix.map((a, i) => html`<tr key=${i}><td class="c">${vnDate(a.date)}</td><td class="c">${a.order_code}</td><td class="c">${a.delivery_code}</td>
        <td>${a.product_code ? a.product_code + " – " : ""}${a.product_name}${a.unit ? ` (${a.unit})` : ""}</td><td class="r">${a.accepted_qty !== null ? num(a.accepted_qty, 3) : ""}</td>
        <td class="r">${a.unit_price !== null ? money(a.unit_price) : ""}</td><td class="r">${money(a.amount)}</td></tr>`)}
      <tr class="total"><td colspan="6">Cộng</td><td class="r">${money(r.appendix.reduce((s, a) => s + n(a.amount), 0))}</td></tr></tbody></table></div>`}
    <div class="terms small">Biên bản lập thành 02 bản có giá trị như nhau, mỗi bên giữ 01 bản. Trong vòng 04 ngày kể từ ngày nhận biên bản,
      nếu Bên B không có ý kiến phản hồi thì số liệu trên được coi là đã được xác nhận.</div>
    <div class="sign c2" style=${{ marginTop: 14 }}><div><b>ĐẠI DIỆN BÊN A</b></div><div><b>ĐẠI DIỆN BÊN B</b></div></div>
    <${Sign} cols=${[["KẾ TOÁN CÔNG NỢ"], ["GIÁM ĐỐC", "(Ký, họ tên, đóng dấu)"], ["KẾ TOÁN"], ["GIÁM ĐỐC", "(Ký, họ tên, đóng dấu)"]]} />
    <${Footer} p=${p} /></div>`;
}

function PrintApp() {
  const [p, setP] = useState(null);
  const [err, setErr] = useState(null);
  const [hidePrice, setHidePrice] = useState(qs.get("hide_price") === "1");
  useEffect(() => {
    api(`/api/print/${TYPE}/${ID}`).then((d) => { setP(d); document.title = `${d.code} – In chứng từ`; }).catch((e) => setErr(e.message));
  }, []);
  if (err) return html`<div class="sheet">Không tải được chứng từ: ${err}</div>`;
  if (!p) return html`<div class="sheet">Đang tải…</div>`;
  return html`<div>
    <div class="toolbar"><button class="primary" onClick=${() => print()}>In / Lưu PDF</button>
      ${TYPE === "dn" && html`<label><input type="checkbox" checked=${hidePrice} onChange=${(e) => setHidePrice(e.target.checked)} />Ẩn đơn giá & thành tiền khi in cho kho/giao nhận</label>`}
      ${TYPE === "dr" && html`<button onClick=${() => { location.href = `/api/reconciliations/${ID}/export.xlsx`; }}>Tải Excel</button>`}
      <span class="hint">Xuất PDF: bấm "In / Lưu PDF" rồi chọn máy in "Lưu thành PDF" (Save as PDF), khổ A4.</span></div>
    ${TYPE === "order" && html`<${OrderSheet} p=${p} />`}
    ${TYPE === "dn" && html`<${DeliverySheet} p=${p} hidePrice=${hidePrice} />`}
    ${TYPE === "dr" && html`<${ReconciliationSheet} p=${p} />`}</div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${PrintApp} />`);
