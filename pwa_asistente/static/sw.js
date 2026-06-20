/**
 * Service Worker — Suite Analítica PWA
 *
 * Qué hace:
 *   - Cachea los archivos estáticos (CSS, JS) para que la app
 *     cargue rápido aunque la conexión sea lenta.
 *   - Las llamadas a /api/* siempre van a la red (datos en tiempo real).
 *
 * El usuario no nota que existe — solo nota que la app es rápida.
 */

const CACHE_NAME = 'suite-analitica-v66';

// Derivar el prefijo de ruta según dónde está instalado el SW
// Local: self.location = http://localhost:8001/sw.js → BASE = ''
// Servidor: self.location = https://servidor/IA/sw.js → BASE = '/IA'
const BASE = self.location.pathname.replace(/\/sw\.js$/, '');

// Solo CSS y JS se cachean — el HTML siempre va a la red para estar al día
const STATIC_ASSETS = [
  `${BASE}/static/style.css?v=66`,
  `${BASE}/static/app.js?v=66`,
];

// Instalar: guardar assets estáticos en caché
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activar: limpiar cachés viejos y notificar a los clientes
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.matchAll({ type: 'window' }))
      .then(clients => clients.forEach(c => c.postMessage({ type: 'SW_UPDATED' })))
  );
  self.clients.claim();
});

// Fetch: caché para CSS/JS, red para todo lo demás
self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // API y HTML siempre van a la red (nunca cachear)
  if (url.includes('/api/') || event.request.mode === 'navigate') return;

  // CSS y JS: caché primero, red como fallback
  if (url.includes('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
  }
});
