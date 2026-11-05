// Tiện ích dùng chung: gọi API, định dạng số tiền kiểu Việt Nam, các ô nhập liệu nhỏ.
export const React = window.React;
export const { useState, useEffect, useRef, useCallback, useMemo } = React;
export const html = window.htm.bind(React.createElement);

// Người dùng & vai trò hiện tại (Cài đặt -> Người dùng). Gửi kèm mọi request: X-User, X-Role.
export function getIdentity() {
  try {
    return { user: localStorage.getItem("sm.user") || "", role: localStorage.getItem("sm.role") || "" };
  } catch (e) { return { user: "", role: "" }; }
}
export function setIdentity(user, role) {
  try { localStorage.setItem("sm.user", user || ""); localStorage.setItem("sm.role", role || ""); } catch (e) { /* bỏ qua */ }
}
export const ROLES = {
  ADMIN: "Quản trị", MANAGER: "Quản lý", CHIEF_ACCOUNTANT: "Kế toán trưởng", ACCOUNTANT: "Kế toán", STAFF: "Nhân viên KD / kho",
};

export async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  const who = getIdentity();
  if (who.user) init.headers["X-User"] = encodeURIComponent(who.user);
  if (who.role) init.headers["X-Role"] = who.role;
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
    delete init.json;
  }
  const res = await fetch(path, init);
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Lỗi ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
    throw new Error(msg);
  }
  return data;
}

// Tiền/kg trả về dạng chuỗi chính xác ("1829047800", "42.4") – chỉ đổi sang số khi hiển thị.
export function money(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("vi-VN", { maximumFractionDigits: 0 });
}
export function num(v, digits = 1) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}
export function vnDate(iso) {
  if (!iso) return "";
  const [y, m, d] = String(iso).slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}
export function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
export function thisPeriod() { return today().slice(0, 7); }
export function periodBounds(p) {
  const [y, m] = p.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  return [`${p}-01`, `${p}-${String(last).padStart(2, "0")}`];
}
export function prevPeriod(p, n = 1) {
  let [y, m] = p.split("-").map(Number);
  m -= n; while (m <= 0) { m += 12; y -= 1; }
  return `${y}-${String(m).padStart(2, "0")}`;
}
// "47.500" / "47,5" / "1.829.047.800" -> chuỗi số chuẩn cho API
export function parseNum(text) {
  if (text === null || text === undefined) return null;
  let s = String(text).trim().replace(/\s/g, "");
  if (!s) return null;
  if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
  else s = s.replace(",", ".");
  return Number.isNaN(Number(s)) ? null : s;
}

export const ASSET_TYPES = {
  EMPTY_CYLINDER: "Vỏ bình", STOVE: "Bếp CN", PIPING: "Hệ thống dẫn gas", EXTINGUISHER: "Bình cứu hỏa",
  VALVE: "Chế/khóa/van", GIFT: "Quà tặng", OTHER: "Khác",
};
export const DIRECTIONS = { LEND: "Cho mượn", RETURN: "Khách trả", INVEST: "Công ty đầu tư", GIFT: "Tặng" };
export const STATUS = {
  DRAFT: "Nháp", POSTED: "Đã ghi sổ", CANCELLED: "Đã hủy", PENDING_REVIEW: "Chờ duyệt", APPLIED: "Đã ghi sổ",
  REJECTED: "Từ chối", FAILED: "Lỗi", SENT: "Đã gửi khách", CONFIRMED: "Khách đã xác nhận (khóa sổ)",
};
// Trạng thái quy trình Đơn hàng -> BBGN -> Đối chiếu
export const FLOW_STATUS = {
  DRAFT: ["Nháp", "neutral"], PENDING_APPROVAL: ["Chờ duyệt", "warn"], APPROVED: ["Đã duyệt", "info"],
  PARTIALLY_DELIVERED: ["Giao từng phần", "warn"], COMPLETED: ["Hoàn tất", "ok"], CANCELLED: ["Đã hủy", "bad"],
  PREPARING: ["Đang chuẩn bị", "neutral"], IN_TRANSIT: ["Đang đi giao", "info"], CONFIRMED: ["Đã xác nhận", "ok"],
  SENT: ["Đã gửi đối tác", "info"], DISPUTED: ["Có chênh lệch", "bad"], RECONCILED: ["Đã khớp & khóa sổ", "ok"],
};
export function FlowBadge({ s }) {
  const [label, tone] = FLOW_STATUS[s] || [s, "neutral"];
  return html`<span class=${"pill " + tone}>${label}</span>`;
}
export const ENTRY_TYPES = {
  DELIVERY_INCREASE: "Giao nhận (tăng nợ)", PAYMENT_DECREASE: "Thanh toán", RETURN_DECREASE: "Trả hàng / giảm giá",
  ADJUSTMENT: "Điều chỉnh", OPENING: "Số dư đầu",
};

