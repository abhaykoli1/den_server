/* Rowdy's Den · Members App — flows.js
   Every money/action flow, wired by data-* attributes on the server-rendered
   pages. All mutations go through /api/member-portal/* with the Bearer token
   (window.DEN.token) — cookies never authorise changes. */
(function () {
  "use strict";
  var DEN = window.DEN || {};
  var U = window.DENx || {};
  var $ = U.$, $$ = U.$$, esc = U.esc, money = U.money, toast = U.toast, api = U.api, busy = U.busy,
      openSheet = U.openSheet, closeSheet = U.closeSheet, confirmSheet = U.confirmSheet;

  document.addEventListener("click", function (e) {

    /* ------------------------------------------------------------ pay an order from wallet */
    var b = e.target.closest("[data-pay-order]");
    if (b) {
      var id = b.getAttribute("data-pay-order"), amt = b.getAttribute("data-amount");
      confirmSheet("Pay " + money(amt) + " from wallet?", "This settles the order and clears it from your tab.", "Pay now").then(function (ok) {
        if (!ok) return; busy(b, true);
        api("POST", "/member-portal/orders/" + id + "/pay", {}).then(function (r) { toast(r.message, "ok"); setTimeout(function () { location.reload(); }, 600); }).catch(function (err) { busy(b, false); toast(err.message, "bad", 4000); });
      });
    }

    /* ------------------------------------------------------------ clear dues from wallet */
    var d = e.target.closest("[data-pay-due]");
    if (d) {
      var due = Number(d.getAttribute("data-due")), wal = Number(d.getAttribute("data-wallet"));
      var max = Math.min(due, wal);
      if (max <= 0) { toast(wal <= 0 ? "Your wallet is empty — top up first or pay at the counter" : "Nothing due", "bad"); return; }
      openSheet('<div class="sh-title">Clear your tab</div><div class="sh-sub">Due ' + money(due) + ' · wallet ' + money(wal) + '</div>' +
        '<div class="field mt16"><label>Amount</label><input class="input" type="number" inputmode="decimal" id="due-amt" min="1" max="' + max + '" step="1" value="' + max + '"></div>' +
        '<div class="chips mt12" style="margin:12px 0 0"><button class="chip" data-amt="' + max + '">Max ' + money(max) + '</button>' + (max > 100 ? '<button class="chip" data-amt="100">₹100</button>' : "") + (max > 200 ? '<button class="chip" data-amt="200">₹200</button>' : "") + (max > 500 ? '<button class="chip" data-amt="500">₹500</button>' : "") + '</div>' +
        '<button class="btn mt20" id="due-go">Pay from wallet</button><p class="hint center mt12">You can also pay cash / UPI at the counter.</p>', function (s) {
          $$("[data-amt]", s).forEach(function (c) { c.addEventListener("click", function () { $("#due-amt", s).value = c.getAttribute("data-amt"); }); });
          $("#due-go", s).addEventListener("click", function () {
            var v = Number($("#due-amt", s).value); if (!(v > 0) || v > max) { toast("Enter an amount up to " + money(max), "bad"); return; }
            busy(this, true);
            api("POST", "/member-portal/dues/pay", { amount: v }).then(function (r) { closeSheet(); toast(r.message, "ok"); setTimeout(function () { location.reload(); }, 600); }).catch(function (err) { busy($("#due-go", s), false); toast(err.message, "bad", 4000); });
          });
        });
    }

    /* ------------------------------------------------------------ wallet top-up (UPI + UTR) */
    var t = e.target.closest("[data-topup]");
    if (t) {
      var offers = []; try { offers = JSON.parse(t.getAttribute("data-offers") || "[]"); } catch (er) { }
      var upi = DEN.upiId || "", qr = DEN.qr || "";
      if (!upi && !qr) { openSheet('<div class="sh-title">Top up at the counter</div><div class="sh-sub">Online top-ups aren\'t enabled for this club yet — pay cash / UPI at the counter and the balance shows here instantly.</div><button class="btn mt20" data-close>Okay</button>'); return; }
      var preset = Number(t.getAttribute("data-amount") || 0) || (offers[0] ? offers[0].amount : 500);
      openSheet('<div class="sh-title">Top up wallet</div><div class="sh-sub">Pay on UPI, then enter the reference — the counter confirms and your balance updates.</div>' +
        (offers.length ? '<div class="list mt16">' + offers.map(function (o) { return '<button class="choice" data-off="' + o.amount + '"><div><div class="t">' + esc(o.name) + ' · ' + money(o.amount) + '</div><div class="s">' + esc(o.benefit) + '</div></div><span class="box"></span></button>'; }).join("") + '</div>' : "") +
        '<div class="field mt12"><label>Amount</label><input class="input" type="number" inputmode="numeric" id="tu-amt" min="50" step="50" value="' + preset + '"></div>' +
        '<div class="card mt12 center" id="tu-pay"></div>' +
        '<div class="field mt12"><label>UPI reference / UTR</label><input class="input" id="tu-utr" placeholder="e.g. 3241 5678 9012" autocomplete="off"></div>' +
        '<button class="btn mt16" id="tu-go">Submit for confirmation</button>', function (s) {
          function draw() {
            var v = Number($("#tu-amt", s).value) || 0; var box = $("#tu-pay", s);
            box.innerHTML = (qr ? '<img class="qr" src="' + qr + '" alt="Club UPI QR">' : "") + (upi ? '<div class="mt12"><span class="upi-id">' + esc(upi) + ' <button class="link xs" data-copy="' + esc(upi) + '">copy</button></span></div><a class="btn sm mt12 auto" href="upi://pay?pa=' + encodeURIComponent(upi) + '&pn=' + encodeURIComponent(DEN.clubName || "Club") + '&am=' + v.toFixed(2) + '&cu=INR&tn=' + encodeURIComponent("Wallet top-up " + (DEN.memberName || "")) + '">Pay ' + money(v) + ' with any UPI app</a>' : "");
            $$("[data-copy]", box).forEach(function (b) { b.addEventListener("click", function () { navigator.clipboard && navigator.clipboard.writeText(b.getAttribute("data-copy")); toast("UPI ID copied", "ok", 1200); }); });
            $$("[data-off]", s).forEach(function (c) { c.classList.toggle("on", Number(c.getAttribute("data-off")) === v); });
          }
          $$("[data-off]", s).forEach(function (c) { c.addEventListener("click", function () { $("#tu-amt", s).value = c.getAttribute("data-off"); draw(); }); });
          $("#tu-amt", s).addEventListener("input", draw); draw();
          $("#tu-go", s).addEventListener("click", function () {
            var v = Number($("#tu-amt", s).value), utr = $("#tu-utr", s).value.trim();
            if (!(v >= 50)) { toast("Minimum top-up is ₹50", "bad"); return; }
            if (utr.length < 4) { toast("Enter the UPI reference number", "bad"); $("#tu-utr", s).focus(); return; }
            busy(this, true);
            api("POST", "/member-portal/wallet/topup-requests", { amount: v, utr: utr }).then(function (r) { closeSheet(); toast(r.message, "ok", 3500); setTimeout(function () { location.reload(); }, 800); }).catch(function (err) { busy($("#tu-go", s), false); toast(err.message, "bad", 4000); });
          });
        });
    }

    /* ------------------------------------------------------------ buy a membership plan */
    var bp = e.target.closest("[data-buy-plan]");
    if (bp) {
      var plan = {}; try { plan = JSON.parse(bp.getAttribute("data-buy-plan")); } catch (er) { }
      var wal2 = Number(DEN.wallet || 0), canWallet = plan.type !== "wallet" && wal2 >= plan.amount;
      var upi2 = DEN.upiId || "", qr2 = DEN.qr || "";
      openSheet('<div class="sh-title">' + esc(plan.name) + ' · ' + money(plan.amount) + '</div><div class="sh-sub">' + esc(plan.benefit) + '</div>' +
        '<div class="list mt16">' +
        (plan.type !== "wallet" ? '<button class="choice ' + (canWallet ? "on" : "off") + '" data-pm="wallet"><div><div class="t">Pay from wallet</div><div class="s">' + (canWallet ? "Instant · balance " + money(wal2) + " → " + money(wal2 - plan.amount) : "Balance " + money(wal2) + " is short") + '</div></div><span class="box"></span></button>' : "") +
        ((upi2 || qr2) ? '<button class="choice ' + (canWallet ? "" : "on") + '" data-pm="upi"><div><div class="t">Pay on UPI</div><div class="s">Activates once the counter confirms your reference</div></div><span class="box"></span></button>' : "") +
        '</div><div id="pl-upi" class="mt12" hidden>' + '<div class="card center">' + (qr2 ? '<img class="qr" src="' + qr2 + '" alt="Club UPI QR">' : "") + (upi2 ? '<div class="mt12"><span class="upi-id">' + esc(upi2) + '</span></div><a class="btn sm mt12 auto" href="upi://pay?pa=' + encodeURIComponent(upi2) + '&pn=' + encodeURIComponent(DEN.clubName || "Club") + '&am=' + Number(plan.amount).toFixed(2) + '&cu=INR&tn=' + encodeURIComponent(plan.name) + '">Pay ' + money(plan.amount) + ' with any UPI app</a>' : "") + '</div>' +
        '<div class="field mt12"><label>UPI reference / UTR</label><input class="input" id="pl-utr" placeholder="Reference from your UPI app"></div></div>' +
        '<p class="hint mt12">Pay cash at the counter? Just ask — plans can be activated there too.</p>' +
        '<button class="btn mt16" id="pl-go">Continue</button>', function (s) {
          var pm = canWallet ? "wallet" : ((upi2 || qr2) ? "upi" : "");
          function draw() { $$("[data-pm]", s).forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-pm") === pm); }); $("#pl-upi", s).hidden = pm !== "upi"; $("#pl-go", s).textContent = pm === "wallet" ? "Buy from wallet · " + money(plan.amount) : pm === "upi" ? "I've paid · submit" : "Pay at the counter"; }
          $$("[data-pm]", s).forEach(function (c) { c.addEventListener("click", function () { if (c.classList.contains("off")) { toast("Not enough wallet balance", "bad"); return; } pm = c.getAttribute("data-pm"); draw(); }); });
          draw();
          $("#pl-go", s).addEventListener("click", function () {
            var btn = this;
            if (pm === "wallet") { busy(btn, true); api("POST", "/member-portal/membership-plans/" + plan.id + "/buy", { mode: "wallet" }).then(function (r) { closeSheet(); toast(r.message, "ok", 3500); setTimeout(function () { location.reload(); }, 800); }).catch(function (err) { busy(btn, false); toast(err.message, "bad", 4000); }); }
            else if (pm === "upi") { var utr = $("#pl-utr", s).value.trim(); if (utr.length < 4) { toast("Enter the UPI reference", "bad"); return; } busy(btn, true); api("POST", "/member-portal/membership-requests", { planId: plan.id, utr: utr }).then(function (r) { closeSheet(); toast(r.message, "ok", 3500); setTimeout(function () { location.reload(); }, 800); }).catch(function (err) { busy(btn, false); toast(err.message, "bad", 4000); }); }
            else closeSheet();
          });
        });
    }

    /* ------------------------------------------------------------ join / leave tournament */
    var jt = e.target.closest("[data-join]");
    if (jt) {
      var tid = jt.getAttribute("data-join"), fee = Number(jt.getAttribute("data-fee") || 0), wal3 = Number(DEN.wallet || 0), tname = jt.getAttribute("data-name") || "the event";
      openSheet('<div class="sh-title">Register for ' + esc(tname) + '</div><div class="sh-sub">' + (fee > 0 ? "Entry fee " + money(fee) : "Free entry") + '</div>' +
        (fee > 0 ? '<div class="list mt16"><button class="choice ' + (wal3 >= fee ? "on" : "off") + '" data-jm="wallet"><div><div class="t">Pay entry from wallet</div><div class="s">Balance ' + money(wal3) + '</div></div><span class="box"></span></button><button class="choice ' + (wal3 >= fee ? "" : "on") + '" data-jm="counter"><div><div class="t">Pay at the counter</div><div class="s">Your spot is held · pay before the event</div></div><span class="box"></span></button></div>' : "") +
        '<button class="btn mt16" id="jn-go">Confirm registration</button>', function (s) {
          var jm = fee > 0 ? (wal3 >= fee ? "wallet" : "counter") : "counter";
          $$("[data-jm]", s).forEach(function (c) { c.addEventListener("click", function () { if (c.classList.contains("off")) return; jm = c.getAttribute("data-jm"); $$("[data-jm]", s).forEach(function (x) { x.classList.toggle("on", x === c); }); }); });
          $("#jn-go", s).addEventListener("click", function () { busy(this, true); api("POST", "/member-portal/tournaments/" + tid + "/join", { mode: jm }).then(function (r) { closeSheet(); toast(r.message, "ok", 3500); setTimeout(function () { location.reload(); }, 800); }).catch(function (err) { busy($("#jn-go", s), false); toast(err.message, "bad", 4000); }); });
        });
    }
    var lv = e.target.closest("[data-leave]");
    if (lv) { confirmSheet("Withdraw your registration?", "You can register again while spots last.", "Withdraw", true).then(function (ok) { if (!ok) return; api("DELETE", "/member-portal/tournaments/" + lv.getAttribute("data-leave") + "/join").then(function (r) { toast(r.message, "ok"); setTimeout(function () { location.reload(); }, 600); }).catch(function (err) { toast(err.message, "bad", 4000); }); }); }

    /* ------------------------------------------------------------ cancel a booking */
    var cb = e.target.closest("[data-cancel-booking]");
    if (cb) { confirmSheet("Cancel this booking?", cb.getAttribute("data-label") || "", "Cancel booking", true).then(function (ok) { if (!ok) return; busy(cb, true); api("DELETE", "/member-portal/bookings/" + cb.getAttribute("data-cancel-booking")).then(function (r) { toast(r.message, "ok"); setTimeout(function () { location.href = "/members/bookings"; }, 600); }).catch(function (err) { busy(cb, false); toast(err.message, "bad", 4000); }); }); }

    /* ------------------------------------------------------------ switch club (multi-club members) */
    var sw = e.target.closest("[data-switch-club]");
    if (sw) { api("POST", "/member-portal/switch-club", { clubId: sw.getAttribute("data-switch-club") }).then(function (r) { toast(r.message, "ok"); setTimeout(function () { location.href = "/members"; }, 500); }).catch(function (err) { toast(err.message, "bad"); }); }
  });
})();
