// Lakehouse × Lakebase 双方向連携デモ（フロントエンド）
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const yen = (n) => (n == null ? "-" : "¥" + Number(n).toLocaleString("ja-JP"));
const stars = (n) => "★".repeat(n) + "☆".repeat(5 - n);

// タイムゾーンなしの時刻（CDF の _timestamp）は UTC として扱う
const toDate = (v) => (v ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + "Z") : null);
const hms = (v) => { const d = toDate(v); return d ? d.toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour12: false }) : "-"; };
const ms = (v) => { const d = toDate(v); return d ? d.getTime() : null; };
const isRecent = (v, sec = 25) => { const t = ms(v); return t && Date.now() - t < sec * 1000; };

let CFG = null;
let currentTab = "customer";

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

function toast(msg, err = false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  $("#toast").appendChild(el);
  setTimeout(() => el.remove(), err ? 6000 : 3200);
}

function starInput(container, initial = 5) {
  container.innerHTML = "";
  container.dataset.value = initial;
  for (let i = 1; i <= 5; i++) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = "★"; b.title = `${i}`;
    b.onclick = () => { container.dataset.value = i; paint(); };
    container.appendChild(b);
  }
  const paint = () => $$("button", container).forEach((b, i) => b.classList.toggle("on", i < +container.dataset.value));
  paint();
}

// ---------------------------------------------------------------- tabs
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
function showTab(name) {
  currentTab = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  refreshTab();
}
function refreshTab() {
  if (currentTab === "customer") { loadCustomerProducts(); loadResponses(); }
  if (currentTab === "internal") { loadInternalProducts(); loadAnalytics(); }
  if (currentTab === "flow") loadFlow();
}

// ---------------------------------------------------------------- config
async function loadConfig() {
  CFG = await api("GET", "/api/config");
  $("#meta").innerHTML = `
    <span class="chip">👤 ${esc(CFG.user)}</span>
    <span class="chip">WS <a href="${CFG.host}" target="_blank">${esc(CFG.host.replace("https://", ""))}</a></span>
    <span class="chip">Lakebase <a href="${CFG.links.lakebase_project}" target="_blank">${esc(CFG.lakebase.project)}</a></span>
    <span class="chip">UC <a href="${CFG.links.uc_schema}" target="_blank">${esc(CFG.tables.uc_products.split(".").slice(0, 2).join("."))}</a></span>`;
}

