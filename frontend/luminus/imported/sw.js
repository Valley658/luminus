const STATIC_CACHE = "luminous-static-v2";

const PAGE_CACHE = "luminous-pages-v2";

const THUMB_CACHE = "luminous-thumbs-v2";

const THUMB_CACHE_LIMIT = 300;

const THUMB_EXTERNAL_HOSTS = [ "i.ytimg.com", "i9.ytimg.com" ];

function isThumbRequest(url) {
    if (url.origin === self.location.origin) {
        return url.pathname.startsWith("/api/drive_thumb/");
    }
    return THUMB_EXTERNAL_HOSTS.includes(url.hostname);
}

async function trimThumbCache(cache) {
    const keys = await cache.keys();
    if (keys.length <= THUMB_CACHE_LIMIT) return;
    const toDelete = keys.slice(0, keys.length - THUMB_CACHE_LIMIT);
    await Promise.all(toDelete.map(key => cache.delete(key)));
}

self.addEventListener("install", event => {
    self.skipWaiting();
});

self.addEventListener("activate", event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== STATIC_CACHE && key !== PAGE_CACHE && key !== THUMB_CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
    const req = event.request;
    if (req.method !== "GET") return;
    const url = new URL(req.url);
    if (isThumbRequest(url)) {
        event.respondWith(caches.open(THUMB_CACHE).then(cache => cache.match(req).then(cached => {
            const networkFetch = fetch(req).then(res => {
                if (res && res.status === 200) {
                    cache.put(req, res.clone());
                    trimThumbCache(cache);
                }
                return res;
            }).catch(() => cached);
            return cached || networkFetch;
        })));
        return;
    }
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith("/api/")) return;
    if (url.pathname.startsWith("/static/uploads/")) return;
    if (url.pathname.startsWith("/static/")) {
        event.respondWith(caches.open(STATIC_CACHE).then(cache => cache.match(req).then(cached => {
            const networkFetch = fetch(req).then(res => {
                if (res && res.status === 200) cache.put(req, res.clone());
                return res;
            }).catch(() => cached);
            return cached || networkFetch;
        })));
        return;
    }
    if (req.mode === "navigate") {
        event.respondWith(fetch(req).then(res => {
            if (res && res.status === 200) {
                const resClone = res.clone();
                caches.open(PAGE_CACHE).then(cache => cache.put(req, resClone));
            }
            return res;
        }).catch(() => caches.open(PAGE_CACHE).then(cache => cache.match(req))));
    }
});