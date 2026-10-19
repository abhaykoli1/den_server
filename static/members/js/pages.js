/* Rowdy's Den · Members App — pages.js
   Page-specific progressive enhancement: live timers + polling, product
   stepper, cart checkout, booking form + calendar, home filters, and the
   login screen (demo Google sign-in). */
(function () {
  "use strict";
  var DEN = window.DEN || {};
  var U = window.DENx || {};
  var $ = U.$, $$ = U.$$, esc = U.esc, money = U.money, toast = U.toast, api = U.api, busy = U.busy,
      openSheet = U.openSheet, closeSheet = U.closeSheet, Cart = U.Cart;
  var CART_KEY = "den.cart." + (DEN.clubId || "x");

  /* ------------------------------------------------------------ live timers + polling */
  var serverOffset = DEN.serverNow ? (Date.now() - Date.parse(DEN.serverNow)) : 0;
  function fmtElapsed(startIso) {
    var secs = Math.max(0, Math.floor((Date.now() - serverOffset - Date.parse(startIso)) / 1000));
    var h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return (h ? String(h).padStart(2, "0") + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }
  function tick() { $$("[data-since]").forEach(function (el) { el.textContent = fmtElapsed(el.getAttribute("data-since")); }); }
  if ($("[data-since]")) { tick(); setInterval(tick, 1000); }
  if ($("[data-live-tables]") && DEN.token) {
    setInterval(function () {
      api("GET", "/member-portal/tables").then(function (r) {
        serverOffset = Date.now() - Date.parse(r.serverNow);
        var changed = false;
        r.tables.forEach(function (t) {
          var el = $('[data-table-id="' + t.id + '"]'); if (!el) return;
          var was = el.getAttribute("data-status"); if (was !== t.status) changed = true;
        });
        if (changed) location.reload();
      }).catch(function () { });
    }, Number(DEN.tablesPollMs || 20000));
  }
  if ($("[data-order-poll]") && DEN.token) {
    var orderId = $("[data-order-poll]").getAttribute("data-order-poll");
    setInterval(function () {
      api("GET", "/member-portal/orders/" + orderId).then(function (r) {
        var cur = $("[data-order-poll]").getAttribute("data-fulfilment");
        if (r.order.fulfilment !== cur || (r.order.paid && $("[data-order-poll]").getAttribute("data-paid") !== "1")) location.reload();
      }).catch(function () { });
    }, Number(DEN.ordersPollMs || 45000));
  }

  /* ------------------------------------------------------------ product page stepper */
  var pq = $("[data-product-qty]");
  if (pq) {
    var pid = pq.getAttribute("data-product-qty"), price = Number(pq.getAttribute("data-price")), stock = Number(pq.getAttribute("data-stock") || 99);
    var q = 1, qEl = $("[data-q]", pq), total = $("[data-product-total]");
    function draw() { qEl.textContent = q; if (total) total.textContent = money(price * q); $("[data-dec]", pq).disabled = q <= 1; $("[data-inc]", pq).disabled = q >= stock; }
    $("[data-dec]", pq).addEventListener("click", function () { q = Math.max(1, q - 1); draw(); });
    $("[data-inc]", pq).addEventListener("click", function () { q = Math.min(stock, q + 1); draw(); });
    var addBtn = $("[data-product-add]");
    if (addBtn) addBtn.addEventListener("click", function () {
      var meta = { name: addBtn.getAttribute("data-name"), price: price, image: addBtn.getAttribute("data-image") || "", emoji: addBtn.getAttribute("data-emoji") || "", kind: addBtn.getAttribute("data-kind") || "", stock: stock, hue: addBtn.getAttribute("data-hue") || "30" };
      if (Cart.qty(pid) + q > stock) { toast("Only " + stock + " in stock", "bad"); return; }
      Cart.add(pid, meta, q);
      var note = $("[data-item-note]"); if (note && note.value.trim()) { try { var notes = JSON.parse(localStorage.getItem(CART_KEY + ".notes") || "{}"); notes[pid] = note.value.trim(); localStorage.setItem(CART_KEY + ".notes", JSON.stringify(notes)); } catch (e) { } }
      toast("Added to cart · " + Cart.qty(pid) + " × " + meta.name, "ok");
      addBtn.textContent = "Added ✓ · View cart"; addBtn.setAttribute("data-go", "1");
      addBtn.onclick = function () { location.href = "/members/cart"; };
    });
    draw();
  }

  /* ------------------------------------------------------------ cart page */
  var cartPage = $("[data-cart-page]");
  if (cartPage) {
    var catalog = {}; (DEN.catalog || []).forEach(function (i) { catalog[i.id] = i; });
    var wallet = Number(DEN.wallet || 0), due = Number(DEN.due || 0), dueLimit = Number(DEN.dueLimit || 0);
    var mode = "wallet", tableId = DEN.myTableId || "", notes = {};
    try { notes = JSON.parse(localStorage.getItem(CART_KEY + ".notes") || "{}"); } catch (e) { }
    function lines() {
      return Cart.lines().map(function (l) { var c = catalog[l.id]; if (!c) return null; return { id: l.id, qty: Math.min(l.qty, c.stockQty || 0), name: c.name, price: c.price, image: c.image, emoji: c.emoji, stock: c.stockQty, inStock: c.inStock, kind: c.kind }; }).filter(Boolean);
    }
    function total() { return lines().reduce(function (s, l) { return s + l.price * l.qty; }, 0); }
    function renderCart() {
      var ls = lines(), t = total(), list = $("[data-cart-lines]");
      if (!ls.length) { list.innerHTML = '<div class="empty"><div class="e">🛒</div><div class="t">Your cart is empty</div><div class="small mt4">Add something from the cafe or the store.</div><div class="row mt16 gap8" style="justify-content:center"><a class="btn sm light" href="/members/cafe">Cafe</a><a class="btn sm light" href="/members/shop">Store</a></div></div>'; $("[data-checkout]").hidden = true; return; }
      $("[data-checkout]").hidden = false;
      list.innerHTML = ls.map(function (l) {
        var th = l.image ? '<img src="' + l.image + '" alt="">' : '<span>' + (l.emoji || "🛍️") + '</span>';
        var low = l.qty >= l.stock ? '<div class="tiny warn b">Only ' + l.stock + ' in stock</div>' : "";
        var note = notes[l.id] ? '<div class="tiny muted">Note: ' + esc(notes[l.id]) + '</div>' : "";
        return '<div class="pmini card solid tight"><div class="th">' + th + '</div><div class="grow"><div class="b">' + esc(l.name) + '</div><div class="small muted">' + money(l.price) + ' each</div>' + low + note + '</div>' +
          '<div class="col" style="align-items:flex-end"><div class="stepper sm"><button data-cq="' + l.id + '" data-d="-1" aria-label="Less"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 12h14"/></svg></button><span class="q">' + l.qty + '</span><button data-cq="' + l.id + '" data-d="1" aria-label="More"' + (l.qty >= l.stock ? " disabled" : "") + '><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button></div><div class="b mt4">' + money(l.price * l.qty) + '</div></div></div>';
      }).join("");
      $$("[data-cq]", list).forEach(function (b) { b.addEventListener("click", function () { var id = b.getAttribute("data-cq"), d = Number(b.getAttribute("data-d")); Cart.set(id, Cart.qty(id) + d); renderCart(); }); });
      $("[data-cart-total]").textContent = money(t);
      var n = ls.reduce(function (s, l) { return s + l.qty; }, 0);
      $$("[data-cart-count-lbl]").forEach(function (el) { el.textContent = n + " item" + (n === 1 ? "" : "s"); });
      // payment options
      var walletOk = wallet >= t, room = dueLimit ? Math.max(0, dueLimit - due) : Infinity, counterOk = t <= room;
      var wc = $('[data-mode="wallet"]'), cc = $('[data-mode="counter"]'), uc = $('[data-mode="upi"]');
      wc.classList.toggle("off", !walletOk); $("[data-wallet-hint]").textContent = walletOk ? "Balance " + money(wallet) + " → " + money(wallet - t) + " after" : "Balance " + money(wallet) + " — short by " + money(t - wallet);
      cc.classList.toggle("off", !counterOk); $("[data-counter-hint]").textContent = counterOk ? "Goes on your tab · pay when you leave" + (dueLimit ? " · " + money(room - t) + " tab room left" : "") : "Tab limit reached (" + money(dueLimit) + ") — clear dues first";
      if (uc) uc.classList.toggle("off", !counterOk);
      if (mode === "wallet" && !walletOk) mode = counterOk ? "counter" : mode;
      $$("[data-mode]").forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-mode") === mode); });
      $("[data-upi-box]") && ($("[data-upi-box]").hidden = mode !== "upi");
      if (mode === "upi" && $("[data-upi-amt]")) { $("[data-upi-amt]").textContent = money(t); var link = $("[data-upi-link]"); if (link) link.href = "upi://pay?pa=" + encodeURIComponent(DEN.upiId || "") + "&pn=" + encodeURIComponent(DEN.clubName || "Club") + "&am=" + t.toFixed(2) + "&cu=INR&tn=" + encodeURIComponent("Order " + (DEN.memberName || "")); }
      var place = $("[data-place]");
      place.textContent = (mode === "wallet" ? "Pay " + money(t) + " from wallet" : mode === "upi" ? "I've paid " + money(t) + " · place order" : "Place order · pay " + money(t) + " at counter");
      place.disabled = (mode === "wallet" && !walletOk) || ((mode === "counter" || mode === "upi") && !counterOk);
    }
    $$("[data-mode]").forEach(function (c) { c.addEventListener("click", function () { if (c.classList.contains("off")) { toast(c.getAttribute("data-mode") === "wallet" ? "Not enough wallet balance for this order" : "Your tab limit is reached", "bad"); return; } mode = c.getAttribute("data-mode"); renderCart(); }); });
    var tsel = $("[data-table-select]"); if (tsel) { tsel.value = tableId; tsel.addEventListener("change", function () { tableId = tsel.value; }); }
    $("[data-place]").addEventListener("click", function () {
      var btn = this, ls = lines(); if (!ls.length) return;
      var body = { items: ls.map(function (l) { return { menuItemId: l.id, quantity: l.qty }; }), mode: mode, note: ($("[data-order-note]") && $("[data-order-note]").value.trim()) || "", tableId: tableId || null };
      var noteBits = ls.filter(function (l) { return notes[l.id]; }).map(function (l) { return l.name + ": " + notes[l.id]; });
      if (noteBits.length) body.note = (body.note ? body.note + " · " : "") + noteBits.join(" · ");
      if (mode === "upi") { var utr = ($("[data-utr]").value || "").trim(); if (utr.length < 4) { toast("Enter the UPI reference / UTR from your payment app", "bad"); $("[data-utr]").focus(); return; } body.utr = utr; }
      busy(btn, true);
      api("POST", "/member-portal/orders", body).then(function (r) {
        Cart.clear(); try { localStorage.removeItem(CART_KEY + ".notes"); } catch (e) { }
        location.href = "/members/orders/" + r.order.id + "?placed=1";
      }).catch(function (e) { busy(btn, false); toast(e.message, "bad", 4000); if (e.status === 400) { api("GET", "/member-portal/catalog").then(function (c) { c.items.forEach(function (i) { catalog[i.id] = i; }); wallet = c.walletBalance; renderCart(); }).catch(function () { }); } });
    });
    renderCart();
  }

  /* ------------------------------------------------------------ booking form */
  var bf = $("[data-booking-form]");
  if (bf) {
    var st = { tableId: bf.getAttribute("data-table") || "", date: bf.getAttribute("data-date") || "", start: "", minutes: 60, players: 2, busy: [], nowIST: "" };
    var rules = { open: Number(bf.getAttribute("data-open") || 8), closeH: Number(bf.getAttribute("data-close-h") || 23), closeM: Number(bf.getAttribute("data-close-m") || 30) };
    var tables = $$("[data-bt]", bf), days = $$("[data-bd]", bf), slotsEl = $("[data-slots]", bf), durs = $$("[data-dur]", bf);
    if (!st.tableId && tables[0]) st.tableId = tables[0].getAttribute("data-bt");
    if (!st.date && days[0]) st.date = days[0].getAttribute("data-bd");
    function hm(mins) { return String(Math.floor(mins / 60)).padStart(2, "0") + ":" + String(mins % 60).padStart(2, "0"); }
    function pretty(hmStr) { var p = hmStr.split(":"), h = Number(p[0]), m = Number(p[1]); var suf = h < 12 ? "AM" : "PM", h12 = h % 12 || 12; return m ? h12 + ":" + String(m).padStart(2, "0") + " " + suf : h12 + " " + suf; }
    function toMin(hmStr) { var p = hmStr.split(":"); return Number(p[0]) * 60 + Number(p[1]); }
    function loadAvail() {
      slotsEl.innerHTML = '<div class="skeleton" style="height:42px;grid-column:1/-1"></div>';
      api("GET", "/member-portal/bookings/availability?tableId=" + encodeURIComponent(st.tableId) + "&date=" + encodeURIComponent(st.date)).then(function (r) { st.busy = r.busy || []; st.nowIST = r.nowIST || ""; drawSlots(); }).catch(function (e) { slotsEl.innerHTML = '<div class="error" style="grid-column:1/-1">' + e.message + '</div>'; });
    }
    function slotState(startMin, minutes) {
      var endMin = startMin + minutes, closeMin = rules.closeH * 60 + rules.closeM;
      if (endMin > closeMin) return "off";
      if (st.nowIST && st.nowIST.slice(0, 10) === st.date) { var nowMin = toMin(st.nowIST.slice(11)); if (startMin <= nowMin) return "past"; }
      for (var i = 0; i < st.busy.length; i++) { var b = st.busy[i], bs = toMin(b.start), be = toMin(b.end); if (startMin < be && endMin > bs) return b.mine ? "mine" : "busy"; }
      return "ok";
    }
    function drawSlots() {
      var html = "", startMin = rules.open * 60, closeMin = rules.closeH * 60 + rules.closeM;
      for (var m = startMin; m + 30 <= closeMin; m += 30) {
        var stt = slotState(m, st.minutes), label = pretty(hm(m));
        if (stt === "off") continue;
        var cls = "slot" + (stt === "busy" ? " busy" : stt === "mine" ? " mine" : stt === "past" ? " past" : "") + (st.start === hm(m) ? " on" : "");
        if (st.start && stt === "ok" && m > toMin(st.start) && m < toMin(st.start) + st.minutes) cls += " in";
        html += '<button type="button" class="' + cls + '" data-slot="' + hm(m) + '">' + label + '</button>';
      }
      slotsEl.innerHTML = html || '<div class="empty" style="grid-column:1/-1">No slots left today</div>';
      if (st.start && slotState(toMin(st.start), st.minutes) !== "ok") { st.start = ""; }
      $$("[data-slot]", slotsEl).forEach(function (b) { b.addEventListener("click", function () { st.start = b.getAttribute("data-slot"); drawSlots(); }); });
      summary();
    }
    function summary() {
      var t = tables.filter(function (x) { return x.getAttribute("data-bt") === st.tableId; })[0];
      var rate = t ? Number(t.getAttribute("data-rate") || 0) : 0, est = Math.round(rate * st.minutes / 60);
      var el = $("[data-bsum]", bf);
      if (!st.start) { el.innerHTML = '<span class="muted">Pick a start time</span>'; $("[data-book]", bf).disabled = true; return; }
      var endMin = toMin(st.start) + st.minutes;
      el.innerHTML = '<div class="b">' + (t ? t.getAttribute("data-name") : "") + ' · ' + pretty(st.start) + ' – ' + pretty(hm(endMin)) + '</div><div class="small muted">' + st.players + ' player' + (st.players > 1 ? "s" : "") + ' · est. ' + money(est) + ' table time (billed at the counter)</div>';
      $("[data-book]", bf).disabled = false;
    }
    tables.forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-bt") === st.tableId); b.addEventListener("click", function () { st.tableId = b.getAttribute("data-bt"); tables.forEach(function (x) { x.classList.toggle("on", x === b); }); st.start = ""; loadAvail(); }); });
    days.forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-bd") === st.date); b.addEventListener("click", function () { st.date = b.getAttribute("data-bd"); days.forEach(function (x) { x.classList.toggle("on", x === b); }); st.start = ""; loadAvail(); }); });
    durs.forEach(function (b) { b.classList.toggle("on", Number(b.getAttribute("data-dur")) === st.minutes); b.addEventListener("click", function () { st.minutes = Number(b.getAttribute("data-dur")); durs.forEach(function (x) { x.classList.toggle("on", x === b); }); drawSlots(); }); });
    var pl = $("[data-players]", bf);
    if (pl) { $("[data-dec]", pl).addEventListener("click", function () { st.players = Math.max(1, st.players - 1); $("[data-q]", pl).textContent = st.players; summary(); }); $("[data-inc]", pl).addEventListener("click", function () { st.players = Math.min(8, st.players + 1); $("[data-q]", pl).textContent = st.players; summary(); }); }
    $("[data-book]", bf).addEventListener("click", function () {
      var btn = this; if (!st.start) return; busy(btn, true);
      api("POST", "/member-portal/bookings", { tableId: st.tableId, date: st.date, start: st.start, end: hm(toMin(st.start) + st.minutes), players: st.players, notes: ($("[data-bnotes]", bf) && $("[data-bnotes]").value.trim()) || "" })
        .then(function (r) { location.href = "/members/bookings/" + r.booking.id + "?new=1"; })
        .catch(function (e) { busy(btn, false); toast(e.message, "bad", 4500); loadAvail(); });
    });
    var scroller = $("[data-tables-scroll]", bf); var onT = $(".tp.on", bf); if (scroller && onT) scroller.scrollLeft = Math.max(0, onT.offsetLeft - 18);
    loadAvail();
  }

  /* ------------------------------------------------------------ calendar (bookings) */
  var cal = $("[data-cal]");
  if (cal) {
    var bookings = []; try { bookings = JSON.parse(cal.getAttribute("data-bookings") || "[]"); } catch (e) { }
    var byDay = {}; bookings.forEach(function (b) { (byDay[b.dateIST] = byDay[b.dateIST] || []).push(b); });
    var todayIso = cal.getAttribute("data-today"), cur = new Date(todayIso + "T00:00:00"), sel = todayIso;
    var listEl = $("[data-cal-list]");
    function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
    function pretty2(hmStr) { var p = hmStr.split(":"), h = Number(p[0]), m = Number(p[1]); var suf = h < 12 ? "AM" : "PM", h12 = h % 12 || 12; return m ? h12 + ":" + String(m).padStart(2, "0") + " " + suf : h12 + " " + suf; }
    function draw() {
      var y = cur.getFullYear(), m = cur.getMonth();
      $("[data-cal-month]", cal).textContent = cur.toLocaleString("en-IN", { month: "long" });
      $("[data-cal-year]", cal).textContent = String(y);
      var first = new Date(y, m, 1), startDow = (first.getDay() + 6) % 7, dim = new Date(y, m + 1, 0).getDate(), prevDim = new Date(y, m, 0).getDate();
      var html = ["M", "T", "W", "T", "F", "S", "S"].map(function (d) { return '<div class="dow">' + d + '</div>'; }).join("");
      for (var i = 0; i < startDow; i++) html += '<div class="day dim">' + (prevDim - startDow + 1 + i) + '</div>';
      for (var d = 1; d <= dim; d++) { var di = iso(new Date(y, m, d)); html += '<button type="button" class="day' + (di === todayIso ? " today" : "") + (di === sel ? " sel" : "") + (byDay[di] ? " has" : "") + '" data-day="' + di + '">' + d + '</button>'; }
      var rem = (7 - ((startDow + dim) % 7)) % 7; for (var k = 1; k <= rem; k++) html += '<div class="day dim">' + k + '</div>';
      $("[data-cal-grid]", cal).innerHTML = html;
      $$("[data-day]", cal).forEach(function (b) { b.addEventListener("click", function () { sel = b.getAttribute("data-day"); draw(); }); });
      drawList();
    }
    function drawList() {
      var rows = byDay[sel] || [];
      var d = new Date(sel + "T00:00:00"), label = d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
      if (!rows.length) { listEl.innerHTML = '<div class="month-head">' + label + '</div><div class="card"><div class="muted small">No bookings this day.</div><a class="btn sm mt12 light" href="/members/bookings?tab=new&date=' + sel + '">Book a table for ' + d.toLocaleDateString("en-IN", { day: "numeric", month: "short" }) + '</a></div>'; return; }
      listEl.innerHTML = '<div class="month-head">' + label + '</div>' + rows.map(function (b) { return '<a class="lrow" href="/members/bookings/' + b.id + '"><div class="ico">🎱</div><div class="grow"><div class="t">' + esc(b.tableName) + '</div><div class="s">' + pretty2(b.startIST) + ' – ' + pretty2(b.endIST) + ' · ' + b.players + ' players</div></div><span class="badge ' + (b.status === "upcoming" ? "ok" : b.status === "cancelled" ? "bad" : "") + '">' + b.status + '</span></a>'; }).join("");
    }
    $("[data-cal-prev]", cal).addEventListener("click", function () { cur = new Date(cur.getFullYear(), cur.getMonth() - 1, 1); draw(); });
    $("[data-cal-next]", cal).addEventListener("click", function () { cur = new Date(cur.getFullYear(), cur.getMonth() + 1, 1); draw(); });
    draw();
  }

  /* ------------------------------------------------------------ home segment filter (Popular / Cafe / Store) */
  var hs = $("[data-home-seg]");
  if (hs) {
    hs.addEventListener("tab", function (e) { $$("[data-home-grid] .pcard").forEach(function (c) { var k = c.getAttribute("data-kind"), pop = c.hasAttribute("data-popular"); c.hidden = e.detail === "popular" ? !pop : k !== e.detail; }); });
    var first = $("[data-tab].on", hs); if (first) hs.dispatchEvent(new CustomEvent("tab", { detail: first.getAttribute("data-tab") }));
  }

  /* ------------------------------------------------------------ search focus behaviour */
  $$("form.search").forEach(function (f) { var i = $("input", f); if (i && i.value) i.style.textAlign = "left"; });

  /* ------------------------------------------------------------ login — Google + preview fallback */
  var gbtn = $("[data-google-fallback]");
  if (gbtn) {
    var gsiRoot = $("#gsi-widget");
    function initials(name) { var p = (name || "?").trim().split(/\s+/); return ((p[0] || "?")[0] + (p[1] ? p[1][0] : "")).toUpperCase(); }
    function hideFallbackWhenGsiRenders() {
      if (gsiRoot && gsiRoot.querySelector("iframe")) gbtn.hidden = true;
    }
    if (gsiRoot && window.MutationObserver) {
      new MutationObserver(hideFallbackWhenGsiRenders).observe(gsiRoot, { childList: true, subtree: true });
      [250, 1000, 2500, 5000].forEach(function (ms) { setTimeout(hideFallbackWhenGsiRenders, ms); });
    }
    function postGoogleCredential(credential) {
      var fd = new FormData(); fd.append("credential", credential); fd.append("next", (DEN.google && DEN.google.next) || "/members");
      fetch("/members/login/google", { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("Google sign-in could not be verified"); location.href = r.url || "/members"; })
        .catch(function (err) { toast(err.message, "bad", 4000); });
    }
    function tryRealGoogle() {
      var g = window.google;
      if (!g || !g.accounts || !g.accounts.id || !DEN.google || !DEN.google.clientId) return false;
      try {
        g.accounts.id.initialize({ client_id: DEN.google.clientId, callback: function (r) { if (r && r.credential) postGoogleCredential(r.credential); } });
        g.accounts.id.prompt();
        return true;
      } catch (err) { return false; }
    }
    function openDemoGoogle() {
      var accs = (DEN.google && DEN.google.accounts) || [];
      openSheet('<div class="sh-title">Continue with Google</div><div class="sh-sub">Preview mode — choose the Google account to sign in with.</div>' +
        (accs.length ? '<div class="list mt16">' + accs.map(function (a) {
          return '<button class="acc-row" data-acc="' + esc(a.email) + '" data-accname="' + esc(a.name) + '"><span class="av">' + esc(initials(a.name)) + '</span><span class="grow"><span class="at">' + esc(a.name) + '</span><span class="as">' + esc(a.email) + '</span></span><span class="chev">→</span></button>';
        }).join("") + '</div>' : "") +
        '<div class="divider"></div><div class="field"><label>Or your Google email</label><input class="input" id="gd-email" type="email" inputmode="email" placeholder="you@gmail.com" autocomplete="email"></div>' +
        '<button class="btn mt16" id="gd-go">Sign in</button><p class="hint center mt12">Any linked member email signs straight in. Unknown emails get a fresh demo member.</p>', function (s) {
          function go(name, email) {
            if (!email) { toast("Choose an account or enter your Google email", "bad"); return; }
            busy($("#gd-go", s), true);
            fetch("/members/login/google-demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name, email: email }), credentials: "same-origin" })
              .then(function (r) { if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || "Sign-in failed"); }); location.href = (DEN.google && DEN.google.next) || "/members"; })
              .catch(function (err) { busy($("#gd-go", s), false); toast(err.message, "bad", 4000); });
          }
          $$("[data-acc]", s).forEach(function (row) { row.addEventListener("click", function () { go(row.getAttribute("data-accname"), row.getAttribute("data-acc")); }); });
          $("#gd-go", s).addEventListener("click", function () { go("", ($("#gd-email", s).value || "").trim()); });
        });
    }
    gbtn.addEventListener("click", function () {
      if (tryRealGoogle()) return;
      if (DEN.google && DEN.google.demo) openDemoGoogle();
      else toast("Google sign-in is still loading — please try again or use email", "bad", 3500);
    });
  }
})();
