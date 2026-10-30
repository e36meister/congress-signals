// Capitol Capital alerts: shows each push as a notification; tapping one opens the app at what it's about.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

// number on the app icon: one per alert since the app was last opened (the app resets it)
async function bumpBadge() {
  try {
    const c = await caches.open("cc-badge");
    const r = await c.match("/n");
    const n = (r ? parseInt(await r.text(), 10) || 0 : 0) + 1;
    await c.put("/n", new Response(String(n)));
    if (self.navigator.setAppBadge) await self.navigator.setAppBadge(n);
  } catch (err) {}
}

self.addEventListener("push", e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { title: "Capitol Capital", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(Promise.all([
    self.registration.showNotification(m.title || "Capitol Capital", {
      body: m.body || "", tag: m.tag || undefined, icon: "/icon-192.png", badge: "/icon-192.png", data: { url: m.url || "/" } }),
    bumpBadge()]));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const u = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin);
  // an outside website (e.g. the Senate filings search for "Check" alerts): open it directly, not inside the app
  if (u.origin !== self.location.origin) {
    u.searchParams.delete("n");
    e.waitUntil(self.clients.openWindow(u.href));
    return;
  }
  const url = u.href;
  // app already open (or in the background): tell it what to show; otherwise open it at that link
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const c of list) { if ("focus" in c) { c.postMessage({ type: "open", url }); return c.focus().catch(() => {}); } }
    return self.clients.openWindow(url);
  }));
});
