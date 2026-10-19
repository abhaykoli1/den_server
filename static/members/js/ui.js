/* Rowdy's Den · Members App — ui.js
   Core primitives: selectors, money, toasts, API client, bottom sheets,
   busy buttons, the cart (localStorage per club), segmented tabs, theme,
   share/copy, install. Exposes `window.DENx` for flows.js + pages.js. */
(function () {
  "use strict";
  var DEN = window.DEN || {};
  var API = DEN.apiBase || "/api";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var money = function (v, sym) {
    sym = sym || (DEN.currency || "₹");
    v = Math.round((Number(v) || 0) * 100) / 100;
    var neg = v < 0; v = Math.abs(v);
    var whole = Math.floor(v), frac = Math.round((v - whole) * 100);
    var s = String(whole);
    if (s.length > 3) { var head = s.slice(0, -3), tail = s.slice(-3); head = head.replace(/\B(?=(\d{2})+(?!\d))/g, ","); s = head + "," + tail; }
    return (neg ? "-" : "") + sym + s + (frac ? "." + (frac < 10 ? "0" + frac : frac) : "");
  };
  window.money = money;

  /* ------------------------------------------------------------ toasts */
  var toastsEl;
  function toast(msg, kind, ms) {
    if (!toastsEl) { toastsEl = document.createElement("div"); toastsEl.className = "toasts"; toastsEl.setAttribute("aria-live", "polite"); document.body.appendChild(toastsEl); }
    var t = document.createElement("div"); t.className = "toast " + (kind || ""); t.textContent = msg;
    toastsEl.appendChild(t);
    setTimeout(function () { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(function () { t.remove(); }, 320); }, ms || 2600);
  }
  window.toast = toast;

  /* ------------------------------------------------------------ api */
  function api(method, path, body, opts) {
    opts = opts || {};
    var headers = { "Accept": "application/json", "X-Client": "members-web" };
    if (DEN.token) headers["Authorization"] = "Bearer " + DEN.token;
    if (body !== undefined && body !== null) headers["Content-Type"] = "application/json";
    return fetch(API + path, { method: method, headers: headers, body: body !== undefined && body !== null ? JSON.stringify(body) : undefined, credentials: "same-origin" })
      .then(function (r) {
        return r.text().then(function (txt) {
          var data = {}; try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { detail: txt }; }
          if (r.status === 401) { window.location.href = "/members/login?err=expired&next=" + encodeURIComponent(location.pathname); throw new Error("Session expired"); }
          if (!r.ok) { var err = new Error(data.detail || data.message || ("Request failed (" + r.status + ")")); err.status = r.status; err.data = data; throw err; }
          return data;
        });
      });
  }
  window.api = api;

  /* ------------------------------------------------------------ busy buttons */
  function busy(btn, on) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>' + (btn.dataset.busy || "Please wait…"); btn.disabled = true; }
    else { if (btn.dataset.label) btn.innerHTML = btn.dataset.label; btn.disabled = false; }
  }

  /* ------------------------------------------------------------ sheets */
  var scrim, sheet;
  function ensureSheet() {
    if (sheet) return;
    scrim = document.createElement("div"); scrim.className = "scrim";
    sheet = document.createElement("div"); sheet.className = "sheet"; sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-modal", "true");
    document.body.appendChild(scrim); document.body.appendChild(sheet);
    scrim.addEventListener("click", closeSheet);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSheet(); });
  }
  function openSheet(html, onOpen) {
    ensureSheet();
    sheet.innerHTML = '<div class="grab"></div>' + html;
    requestAnimationFrame(function () { scrim.classList.add("open"); sheet.classList.add("open"); });
    document.body.style.overflow = "hidden";
    $$("[data-close]", sheet).forEach(function (b) { b.addEventListener("click", closeSheet); });
    if (onOpen) onOpen(sheet);
    return sheet;
  }
  function closeSheet() { if (!sheet) return; scrim.classList.remove("open"); sheet.classList.remove("open"); document.body.style.overflow = ""; }
  function confirmSheet(title, sub, okLabel, danger) {
    return new Promise(function (resolve) {
      openSheet('<div class="sh-title">' + title + '</div><div class="sh-sub">' + (sub || "") + '</div>' +
        '<div class="row mt20 gap8"><button class="btn soft" data-close>Keep</button><button class="btn ' + (danger ? "danger" : "") + '" id="sh-ok">' + okLabel + '</button></div>', function (s) {
          $("#sh-ok", s).addEventListener("click", function () { closeSheet(); resolve(true); });
          $("[data-close]", s).addEventListener("click", function () { resolve(false); });
        });
    });
  }

  /* ------------------------------------------------------------ cart (localStorage, per club) */
  var CART_KEY = "den.cart." + (DEN.clubId || "x");
  var Cart = {
    items: {},
    load: function () { try { this.items = JSON.parse(localStorage.getItem(CART_KEY) || "{}") || {}; } catch (e) { this.items = {}; } return this; },
    save: function () { try { localStorage.setItem(CART_KEY, JSON.stringify(this.items)); } catch (e) { } this.render(); return this; },
    qty: function (id) { return (this.items[id] && this.items[id].qty) || 0; },
    count: function () { var n = 0; for (var k in this.items) n += this.items[k].qty; return n; },
    set: function (id, qty, meta) {
      qty = Math.max(0, Math.min(50, qty | 0));
      if (qty === 0) delete this.items[id]; else this.items[id] = Object.assign({}, meta || this.items[id] || {}, { qty: qty });
      return this.save();
    },
    add: function (id, meta, by) { return this.set(id, this.qty(id) + (by || 1), meta); },
    clear: function () { this.items = {}; return this.save(); },
    lines: function () { var out = []; for (var k in this.items) out.push(Object.assign({ id: k }, this.items[k])); return out; },
    render: function () {
      var n = this.count();
      $$("[data-cart-count]").forEach(function (el) { el.textContent = n; el.hidden = n === 0; });
      $$("[data-cart-add]").forEach(function (btn) {
        var id = btn.getAttribute("data-cart-add"); var q = Cart.qty(id);
        if (btn.classList.contains("add")) { btn.classList.toggle("in", q > 0); btn.innerHTML = q > 0 ? (q + ' <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>') : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'; }
      });
    }
  };
  Cart.load();
  window.Cart = Cart;

  document.addEventListener("click", function (e) {
    var add = e.target.closest("[data-cart-add]");
    if (add) {
      e.preventDefault(); e.stopPropagation();
      var meta = { name: add.getAttribute("data-name"), price: Number(add.getAttribute("data-price")), image: add.getAttribute("data-image") || "", emoji: add.getAttribute("data-emoji") || "", kind: add.getAttribute("data-kind") || "", stock: Number(add.getAttribute("data-stock") || 99), hue: add.getAttribute("data-hue") || "30" };
      var id = add.getAttribute("data-cart-add");
      if (meta.stock && Cart.qty(id) + 1 > meta.stock) { toast("Only " + meta.stock + " left in stock", "bad"); return; }
      Cart.add(id, meta, 1);
      toast("Added " + meta.name + " · " + Cart.qty(id) + " in cart", "ok", 1400);
    }
  });

  /* ------------------------------------------------------------ segmented tabs (client-side) */
  $$("[data-tabs]").forEach(function (seg) {
    var group = seg.getAttribute("data-tabs");
    var btns = $$("[data-tab]", seg);
    function show(name, push) {
      btns.forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === name); });
      $$('[data-panel="' + group + '"]').forEach(function (p) { p.hidden = p.getAttribute("data-name") !== name; });
      if (push && seg.hasAttribute("data-url")) { var u = new URL(location.href); u.searchParams.set("tab", name); history.replaceState(null, "", u); }
      seg.dispatchEvent(new CustomEvent("tab", { detail: name }));
    }
    btns.forEach(function (b) { b.addEventListener("click", function () { show(b.getAttribute("data-tab"), true); }); });
    var init = (btns.filter(function (b) { return b.classList.contains("on"); })[0] || btns[0]);
    if (init) show(init.getAttribute("data-tab"), false);
  });

  /* ------------------------------------------------------------ theme */
  $$("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      var fd = new FormData(); fd.append("theme", next);
      fetch("/members/theme", { method: "POST", body: fd, credentials: "same-origin" });
      b.querySelector("[data-theme-label]") && (b.querySelector("[data-theme-label]").textContent = next === "dark" ? "On" : "Off");
    });
  });

  /* ------------------------------------------------------------ share + copy */
  $$("[data-share]").forEach(function (b) {
    b.addEventListener("click", function () {
      var data = { title: b.getAttribute("data-share-title") || document.title, text: b.getAttribute("data-share-text") || "", url: b.getAttribute("data-share") || location.href };
      if (navigator.share) navigator.share(data).catch(function () { });
      else { navigator.clipboard && navigator.clipboard.writeText(data.text + " " + data.url); toast("Copied to clipboard", "ok"); }
    });
  });
  $$("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () { navigator.clipboard && navigator.clipboard.writeText(b.getAttribute("data-copy")); toast("Copied", "ok", 1200); });
  });

  /* ------------------------------------------------------------ install prompt */
  var deferredPrompt; window.addEventListener("beforeinstallprompt", function (e) { e.preventDefault(); deferredPrompt = e; $$("[data-install]").forEach(function (b) { b.hidden = false; }); });
  $$("[data-install]").forEach(function (b) { b.addEventListener("click", function () { if (deferredPrompt) { deferredPrompt.prompt(); deferredPrompt = null; b.hidden = true; } else toast("Use your browser menu → Add to Home Screen", "", 3500); }); });

  Cart.render();

  /* shared with flows.js + pages.js */
  window.DENx = { $: $, $$: $$, esc: esc, money: money, toast: toast, api: api, busy: busy, openSheet: openSheet, closeSheet: closeSheet, confirmSheet: confirmSheet, Cart: Cart };
})();