// ---------------------------------------------------------------- đọc số tiền bằng chữ tiếng Việt
const DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"];

function readTriple(n, full, zeroTens) {
  const h = Math.floor(n / 100), t = Math.floor((n % 100) / 10), u = n % 10;
  const out = [];
  if (full || h > 0) out.push(DIGITS[h] + " trăm");
  if (t === 0) {
    if (u > 0 && (full || h > 0)) out.push(zeroTens);
  } else if (t === 1) out.push("mười");
  else out.push(DIGITS[t] + " mươi");
  if (u > 0) {
    if (u === 1 && t >= 2) out.push("mốt");
    else if (u === 4 && t >= 2) out.push("tư");
    else if (u === 5 && t >= 1) out.push("lăm");
    else out.push(DIGITS[u]);
  }
  return out.join(" ");
}

function readBelowBillion(n, full, zeroTens) {
  // n < 1.000.000.000 (BigInt) ; full = đã có nhóm lớn hơn đứng trước
  const groups = [Number(n / 1000000n), Number((n / 1000n) % 1000n), Number(n % 1000n)];
  const scales = ["triệu", "nghìn", ""];
  const out = [];
  let started = full;
  groups.forEach((g, i) => {
    if (g === 0) return;
    out.push((readTriple(g, started, zeroTens) + " " + scales[i]).trim());
    started = true;
  });
  return out.join(" ");
}

function readBig(n, zeroTens) {
  if (n < 1000000000n) return readBelowBillion(n, false, zeroTens);
  const head = readBig(n / 1000000000n, zeroTens) + " tỷ";
  const rest = n % 1000000000n;
  return rest === 0n ? head : head + " " + readBelowBillion(rest, true, zeroTens);
}

/**
 * Đọc số tiền thành chữ tiếng Việt: 1250000 -> "Một triệu hai trăm năm mươi nghìn đồng chẵn".
 * Xử lý: mốt (21), tư (24), lăm (15, 25), linh/lẻ (105), mười (10–19), "không trăm" ở nhóm sau, tỷ/nghìn tỷ.
 * @param amount số hoặc chuỗi số ("1829047800", "-5000", "1.250.000")
 * @param opts { zero: "lẻ" (mặc định, giống bảng kê Excel) | "linh", unit: "đồng", suffix: "chẵn" }
 */
export function readVietnameseCurrency(amount, opts = {}) {
  const zeroTens = opts.zero || "lẻ";
  const unit = opts.unit === undefined ? "đồng" : opts.unit;
  const suffix = opts.suffix === undefined ? "chẵn" : opts.suffix;
  let text = String(amount ?? "").trim().replace(/\s/g, "");
  if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(text)) text = text.replace(/\./g, "").replace(",", ".");
  if (!/^-?\d+(\.\d+)?$/.test(text)) return "";
  const negative = text.startsWith("-");
  let [intPart, frac = ""] = text.replace("-", "").split(".");
  let n = BigInt(intPart || "0");
  if (frac && Number(frac[0]) >= 5) n += 1n;          // làm tròn tới đồng
  let words = n === 0n ? "không" : readBig(n, zeroTens);
  if (negative && n !== 0n) words = "âm " + words;
  words = [words, unit, n === 0n ? "" : suffix].filter(Boolean).join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// ---------------------------------------------------------------- biểu tượng SVG (Lucide, ISC license)
const ICONS = {
  home: "M3 9.5 12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13h6M9 17h6",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  scan: "M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  sheet: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18",
  wallet: "M20 12V8H6a2 2 0 0 1 0-4h12v4M4 6v12a2 2 0 0 0 2 2h14v-4M18 12a2 2 0 0 0 0 4h4v-4z",
  box: "M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  cart: "M9 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2M20 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6",
  truck: "M1 3h15v13H1zM16 8h4l3 3v5h-7zM5.5 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5M18.5 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5",
  scale: "M16 16l3-8 3 8a5 5 0 0 1-6 0M2 16l3-8 3 8a5 5 0 0 1-6 0M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2",
  printer: "M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01",
  lock: "M5 11h14v10H5zM7 11V7a5 5 0 0 1 10 0v4",
  plus: "M12 5v14M5 12h14",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  camera: "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  arrowLeft: "M19 12H5M12 19l-7-7 7-7",
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
};
export function Icon({ name, size = 16, label }) {
  return html`<svg class="ico" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden=${label ? undefined : "true"}
    role=${label ? "img" : undefined} aria-label=${label}><path d=${ICONS[name] || ""} /></svg>`;
}

