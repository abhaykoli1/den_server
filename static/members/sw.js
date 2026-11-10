/* Member app shell caching only. Private pages and API data are always network-only. */
"use strict";

var CACHE_NAME = "den-members-shell-v4.1.19";
var SHELL_ASSETS = [
  "/members/static/members/app.css?v=4.1.19",
  "/members/static/members/js/ui.js?v=4.1.19",
  "/members/static/members/js/flows.js?v=4.1.19",
  "/members/static/members/js/pages.js?v=4.1.19",
  "/members/static/members/icons/icon-192.png",
  "/members/static/members/icons/icon-512.png",
  "/members/static/members/icons/icon-maskable-512.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE_NAME).then(function (cache) {
    return cache.addAll(SHELL_ASSETS);
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (key) {
      return key.indexOf("den-members-shell-") === 0 && key !== CACHE_NAME;
    }).map(function (key) { return caches.delete(key); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  var url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  /* Never persist authenticated HTML, member details, or payment/API responses. */
  if (request.mode === "navigate" && url.pathname.indexOf("/members/") === 0) {
    event.respondWith(fetch(request).catch(function () {
      return new Response(
        '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f3eee5"><title>Offline · Members</title><style>body{font:16px system-ui,sans-serif;background:#f3eee5;color:#211b16;margin:0;min-height:100vh;display:grid;place-items:center}.card{max-width:320px;margin:24px;padding:28px;border-radius:22px;background:#fff;text-align:center;box-shadow:0 12px 36px #211b1618}h1{font-size:22px}p{color:#77716d;line-height:1.5}button{border:0;border-radius:12px;background:#188b51;color:white;padding:13px 20px;font:600 15px system-ui}</style><main class="card"><h1>You’re offline</h1><p>Reconnect to the internet to view your club account and continue.</p><button onclick="location.reload()">Try again</button></main></html>',
        { status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } }
      );
    }));
    return;
  }

  /* Cache only same-origin static app assets; never /members/img or API URLs. */
  if (url.pathname.indexOf("/members/static/members/") === 0) {
    event.respondWith(caches.match(request).then(function (cached) {
      var fresh = fetch(request).then(function (response) {
        if (response.ok) {
          var copy = response.clone();
          caches.open(CACHE_NAME).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      });
      return cached || fresh;
    }));
  }
});
