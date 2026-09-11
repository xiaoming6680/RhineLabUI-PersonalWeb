/* The build replaces both tokens; this file is never registered in development. */
const VERSION = __CACHE_VERSION__;
const FILES = __PRECACHE_FILES__;
const PREFIX = `rhine-lab:${new URL(self.registration.scope).pathname}:`;
const CACHE = PREFIX + VERSION;
const urls = FILES.map(path => new URL(path, self.registration.scope).href);
const allowed = new Set(urls);
const index = new URL("index.html", self.registration.scope).href;

// Cloudflare Pages answers /index.html with a 308 to /, so the precached entry is
// a redirected response. Browsers refuse a redirected response for a navigation
// request (Chromium fails the page with ERR_FAILED), so store a plain copy instead.
async function clean(response) {
  if (!response?.redirected) return response;
  return new Response(await response.arrayBuffer(), {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers
  });
}
async function storeClean(cache, key) {
  const response = await cache.match(key);
  if (response?.redirected) await cache.put(key, await clean(response));
}

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(CACHE);
      // Conditional validation also catches model/font changes at stable URLs.
      await cache.addAll(urls.map(url => new Request(url, { cache: "no-cache" })));
      await storeClean(cache, index);
    } catch (error) {
      await caches.delete(CACHE);
      throw error;
    }
  })());
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys())
      if (key.startsWith(PREFIX) && key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});
self.addEventListener("message", event => {
  if (event.data?.type === "RHINE_APPLY_UPDATE") event.waitUntil(self.skipWaiting());
});
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  url.search = "";
  url.hash = "";
  const navigation = event.request.mode === "navigate" &&
    (url.href === self.registration.scope || url.href === index);
  const key = navigation ? index : url.href;
  if (!allowed.has(key)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    // HTML, hashed bundles and stable model URLs come from the same release.
    // A new release stays waiting until the user chooses to restart or exits.
    const cached = await clean(await cache.match(key));
    return cached ?? fetch(event.request);
  })());
});