// Ngăn kéo xem nhanh chứng từ (không chuyển trang)
export function Drawer({ title, children, onClose }) {
  useEffect(() => {
    const f = (e) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", f); return () => removeEventListener("keydown", f);
  }, []);
  return html`<div class="drawer-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label=${title}>
      <header><h3>${title}</h3><button class="icon-btn" aria-label="Đóng" onClick=${onClose}><${Icon} name="x" /></button></header>
      <div class="drawer-body">${children}</div></aside></div>`;
}

export function openPrint(type, id, extra = "") {
  window.open(`print.html?type=${type}&id=${id}${extra}`, "_blank", "noopener");
}

export function Msg({ msg }) {
  if (!msg) return null;
  return html`<div class=${"msg " + (msg.type || "ok")}>${msg.text}</div>`;
}

export function Badge({ s }) {
  return html`<span class=${"badge " + s}>${STATUS[s] || s}</span>`;
}

// Ô nhập số: hiển thị theo kiểu VN, trả về chuỗi số chuẩn
export function NumInput({ value, onChange, width = 110, placeholder, autoFocus, onKeyDown }) {
  const [text, setText] = useState(value ?? "");
  const [focus, setFocus] = useState(false);
  useEffect(() => { if (!focus) setText(value === null || value === undefined ? "" : String(value)); }, [value, focus]);
  return html`<input class="num" style=${{ width }} value=${focus ? text : (text === "" ? "" : num(text, 3))}
    placeholder=${placeholder} autoFocus=${autoFocus} onKeyDown=${onKeyDown}
    onFocus=${() => setFocus(true)}
    onBlur=${() => { setFocus(false); onChange(parseNum(text)); }}
    onInput=${(e) => { setText(e.target.value); onChange(parseNum(e.target.value)); }} />`;
}

export function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const reload = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    Promise.resolve().then(fn)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((e) => setState({ data: null, error: e.message, loading: false }));
  }, deps); // eslint-disable-line
  useEffect(reload, [reload]);
  return { ...state, reload };
}

export function PartnerSelect({ partners, value, onChange, all = false, label = "Khách hàng" }) {
  return html`<label class="grow">${label}
    <select value=${value || ""} onChange=${(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">${all ? "— Tất cả —" : "— Chọn —"}</option>
      ${(partners || []).map((p) => html`<option key=${p.id} value=${p.id}>${p.code} – ${p.name}</option>`)}
    </select></label>`;
}

export function Modal({ title, children, onClose }) {
  return html`<div class="modal" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="box"><h3 style=${{ marginTop: 0 }}>${title}</h3>${children}</div></div>`;
}

export function AskReason({ title, onOk, onClose }) {
  const [text, setText] = useState("");
  return html`<${Modal} title=${title} onClose=${onClose}>
    <textarea style=${{ width: "100%" }} rows="3" autoFocus value=${text} onInput=${(e) => setText(e.target.value)}
      placeholder="Lý do (bắt buộc)"></textarea>
    <div class="row" style=${{ marginTop: 10 }}>
      <button class="primary" disabled=${!text.trim()} onClick=${() => onOk(text.trim())}>Đồng ý</button>
      <button onClick=${onClose}>Thôi</button></div>
  <//>`;
}

// Hộp xác nhận (xóa...): nội dung tùy ý, nút đỏ
export function Confirm({ title, children, okText = "Đồng ý", onOk, onClose, danger = true }) {
  return html`<${Modal} title=${title} onClose=${onClose}>
    ${children}
    <div class="row" style=${{ marginTop: 10 }}>
      <button class=${danger ? "danger" : "primary"} autoFocus onClick=${onOk}>${okText}</button>
      <button onClick=${onClose}>Thôi</button></div>
  <//>`;
}

export const VAT_MODES = { INCLUDED: "Giá đã gồm thuế GTGT", EXCLUDED: "Giá chưa thuế – cộng thuế GTGT riêng" };
export const STATEMENT_TEMPLATES = {
  STANDARD: "Mẫu chung (Stargate) – theo bếp, nợ cũ, thanh toán",
  VAT_DETAIL: "Mẫu tách thuế GTGT (Welstory) – chưa thuế | thuế | tổng cộng",
};

export function download(url) {
  const a = document.createElement("a");
  a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
}