// ---------------------------------------------------------------- 顧客アプリ（Lakebase）
let lastProducts = new Map();
async function loadCustomerProducts() {
  try {
    const d = await api("GET", "/api/customer/products");
    $("#c-src").textContent = CFG.tables.lb_synced_products.split(".").slice(1).join(".");
    $("#c-lat").textContent = `製品一覧の取得 ${d.elapsed_ms} ms`;
    const prev = lastProducts;
    lastProducts = new Map(d.rows.map((r) => [r.product_id, r.updated_at]));
    $("#c-products").innerHTML = d.rows.map((r) => {
      const fresh = prev.size && prev.get(r.product_id) !== r.updated_at;
      return `<div class="pcard ${fresh ? "fresh" : ""}">
        <span class="pid">${esc(r.product_id)}</span>
        <span class="cat">${esc(r.category)}</span>
        <span class="name">${esc(r.product_name)}</span>
        <span class="price">${yen(r.price)}</span>
        <span class="d">${esc(r.description)}</span>
        <span class="rate">${r.avg_rating ? `★ ${r.avg_rating}（${r.responses}件）` : "まだ回答がありません"}</span>
        <button class="btn small" data-answer="${esc(r.product_id)}">この製品に回答する</button>
      </div>`;
    }).join("") || `<div class="empty">販売中の製品がありません</div>`;
    $$("[data-answer]").forEach((b) => (b.onclick = () => { $("#sf-product").value = b.dataset.answer; $("#survey-form [name=customer_name]").focus(); }));
    const sel = $("#sf-product"), keep = sel.value;
    sel.innerHTML = d.rows.map((r) => `<option value="${esc(r.product_id)}">${esc(r.product_name)}（${esc(r.product_id)}）</option>`).join("");
    if (keep && d.rows.some((r) => r.product_id === keep)) sel.value = keep;
  } catch (e) { $("#c-products").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

async function loadResponses() {
  try {
    const d = await api("GET", "/api/customer/responses");
    $("#r-lat").textContent = `Lakebase から ${d.elapsed_ms} ms`;
    $("#c-responses").innerHTML = d.rows.map((r) => `
      <div class="resp">
        <div class="top"><b>${esc(r.customer_name)}</b>　${esc(r.product_name || r.product_id)}　<span class="st">${stars(r.rating)}</span>　#${r.response_id}・${hms(r.updated_at)}</div>
        <div class="cm">${esc(r.comment) || '<span class="muted">（コメントなし）</span>'}</div>
        <div class="ops">
          <button class="btn small" data-edit='${esc(JSON.stringify({ id: r.response_id, rating: r.rating, comment: r.comment }))}'>編集</button>
          <button class="btn small danger" data-del="${r.response_id}">削除</button>
        </div>
      </div>`).join("") || `<div class="empty">回答はまだありません</div>`;
    $$("[data-edit]").forEach((b) => (b.onclick = () => openEdit(JSON.parse(b.dataset.edit))));
    $$("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!confirm(`回答 #${b.dataset.del} を削除しますか？（Lakebase で DELETE）`)) return;
      try { const r = await api("DELETE", `/api/customer/responses/${b.dataset.del}`); toast(`削除しました（Lakebase ${r.elapsed_ms} ms）`); loadResponses(); loadCustomerProducts(); }
      catch (e) { toast(e.message, true); }
    }));
  } catch (e) { $("#c-responses").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

$("#survey-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target, btn = $("button[type=submit]", f);
  const body = Object.fromEntries(new FormData(f));
  body.rating = +$("#sf-stars").dataset.value;
  btn.disabled = true;
  try {
    const r = await api("POST", "/api/customer/responses", body);
    toast(`回答 #${r.response_id} を登録しました（Lakebase に INSERT：${r.elapsed_ms} ms）`);
    f.comment.value = "";
    loadResponses(); loadCustomerProducts(); pollEvents();
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});

function openEdit({ id, rating, comment }) {
  const f = $("#edit-form");
  f.response_id.value = id; f.comment.value = comment || "";
  starInput($("#ed-stars"), rating);
  $("#edit-dlg").showModal();
}
$("#edit-dlg").addEventListener("close", async () => {
  if ($("#edit-dlg").returnValue !== "ok") return;
  const f = $("#edit-form");
  try {
    const r = await api("PUT", `/api/customer/responses/${f.response_id.value}`, { rating: +$("#ed-stars").dataset.value, comment: f.comment.value });
    toast(`回答 #${f.response_id.value} を更新しました（Lakebase で UPDATE：${r.elapsed_ms} ms）`);
    loadResponses(); loadCustomerProducts(); pollEvents();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- 社内 製品マスタ（DBSQL）
let internalRows = [];
async function loadInternalProducts() {
  try {
    const d = await api("GET", "/api/internal/products");
    internalRows = d.rows;
    $("#i-src").textContent = CFG.tables.uc_products;
    $("#i-lat").textContent = `製品マスタの取得 ${d.elapsed_ms} ms（SQL ウェアハウス）`;
    $("#i-products").innerHTML = `<tr><th>製品ID</th><th>製品名</th><th>カテゴリ</th><th>価格</th><th>販売状況</th><th>説明</th><th>更新</th><th></th></tr>` +
      d.rows.map((r) => `<tr class="${isRecent(r.updated_at) ? "hot" : ""}">
        <td><code>${esc(r.product_id)}</code></td><td><b>${esc(r.product_name)}</b></td><td>${esc(r.category)}</td><td>${yen(r.price)}</td>
        <td><span class="pill ${r.status === "販売中" ? "on" : "off"}">${esc(r.status)}</span></td>
        <td class="muted">${esc(r.description)}</td><td class="muted">${hms(r.updated_at)}</td>
        <td style="white-space:nowrap">
          <button class="btn small" data-pedit="${esc(r.product_id)}">編集</button>
          <button class="btn small" data-ptoggle="${esc(r.product_id)}">${r.status === "販売中" ? "販売終了にする" : "販売再開"}</button>
          <button class="btn small danger" data-pdel="${esc(r.product_id)}">削除</button>
        </td></tr>`).join("");
    $$("[data-pedit]").forEach((b) => (b.onclick = () => editProduct(b.dataset.pedit)));
    $$("[data-ptoggle]").forEach((b) => (b.onclick = () => toggleProduct(b.dataset.ptoggle)));
    $$("[data-pdel]").forEach((b) => (b.onclick = () => deleteProduct(b.dataset.pdel)));
  } catch (e) { $("#i-products").innerHTML = `<tr><td><div class="err">${esc(e.message)}</div></td></tr>`; }
}

function editProduct(pid) {
  const r = internalRows.find((x) => x.product_id === pid), f = $("#product-form");
  ["product_id", "product_name", "category", "price", "status", "description"].forEach((k) => (f[k].value = r[k] ?? ""));
  $("#pf-title").textContent = `製品を編集：${pid}`;
  $("#pf-submit").textContent = "更新（DBSQL で MERGE）";
  $("#pf-cancel").hidden = false;
  f.scrollIntoView({ behavior: "smooth", block: "center" });
}
function resetProductForm() {
  const f = $("#product-form"); f.reset(); f.product_id.value = "";
  $("#pf-title").textContent = "製品を新規登録";
  $("#pf-submit").textContent = "登録（DBSQL で MERGE）";
  $("#pf-cancel").hidden = true;
}
$("#pf-cancel").onclick = resetProductForm;

async function saveProduct(body, okMsg) {
  const r = await api("POST", "/api/internal/products", body);
  toast(`${okMsg} ${r.product_id}（DBSQL ${r.elapsed_ms} ms）。顧客アプリへの配信を計測中…`);
  loadInternalProducts(); pollEvents();
  return r;
}
$("#product-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = Object.fromEntries(new FormData(ev.target));
  body.price = +body.price; if (!body.product_id) delete body.product_id;
  const btn = $("#pf-submit"); btn.disabled = true;
  try { await saveProduct(body, body.product_id ? "更新しました" : "登録しました"); resetProductForm(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});
async function toggleProduct(pid) {
  const r = { ...internalRows.find((x) => x.product_id === pid) };
  r.status = r.status === "販売中" ? "販売終了" : "販売中";
  delete r.updated_at;
  try { await saveProduct(r, `「${r.status}」にしました`); } catch (e) { toast(e.message, true); }
}
async function deleteProduct(pid) {
  if (!confirm(`製品 ${pid} を製品マスタから削除しますか？（DBSQL で DELETE）`)) return;
  try { const r = await api("DELETE", `/api/internal/products/${pid}`); toast(`削除しました（DBSQL ${r.elapsed_ms} ms）`); loadInternalProducts(); pollEvents(); }
  catch (e) { toast(e.message, true); }
}

function analyticsTable(rows) {
  return `<tr><th>製品</th><th>販売</th><th>件数</th><th>平均評価</th><th>最終回答</th></tr>` +
    rows.map((r) => `<tr class="${isRecent(r.last_updated, 40) ? "hot" : ""}"><td><b>${esc(r.product_name)}</b> <span class="muted">${esc(r.product_id)}</span></td>
      <td><span class="pill ${r.status === "販売中" ? "on" : "off"}">${esc(r.status)}</span></td>
      <td><b>${r.responses}</b></td><td>${r.avg_rating ?? "-"}</td><td class="muted">${hms(r.last_updated)}</td></tr>`).join("");
}
const UNAVAILABLE = () => `<div class="unavail"><b>Lakebase CDF の履歴テーブル（${esc(CFG.tables.uc_cdf_history.split(".").pop())}）がまだありません。</b><br>CDF を開始してから最初のデータが届くまで数十秒かかります。書き込み先のカタログにはデフォルトストレージを使えません。</div>`;
const changeTag = (t) => `<span class="ct ${esc(t)}">${esc(t)}</span>`;

function fillPart(tableSel, latSel, part, render) {
  if (!part || part.error) { $(tableSel).innerHTML = `<tr><td><div class="err">${esc(part ? part.error : "取得できませんでした")}</div></td></tr>`; $(latSel).textContent = ""; return; }
  $(tableSel).innerHTML = render(part.rows);
  $(latSel).textContent = `${part.elapsed_ms} ms`;
}

async function loadAnalytics() {
  $("#an-sum-lat").textContent = "集計中…"; $("#an-chg-lat").textContent = "";
  try {
    const d = await api("GET", "/api/internal/analytics");
    if (d.unavailable) {
      $("#an-sum").innerHTML = `<tr><td>${UNAVAILABLE()}</td></tr>`; $("#an-chg").innerHTML = "";
      $("#an-sum-lat").textContent = ""; return;
    }
    fillPart("#an-sum", "#an-sum-lat", d.summary, analyticsTable);
    fillPart("#an-chg", "#an-chg-lat", d.changes, (rows) =>
      `<tr><th>変更種別</th><th>#</th><th>氏名</th><th>評価</th><th>コメント</th><th>Delta 反映</th></tr>` +
      rows.map((r) => `<tr class="${isRecent(r._timestamp, 40) ? "hot" : ""}"><td>${changeTag(r._pg_change_type)}</td><td>${r.response_id}</td>
        <td>${esc(r.customer_name)}</td><td>${r.rating ?? "-"}</td><td class="muted">${esc((r.comment || "").slice(0, 18))}</td><td class="muted">${hms(r._timestamp)}</td></tr>`).join(""));
  } catch (e) { $("#an-sum").innerHTML = `<tr><td><div class="err">${esc(e.message)}</div></td></tr>`; }
}
$("#an-refresh").onclick = loadAnalytics;

// ---------------------------------------------------------------- データフロー
const sigP = (r) => [r.product_name, r.price, r.status, ms(r.updated_at)].join("|");
function setLat(sel, part) { $(sel).textContent = part && part.elapsed_ms != null ? ` ${part.elapsed_ms} ms` : ""; }
function errOr(part, render) {
  if (!part) return "";
  if (part.unavailable) return `<tr><td>${UNAVAILABLE()}</td></tr>`;
  if (part.error) return `<tr><td><div class="err">${esc(part.error)}</div></td></tr>`;
  return render(part.rows);
}

let flowBusy = false;
async function loadFlow() {
  if (flowBusy) return; flowBusy = true;
  try {
    const d = await api("GET", "/api/flow/snapshot");
    $("#f-at").textContent = `最終更新 ${new Date().toLocaleTimeString("ja-JP", { hour12: false })}`;
    const lbMap = new Map((d.lb_products.rows || []).map((r) => [r.product_id, r]));
    const ucMap = new Map((d.uc_products.rows || []).map((r) => [r.product_id, r]));
    const prodRow = (r, other) => {
      const o = other.get(r.product_id), diff = !o || sigP(o) !== sigP(r);
      return `<tr class="${diff ? "diff" : ""} ${isRecent(r.updated_at) ? "hot" : ""}"><td><code>${esc(r.product_id)}</code></td><td>${esc(r.product_name)}</td><td>${yen(r.price)}</td>
        <td><span class="pill ${r.status === "販売中" ? "on" : "off"}">${esc(r.status)}</span></td><td class="muted">${hms(r.updated_at)}</td>
        <td>${diff ? '<span class="ct update_preimage">未反映</span>' : '<span class="ct insert">一致</span>'}</td></tr>`;
    };
    const ph = `<tr><th>ID</th><th>製品名</th><th>価格</th><th>状況</th><th>更新</th><th>相手側</th></tr>`;
    $("#f-ucp").innerHTML = errOr(d.uc_products, (rows) => ph + rows.map((r) => prodRow(r, lbMap)).join(""));
    $("#f-lbp").innerHTML = errOr(d.lb_products, (rows) => ph + rows.map((r) => prodRow(r, ucMap)).join("") +
      [...ucMap.keys()].filter((k) => !lbMap.has(k)).map((k) => `<tr class="diff"><td><code>${esc(k)}</code></td><td colspan="5" class="muted">同期待ち…</td></tr>`).join(""));
    setLat("#f-ucp-lat", d.uc_products); setLat("#f-lbp-lat", d.lb_products);

    const sh = `<tr><th>#</th><th>製品</th><th>氏名</th><th>評価</th><th>更新</th></tr>`;
    const survRow = (r) => `<tr class="${isRecent(r.updated_at) ? "hot" : ""}"><td>${r.response_id}</td><td><code>${esc(r.product_id)}</code></td><td>${esc(r.customer_name)}</td><td>${"★".repeat(r.rating)}</td><td class="muted">${hms(r.updated_at)}</td></tr>`;
    $("#f-lbs").innerHTML = errOr(d.lb_surveys, (rows) => sh + rows.map(survRow).join(""));
    $("#f-cdf").innerHTML = errOr(d.cdf_history, (rows) => `<tr><th>変更種別</th><th>#</th><th>氏名</th><th>評価</th><th>Delta 反映</th></tr>` +
      rows.map((r) => `<tr class="${isRecent(r._timestamp) ? "hot" : ""}"><td>${changeTag(r._pg_change_type)}</td><td>${r.response_id}</td><td>${esc(r.customer_name)}</td><td>${r.rating ?? "-"}</td><td class="muted">${hms(r._timestamp)}</td></tr>`).join(""));
    setLat("#f-lbs-lat", d.lb_surveys); setLat("#f-cdf-lat", d.cdf_history);

    $("#n-uc-history").classList.toggle("off", !!d.cdf_history.unavailable);
    const ss = d.sync_status.rows || {};
    $("#a1-st").textContent = `${(ss.state || "").replace("SYNCED_TABLE_", "") || "-"}${ss.last_sync_end ? "・最終同期 " + hms(ss.last_sync_end) : ""}`;
    const cs = (d.cdf_status.rows || [])[0];
    $("#a2-st").textContent = cs ? `${cs.status}・最終書き込み ${hms(cs.last_write_time)}` : "開始待ち";
  } catch (e) { toast("データフローの取得に失敗: " + e.message, true); }
  finally { flowBusy = false; }
}
setInterval(() => { if (currentTab === "flow" && $("#auto").checked) loadFlow(); }, 5000);

// ---------------------------------------------------------------- 連携トラッカー
const EXPECT_SEC = { synced: 15, cdf: 20 };
const FLOWNO = { synced: ["f1", "①"], cdf: ["f2", "②"] };
const prevStatus = new Map();
let seenEvents = new Set();

async function pollEvents() {
  let list;
  try { list = await api("GET", "/api/events"); } catch { return; }
  const now = Date.now() / 1000;
  const arrivedNow = new Set();
  list.forEach((e) => e.targets.forEach((t) => {
    const k = `${e.id}:${t.name}`;
    if (prevStatus.get(k) === "waiting" && t.status === "arrived") arrivedNow.add(t.name);
    prevStatus.set(k, t.status);
  }));
  if (!list.length) {
    $("#events").innerHTML = `<div class="empty">まだ書き込みがありません。<br>アンケートを送信するか、製品を登録してください。</div>`;
    ["#a1", "#a2"].forEach((id) => $(id).classList.remove("active"));
    return;
  }
  $("#events").innerHTML = list.map((e) => {
    const isNew = !seenEvents.has(e.id);
    return `<div class="ev ${isNew ? "new" : ""}">
      <div class="ev-top"><span class="ev-org ${e.origin}">${e.origin === "lakebase" ? "顧客アプリ → Lakebase" : "社内システム → Lakehouse"}</span><span>${new Date(e.written_at * 1000).toLocaleTimeString("ja-JP", { hour12: false })}</span></div>
      <div class="ev-label">${esc(e.label)}</div>
      ${e.targets.map((t) => {
        const el = t.status === "arrived" ? t.lag_sec : now - e.written_at;
        const pct = t.status === "arrived" ? 100 : Math.min(95, (el / EXPECT_SEC[t.name]) * 100);
        let res;
        if (t.status === "arrived") res = `✓ ${t.lag_sec.toFixed(1)} 秒で到達`;
        else if (t.status === "waiting") res = `<span class="spin"></span>${el.toFixed(0)} 秒経過`;
        else if (t.status === "unavailable") res = "対象外";
        else res = t.status === "timeout" ? "タイムアウト" : "エラー";
        const extra = t.delta_processed_at ? `／Delta 反映 ${hms(t.delta_processed_at)}` : "";
        return `<div class="tg">
          <span class="nm"><span class="flowno ${FLOWNO[t.name][0]}">${FLOWNO[t.name][1]}</span>${esc(t.label.replace(/^[①②]\s*/, ""))}</span>
          <span class="res ${t.status}">${res}</span>
          ${t.status === "unavailable" ? "" : `<div class="bar"><i class="${t.status === "arrived" ? "done" : ""}" style="width:${pct}%"></i></div>`}
          <span class="dt">${esc(t.detail)}${extra}${t.status === "waiting" ? `（確認 ${t.probes} 回）` : ""}</span>
        </div>`;
      }).join("")}
    </div>`;
  }).join("");
  seenEvents = new Set(list.map((e) => e.id));

  // ダイアグラム：到達待ちのフローの矢印を光らせる
  const waiting = new Set(list.flatMap((e) => e.targets.filter((t) => t.status === "waiting").map((t) => t.name)));
  $("#a1").classList.toggle("active", waiting.has("synced"));
  $("#a2").classList.toggle("active", waiting.has("cdf"));
  $("#n-lb-products").classList.toggle("pulse", waiting.has("synced"));
  $("#n-uc-history").classList.toggle("pulse", waiting.has("cdf"));

  // 反対側に届いたら、表示中の画面を自動で読み直す
  if (arrivedNow.size) {
    if (arrivedNow.has("synced")) {
      if (currentTab === "customer") loadCustomerProducts();
      toast("製品マスタの変更が顧客アプリ（Lakebase）に届きました");
    }
    if (arrivedNow.has("cdf")) {
      if (currentTab === "internal") loadAnalytics();
    }
    if (currentTab === "flow") loadFlow();
  }
}
setInterval(pollEvents, 1000);

// ---------------------------------------------------------------- 初期状態に戻す
let resetTimer = null;
$("#reset-open").onclick = () => {
  $("#reset-confirm").hidden = false; $("#reset-progress").hidden = true;
  $("#reset-dlg").showModal();
};
$("#reset-cancel").onclick = () => $("#reset-dlg").close();
$("#reset-dlg").addEventListener("cancel", (e) => { if (resetTimer) e.preventDefault(); }); // 実行中は Esc で閉じない
$("#reset-run").onclick = async () => {
  try { await api("POST", "/api/admin/reset"); } catch (e) { if (!/実行中/.test(e.message)) { toast(e.message, true); return; } }
  showResetProgress();
};
$("#reset-close").onclick = () => { $("#reset-dlg").close(); lastProducts = new Map(); refreshTab(); };

function showResetProgress() {
  $("#reset-confirm").hidden = true; $("#reset-progress").hidden = false;
  $("#reset-close").hidden = true; $("#reset-msg").textContent = "";
  $("#reset-title").textContent = "初期状態に戻しています…";
  if (!$("#reset-dlg").open) $("#reset-dlg").showModal();
  clearInterval(resetTimer);
  resetTimer = setInterval(pollReset, 1000);
  pollReset();
}

async function pollReset() {
  let st;
  try { st = await api("GET", "/api/admin/reset"); } catch { return; }
  const now = Date.now() / 1000;
  $("#reset-steps").innerHTML = st.steps.map((s, i) => {
    const ic = { pending: "・", running: '<span class="spin"></span>', done: "✓", error: "✗" }[s.status];
    const sec = s.status === "done" ? `${s.sec} 秒` : s.status === "running" ? "" : "";
    return `<li class="${s.status}"><span class="ic ${s.status}">${ic}</span><span>${i + 1}. ${esc(s.label)}</span><span class="sec">${sec}</span>${s.detail ? `<span class="dt">${esc(s.detail)}</span>` : ""}</li>`;
  }).join("");
  const el = st.started_at ? Math.round((st.finished_at || now) - st.started_at) : 0;
  if (st.running) { $("#reset-msg").innerHTML = `<span class="muted">経過 ${el} 秒</span>`; return; }
  clearInterval(resetTimer); resetTimer = null;
  $("#reset-close").hidden = false;
  if (st.error) {
    $("#reset-title").textContent = "初期化に失敗しました";
    $("#reset-msg").innerHTML = `<div class="err">${esc(st.error)}</div>`;
  } else {
    $("#reset-title").textContent = "初期状態に戻しました";
    $("#reset-msg").innerHTML = `<span class="muted">所要 ${el} 秒。製品 6 件・アンケート 5 件・変更履歴 5 件の状態です。</span>`;
  }
}

// ---------------------------------------------------------------- init
(async () => {
  starInput($("#sf-stars"), 5);
  try { await loadConfig(); } catch (e) { toast("設定の取得に失敗: " + e.message, true); return; }
  refreshTab();
  pollEvents();
  try { if ((await api("GET", "/api/admin/reset")).running) showResetProgress(); } catch {}
})();
