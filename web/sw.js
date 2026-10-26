// ============================================================
//  GOEVENT — Service Worker PWA
//  Cache offline, sync background, notifications push
// ============================================================

const APP_VERSION = "v1.2.1"; // À incrémenter à chaque changement de ce fichier ou de l'App Shell
const CACHE_STATIC = "goevent-static-" + APP_VERSION;
const CACHE_API = "goevent-api-" + APP_VERSION;
// API de prod + API locale (dev). Seules les lectures publiques de la liste
// blanche CACHEABLE_API passent par le service worker : tout le reste
// (POST/PUT/DELETE, paiement, scan, billets, admin) va directement au réseau,
// sans délai ni cache — un délai artificiel sur un POST de paiement ferait
// croire à un échec pendant que le serveur traite → second clic → double paiement.
const API_ORIGINS = ["https://api.goevent.africa", "http://localhost:8000", "http://127.0.0.1:8000"];

// Fichiers à mettre en cache immédiatement (App Shell)
const APP_SHELL = [
  "/",
  "/index.html",
  "/login.html",
  "/signup.html",
  "/event.html",
  "/event_detail.html",
  "/user_dashboard.html",
  "/artist_dashboard.html",
  "/organisation_dashboard.html",
  "/a_propos.html",
  "/scanner.html",
  "/ticket_view.html",
  "/invitation.html",
  "/bt-api.js",
  "/auth-guard.js",
  "/vendor/qrcode-generator-2.0.4.js",
  "/vendor/html5-qrcode-2.3.8.min.js",
  "/pwa.js",
  "/manifest.json",
  "/icons/icon-192.svg",
  "/icons/icon-512.svg",
  // Fonts Google (si disponibles)
  "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Manrope:wght@400;500;600;700;800&display=swap",
  "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap",
];

// Routes API publiques mises en cache (GET uniquement, réseau d'abord)
const CACHEABLE_API = ["/events", "/categories"];

// ── INSTALL ──────────────────────────────────────────────────
self.addEventListener("install", function (event) {
  console.log("[SW] Install GoEvent", APP_VERSION);
  event.waitUntil(
    caches
      .open(CACHE_STATIC)
      .then(function (cache) {
        return Promise.allSettled(
          APP_SHELL.map(function (url) {
            return cache.add(url).catch(function (err) {
              console.log("[SW] Cache miss:", url, err.message);
            });
          }),
        );
      })
      .then(function () {
        return self.skipWaiting();
      }),
  );
});

// ── ACTIVATE ─────────────────────────────────────────────────
self.addEventListener("activate", function (event) {
  console.log("[SW] Activate GoEvent", APP_VERSION);
  event.waitUntil(
    // Supprimer les anciens caches (y compris ceux de Bangui Tickets)
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key !== CACHE_STATIC && key !== CACHE_API;
            })
            .map(function (key) {
              console.log("[SW] Suppression ancien cache:", key);
              return caches.delete(key);
            }),
        );
      })
      .then(function () {
        return self.clients.claim();
      }),
  );
});

// ── FETCH — Stratégie par type de requête ────────────────────
self.addEventListener("fetch", function (event) {
  var request = event.request;
  var url = new URL(request.url);

  // Jamais d'interception hors GET : le navigateur envoie la requête tel quel.
  if (request.method !== "GET") return;

  if (API_ORIGINS.indexOf(url.origin) !== -1) {
    var cacheable = CACHEABLE_API.some(function (path) {
      return url.pathname === path || url.pathname.startsWith(path + "/");
    });
    // Requêtes authentifiées ou hors liste blanche : réseau direct, pas de cache
    if (cacheable && !request.headers.get("Authorization")) event.respondWith(networkFirstAPI(request));
    return;
  }
  if (
    url.hostname === "fonts.googleapis.com" ||
    url.hostname === "fonts.gstatic.com"
  ) {
    event.respondWith(cacheFirst(event.request));
    return;
  }
  if (url.hostname === "cdn.tailwindcss.com") {
    event.respondWith(cacheFirst(event.request));
    return;
  }
  if (event.request.destination === "image") {
    event.respondWith(cacheFirstWithFallback(event.request));
    return;
  }
  if (url.origin === self.location.origin) {
    // Pages HTML : réseau d'abord (fin des versions périmées après déploiement),
    // cache seulement hors ligne. Scripts/styles/images : stale-while-revalidate.
    if (request.mode === "navigate" || (request.headers.get("Accept") || "").indexOf("text/html") !== -1) {
      event.respondWith(networkFirstPage(request));
    } else {
      event.respondWith(staleWhileRevalidate(request));
    }
    return;
  }
});

// ── STRATÉGIES DE CACHE ───────────────────────────────────────

