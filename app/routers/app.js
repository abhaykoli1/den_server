/* ============================================================
   Rowdy's Den — Members Web App (mobile-only, HTML template SPA)
   Talks ONLY to the existing den_server backend:
     /api/auth/*  +  /api/member-portal/*  +  owner's due-collect
     endpoint for self due-payment (backend-validated, old-due-first).
   The frontend NEVER computes billing, wallet or stock math that
   matters — every rupee shown comes from the server.
   ============================================================ */
(function () {
  "use strict";

  /* ---------------- config + tiny utils ---------------- */
  var CFG = window.DEN_CONFIG || {};
  var API_BASE = (CFG.API_BASE || "/api").replace(/\/+$/, "");
  var TZ = CFG.CLUB_TIMEZONE || "Asia/Kolkata";
  var TABLES_POLL = CFG.TABLES_POLL_MS || 20000;
  var ORDERS_POLL = CFG.ORDERS_POLL_MS || 45000;

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  var inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });
  function money(n) { return "\u20B9" + inr.format(Number(n) || 0); }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function parseISO(s) { var d = new Date(s); return isNaN(d) ? null : d; }
  var dtf = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true });
  var dtfDay = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
  function fmtDateTime(s) { var d = parseISO(s); return d ? dtf.format(d) : "—"; }
  function fmtDay(s) { var d = parseISO(s); return d ? dtfDay.format(d) : "—"; }
  function istMonthOf(s) {
    var d = parseISO(s); if (!d) return "";
    var parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit" }).format(d);
    return parts.slice(0, 7); // YYYY-MM
  }
  function currentISTMonth() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit" }).format(new Date()).slice(0, 7);
  }
  function rel(s) {
    var d = parseISO(s); if (!d) return "";
    var sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (sec < 60) return "just now";
    var m = Math.floor(sec / 60);
    if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    var days = Math.floor(h / 24);
    if (days < 7) return days + "d ago";
    return fmtDay(s);
  }
  function fmtElapsed(ms) {
    if (ms < 0) ms = 0;
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return h > 0 ? h + ":" + pad2(m) + ":" + pad2(ss) : m + ":" + pad2(ss);
  }
  function debounce(fn, ms) {
    var t; return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms); };
  }

  /* ---------------- icons (inline SVG, lucide-style) ---------------- */
  function ic(paths, size) {
    return '<svg width="' + (size || 20) + '" height="' + (size || 20) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + "</svg>";
  }
  var I = {
    wallet: ic('<path d="M20 7H5a2 2 0 0 1 0-4h13v4"/><path d="M20 7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5"/><circle cx="17.5" cy="13.5" r="1.2" fill="currentColor"/>'),
    alert: ic('<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4m0 3h.01"/>'),
    clock: ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>'),
    check: ic('<path d="m4.5 12.5 5 5 10-11"/>'),
    x: ic('<path d="M18 6 6 18M6 6l12 12"/>'),
    plus: ic('<path d="M12 5v14M5 12h14"/>'),
    minus: ic('<path d="M5 12h14"/>'),
    trophy: ic('<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4Z"/><path d="M7 6H4a1 1 0 0 0-1 1c0 2.5 2 4 4 4M17 6h3a1 1 0 0 1 1 1c0 2.5-2 4-4 4"/>'),
    food: ic('<path d="M7 2v20M4 2v6a3 3 0 0 0 6 0V2M7 13h3"/><path d="M17 2c-2 2-2.5 5-2.5 8.5 0 2 .8 3.5 2.5 3.5s2.5-1.5 2.5-3.5C19.5 7 19 4 17 2ZM17 14v8"/>'),
    box: ic('<path d="M21 8 12 3 3 8v8l9 5 9-5V8Z"/><path d="m3 8 9 5 9-5M12 13v8"/>'),
    receipt: ic('<path d="M5 2h14v20l-2.5-1.5L14 22l-2.5-1.5L9 22l-2.5-1.5L4 22V2h1Z"/><path d="M9 7h6M9 11h6"/>'),
    card: ic('<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19M6 15h4"/>'),
    cash: ic('<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/><path d="M5.5 9.5h.01M18.5 14.5h.01"/>'),
    refresh: ic('<path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v5h-5"/>'),
    logout: ic('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>'),
    sun: ic('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    moon: ic('<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/>'),
    shield: ic('<path d="M12 2 4 6v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6l-8-4Z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>'),
    user: ic('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/>'),
    mail: ic('<rect x="2.5" y="4.5" width="19" height="15" rx="2"/><path d="m3 7 9 6 9-6"/>'),
    phone: ic('<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8.1 10a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/>'),
    cal: ic('<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M8 2.5V6M16 2.5V6M3 10h18"/>'),
    qr: ic('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M14 14h3v3h4v4h-4v-4h-3v-3Z"/>'),
    arrowR: ic('<path d="M5 12h14m-6-6 6 6-6 6"/>'),
    wifiOff: ic('<path d="m2 2 20 20M8.5 16.5a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 3.9-2.3M12 20h.01M1 8.6A15 15 0 0 1 6.7 5.6M23 8.6a15 15 0 0 0-8.5-4"/>'),
    bell: ic('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'),
    cup: ic('<path d="M17 9h4v4a5 5 0 0 1-5 5h-1M4 2v18a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V2M4 2h13"/>'),
    zap: ic('<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>'),
    info: ic('<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>'),
    chevR: ic('<path d="m9 6 6 6-6 6"/>'),
    trash: ic('<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'),
  };

  /* ---------------- local store ---------------- */
  var LS = { token: "denm.token", user: "denm.user", theme: "denm.theme", cart: "denm.cart" };
  var Store = {
    token: localStorage.getItem(LS.token) || "",
    user: null,
    dashboard: null,
    sessions: [],
    rankings: null,
    ordersCache: null,
    cart: {},
    clockOffsetMs: 0,
    clubId: "",
    memberId: "",
    saveToken: function (t) { this.token = t || ""; t ? localStorage.setItem(LS.token, t) : localStorage.removeItem(LS.token); },
    saveUser: function (u) { this.user = u; try { localStorage.setItem(LS.user, JSON.stringify(u)); } catch (e) {} },
    loadUser: function () { try { this.user = JSON.parse(localStorage.getItem(LS.user) || "null"); } catch (e) { this.user = null; } },
    saveCart: function () { try { localStorage.setItem(LS.cart + "." + (this.clubId || "x"), JSON.stringify(this.cart)); } catch (e) {} },
    loadCart: function () { try { this.cart = JSON.parse(localStorage.getItem(LS.cart + "." + (this.clubId || "x")) || "{}") || {}; } catch (e) { this.cart = {}; } },
    resetClubData: function () {
      this.dashboard = null; this.sessions = []; this.rankings = null; this.ordersCache = null; this.cart = {};
    },
  };
  Store.loadUser();

  function member() { return (Store.dashboard && Store.dashboard.member) || {}; }
  function club() { return (Store.dashboard && Store.dashboard.club) || {}; }

  /* ---------------- api client ---------------- */
  function ApiError(status, message, data) {
    this.status = status; this.message = message; this.data = data;
  }
  var Api = {
    req: async function (method, path, opts) {
      opts = opts || {};
      var ctrl = new AbortController();
      var timer = setTimeout(function () { ctrl.abort(); }, opts.timeout || 20000);
      var headers = { "Content-Type": "application/json", "X-Client": "member-web" };
      if (Store.token) headers.Authorization = "Bearer " + Store.token;
      try {
        var res = await fetch(API_BASE + path, {
          method: method, headers: headers,
          body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
          signal: ctrl.signal,
        });
        var data = null;
        try { data = await res.json(); } catch (e) { /* non-JSON */ }
        if (res.status === 401) {
          Auth.logout(true);
          throw new ApiError(401, (data && (data.detail || data.message)) || "Session expired — please sign in again.");
        }
        if (!res.ok) {
          var msg = (data && (data.detail || data.message)) || ("Request failed (" + res.status + ")");
          throw new ApiError(res.status, msg, data);
        }
        return data;
      } catch (e) {
        if (e instanceof ApiError) throw e;
        if (e && e.name === "AbortError") throw new ApiError(0, "Network is slow — please try again.");
        throw new ApiError(0, "Could not reach the server. Check your connection.");
      } finally { clearTimeout(timer); }
    },
    get: function (p, o) { return this.req("GET", p, o); },
    post: function (p, b, o) { return this.req("POST", p, Object.assign({}, o, { body: b })); },
    patch: function (p, b, o) { return this.req("PATCH", p, Object.assign({}, o, { body: b })); },
  };
  function friendlyError(e) {
    if (!e) return "Something went wrong — please try again.";
    if (e.status === 0) return e.message;
    if (e.status === 402) return "Club billing is paused on this account. Please pay at the counter.";
    if (e.status === 403) return e.message || "You don't have access to this. Please contact the club.";
    return e.message || "Something went wrong — please try again.";
  }

  /* ---------------- toasts / sheet / state blocks ---------------- */
  function toast(msg, kind) {
    var box = $("#toasts");
    var el = document.createElement("div");
    el.className = "toast" + (kind === "err" ? " err" : kind === "ok" ? " ok" : "");
    el.innerHTML = "<span>" + (kind === "err" ? I.alert : kind === "ok" ? I.check : I.info) + "</span><span>" + esc(msg) + "</span>";
    box.appendChild(el);
    setTimeout(function () { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(function () { el.remove(); }, 320); }, 3600);
    while (box.children.length > 3) box.firstChild.remove();
  }
  var Sheet = {
    open: function (html, label) {
      var s = $("#sheet");
      s.innerHTML = '<div class="grab" aria-hidden="true"></div>' + html;
      if (label) s.setAttribute("aria-label", label);
      $("#scrim").classList.add("open");
      requestAnimationFrame(function () { s.classList.add("open"); });
      var f = s.querySelector("input,button.btn"); if (f) setTimeout(function () { try { f.focus({ preventScroll: true }); } catch (e) {} }, 320);
    },
    close: function () {
      $("#sheet").classList.remove("open");
      $("#scrim").classList.remove("open");
    },
  };
  function skeleton(h) { return '<div class="sk" style="height:' + (h || 18) + 'px"></div>'; }
  function skeletons(n, h, gap) {
    var s = ""; for (var i = 0; i < n; i++) s += '<div style="margin-top:' + (gap || 10) + 'px">' + skeleton(h) + "</div>";
    return s;
  }
  function emptyBlock(icon, title, sub, btnHtml) {
    return '<div class="empty"><div class="eart">' + icon + "</div><b>" + esc(title) + "</b><p>" + esc(sub) + "</p>" + (btnHtml || "") + "</div>";
  }
  function errBlock(title, msg, retry) {
    return '<div class="errbox"><b>' + esc(title) + "</b>" + esc(msg) +
      (retry ? '<div class="mt12"><button class="btn sm ghost" data-retry>' + I.refresh + " Try again</button></div>" : "") + "</div>";
  }
  function wireRetry(root, fn) {
    var b = root.querySelector("[data-retry]");
    if (b) b.addEventListener("click", fn);
  }

  /* ---------------- auth ---------------- */
  var Auth = {
    devMode: true,
    googleReady: false,
    async checkDevMode() {
      try {
        var r = await Api.get("/auth/dev-mode", { timeout: 8000 });
        this.devMode = !!(r && r.devMode);
      } catch (e) { this.devMode = true; }
    },
    async restore() {
      if (!Store.token) return false;
      try {
        var me = await Api.get("/auth/me");
        if (!me || me.role !== "member") { this.logout(false); return "role"; }
        Store.saveUser(me);
        return true;
      } catch (e) { return false; } // 401 already logged out inside Api
    },
    logout: function (expired) {
      Store.saveToken("");
      Store.saveUser(null);
      Store.resetClubData();
      try { localStorage.removeItem(LS.user); } catch (e) {}
      Sheet.close();
      if (location.hash !== "#/login") location.hash = "#/login";
      else Router.render("login");
      if (expired) toast("Session expired — please sign in again.", "err");
    },
  };

  /* ---------------- data loading (race-safe) ---------------- */
  var loadSeq = 0;
  var Data = {
    async loadDashboard(opts) {
      opts = opts || {};
      var my = ++loadSeq;
      var dash = await Api.get("/member-portal/dashboard");
      if (my !== loadSeq) return null; // stale — a newer load won
      Store.dashboard = dash;
      Store.clubId = (dash.club && dash.club.id) || "";
      Store.memberId = (dash.member && dash.member.id) || "";
      Store.loadCart();
      // clock offset so elapsed timers match the server clock
      var srv = parseISO(dash.serverNow);
      Store.clockOffsetMs = srv ? srv.getTime() - Date.now() : 0;
      // Live-table merge: open sessions (endedAt == null) are the live source
      // of truth the owner app itself uses. Includes the SERVER's own
      // running-bill preview — the frontend never computes billing.
      try {
        var ss = await Api.get("/clubs/" + encodeURIComponent(Store.clubId) + "/sessions?club_id=" + encodeURIComponent(Store.clubId));
        if (my === loadSeq && Array.isArray(ss)) Store.sessions = ss;
      } catch (e) { /* keep dashboard-only statuses */ }
      try {
        await Booking.load();
      } catch (e) {
        if (e && e.status === 404) { Booking.supported = false; Booking.list = []; }
        /* offline/other: tables still render; booking UI stays hidden or stale-safe */
      }
      if (!opts.silent) Router.refreshChrome();
      return dash;
    },
    async loadRankings() {
      var r = await Api.get("/member-portal/rankings");
      Store.rankings = r; return r;
    },
    async loadOrders() {
      var r = await Api.get("/member-portal/orders");
      var o = (r && r.orders) || [];
      Store.ordersCache = o; return o;
    },
    openSessions() {
      return (Store.sessions || []).filter(function (s) { return s && !s.endedAt; });
    },
    liveByTable() {
      var m = {};
      this.openSessions().forEach(function (s) { if (s.tableId) m[s.tableId] = s; });
      // fallback: dashboard-marked live tables (older servers)
      ((Store.dashboard && Store.dashboard.tables) || []).forEach(function (t) {
        if (t.status === "live" && !m[t.id]) m[t.id] = { id: t.sessionId || "", startedAt: t.startedAt || "", players: (t.players || []).map(function (p) { return { label: p }; }), preview: null, _dash: true };
      });
      return m;
    },
  };

  /* ---------------- router ---------------- */
  var NAV_ROUTES = ["home", "tables", "memberships", "activity", "profile"];
  var ALL_ROUTES = ["login", "clubs", "home", "tables", "wallet", "memberships", "order", "orders", "activity", "frames", "profile", "settings"];
  var pollTimer = null;
  var pendingOrderQuery = "";
  var Router = {
    current: "",
    go: function (r) { location.hash = "#/" + r; },
    parse: function () {
      var h = (location.hash || "#/home").replace(/^#\//, "").split("?")[0];
      return ALL_ROUTES.indexOf(h) >= 0 ? h : "home";
    },
    show: function (route) {
      $all(".view").forEach(function (v) { v.classList.remove("active"); });
      var el = $("#v-" + route);
      if (el) { el.classList.add("active"); $("#main").scrollTop = 0; window.scrollTo(0, 0); }
      var isNav = NAV_ROUTES.indexOf(route) >= 0;
      $("#navbar").classList.toggle("hidden", !isNav);
      $all(".navbtn").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-route") === route); });
      this.current = route;
    },
    refreshChrome: function () {
      // greeting + bell dot
      var m = member(), c = club();
      var name = (m.name || (Store.user && Store.user.name) || "Member").trim();
      $("#home-hello").textContent = "Hi, " + name.split(" ")[0];
      $("#home-sub").textContent = (c.name || "Rowdy's Den") + (m.planName ? " · " + m.planName : "");
      var av = $("#home-avatar");
      var pic = (Store.user && Store.user.picture) || "";
      if (pic) { av.innerHTML = ""; var im = document.createElement("img"); im.src = pic; im.alt = ""; av.appendChild(im); }
      else av.textContent = (name[0] || "R").toUpperCase();
      var alerts = computeAlerts();
      $("#bell-dot").hidden = alerts.length === 0;
    },
    render: function (route) {
      this.stopPoll();
      Sheet.close();
      if (!Store.token && route !== "login") { location.hash = "#/login"; return; }
      if (!Store.token && route === "login") { this.show("login"); Views.login(); return; }
      if (route === "login") { this.show("login"); Views.login(); return; }
      if (route === "clubs") { this.show("clubs"); Views.clubs(); return; }
      if (!Store.dashboard && ["home", "tables", "wallet", "memberships", "order", "orders", "activity", "frames", "profile", "settings"].indexOf(route) >= 0) {
        this.show(route);
        Views.loading(route);
        Data.loadDashboard().then(function (d) {
          if (!d) return;
          Router.render(route);
        }).catch(function (e) { Views.dataError(route, e); });
        return;
      }
      this.show(route);
      this.refreshChrome();
      if (route === "home") Views.home();
      else if (route === "tables") { Views.tables(); this.poll(TABLES_POLL, function () { Data.loadDashboard({ silent: true }).then(function (d) { if (d && Router.current === "tables") Views.tables(true); }); }); }
      else if (route === "wallet") Views.wallet();
      else if (route === "memberships") Views.memberships();
      else if (route === "order") Views.order();
      else if (route === "orders") { Views.orders(); this.poll(ORDERS_POLL, function () { Data.loadOrders().then(function () { if (Router.current === "orders") Views.orders(true); }).catch(function () {}); }); }
      else if (route === "activity") Views.activity();
      else if (route === "frames") Views.frames();
      else if (route === "profile") Views.profile();
      else if (route === "settings") Views.settings();
      updateCartBar();
    },
    poll: function (ms, fn) {
      this.stopPoll();
      pollTimer = setInterval(function () { if (!document.hidden && navigator.onLine) fn(); }, ms);
    },
    stopPoll: function () { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } },
  };

  /* ---------------- alerts ---------------- */
  function computeAlerts() {
    var m = member(), out = [];
    var due = Number(m.dueAmount) || 0;
    var wal = Number(m.walletBalance) || 0;
    if (due > 0) out.push({ tone: "red", icon: I.alert, title: "Due pending — " + money(due), sub: "Old dues are always settled first.", go: "wallet" });
    if (wal < 100) out.push({ tone: "yellow", icon: I.wallet, title: wal <= 0 ? "Wallet is empty" : "Low wallet — " + money(wal), sub: "Top up at the counter to keep ordering.", go: "wallet" });
    if (m.planExpiresAt) {
      var d = parseISO(m.planExpiresAt);
      if (d) {
        var days = Math.ceil((d.getTime() - Date.now()) / 86400000);
        if (days < 0) out.push({ tone: "red", icon: I.cal, title: "Membership expired", sub: "Renew your plan at the counter.", go: "profile" });
        else if (days <= 7) out.push({ tone: "yellow", icon: I.cal, title: "Plan expires in " + days + "d", sub: (m.planName || "Membership") + " · " + fmtDay(m.planExpiresAt), go: "profile" });
      }
    }
    if (Booking.supported && Booking.loaded) {
      var up = Booking.upcoming();
      if (up.length) {
        var nb = up[0];
        var nbD = parseISO(nb.startAt);
        var inHrs = nbD ? Math.round((nbD.getTime() - Date.now()) / 3600000) : -1;
        if (inHrs >= 0 && inHrs <= 30) out.push({ tone: "blue", icon: I.cal, title: "Table booked — " + (nb.tableName || "your table"), sub: slotLabel(nb) + ". Tap to view.", go: "tables" });
      }
    }
    var liveMine = Data.openSessions().filter(function (s) {
      return (s.players || []).some(function (p) { return p.memberId === Store.memberId; });
    });
    if (liveMine.length) out.push({ tone: "blue", icon: I.zap, title: "You're on " + (liveMine[0].tableName || "a table"), sub: "Live since " + fmtDateTime(liveMine[0].startedAt) + ".", go: "tables" });
    return out;
  }

  /* ============================================================ VIEWS */
  var Views = {
    /* ---------- LOGIN ---------- */
    login: function () {
      if (Store.token) { Router.go("home"); return; }
      var box = $("#login-box");
      var gcid = (CFG.GOOGLE_CLIENT_ID || "375395125425-st5ba3fcsu5nqkcioovofa2ib1s21gmh.apps.googleusercontent.com").trim();
      box.innerHTML =
        '<div id="login-err"></div>' +
        '<form id="login-form" novalidate>' +
        '<div class="field"><label for="login-email">Member email</label>' +
        '<input class="input" id="login-email" type="email" inputmode="email" autocomplete="email" placeholder="you@example.com" />' +
        '<div class="ferr" id="login-email-err" hidden></div></div>' +
        '<div class="mt12"><button class="btn primary" id="login-btn" type="submit">Continue</button></div>' +
        "</form>" +
        '<div class="or">OR</div><div id="gis-btn"><button type="button" class="gbtn" id="google-fallback"><b style="font-size:20px;color:#4285f4">G</b> Continue with Google</button></div>' +
        '<div id="demo-box"></div>' +
        '<p class="tiny muted center mt16">Use the email id registered at your club.<br/>Owner / staff accounts cannot open member screens.</p>';
      $("#login-form").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var email = $("#login-email").value.trim();
        var ferr = $("#login-email-err");
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
          ferr.textContent = "Enter a valid email id."; ferr.hidden = false; return;
        }
        ferr.hidden = true;
        Views.doEmailLogin(email);
      });
      // Demo email login shortcuts hidden; use Google Sign-In instead.
      var googleFallback = $("#google-fallback");
      if (googleFallback) googleFallback.addEventListener("click", function () { Views.initGoogle(gcid); });
      if (gcid) Views.initGoogle(gcid);
    },
    loginBusy: function (busy) {
      var b = $("#login-btn");
      if (!b) return;
      b.disabled = busy;
      b.innerHTML = busy ? '<span class="spinner"></span> Signing in…' : "Continue";
    },
    loginError: function (msg) {
      $("#login-err").innerHTML = errBlock("Could not sign you in", msg, false);
    },
    doEmailLogin: async function (email) {
      this.loginBusy(true);
      $("#login-err").innerHTML = "";
      try {
        var r = await Api.post("/auth/dev", { email: email });
        this.afterLogin(r);
      } catch (e) { this.loginError(friendlyError(e)); }
      this.loginBusy(false);
    },
    afterLogin: function (r) {
      var u = (r && r.user) || {};
      if (u.role !== "member") {
        this.loginError("This account is a " + (u.role || "non-member") + " account. Members only — ask your club to register your email, or use the billing app.");
        return;
      }
      Store.saveToken(r.token);
      Store.saveUser(u);
      Store.resetClubData();
      var memberships = u.memberships || [];
      if (memberships.length > 1) Router.go("clubs");
      else Router.go("home");
      toast("Welcome back, " + ((u.name || "member").split(" ")[0]) + "!", "ok");
    },
    initGoogle: function (clientId) {
      if (Auth.googleReady) { this.renderGis(clientId); return; }
      var s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true; s.defer = true;
      s.onload = function () { Auth.googleReady = true; Views.renderGis(clientId); };
      s.onerror = function () { var d = $("#gis-btn"); if (d) d.innerHTML = ""; };
      document.head.appendChild(s);
    },
    renderGis: function (clientId) {
      var host = $("#gis-btn");
      if (!host || !window.google || !window.google.accounts) return;
      try {
        window.google.accounts.id.initialize({
          client_id: clientId, use_fedcm_for_prompt: false,
          callback: function (cred) {
            (async function () {
              try {
                var r = await Api.post("/auth/google", { idToken: cred.credential });
                Views.afterLogin(r);
              } catch (e) { Views.loginError(friendlyError(e)); }
            })();
          },
        });
        window.google.accounts.id.renderButton(host, { theme: "outline", size: "large", width: 320, text: "signin_with" });
      } catch (e) { host.innerHTML = ""; }
    },

    /* ---------- CLUBS ---------- */
    clubs: async function () {
      var u = Store.user || {};
      var memberships = u.memberships || [];
      var list = $("#clubs-list");
      if (!memberships.length) {
        list.innerHTML = emptyBlock(I.shield, "No club memberships", "You are not linked to an active club membership. Ask your club to register this email.", "");
        $("#clubs-go").style.display = "none";
        return;
      }
      $("#clubs-go").style.display = "";
      // Restored sessions carry memberships without embedded club docs,
      // so resolve the primary club name from a silent dashboard load.
      if (!memberships[0].club && !Store.dashboard) {
        list.innerHTML = skeletons(2, 84);
        try { await Data.loadDashboard({ silent: true }); } catch (e) { /* names fall back below */ }
        memberships = (Store.user || {}).memberships || memberships;
      }
      var primaryClub = club();
      var sel = 0;
      list.innerHTML = memberships.map(function (ms, i) {
        var c = ms.club || (i === 0 ? primaryClub : {});
        var status = ms.active === false ? "Inactive" : ((Number(ms.dueAmount) || 0) > 0 ? "Due " + money(ms.dueAmount) : "Active");
        return '<button class="pick' + (i === 0 ? " sel" : "") + '" data-i="' + i + '">' +
          (i === 0 ? '<span class="flag">Primary</span>' : "") +
          '<span class="radio">' + (i === 0 ? I.check : "") + "</span>" +
          '<span class="grow"><b class="t">' + esc(c.name || "Club") + "</b>" +
          '<span class="s">' + esc(ms.planName || "Regular member") + " · " + esc(status) + "</span></span></button>";
      }).join("");
      $all(".pick", list).forEach(function (b) {
        b.addEventListener("click", function () {
          var i = Number(b.getAttribute("data-i"));
          if (i !== 0) {
            toast("The server keeps your primary club active — multi-club switching isn't enabled yet.");
            return;
          }
          sel = i;
          $all(".pick", list).forEach(function (x) { x.classList.remove("sel"); $(".radio", x).innerHTML = ""; });
          b.classList.add("sel"); $(".radio", b).innerHTML = I.check;
        });
      });
      $("#clubs-go").onclick = function () { Router.go("home"); };
    },

    /* ---------- loading / error shells ---------- */
    loading: function (route) {
      if (route === "home") $("#home-body").innerHTML = skeletons(5, 86);
      else if (route === "tables") $("#tables-grid").innerHTML = skeletons(4, 150);
      else if (route === "wallet") $("#wallet-body").innerHTML = skeletons(4, 70);
      else if (route === "order") $("#order-list").innerHTML = skeletons(5, 64);
      else if (route === "orders") $("#orders-list").innerHTML = skeletons(4, 90);
      else if (route === "activity") $("#activity-list").innerHTML = skeletons(6, 60);
      else if (route === "frames") $("#frames-body").innerHTML = skeletons(3, 90);
      else if (route === "profile") $("#profile-body").innerHTML = skeletons(4, 60);
      else if (route === "settings") $("#settings-body").innerHTML = skeletons(3, 52);
    },
    dataError: function (route, e) {
      var html = errBlock("Could not load your club data", friendlyError(e), true);
      var map = { home: "#home-body", tables: "#tables-grid", wallet: "#wallet-body", order: "#order-list", orders: "#orders-list", activity: "#activity-list", frames: "#frames-body", profile: "#profile-body", settings: "#settings-body" };
      var host = $(map[route] || "#home-body");
      host.innerHTML = html;
      wireRetry(host, function () { Router.render(route); });
    },

    /* ---------- HOME ---------- */
    home: function () {
      var m = member(), c = club();
      var tables = (Store.dashboard.tables || []);
      var liveMap = Data.liveByTable();
      var liveCount = Object.keys(liveMap).length;
      var due = Number(m.dueAmount) || 0, wal = Number(m.walletBalance) || 0;
      var pass = Number(m.passFramesLeft) || 0;
      var isOpen = c.settings && c.settings.isOpen !== false;
      var rankTxt = Store.rankings && Store.rankings.me ? "#" + Store.rankings.me.rank : "—";
      var alerts = computeAlerts();
      var acts = (Store.dashboard.activity || []).slice(0, 3);

      var firstLiveId = Object.keys(liveMap)[0];
      var firstLive = firstLiveId ? liveMap[firstLiveId] : null;
      var firstLiveTable = firstLive ? tables.filter(function (t) { return t.id === firstLiveId; })[0] : null;

      var h = "";
      var statusBadge = $("#club-status-badge");
      if (statusBadge) {
        statusBadge.textContent = isOpen ? "OPEN" : "CLOSED";
        statusBadge.className = "club-status-badge " + (isOpen ? "open" : "closed");
      }
      // wallet + membership card (lavender)
      h += '<section class="card lav" style="marginTop:16px" aria-label="Wallet and membership">' +
        '<div class="spread"><span class="card-title">Wallet &amp; Membership</span><button class="link-btn" style="color:inherit;padding:6px" data-go="wallet" aria-label="Open wallet">' + I.chevR + "</button></div>" +
        '<div class="stat-row">' +
        statBtn("wallet", I.wallet, "Wallet", money(wal)) +
        statBtn("wallet", I.alert, "Due", due > 0 ? money(due) : "Clear") +
        statBtn("profile", I.box, "Pass", pass > 0 ? pass + " frames" : "—") +
        statBtn("frames", I.trophy, "Rank", rankTxt) +
        "</div>" +
        '<div class="pill-line"><span>' + esc(m.badge ? String(m.badge).toUpperCase() + " MEMBER" : "CLUB MEMBER") + (m.planName ? " · " + esc(m.planName) : "") + "</span></div></section>";

      // alerts
      alerts.forEach(function (a) {
        h += '<div class="alert ' + a.tone + '" role="alert"><span>' + a.icon + '</span><span class="grow"><b>' + esc(a.title) + "</b><p>" + esc(a.sub) + "</p></span>" +
          '<button class="link-btn" data-go="' + a.go + '" aria-label="Open">Open</button></div>';
      });

      // live now (yellow)
      h += '<section class="card yel mt16" aria-label="Live tables"><div class="hero-split"><div class="grow">' +
        '<div class="card-title">Live Now</div>' +
        (liveCount
          ? '<p class="small" style="margin:6px 0 12px">' + liveCount + " table" + (liveCount > 1 ? "s" : "") + " in play" +
            (firstLiveTable ? ' · <b>' + esc(firstLiveTable.name) + '</b> <span data-since="' + esc(firstLive.startedAt || "") + '"></span>' : "") + "</p>"
          : '<p class="small" style="margin:6px 0 12px">All tables are free right now. Walk in and play.</p>') +
        '<button class="see-all" data-go="tables">See All <span class="arr">' + I.arrowR + "</span></button>" +
        "</div>" + liveArt() + "</div></section>";

      // quick actions
      h += '<div class="spread mt16"><span class="section-title">Quick Actions</span></div>' +
        '<div class="qa-grid mt8">' +
        qaBtn("wallet", "Add money", I.plus, "", "addmoney") +
        qaBtn("wallet", "Pay due", I.cash, "warn", "paydue") +
        // HIDDEN (2026-09-09): Order-food quick action parked for now — uncomment to restore.
        // qaBtn("order", "Order food", I.food, "gold", "") +
        qaBtn("activity", "Activity", I.receipt, "green", "") +
        "</div>";

      // recent activity
      h += '<div class="spread mt16"><span class="section-title">Recent Activity</span><button class="link-btn" data-go="activity">See All</button></div>';
      if (!acts.length) h += emptyBlock(I.receipt, "No activity yet", "Payments, frames and orders will show up here.", "");
      else {
        h += '<div class="rows mt8">';
        acts.forEach(function (a) { h += activityRow(a, true); });
        h += "</div>";
      }
      $("#home-body").innerHTML = h;
      wireGo($("#home-body"));
      // lazy rankings for rank chip
      if (!Store.rankings) {
        Data.loadRankings().then(function () { if (Router.current === "home") Views.home(); }).catch(function () {});
      }
      function statBtn(go, icon, label, val) {
        return '<button class="stat" data-go="' + go + '"><span class="stat-bubble">' + icon + "</span><b>" + esc(label) + "</b><span>" + esc(val) + "</span></button>";
      }
      function qaBtn(go, label, icon, tone, act) {
        return '<button class="qa ' + tone + '" data-go="' + go + '"' + (act ? ' data-act="' + act + '"' : "") + '><span class="qa-ic">' + icon + "</span>" + esc(label) + "</button>";
      }
      function liveArt() {
        return '<svg class="hero-art" viewBox="0 0 120 120" aria-hidden="true"><ellipse cx="60" cy="104" rx="44" ry="8" fill="rgba(0,0,0,0.12)"/>' +
          '<rect x="18" y="46" width="84" height="46" rx="10" fill="#111114"/><rect x="24" y="52" width="72" height="26" rx="6" fill="#2E5BFF"/>' +
          '<circle cx="46" cy="65" r="7" fill="#fff"/><circle cx="74" cy="65" r="7" fill="#FFD75E"/>' +
          '<path d="M10 92 34 60M110 92 86 60" stroke="#111114" stroke-width="6" stroke-linecap="round"/>' +
          '<circle cx="60" cy="26" r="14" fill="#111114"/><text x="60" y="31" font-size="13" font-weight="bold" fill="#fff" text-anchor="middle">8</text></svg>';
      }
    },

    /* ---------- TABLES ---------- */
    tablesFilter: "all",
    tables: function (soft) {
      var tables = ((Store.dashboard && Store.dashboard.tables) || []).slice();
      tables.sort(function (a, b) { return (a.sortOrder || 0) - (b.sortOrder || 0) || String(a.name).localeCompare(String(b.name)); });
      var liveMap = Data.liveByTable();
      var f = this.tablesFilter;
      var shown = tables.filter(function (t) {
        var live = !!liveMap[t.id];
        return f === "all" || (f === "live" ? live : !live);
      });
      var liveCount = Object.keys(liveMap).length;
      $("#tables-note").textContent = liveCount
        ? liveCount + " live · " + (tables.length - liveCount) + " free"
        : tables.length ? "All tables free" : "";
      var grid = $("#tables-grid");
      if (!tables.length) {
        grid.innerHTML = emptyBlock(I.info, "No tables found", "The club hasn't added tables yet.", "");
        return;
      }
      if (!shown.length) {
        grid.innerHTML = emptyBlock(I.info, f === "live" ? "Nothing live right now" : "No free tables", f === "live" ? "All tables are free. Walk in and play." : "Every table is in play — please wait a little.", "");
        return;
      }
      grid.innerHTML = shown.map(function (t) {
        var s = liveMap[t.id];
        var live = !!s;
        var r = t.rate || {};
        var players = s ? (s.players || []).map(function (p) { return p.label; }).filter(Boolean) : [];
        var peak = r.peakHourlyRate ? "Peak " + money(r.peakHourlyRate) + "/hr " + r.peakStartHour + "–" + r.peakEndHour : "No peak pricing";
        return '<article class="tfull" aria-label="' + esc(t.name) + ", " + (live ? "live" : "free") + '">' +
          '<div class="tfull-art ' + (live ? "live" : "free") + '">' +
          '<span class="status-tag ' + (live ? "live" : "free") + '">' + (live ? '<span class="pulse"></span>LIVE' : "○ FREE") + "</span>" +
          (live
            ? '<div class="elapsed" data-since="' + esc(s.startedAt || "") + '">0:00</div><div class="tsub">' + esc(players.slice(0, 3).join(" · ") || "In play") + "</div>"
            : '<div class="elapsed" style="font-size:20px">Ready to play</div><div class="tsub">' + money(r.hourlyRate || 0) + "/hr · Min " + money(r.minCharge || 0) + "</div>") +
          "</div>" +
          '<div class="tfull-body"><div class="spread"><span class="tfull-name">' + esc(t.name) + "</span>" +
          '<button class="link-btn" data-table="' + esc(t.id) + '">Details</button></div>' +
          '<div class="tfull-meta">' + esc(live && s.startedAt ? "Since " + fmtDateTime(s.startedAt) : peak) + (live && players.length > 3 ? " · +" + (players.length - 3) + " more" : "") + "</div>" +
          (Booking.supported ? '<div class="tbtn-row"><button class="btn primary sm block" data-book="' + esc(t.id) + '">' + I.cal + " Book Slot</button></div>" : "") +
          "</div></article>";
      }).join("");
      tickClock(grid);
      $all("[data-table]", grid).forEach(function (b) {
        b.addEventListener("click", function () { Views.tableSheet(b.getAttribute("data-table")); });
      });
      $all("[data-book]", grid).forEach(function (b) {
        b.addEventListener("click", function () { Views.bookSheet(b.getAttribute("data-book")); });
      });
      Views.bookingsSec();
    },
    bookingsSec: async function () {
      var host = $("#bookings-sec");
      if (!host) return;
      if (!Booking.supported) { host.innerHTML = ""; return; }
      try { await Booking.load(); } catch (e) { if (!Booking.loaded) { host.innerHTML = ""; return; } }
      var up = Booking.upcoming();
      if (!up.length) {
        host.innerHTML = '<section class="card soft" style="margin-bottom:12px"><div class="spread"><span><b>My Bookings</b><br/><span class="tiny muted">No upcoming slots — reserve one below.</span></span><span class="tag grey">0</span></div></section>';
        return;
      }
      host.innerHTML = '<div class="spread" style="margin:2px 2px 10px"><span class="section-title">My Bookings</span><span class="tag blue">' + up.length + "</span></div>" +
        '<div class="rows" style="margin-bottom:12px">' + up.slice(0, 5).map(function (b) {
          return '<div class="lrow"><span class="lic blue">' + I.cal + '</span><span class="grow"><b class="t">' + esc(b.tableName || "Table") + "</b>" +
            '<span class="s">' + esc(slotLabel(b)) + (b.players ? " · " + b.players + " players" : "") + "</span></span>" +
            '<button class="link-btn" data-cancel-bk="' + esc(b.id) + '">Cancel</button></div>';
        }).join("") + "</div>";
      $all("[data-cancel-bk]", host).forEach(function (btn) {
        btn.addEventListener("click", async function () {
          if (!btn.getAttribute("data-armed")) {
            btn.setAttribute("data-armed", "1");
            btn.textContent = "Confirm?";
            btn.style.color = "var(--red)";
            setTimeout(function () { if (btn.isConnected) { btn.removeAttribute("data-armed"); btn.textContent = "Cancel"; btn.style.color = ""; } }, 4000);
            return;
          }
          btn.disabled = true; btn.textContent = "…";
          try {
            await Api.req("DELETE", "/clubs/" + encodeURIComponent(Store.clubId) + "/bookings/" + encodeURIComponent(btn.getAttribute("data-cancel-bk")) + "?club_id=" + encodeURIComponent(Store.clubId));
            toast("Booking cancelled.", "ok");
            await Booking.load();
            Views.bookingsSec();
            Router.refreshChrome();
          } catch (e) { btn.disabled = false; btn.textContent = "Cancel"; toast(friendlyError(e), "err"); }
        });
      });
    },
    bookSheet: function (tableId) {
      var t = ((Store.dashboard.tables) || []).filter(function (x) { return x.id === tableId; })[0];
      if (!t) return;
      if (!Booking.supported) { toast("Slot booking isn't enabled on this club's server yet."); return; }
      var r = t.rate || {};
      var today = istToday();
      var maxD = addDaysISO(today, 14);
      var nowHM = istNowHM();
      var dh = (nowHM.h + 1) % 24;
      var state = { date: today, start: pad2(dh) + ":00", end: pad2((dh + 1) % 24) + ":00", players: 2 };
      var STARTS = hmOptions(8, 22), ENDS = hmOptions(8, 23);
      if (STARTS.indexOf(state.start) < 0) state.start = "10:00";
      function playerRate() {
         var rates = r.ratesByPlayers || {};
         return Number(rates[String(state.players)] || r.hourlyRate || 0);
      }
      Sheet.open(
        '<b style="font-size:18px">Book ' + esc(t.name) + "</b>" +
        '<p class="muted small"><span id="bk-rate">' + money(playerRate()) + "/hr · " + state.players + " players</span> · Min " + money(r.minCharge || 0) + " · free cancellation until the slot starts</p>" +
        '<div class="field"><label for="bk-date">Date</label><input class="input" id="bk-date" type="date" min="' + today + '" max="' + maxD + '" value="' + today + '" /></div>' +
        '<div class="taken-chips" id="bk-taken"></div>' +
        '<div class="time-grid"><div class="field"><label for="bk-start">From</label><select class="input" id="bk-start"></select></div>' +
        '<div class="field"><label for="bk-end">To</label><select class="input" id="bk-end"></select></div></div>' +
        '<div class="field"><label>Players</label><div><span class="qty"><button id="bk-minus" aria-label="Fewer players">' + I.minus + '</button><b id="bk-pcount">2</b><button id="bk-plus" aria-label="More players">' + I.plus + "</button></span><div class=\"tiny muted mt8\">2 to 4 players</div></div></div>" +
        '<div class="field"><label for="bk-notes">Note for the counter (optional)</label><input class="input" id="bk-notes" maxlength="120" placeholder="e.g. need 4 cues" /></div>' +
        '<div class="ferr" id="bk-err" hidden></div>' +
        '<div class="mt16"><button class="btn primary" id="bk-confirm">Confirm Booking</button></div>',
        "Book " + t.name
      );
      var dateEl = $("#bk-date"), startEl = $("#bk-start"), endEl = $("#bk-end"), errEl = $("#bk-err"), goBtn = $("#bk-confirm");
      function paintTaken() {
        var taken = Booking.upcoming().filter(function (b) { return b.tableId === tableId && b.dateIST === state.date; });
        $("#bk-taken").innerHTML = taken.length ? '<span class="tiny muted">Taken:</span> ' + taken.map(function (b) { return '<span class="taken">' + esc(b.startIST) + "–" + esc(b.endIST) + "</span>"; }).join("") : "";
      }
      function fillTimes() {
        startEl.innerHTML = STARTS.map(function (x) { return '<option value="' + x + '"' + (x === state.start ? " selected" : "") + ">" + x + "</option>"; }).join("");
        var ends = ENDS.filter(function (x) { return x > state.start; });
        if (ends.indexOf(state.end) < 0) state.end = ends[0] || "23:30";
        endEl.innerHTML = ends.map(function (x) { return '<option value="' + x + '"' + (x === state.end ? " selected" : "") + ">" + x + "</option>"; }).join("");
      }
      function validate() {
        var sMs = istToUtcMs(state.date, state.start), eMs = istToUtcMs(state.date, state.end);
        if (!(eMs > sMs)) return "End time must be after start time.";
        var dur = (eMs - sMs) / 60000;
        if (dur < 30) return "Minimum slot is 30 minutes.";
        if (dur > 240) return "Maximum slot is 4 hours — split longer sessions.";
        if (sMs <= Date.now() + Store.clockOffsetMs + 5 * 60000) return "Pick a start time at least a few minutes in the future.";
        return "";
      }
      function refresh() {
        paintTaken();
        $("#bk-rate").textContent = money(playerRate()) + "/hr · " + state.players + " players";
        $("#bk-pcount").textContent = state.players;
        var msg = validate(); errEl.hidden = !msg; errEl.textContent = msg; goBtn.disabled = !!msg;
      }
      dateEl.addEventListener("change", function () { state.date = dateEl.value || today; refresh(); });
      startEl.addEventListener("change", function () { state.start = startEl.value; fillTimes(); refresh(); });
      endEl.addEventListener("change", function () { state.end = endEl.value; refresh(); });
      $("#bk-minus").addEventListener("click", function () { state.players = Math.max(2, state.players - 1); refresh(); });
      $("#bk-plus").addEventListener("click", function () { state.players = Math.min(4, state.players + 1); refresh(); });
      fillTimes(); refresh();
      goBtn.addEventListener("click", async function () {
        var msg = validate();
        if (msg) { errEl.hidden = false; errEl.textContent = msg; return; }
        goBtn.disabled = true; goBtn.innerHTML = '<span class="spinner"></span> Reserving…';
        try {
          var res = await Api.post("/clubs/" + encodeURIComponent(Store.clubId) + "/bookings?club_id=" + encodeURIComponent(Store.clubId),
            { tableId: tableId, date: state.date, start: state.start, end: state.end, players: state.players, notes: $("#bk-notes").value.trim() });
          var b = (res && res.booking) || {};
          await Booking.load();
          var lbl = b.dateIST ? slotLabel(b) : slotLabel({ dateIST: state.date, startIST: state.start, endIST: state.end, startAt: new Date(istToUtcMs(state.date, state.start)).toISOString() });
          Sheet.open('<div class="center" style="padding:10px 0"><div class="avatar" style="width:72px;height:72px;margin:0 auto;background:var(--green);font-size:30px">' + I.check + "</div>" +
            '<h3 class="mt12" style="font-size:20px">Slot booked</h3><p class="muted small">' + esc(b.tableName || t.name) + " · " + esc(lbl) +
            "<br/>Show this at the counter — free cancellation until it starts.</p>" +
            '<div class="mt12"><button class="btn primary" id="bk-done">Done</button></div></div>', "Slot booked");
          $("#bk-done").addEventListener("click", function () { Sheet.close(); if (Router.current === "tables") Views.tables(true); Router.refreshChrome(); });
        } catch (e) {
          goBtn.disabled = false; goBtn.textContent = "Confirm Booking";
          errEl.hidden = false; errEl.textContent = friendlyError(e);
        }
      });
    },
    tableSheet: function (tableId) {
      var t = ((Store.dashboard.tables) || []).filter(function (x) { return x.id === tableId; })[0];
      if (!t) return;
      var s = Data.liveByTable()[tableId];
      var live = !!s;
      var r = t.rate || {};
      var players = s ? (s.players || []).map(function (p) { return p.label; }).filter(Boolean) : [];
      var peak = r.peakHourlyRate ? "Peak " + money(r.peakHourlyRate) + "/hr (" + r.peakStartHour + ":00–" + r.peakEndHour + ":00)" : "No peak pricing";
      Sheet.open(
        '<div class="spread"><b style="font-size:18px">' + esc(t.name) + "</b>" +
        '<span class="tag ' + (live ? "red" : "green") + '">' + (live ? "● Live" : "○ Free") + "</span></div>" +
        (live ? '<p class="muted small">In play for <b data-since="' + esc(s.startedAt || "") + '"></b> · started ' + esc(fmtDateTime(s.startedAt)) + "</p>"
          : '<p class="muted small">This table is free right now.</p>') +
        (players.length ? '<div class="kv"><span>Players</span><b>' + esc(players.join(" · ")) + "</b></div>" : "") +
        '<div class="kv"><span>Rate</span><b>' + money(r.hourlyRate || 0) + "/hr</b></div>" +
        '<div class="kv"><span>Minimum charge</span><b>' + money(r.minCharge || 0) + "</b></div>" +
        '<div class="kv"><span>Peak</span><b>' + esc(peak) + "</b></div>" +
        (r.ratesByPlayers && Object.keys(r.ratesByPlayers).length ? '<div class="kv"><span>Group rates</span><b>' + esc(Object.keys(r.ratesByPlayers).map(function (k) { return k + "p " + money(r.ratesByPlayers[k]); }).join(" · ")) + "</b></div>" : "") +
        (Booking.supported ? '<div class="mt12"><button class="btn primary block" id="sheet-book">' + I.cal + " Book Slot</button></div>" : "") +
        '<div class="mt12"><button class="btn ghost block" id="sheet-close">Close</button></div>',
        t.name + " details"
      );
      tickClock($("#sheet"));
      var shBook = $("#sheet-book");
      if (shBook) shBook.addEventListener("click", function () { Views.bookSheet(tableId); });
      $("#sheet-close").addEventListener("click", Sheet.close);
    },

    /* ---------- WALLET ---------- */
    wallet: function () {
      var m = member();
      var due = Number(m.dueAmount) || 0, wal = Number(m.walletBalance) || 0;
      var txns = (Store.dashboard.activity || []).filter(function (a) {
        return a.tag === "PAYMENT" || /due|wallet|payment/i.test(a.message || "");
      }).slice(0, 20);
      var h = '<section class="card lav"><div class="tiny" style="font-weight:800;letter-spacing:.06em">WALLET BALANCE</div>' +
        '<div style="font-size:40px;font-weight:800;letter-spacing:-.02em">' + money(wal) + "</div>" +
        '<div class="small" style="margin-top:4px">' + (due > 0 ? "Due pending <b>" + money(due) + "</b> — old dues settle first" : "No dues. You're all clear.") + "</div>" +
        '<div class="btn-row mt12"><button class="btn light sm" id="w-add">' + I.plus + " Add Money</button>" +
        '<button class="btn dark sm" id="w-pay" ' + (due <= 0 ? "disabled" : "") + ">" + I.cash + " Pay Due</button></div></section>";
      if (due > 0) h += '<div class="alert red">' + I.alert + '<span class="grow"><b>Pay ' + money(due) + ' to clear your dues</b><p>Part payments are allowed — every payment cuts the oldest due first.</p></span></div>';
      h += '<div class="spread mt16"><span class="section-title">Transactions</span></div>';
      if (!txns.length) h += emptyBlock(I.wallet, "No transactions yet", "Wallet and due payments will appear here.", "");
      else { h += '<div class="rows mt8">'; txns.forEach(function (a) { h += activityRow(a, true); }); h += "</div>"; }
      var host = $("#wallet-body");
      host.innerHTML = h;
      $("#w-add").addEventListener("click", Views.addMoneySheet);
      var pay = $("#w-pay");
      if (pay && !pay.disabled) pay.addEventListener("click", Views.payDueSheet);
    },
    addMoneySheet: function () {
      var c = club();
      var qr = c.qrCode || "";
      var sel = 500, custom = "";
      Sheet.open(
        '<b style="font-size:18px">Add Money</b>' +
        '<p class="muted small">Top up your wallet safely. ' + (qr ? "Scan the club's payment QR, then tap Done — staff credit your wallet on confirmation." : "Pay at the club counter (UPI / cash) — staff credit your wallet instantly.") + "</p>" +
        '<div class="amt-grid" role="radiogroup" aria-label="Amount">' +
        [200, 500, 1000].map(function (a) { return '<button class="amt-pick' + (a === 500 ? " sel" : "") + '" data-amt="' + a + '">' + money(a) + "</button>"; }).join("") + "</div>" +
        '<div class="field"><label for="am-custom">Or custom amount</label><input class="input" id="am-custom" inputmode="numeric" placeholder="e.g. 750" /></div>' +
        (qr ? '<div class="qrbox mt12"><img src="' + qr.replace(/"/g, "") + '" alt="Club payment QR code" /></div>' : "") +
        '<div class="card soft mt12 tiny muted">How it works: pay ' + "<b id='am-echo'>" + money(500) + "</b>" + " via the club QR or counter → staff confirm → your wallet updates here. The app never changes balances by itself.</div>" +
        '<div class="mt16"><button class="btn primary" id="am-done">Done</button></div>',
        "Add money"
      );
      var echo = $("#am-echo");
      $all(".amt-pick", $("#sheet")).forEach(function (b) {
        b.addEventListener("click", function () {
          $all(".amt-pick", $("#sheet")).forEach(function (x) { x.classList.remove("sel"); });
          b.classList.add("sel"); sel = Number(b.getAttribute("data-amt")); $("#am-custom").value = "";
          echo.textContent = money(sel);
        });
      });
      $("#am-custom").addEventListener("input", function (ev) {
        var v = parseInt(ev.target.value.replace(/\D/g, ""), 10);
        if (v > 0) { sel = v; custom = v; $all(".amt-pick", $("#sheet")).forEach(function (x) { x.classList.remove("sel"); }); echo.textContent = money(v); }
      });
      $("#am-done").addEventListener("click", async function () {
        Sheet.close();
        toast("After paying " + money(sel) + ", ask the counter to credit your wallet.");
        try { await Data.loadDashboard({ silent: true }); if (Router.current === "wallet") Views.wallet(); } catch (e) {}
      });
    },
    payDueSheet: function () {
      var m = member();
      var due = Number(m.dueAmount) || 0;
      if (due <= 0) { toast("You have no pending due."); return; }
      var mode = "upi";
      Sheet.open(
        '<b style="font-size:18px">Pay Due</b>' +
        '<p class="muted small">Pending due <b>' + money(due) + "</b>. Old dues are settled first — part payment is fine.</p>" +
        '<div class="field"><label for="pd-amt">Amount (max ' + money(due) + ")</label>" +
        '<input class="input" id="pd-amt" inputmode="decimal" value="' + due + '" /><div class="ferr" id="pd-err" hidden></div></div>' +
        '<div class="field"><label>Paid via</label><div class="chips" id="pd-modes">' +
        [["upi", "UPI"], ["cash", "Cash"], ["card", "Card"]].map(function (x) {
          return '<button class="chip' + (x[0] === "upi" ? " active" : "") + '" data-mode="' + x[0] + '">' + x[1] + "</button>";
        }).join("") + "</div></div>" +
        '<div class="mt16"><button class="btn primary" id="pd-pay">Pay ' + money(due) + "</button></div>" +
        '<p class="tiny muted center mt8">Payment is recorded with the club immediately. Complete UPI / cash with the counter if asked.</p>',
        "Pay due"
      );
      var amtInput = $("#pd-amt"), payBtn = $("#pd-pay"), ferr = $("#pd-err");
      $all("#pd-modes .chip").forEach(function (b) {
        b.addEventListener("click", function () {
          $all("#pd-modes .chip").forEach(function (x) { x.classList.remove("active"); });
          b.classList.add("active"); mode = b.getAttribute("data-mode");
        });
      });
      amtInput.addEventListener("input", function () {
        var v = Number(amtInput.value);
        payBtn.textContent = "Pay " + money(v > 0 ? v : 0);
      });
      payBtn.addEventListener("click", async function () {
        var v = Math.round(Number(amtInput.value) * 100) / 100;
        if (!(v > 0)) { ferr.textContent = "Enter an amount greater than zero."; ferr.hidden = false; return; }
        if (v > due) { ferr.textContent = "Payment cannot exceed the pending due (" + money(due) + ")."; ferr.hidden = false; return; }
        ferr.hidden = true;
        payBtn.disabled = true;
        payBtn.innerHTML = '<span class="spinner"></span> Confirming payment…';
        try {
          var r = await Api.post("/clubs/" + encodeURIComponent(Store.clubId) + "/members/" + encodeURIComponent(Store.memberId) + "/payments?club_id=" + encodeURIComponent(Store.clubId), { amount: v, mode: mode });
          var left = r && r.member ? Number(r.member.dueAmount) || 0 : Math.max(0, due - v);
          Sheet.close();
          toast("Paid " + money(v) + (left > 0 ? " · " + money(left) + " still due." : " · dues cleared!"), "ok");
          await Data.loadDashboard({ silent: true });
          if (Router.current === "wallet") Views.wallet(); else Router.refreshChrome();
        } catch (e) {
          payBtn.disabled = false;
          payBtn.textContent = "Pay " + money(v);
          ferr.textContent = friendlyError(e); ferr.hidden = false;
        }
      });
    },

    memberships: async function () {
      var host = $("#membership-plans-list");
      host.innerHTML = skeletons(3, 120, 12);
      try {
        var r = await Api.get("/member-portal/membership-plans");
        var plans = r.plans || [];
        if (!plans.length) { host.innerHTML = emptyBlock(I.shield, "No plans available", "Your club has not published any membership plans yet.", ""); return; }
        host.innerHTML = plans.map(function (p) {
          var duration = p.days ? p.days + " days" : (p.frames ? p.frames + " frames" : (p.type || "Membership"));
          return '<section class="card mt12"><div class="spread"><div><b class="card-title">' + esc(p.name || "Membership") + '</b><div class="muted small mt8">' + esc(duration) + '</div></div><b style="font-size:20px">' + money(p.amount || 0) + '</b></div>' + (p.description ? '<p class="muted small">' + esc(p.description) + '</p>' : '') + '<button class="btn primary sm block mt12" data-buy-plan="' + esc(p.id) + '">Buy Membership</button></section>';
        }).join("");
        $all("[data-buy-plan]", host).forEach(function (b) { b.addEventListener("click", function () {
          var plan = plans.find(function (x) { return x.id === b.getAttribute("data-buy-plan"); });
          var qr = (Store.dashboard && Store.dashboard.club && Store.dashboard.club.qrCode) || "";
          Sheet.open('<h2>Buy ' + esc(plan.name || "Membership") + '</h2><p class="muted small">Pay ₹' + money(plan.amount || 0) + ' using your club QR, then enter the UTR/reference number.</p>' + (qr ? '<div class="qrbox mt12"><img src="' + qr.replace(/"/g, "") + '" alt="Club payment QR" /></div>' : '<div class="alert yellow mt12">Club payment QR is not configured. Contact the club.</div>') + '<div class="field mt12"><label for="membership-utr">UPI UTR / reference number</label><input class="input" id="membership-utr" maxlength="80" placeholder="Enter UTR number" /></div><div id="membership-err"></div><button class="btn primary block mt12" id="membership-submit">Submit payment request</button>', "Membership payment");
          $("#membership-submit").addEventListener("click", async function () { var utr = $("#membership-utr").value.trim(); if (utr.length < 4) { $("#membership-err").innerHTML = errBlock("Invalid UTR", "Enter a valid payment reference number.", false); return; } this.disabled = true; try { var r = await Api.post("/member-portal/membership-requests", { planId: plan.id, utr: utr }); Sheet.close(); toast(r.message || "Request submitted", "ok"); } catch (e) { this.disabled = false; $("#membership-err").innerHTML = errBlock("Could not submit", friendlyError(e), false); } });
        }); });
      } catch (e) { host.innerHTML = errBlock("Could not load memberships", friendlyError(e), true); }
    },

    /* ---------- ORDER ---------- */
    orderCat: "All",
    orderQuery: "",
    order: function () {
      if (pendingOrderQuery) { this.orderQuery = pendingOrderQuery; pendingOrderQuery = ""; }
      var menu = (Store.dashboard.menuItems || []).slice();
      menu.sort(function (a, b) { return String(a.category).localeCompare(String(b.category)) || String(a.name).localeCompare(String(b.name)); });
      var cats = ["All"].concat(Array.from(new Set(menu.map(function (x) { return x.category || "Cafe"; }))));
      if (cats.indexOf(this.orderCat) < 0) this.orderCat = "All";
      var chips = $("#order-chips");
      chips.innerHTML = cats.map(function (c) {
        return '<button class="chip' + (c === Views.orderCat ? " active" : "") + '" data-cat="' + esc(c) + '">' + esc(c) + "</button>";
      }).join("");
      $all("[data-cat]", chips).forEach(function (b) {
        b.addEventListener("click", function () { Views.orderCat = b.getAttribute("data-cat"); Views.order(); });
      });
      var si = $("#order-search");
      si.value = this.orderQuery;
      si.oninput = debounce(function () { Views.orderQuery = si.value.trim(); Views.orderList(); }, 220);
      this.orderList();
    },
    orderList: function () {
      var menu = (Store.dashboard.menuItems || []);
      var q = this.orderQuery.toLowerCase();
      var shown = menu.filter(function (x) {
        var okCat = Views.orderCat === "All" || (x.category || "Cafe") === Views.orderCat;
        var okQ = !q || (x.name || "").toLowerCase().indexOf(q) >= 0;
        return okCat && okQ;
      });
      var host = $("#order-list");
      if (!menu.length) { host.innerHTML = emptyBlock(I.food, "Menu is empty", "The club hasn't published its menu yet.", ""); return; }
      if (!shown.length) { host.innerHTML = emptyBlock(I.food, "No dishes found", "Try a different search or category.", ""); return; }
      host.innerHTML = shown.map(function (x) {
        var qty = Store.cart[x.id] || 0;
        var stock = Number(x.stockQty) || 0;
        var low = stock <= 5;
        return '<div class="lrow"><span class="lic ' + ((x.category || "") === "Snacks" ? "gold" : "blue") + '">' + ((x.category || "") === "Snacks" ? I.box : I.cup) + "</span>" +
          '<span class="grow"><b class="t">' + esc(x.name) + "</b>" +
          '<span class="s">' + esc(x.category || "Cafe") + " · " + money(x.price) + (low ? ' · <b style="color:var(--red)">Only ' + stock + " left</b>" : "") + "</span></span>" +
          '<span data-cart="' + esc(x.id) + '">' + (qty ? qtyHtml(qty) : '<button class="addbtn" data-add="' + esc(x.id) + '">Add</button>') + "</span></div>";
      }).join("");
      function qtyHtml(q) { return '<span class="qty"><button data-dec aria-label="Decrease quantity">' + I.minus + "</button><b>" + q + "</b>" + '<button data-inc aria-label="Increase quantity">' + I.plus + "</button></span>"; }
      $all("[data-add]", host).forEach(function (b) {
        b.addEventListener("click", function () { Cart.set(b.getAttribute("data-add"), 1); });
      });
      $all("[data-cart]", host).forEach(function (wrap) {
        var id = wrap.getAttribute("data-cart");
        var inc = wrap.querySelector("[data-inc]"), dec = wrap.querySelector("[data-dec]");
        if (inc) inc.addEventListener("click", function () { Cart.set(id, (Store.cart[id] || 0) + 1); });
        if (dec) dec.addEventListener("click", function () { Cart.set(id, (Store.cart[id] || 0) - 1); });
      });
    },
    cartSheet: function () {
      var menu = (Store.dashboard.menuItems || []);
      var byId = {}; menu.forEach(function (x) { byId[x.id] = x; });
      var lines = Object.keys(Store.cart).map(function (id) { return { item: byId[id], qty: Store.cart[id] }; }).filter(function (l) { return l.item && l.qty > 0; });
      if (!lines.length) return;
      var total = lines.reduce(function (s, l) { return s + Number(l.item.price) * l.qty; }, 0);
      var wal = Number(member().walletBalance) || 0;
      var short = total > wal;
      Sheet.open(
        '<div class="spread"><b style="font-size:18px">Your Order</b><button class="link-btn" id="cart-clear">Clear</button></div>' +
        '<div class="rows mt8">' + lines.map(function (l) {
          return '<div class="lrow"><span class="grow"><b class="t">' + esc(l.item.name) + "</b>" +
            '<span class="s">' + money(l.item.price) + " × " + l.qty + "</span></span>" +
            '<span class="amt">' + money(Number(l.item.price) * l.qty) + "</span></div>";
        }).join("") + "</div>" +
        '<div class="kv mt8"><span>Total (confirmed by club at checkout)</span><b>' + money(total) + "</b></div>" +
        '<div class="kv"><span>Wallet balance</span><b>' + money(wal) + "</b></div>" +
        (short ? '<div class="alert yellow">' + I.wallet + '<span class="grow"><b>Wallet short by ' + money(total - wal) + "</b><p>Top up first — orders pay securely from wallet only.</p></span><button class='link-btn' id='cart-topup'>Add</button></div>" : "") +
        '<div class="mt16"><button class="btn primary" id="cart-place" ' + (short ? "disabled" : "") + ">" + I.shield + " Place Order · " + money(total) + "</button></div>" +
        '<p class="tiny muted center mt8">Stock is checked atomically by the server. Nothing is charged until the club confirms.</p>',
        "Your order"
      );
      $("#cart-clear").addEventListener("click", function () { Cart.clear(); Sheet.close(); });
      var top = $("#cart-topup");
      if (top) top.addEventListener("click", function () { Sheet.close(); Views.addMoneySheet(); });
      var place = $("#cart-place");
      if (place && !place.disabled) place.addEventListener("click", Views.placeOrder);
    },
    placing: false,
    placeOrder: async function () {
      if (Views.placing) return;
      var menu = (Store.dashboard.menuItems || []);
      var byId = {}; menu.forEach(function (x) { byId[x.id] = x; });
      var items = Object.keys(Store.cart).filter(function (id) { return byId[id] && Store.cart[id] > 0; })
        .map(function (id) { return { menuItemId: id, quantity: Store.cart[id] }; });
      if (!items.length) { Sheet.close(); return; }
      Views.placing = true;
      var btn = $("#cart-place");
      if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Placing order…'; }
      try {
        var r = await Api.post("/member-portal/orders", { items: items, mode: "wallet" });
        var order = (r && r.order) || {};
        Cart.clear();
        Sheet.close();
        Sheet.open(
          '<div class="center" style="padding:10px 0">' +
          '<div class="avatar" style="width:72px;height:72px;margin:0 auto;background:var(--green);font-size:30px">' + I.check + "</div>" +
          '<h3 class="mt12" style="font-size:20px">Order confirmed</h3>' +
          '<p class="muted small">Paid ' + money(order.total || order.amount || 0) + " from wallet · Order " + esc(String(order.id || "").slice(-6).toUpperCase()) + "<br/>New wallet balance " + money(r.newWalletBalance) + "</p>" +
          '<div class="btn-row mt12"><button class="btn ghost" id="ok-track">Track Order</button><button class="btn primary" id="ok-home">Done</button></div></div>',
          "Order confirmed"
        );
        $("#ok-track").addEventListener("click", function () { Sheet.close(); Router.go("orders"); });
        $("#ok-home").addEventListener("click", function () { Sheet.close(); Router.go("home"); });
        await Data.loadDashboard({ silent: true });
        if (Router.current === "order") Views.order();
        Router.refreshChrome();
      } catch (e) {
        if (btn) { btn.disabled = false; btn.innerHTML = "Try Again"; }
        toast(friendlyError(e), "err");
      }
      Views.placing = false;
    },

    /* ---------- ORDERS ---------- */
    orders: async function (soft) {
      var host = $("#orders-list");
      var show = function (orders) {
        if (!orders.length) {
          host.innerHTML = emptyBlock(I.receipt, "No orders yet", "Food you order from the app will show up here.", '<button class="btn primary sm" data-go="order">Order Food</button>');
          wireGo(host); return;
        }
        host.innerHTML = orders.map(function (o) {
          var items = (o.items || []).map(function (l) { return esc(l.name) + " ×" + l.qty; }).join(", ");
          var st = String(o.status || (o.paid ? "paid" : "unpaid")).toLowerCase();
          var tag = st === "paid" ? "green" : st === "cancelled" || st === "failed" ? "red" : "gold";
          return '<div class="lrow" style="align-items:flex-start"><span class="lic blue">' + I.receipt + '</span><span class="grow">' +
            "<b class='t'>Order " + esc(String(o.id || "").slice(-6).toUpperCase()) + ' <span class="tag ' + tag + '">' + esc(st) + "</span></b>" +
            '<span class="s">' + esc(items || "—") + "</span>" +
            '<span class="s">' + esc(fmtDateTime(o.createdAt)) + " · " + esc(o.paymentMode || o.mode || "wallet") + "</span>" +
            '<div class="tl"><div class="hit">Placed &amp; paid ' + esc(fmtDateTime(o.paidAt || o.createdAt)) + "</div><div>Preparing at the counter</div><div>Ready for pickup</div></div>" +
            '</span><span class="amt">' + money(o.total || o.amount || 0) + "</span></div>";
        }).join("");
      };
      if (Store.ordersCache && soft) { show(Store.ordersCache); return; }
      if (!soft) host.innerHTML = skeletons(3, 110);
      try {
        var orders = Store.ordersCache || await Data.loadOrders();
        if (!Store.ordersCache) Store.ordersCache = orders;
        show(orders);
      } catch (e) { host.innerHTML = errBlock("Orders could not be loaded", friendlyError(e), true); wireRetry(host, function () { Views.orders(); }); }
    },

    /* ---------- ACTIVITY ---------- */
    actFilter: "all",
    actShown: 30,
    ACTS: [["all", "All"],
    // ["orders", "Orders"] ["payments", "Payments"]
    , ["frames", "Frames"], ["due", "Due"] , ["membership", "Membership"] ],
    activity: function () {
      var chips = $("#activity-chips");
      chips.innerHTML = this.ACTS.map(function (x) {
        return '<button class="chip' + (x[0] === Views.actFilter ? " active" : "") + '" data-af="' + x[0] + '">' + x[1] + "</button>";
      }).join("");
      $all("[data-af]", chips).forEach(function (b) {
        b.addEventListener("click", function () { Views.actFilter = b.getAttribute("data-af"); Views.actShown = 30; Views.activity(); });
      });
      this.activityList();
    },
    activityList: function () {
      var dueSummary = $("#activity-due-summary");
      if (dueSummary) {
        var dueAmount = Number(member().dueAmount) || 0;
        dueSummary.innerHTML =
          '<div class="activity-due-summary' + (dueAmount > 0 ? " has-due" : " cleared") + '">' +
          '<b class="activity-due-amount">' + money(dueAmount) + "</b>" +
          '<span class="activity-due-label">' + (dueAmount > 0 ? "Amount left to pay" : "All dues are cleared") + "</span>" +
          "</div>";
      }
      var all = (Store.dashboard.activity || []).slice();
      var dueFrames = (Store.dashboard.dueFrames || []).map(function (frame) {
        var settlement = frame.memberSettlement || {};
        return {
          id: "due-frame-" + (frame.id || ""),
          type: "frames",
          tag: "BILLING",
          message: "Frame bill · " + (frame.tableName || "Table"),
          createdAt: frame.createdAt,
          amount: frame.memberDue,
          mode: "due",
          memberDue: frame.memberDue,
          refType: "frame",
          refId: frame.id,
          due: true,
          paidParts: settlement,
        };
      });
      all = all.concat(dueFrames);
      var f = this.actFilter;
      var shown = all.filter(function (a) {
        if (f === "all") return true;
        if (f === "payments") return a.tag === "PAYMENT";
        if (f === "orders") return a.type === "items";
        if (f === "frames") return a.type === "frames" && a.due === true;
        if (f === "membership") return a.type === "memberships";
        if (f === "due") return a.type === "due" || a.due === true;
        return true;
      });
      var host = $("#activity-list");
      if (!shown.length) {
        var label = { all: "No activity yet", payments: "No payments yet", orders: "No food orders yet", frames: "No frame bills yet", membership: "No membership activity", due: "No due payments yet" }[f];
        host.innerHTML = emptyBlock(I.receipt, label, f === "all" ? "Payments, frames and orders will show up here." : "Nothing in this category yet.", "");
      } else {
        host.innerHTML = '<div class="rows">' + shown.slice(0, this.actShown).map(function (a) { return activityRow(a, false); }).join("") + "</div>";
      }
      $("#activity-more").hidden = shown.length <= this.actShown;
    },

    /* ---------- FRAMES ---------- */
    framesMonth: "",
    frames: async function () {
      var host = $("#frames-body");
      if (!this.framesMonth) this.framesMonth = currentISTMonth();
      if (!Store.rankings) {
        host.innerHTML = skeletons(3, 90);
        try { await Data.loadRankings(); } catch (e) { host.innerHTML = errBlock("Frames could not be loaded", friendlyError(e), true); wireRetry(host, function () { Store.rankings = null; Views.frames(); }); return; }
      }
      var me = (Store.rankings && Store.rankings.me) || { won: 0, lost: 0, frames: 0, rank: "—" };
      var total = (Store.rankings && Store.rankings.rankings && Store.rankings.rankings.length) || 0;
      var dues = (Store.dashboard.dueFrames || []).filter(function (f) { return istMonthOf(f.createdAt) === Views.framesMonth; });
      var settled = (Store.dashboard.activity || []).filter(function (a) { return a.type === "frames"; });
      var h = '<section class="card dark"><div class="spread"><span class="card-title">Season Record</span><span class="tag gold">RANK #' + esc(me.rank) + (total ? " / " + total : "") + "</span></div>" +
        '<div class="stat-row">' +
        '<div class="stat"><span class="stat-bubble">' + I.check + "</span><b>Played</b><span>" + (me.frames || 0) + "</span></div>" +
        '<div class="stat"><span class="stat-bubble">' + I.trophy + "</span><b>Won</b><span>" + (me.won || 0) + "</span></div>" +
        '<div class="stat"><span class="stat-bubble">' + I.x + "</span><b>Lost</b><span>" + (me.lost || 0) + "</span></div>" +
        "</div></section>";
      h += '<div class="mt16 frames-toolbar"><div class="section-title">Pending frame dues</div><div class="frames-month-row"><label class="month-picker"><span class="month-picker-icon" aria-hidden="true">' + I.cal + '</span><input type="month" id="fr-month" value="' + esc(this.framesMonth) + '" aria-label="Filter by month" /></label></div></div>';
      if (!dues.length) h += emptyBlock(I.check, "No frames found for this month", "No pending frame dues in " + esc(this.framesMonth) + ".", "");
      else {
        h += '<div class="rows mt8">' + dues.map(function (f) {
          var st = f.memberSettlement || {};
          var split = [];
          if (st.walletPart) split.push("wallet " + money(st.walletPart));
          if (st.passPart) split.push("pass " + money(st.passPart));
          if (st.cashPart) split.push("cash " + money(st.cashPart));
          return '<div class="lrow" style="align-items:flex-start"><span class="lic red">' + I.alert + '</span><span class="grow">' +
            "<b class='t'>" + esc(f.tableName || "Table") + " · " + esc(fmtDateTime(f.createdAt)) + "</b>" +
            '<span class="s">' + esc((f.players || []).map(function (p) { return p.label; }).filter(Boolean).join(" vs ") || "Match") + "</span>" +
            '<span class="s">Bill ' + money(f.totalAmount) + (split.length ? " · paid via " + esc(split.join(" + ")) : "") + "</span>" +
            '</span><span class="amt neg">' + money(f.memberDue) + "<br/><span class='tag red'>due</span></span></div>";
        }).join("") + "</div>";
      }
      if (settled.length) {
        h += '<div class="spread mt16"><span class="section-title">Settled frames</span></div><div class="rows mt8">' +
          settled.slice(0, 10).map(function (a) { return activityRow(a, true); }).join("") + "</div>";
      }
      host.innerHTML = h;
      $("#fr-month").addEventListener("change", function (ev) { Views.framesMonth = ev.target.value || currentISTMonth(); Views.frames(); });
    },

    /* ---------- PROFILE ---------- */
    profile: function () {
      var m = member(), c = club(), u = Store.user || {};
      var h = '<section class="card soft center"><div class="avatar" style="width:72px;height:72px;margin:0 auto;font-size:26px">' +
        ((u.picture ? '<img src="' + esc(u.picture) + '" alt="" />' : esc((m.name || "M")[0].toUpperCase()))) + "</div>" +
        '<h2 class="mt8" style="font-size:21px">' + esc(m.name || u.name || "Member") + "</h2>" +
        '<p class="muted small" style="margin:4px 0 0">' + esc(c.name || "") + (m.planName ? " · " + esc(m.planName) : "") + "</p>" +
        '<div class="mt12 btn-row"><button class="btn ghost sm" id="p-edit">' + I.user + ' Edit</button><button class="btn ghost sm" id="p-clubs">' + I.shield + ' Clubs</button><button class="btn ghost sm" id="p-settings" aria-label="Settings">' + I.info + "</button></div></section>";
      h += '<section class="card mt16"><div class="kv"><span>Email</span><b>' + esc(m.email || u.email || "—") + "</b></div>" +
        '<div class="kv"><span>Phone</span><b>' + esc(m.phone || u.phone || "—") + "</b></div>" +
        '<div class="kv"><span>Member since</span><b>' + esc(fmtDay(m.createdAt)) + "</b></div>" +
        '<div class="kv"><span>Plan</span><b>' + esc(m.planName || "Regular") + (m.planType ? " (" + esc(m.planType) + ")" : "") + "</b></div>" +
        '<div class="kv"><span>Plan expiry</span><b>' + (m.planExpiresAt ? esc(fmtDay(m.planExpiresAt)) : "—") + "</b></div>" +
        '<div class="kv"><span>Pass frames left</span><b>' + (Number(m.passFramesLeft) || 0) + "</b></div>" +
        (m.tableDiscountPercent ? '<div class="kv"><span>Table discount</span><b>' + esc(m.tableDiscountPercent) + "%</b></div>" : "") +
        '<div class="kv"><span>Wallet</span><b>' + money(m.walletBalance) + "</b></div>" +
        '<div class="kv"><span>Due</span><b>' + money(m.dueAmount) + "</b></div></section>";
      h += '<div class="btn-row mt16"><button class="btn ghost block" id="p-frames">' + I.trophy + ' Frames</button></div>';
      h += '<div class="mt12"><button class="btn danger" id="p-logout">' + I.logout + ' Log Out</button></div>';
      h += '<p class="foot">Member id ' + esc(Store.memberId) + "</p>";
      var host = $("#profile-body");
      host.innerHTML = h;
      $("#p-edit").addEventListener("click", Views.editProfileSheet);
      $("#p-clubs").addEventListener("click", function () { Router.go("clubs"); });
      $("#p-settings").addEventListener("click", function () { Router.go("settings"); });
      $("#p-frames").addEventListener("click", function () { Router.go("frames"); });
      $("#p-logout").addEventListener("click", function () { Auth.logout(false); toast("Signed out. See you at the Den!"); });
    },
    editProfileSheet: function () {
      var m = member(), u = Store.user || {};
      Sheet.open(
        '<b style="font-size:18px">Edit Profile</b><p class="muted small">Only your name and phone can be changed here. Balances, plans and dues are managed by the club.</p>' +
        '<div class="field"><label for="ep-name">Name</label><input class="input" id="ep-name" value="' + esc(m.name || u.name || "") + '" maxlength="80" /></div>' +
        '<div class="field"><label for="ep-phone">Phone</label><input class="input" id="ep-phone" value="' + esc(m.phone || u.phone || "") + '" maxlength="20" inputmode="tel" /></div>' +
        '<div class="ferr" id="ep-err" hidden></div>' +
        '<div class="mt16"><button class="btn primary" id="ep-save">Save Changes</button></div>',
        "Edit profile"
      );
      $("#ep-save").addEventListener("click", async function () {
        var btn = $("#ep-save"), ferr = $("#ep-err");
        var name = $("#ep-name").value.trim(), phone = $("#ep-phone").value.trim();
        if (!name) { ferr.textContent = "Name cannot be empty."; ferr.hidden = false; return; }
        ferr.hidden = true; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Saving…';
        try {
          var r = await Api.patch("/auth/me", { name: name, phone: phone });
          if (r && r.name) { Store.saveUser(Object.assign({}, Store.user, { name: r.name, phone: r.phone })); }
          Sheet.close(); toast("Profile updated.", "ok");
          await Data.loadDashboard({ silent: true });
          if (Router.current === "profile") Views.profile(); else Router.refreshChrome();
        } catch (e) { btn.disabled = false; btn.textContent = "Save Changes"; ferr.textContent = friendlyError(e); ferr.hidden = false; }
      });
    },

    /* ---------- SETTINGS ---------- */
    settings: function () {
      var u = Store.user || {};
      var isiOS = /iphone|ipad|ipod/i.test(navigator.userAgent || "");
      var isStandalone = false;
      try { isStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true; } catch (e) {}
      var installHtml = "";
      if (PWA.deferred) installHtml = '<button class="btn primary block mt8" id="s-install">Install App</button>';
      else if (isiOS && !isStandalone) installHtml = '<div class="card soft mt8 small">Install on iPhone: tap <b>Share \u2192 Add to Home Screen</b>.</div>';
      else if (!isStandalone) installHtml = '<div class="card soft mt8 small">Install from the browser menu (\u22EE \u2192 <b>Install app</b>) for the full-screen experience.</div>';
      var h = '<div class="spread"><span class="section-title">Appearance</span></div>' +
        '<div class="theme-row mt8"><div class="theme-pick sel">' + I.sun + ' Light</div></div>' + installHtml +
        '<div class="spread mt16"><span class="section-title">Account</span></div>' +
        '<section class="card mt8"><div class="kv"><span>Signed in as</span><b>' + esc(u.email || member().email || "") + "</b></div>" +
        '<div class="kv"><span>Club</span><b>' + esc(club().name || "") + "</b></div>" +
        '<div class="kv"><span>Notifications</span><b>Managed by the club</b></div></section>' +
        '<details class="tos"><summary>Privacy</summary><p>Your profile, wallet, orders, frames and activity stay between you and your club. The app stores only your sign-in token on this device — nothing is shared with third parties.</p></details>' +
        '<details class="tos"><summary>Terms</summary><p>Wallet top-ups and due payments are collected by the club. Food orders pay from wallet only and stock is confirmed by the club server. Final table bills are always made at the counter.</p></details>' +
        '<div class="mt16"><button class="btn danger" id="s-logout">' + I.logout + ' Log Out</button></div>' +
        '<p class="foot">Rowdy\'s Den Members · v1.0 · ' + esc(API_BASE) + "</p>";
      var host = $("#settings-body");
      host.innerHTML = h;
      var instBtn = $("#s-install");
      if (instBtn) instBtn.addEventListener("click", async function () {
        if (!PWA.deferred) return;
        instBtn.disabled = true;
        try { PWA.deferred.prompt(); await PWA.deferred.userChoice; } catch (e) {}
        PWA.deferred = null;
        Views.settings();
      });
      $("#s-logout").addEventListener("click", function () { Auth.logout(false); toast("Signed out. See you at the Den!"); });
    },
  };

  /* ---------------- activity row ---------------- */
  function activityRow(a, compact) {
    var tag = a.tag || "", type = a.type || "";
    var icon = I.receipt, tone = "";
    if (type === "frames") { icon = I.zap; tone = a.due ? "red" : "gold"; }
    else if (type === "items") { icon = I.food; tone = "blue"; }
    else if (type === "memberships") { icon = I.trophy; tone = "lav"; }
    else if (type === "due") { icon = I.cash; tone = "green"; }
    else if (tag === "PAYMENT") { icon = I.wallet; tone = "green"; }
    else if (tag === "WARNING") { icon = I.alert; tone = "red"; }
    var amt = a.amount != null ? '<span class="amt' + (a.due ? " neg" : "") + '">' + money(a.amount) + "</span>" : "";
    return '<div class="lrow"><span class="lic ' + tone + '">' + icon + '</span><span class="grow">' +
      "<b class='t'>" + esc(a.message || type || tag || "Activity") + "</b>" +
      '<span class="s">' + esc(fmtDateTime(a.createdAt)) + (a.mode ? " · " + esc(a.mode) : "") + "</span></span>" + amt + "</div>";
  }

  /* ---------------- slot bookings ---------------- */
  var Booking = { supported: true, loaded: false, list: [] };
  Booking.load = async function () {
    // 404 = club server predates the bookings module -> hide booking UI, no errors.
    var r = await Api.get("/clubs/" + encodeURIComponent(Store.clubId) + "/bookings?club_id=" + encodeURIComponent(Store.clubId) + "&mine=1");
    Booking.supported = true;
    Booking.loaded = true;
    Booking.list = ((r && r.bookings) || []).slice().sort(function (a, b) {
      return String(a.startAt).localeCompare(String(b.startAt));
    });
  };
  Booking.upcoming = function () {
    return Booking.list.filter(function (b) { return b.status === "upcoming"; });
  };
  function istToday() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  }
  function addDaysISO(ymd, n) {
    var d = parseISO(ymd + "T00:00:00Z") || new Date();
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function istNowHM() {
    try {
      var p = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()).split(":");
      return { h: (+p[0]) % 24, m: +p[1] };
    } catch (e) { var d = new Date(); return { h: d.getHours(), m: d.getMinutes() }; }
  }
  function istToUtcMs(dateStr, hm) {
    var dp = String(dateStr).split("-"), tp = String(hm).split(":");
    return Date.UTC(+dp[0], +dp[1] - 1, +dp[2], +tp[0], +tp[1]) - (5 * 60 + 30) * 60000;
  }
  function hmOptions(fromH, toHLast) {
    var out = [];
    for (var h = fromH; h <= toHLast; h++) { out.push(pad2(h) + ":00"); out.push(pad2(h) + ":30"); }
    return out;
  }
  function slotLabel(b) {
    var day = (b.dateIST === istToday()) ? "Today" : (b.dateIST === addDaysISO(istToday(), 1) ? "Tomorrow" : fmtDay(b.startAt));
    return day + " " + (b.startIST || "") + "–" + (b.endIST || "");
  }

  /* ---------------- cart ---------------- */
  var Cart = {
    set: function (id, qty) {
      var menu = (Store.dashboard.menuItems || []);
      var item = menu.filter(function (x) { return x.id === id; })[0];
      if (!item) return;
      qty = Math.max(0, Math.min(50, qty | 0));
      var stock = Number(item.stockQty) || 0;
      if (qty > stock) { toast("Only " + stock + " " + item.name + " left in stock.", "err"); qty = stock; }
      if (qty <= 0) delete Store.cart[id]; else Store.cart[id] = qty;
      Store.saveCart();
      if (Router.current === "order") Views.orderList();
      updateCartBar();
    },
    clear: function () { Store.cart = {}; Store.saveCart(); if (Router.current === "order") Views.orderList(); updateCartBar(); },
    count: function () { return Object.keys(Store.cart).reduce(function (s, k) { return s + (Store.cart[k] || 0); }, 0); },
    total: function () {
      var byId = {}; (Store.dashboard.menuItems || []).forEach(function (x) { byId[x.id] = x; });
      return Object.keys(Store.cart).reduce(function (s, k) { return byId[k] ? s + Number(byId[k].price) * Store.cart[k] : s; }, 0);
    },
  };
  function updateCartBar() {
    var bar = $("#cartbar");
    var n = Cart.count();
    var show = n > 0 && (Router.current === "order");
    bar.classList.toggle("show", show);
    $("#cartbar-count").textContent = n + " item" + (n === 1 ? "" : "s");
    $("#cartbar-total").textContent = money(Cart.total()) + " · wallet";
  }

  /* ---------------- misc wiring ---------------- */
  function wireGo(root) {
    $all("[data-go]", root).forEach(function (b) {
      b.addEventListener("click", function () {
        var go = b.getAttribute("data-go"), act = b.getAttribute("data-act");
        Router.go(go);
        if (act === "addmoney") setTimeout(Views.addMoneySheet, 350);
        if (act === "paydue") setTimeout(function () { if ((Number(member().dueAmount) || 0) > 0) Views.payDueSheet(); }, 350);
      });
    });
  }
  function tickClock(root) {
    var now = Date.now() + Store.clockOffsetMs;
    $all("[data-since]", root || document).forEach(function (el) {
      var d = parseISO(el.getAttribute("data-since"));
      el.textContent = d ? fmtElapsed(now - d.getTime()) : "";
    });
  }
  setInterval(function () { tickClock(document); }, 1000);

  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", "light");
    try { localStorage.removeItem(LS.theme); } catch (e) {}
  }

  /* ---------------- PWA install ---------------- */
  var PWA = { deferred: null };
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    PWA.deferred = e;
    if (Router.current === "settings") Views.settings();
  });
  window.addEventListener("appinstalled", function () {
    PWA.deferred = null;
    toast("App installed — see you on the home screen!");
    if (Router.current === "settings") Views.settings();
  });

  /* ---------------- boot ---------------- */
  async function boot() {
    try {
      setTheme("light");
    } catch (e) {}

    $("#scrim").addEventListener("click", Sheet.close);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") Sheet.close(); });
    $all(".navbtn").forEach(function (b) {
      b.addEventListener("click", function () { Router.go(b.getAttribute("data-route")); });
    });
    $all("[data-back]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (Router.current === "settings") Router.go("profile");
        else Router.go("home");
      });
    });
    $("#cartbar").addEventListener("click", Views.cartSheet);
    $("#home-bell").addEventListener("click", function () { Router.go("activity"); });
    $("#home-search-form").addEventListener("submit", function (e) {
      e.preventDefault();
      pendingOrderQuery = $("#home-search").value.trim();
      Views.orderCat = "All";
      if (Router.current === "order") {
        Views.orderQuery = pendingOrderQuery; pendingOrderQuery = "";
        Views.order();
      } else Router.go("order");
    });
    $("#order-search-form").addEventListener("submit", function (e) { e.preventDefault(); });
    $all("#tables-chips .chip").forEach(function (b) {
      b.addEventListener("click", function () {
        $all("#tables-chips .chip").forEach(function (x) { x.classList.remove("active"); x.setAttribute("aria-selected", "false"); });
        b.classList.add("active"); b.setAttribute("aria-selected", "true");
        Views.tablesFilter = b.getAttribute("data-f");
        Views.tables();
      });
    });
    $("#activity-more").addEventListener("click", function () { Views.actShown += 30; Views.activityList(); });

    var offbar = $("#offbar");
    var net = function () { offbar.classList.toggle("show", !navigator.onLine); };
    window.addEventListener("online", net); window.addEventListener("offline", net); net();

    window.addEventListener("hashchange", function () { Router.render(Router.parse()); });

    if ("serviceWorker" in navigator && /^https?:/.test(location.protocol)) {
      navigator.serviceWorker.register("./sw.js").catch(function () {});
    }

    await Auth.checkDevMode();
    if (!location.hash) location.hash = "#/login";
    if (Store.token) {
      var ok = await Auth.restore();
      if (ok === "role") {
        // non-member token (owner/staff/master) — refuse member screens
        $("#login-box").innerHTML = errBlock("Members only", "This sign-in belongs to an owner / staff account. Member screens need a club membership — ask the club to register your email.", false) +
          '<div class="mt12"><button class="btn primary" id="blocked-ok">Use Member Sign-In</button></div>';
        Router.show("login");
        $("#blocked-ok").addEventListener("click", function () { Store.saveToken(""); location.hash = "#/login"; Router.render("login"); });
        return;
      }
      if (ok) {
        var memberships = (Store.user && Store.user.memberships) || [];
        var route = Router.parse();
        if (route === "login") route = memberships.length > 1 ? "clubs" : "home";
        try { await Data.loadDashboard({ silent: true }); } catch (e) { /* render() shows retry */ }
        Router.render(route);
        return;
      }
    }
    Router.render("login");
  }

  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", boot) : boot();
})();