// Lectures publiques uniquement (filtrées dans le handler fetch)
async function networkFirstAPI(request) {
  try {
    var response = await fetchWithTimeout(request, 8000);
    if (response.ok) {
      var cache = await caches.open(CACHE_API);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    var cached = await caches.match(request);
    if (cached) {
      console.log("[SW] Offline — cache API:", request.url);
      return cached;
    }
    return new Response(
      JSON.stringify({
        detail: "Hors ligne — vérifiez votre connexion",
        offline: true,
      }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
  }
}

async function networkFirstPage(request) {
  var cache = await caches.open(CACHE_STATIC);
  try {
    var response = await fetch(request);
    // Pages avec paramètres sensibles (ex. ticket_view.html?qr_hash=…) : jamais mises en cache par URL complète
    if (response.ok) cache.put(new URL(request.url).pathname, response.clone());
    return response;
  } catch (err) {
    var cached = await cache.match(new URL(request.url).pathname);
    if (cached) return cached;
    var shell = await cache.match("/index.html");
    if (shell) return shell;
    return new Response("Hors ligne", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
}

async function cacheFirst(request) {
  var cached = await caches.match(request);
  if (cached) return cached;
  try {
    var response = await fetch(request);
    if (response.ok) {
      var cache = await caches.open(CACHE_STATIC);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response("Ressource non disponible hors ligne", { status: 503 });
  }
}

async function cacheFirstWithFallback(request) {
  var cached = await caches.match(request);
  if (cached) return cached;
  try {
    var response = await fetchWithTimeout(request, 3000);
    if (response.ok) {
      var cache = await caches.open(CACHE_STATIC);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" viewBox="0 0 400 200"><rect fill="#d1fae5" width="400" height="200"/><text x="200" y="110" text-anchor="middle" font-family="sans-serif" font-size="14" fill="#065f46">🎵 Image non disponible hors ligne</text></svg>';
    return new Response(svg, { headers: { "Content-Type": "image/svg+xml" } });
  }
}

async function staleWhileRevalidate(request) {
  var cache = await caches.open(CACHE_STATIC);
  var cached = await cache.match(request);

  var fetchPromise = fetch(request)
    .then(function (response) {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(function () {
      return null;
    });

  return cached || fetchPromise;
}

function fetchWithTimeout(request, ms) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error("Timeout " + ms + "ms"));
    }, ms);
    fetch(request)
      .then(function (response) {
        clearTimeout(timer);
        resolve(response);
      })
      .catch(function (err) {
        clearTimeout(timer);
        reject(err);
      });
  });
}

// ── BACKGROUND SYNC ───────────────────────────────────────────
self.addEventListener("sync", function (event) {
  console.log("[SW] Background sync:", event.tag);
  if (event.tag === "sync-tickets") {
    event.waitUntil(syncPendingTickets());
  }
});

async function syncPendingTickets() {
  try {
    var clients = await self.clients.matchAll();
    clients.forEach(function (client) {
      client.postMessage({ type: "SYNC_COMPLETE", tag: "sync-tickets" });
    });
  } catch (err) {
    console.log("[SW] Sync failed:", err);
  }
}

// ── PUSH NOTIFICATIONS ────────────────────────────────────────
self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {
      title: "GoEvent",
      body: event.data ? event.data.text() : "Nouvelle notification",
    };
  }

  var options = {
    body: data.body || "Vous avez une nouvelle notification",
    icon: "/icons/icon-192.svg",
    badge: "/icons/icon-72.svg",
    vibrate: [200, 100, 200],
    tag: data.tag || "goevent-notification",
    renotify: true,
    data: {
      url: data.url || "/index.html",
      dateOfArrival: Date.now(),
    },
    actions: [
      { action: "open", title: "Voir", icon: "/icons/icon-72.svg" },
      { action: "dismiss", title: "Ignorer", icon: "/icons/icon-72.svg" },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(data.title || "GoEvent 🎟️", options),
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  if (event.action === "dismiss") return;

  var urlToOpen =
    event.notification.data && event.notification.data.url
      ? event.notification.data.url
      : "/index.html";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(function (clients) {
        for (var i = 0; i < clients.length; i++) {
          if (clients[i].url.includes(self.location.origin)) {
            clients[i].focus();
            clients[i].navigate(urlToOpen);
            return;
          }
        }
        return self.clients.openWindow(urlToOpen);
      }),
  );
});

// ── MESSAGE depuis le client ──────────────────────────────────
self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
  if (event.data && event.data.type === "CLEAR_CACHE") {
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.map(function (key) {
          return caches.delete(key);
        }),
      );
    });
  }
});

console.log("[SW] GoEvent Service Worker", APP_VERSION, "chargé");
