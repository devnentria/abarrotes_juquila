'use strict';

// ── Autenticación ─────────────────────────────────────────────────────────────
const TOKEN_KEY = 'suite_token';

function getToken()        { return localStorage.getItem(TOKEN_KEY); }
function saveToken(token)  { localStorage.setItem(TOKEN_KEY, token); }
function clearToken()      { localStorage.removeItem(TOKEN_KEY); }

/**
 * Wrapper de fetch que inyecta el JWT en cada petición.
 * Si el servidor responde 401, limpia la sesión y muestra el login.
 *
 * @param {string} url     - Endpoint a consultar.
 * @param {object} options - Opciones adicionales de fetch (method, body, etc.).
 * @returns {Promise<Response>}
 */
async function authFetch(url, options = {}) {
  const headers = { ...options.headers, 'Authorization': `Bearer ${getToken()}` };
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    mostrarLogin();
    throw new Error('Sesión expirada');
  }
  return res;
}

const MAX_INTENTOS = 3;
let _intentosFallidos = 0;

/**
 * Muestra un banner informativo si el usuario accede desde un dispositivo
 * no recomendado para esta URL. No bloquea el acceso.
 *
 * PWA (supra.nentria.com)   → avisa si está en desktop
 * Studio (studio.nentria.com) → avisa si está en móvil
 */
function mostrarAvisoDispositivo() {
  const esMovil    = window.innerWidth < 768;
  const esStudio   = window.location.hostname.startsWith('studio');
  const clave      = `device_warning_${esStudio ? 'studio' : 'pwa'}_dismissed`;

  if (sessionStorage.getItem(clave)) return;

  const debeAvisar = esStudio ? esMovil : !esMovil;
  if (!debeAvisar) return;

  const titulo  = esStudio
    ? 'Studio está diseñado para computadora'
    : 'El Asistente está diseñado para celular';
  const mensaje = esStudio
    ? 'En móvil la experiencia puede no ser óptima. Te recomendamos abrirlo desde una computadora o laptop.'
    : 'En computadora la experiencia puede no ser óptima. Te recomendamos instalarlo desde tu teléfono.';

  const banner = document.createElement('div');
  banner.className = 'device-warning-banner';
  banner.innerHTML = `
    <span class="dw-icon">⚠️</span>
    <span class="dw-text"><strong>${titulo}</strong>${mensaje}</span>
    <button class="dw-close" aria-label="Cerrar">✕</button>
  `;
  banner.querySelector('.dw-close').addEventListener('click', () => {
    sessionStorage.setItem(clave, '1');
    banner.remove();
  });
  document.body.appendChild(banner);
}


function togglePassword() {
  const input = document.getElementById('login-password');
  const icon  = document.getElementById('eye-icon');
  const mostrar = input.type === 'password';
  input.type = mostrar ? 'text' : 'password';
  icon.innerHTML = mostrar
    ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
    : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
}

function mostrarLogin() {
  document.getElementById('login-screen').classList.remove('hidden');
}

function ocultarLogin() {
  document.getElementById('login-screen').classList.add('hidden');
}

function mostrarErrorLogin(msg) {
  const errorEl = document.getElementById('login-error');
  errorEl.textContent = msg;
  errorEl.classList.remove('hidden');
}

async function iniciarSesion() {
  const email    = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errorEl  = document.getElementById('login-error');
  const btn      = document.getElementById('login-btn');

  errorEl.classList.add('hidden');

  if (!email || !password) {
    mostrarErrorLogin('Ingresa tu usuario y contraseña.');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Entrando...';

  try {
    const body = new URLSearchParams({ username: email, password });
    const res  = await fetch('/auth/login', { method: 'POST', body });
    const data = await res.json();

    if (!res.ok) {
      _intentosFallidos++;
      if (_intentosFallidos >= MAX_INTENTOS) {
        mostrarErrorLogin('Demasiados intentos fallidos. Contacta a tu supervisor para restablecer tu acceso.');
        btn.disabled = true;
        btn.textContent = 'Bloqueado';
      } else {
        const restantes = MAX_INTENTOS - _intentosFallidos;
        mostrarErrorLogin(`Usuario o contraseña incorrectos. ${restantes} intento${restantes > 1 ? 's' : ''} restante${restantes > 1 ? 's' : ''}.`);
        btn.disabled = false;
        btn.textContent = 'Entrar';
      }
      return;
    }

    _intentosFallidos = 0;
    saveToken(data.access_token);


    state.usuario = { nombre: data.nombre, rol: data.rol, foto_perfil: data.foto_perfil || null };
    renderAvatarHeader(data.nombre, data.foto_perfil);
    ocultarLogin();
    await cargarDatos();

  } catch {
    mostrarErrorLogin('Error de conexión. Intenta de nuevo.');
    btn.disabled = false;
    btn.textContent = 'Entrar';
  } finally {
    if (!btn.disabled) btn.textContent = 'Entrar';
  }
}

// ── Estado global ─────────────────────────────────────────────────────────────
const state = {
  sucursales: null,   // ventas del mes por sucursal (Inicio)
  stock:      null,   // existencias por sucursal (Inventario)
  medicos:    null,   // duplicados de médicos
};

// ── Formatters ────────────────────────────────────────────────────────────────
const MXN = new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN', maximumFractionDigits: 0 });
const NUM  = new Intl.NumberFormat('es-MX');

function fmtMXN(v)  { return MXN.format(v || 0); }
function fmtNum(v)  { return NUM.format(v || 0); }
// Formato corto para espacios reducidos: $1.2M, $450K, $980
function fmtMXN_corto(v) {
  const n = v || 0;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`;
  return MXN.format(n);
}
function fmtDias(n) {
  if (n == null) return '';
  if (n <= 0)  return '<span class="tag tag-danger">Caducado</span>';
  if (n <= 30) return `<span class="tag tag-danger">${n} días</span>`;
  if (n <= 60) return `<span class="tag tag-warn">${n} días</span>`;
  return `<span class="tag tag-success">${n} días</span>`;
}

// ── Navegación ────────────────────────────────────────────────────────────────
const SUBTITLES = {
  inicio:     'Inicio',
  dashboards: 'Dashboards',
  chat:       'Asistente IA',
  medicos:    'Médicos',
  inventario: 'Inventario',
};

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
  document.getElementById(`view-${name}`)?.classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === name);
  });
  document.getElementById('header-subtitle').textContent = SUBTITLES[name] || '';
}

// ── Render: Inicio — ventas del mes por sucursal ──────────────────────────────
function renderInicio() {
  if (!state.sucursales) return;
  const el = document.getElementById('lista-inicio-sucursales');
  if (!el) return;

  el.innerHTML = state.sucursales.sucursales.map(s => {
    const up  = s.variacion_pct !== null && s.variacion_pct >= 0;
    const pct = s.variacion_pct !== null ? Math.abs(s.variacion_pct) : null;

    return `
      <div class="card-item sucursal-card" data-cve="${s.cve_sucursal}" data-modo="inicio">
        <div class="card-row sucursal-header">
          <span class="card-title">${s.sucursal}</span>
          <div style="display:flex;align-items:center;gap:8px">
            <button class="ia-flash-btn" onclick="event.stopPropagation();iaFlashSucursal(${s.cve_sucursal},'${s.sucursal.replace(/'/g,"\\'")}',this)" aria-label="Resumen IA">✦ IA</button>
            <span class="chevron">›</span>
          </div>
        </div>
        <div class="card-row card-row-wrap" style="margin-top:4px;align-items:flex-end;gap:6px">
          <span class="card-monto">${fmtMXN(s.ventas_mes)}</span>
          ${pct !== null
            ? `<span class="kpi-delta ${up ? 'up' : 'down'}">${up ? '▲' : '▼'} ${pct}% vs mes ant.</span>`
            : '<span class="card-sub">Sin comparativo</span>'
          }
        </div>
        <span class="card-sub">${fmtNum(s.facturas_mes)} facturas este mes</span>
        <div class="sucursal-detalle" id="detalle-inicio-${s.cve_sucursal}"></div>
      </div>
    `;
  }).join('');

  el.querySelectorAll('.sucursal-card').forEach(card => {
    card.addEventListener('click', () => toggleDetalle(card));
  });
}

// ── Render: Inventario — sucursales expandibles con detalle de stock ──────────
function renderInventario() {
  if (!state.stock) return;
  renderSucursalCards('lista-inv-sucursales', state.stock.sucursales, 'inventario');
}

// ── Render: Médicos duplicados ────────────────────────────────────────────────
function renderMedicos() {
  if (!state.medicos) return;
  const el = document.getElementById('lista-medicos');
  if (!el) return;

  const { por_cedula, por_nombre } = state.medicos;
  let html = `
    <div id="ia-medicos-banner" class="ia-medicos-banner">
      <div class="ia-medicos-loading">
        <div class="spinner spinner-sm"></div>
        <span>Analizando catálogo de médicos...</span>
      </div>
    </div>`;
  // Cargar insight IA en segundo plano (no bloquea el render)
  setTimeout(iaFlashMedicos, 50);

  // Duplicados confirmados por cédula
  html += `<div class="section-title">Duplicados confirmados · misma cédula · ${por_cedula.length}</div>`;
  if (por_cedula.length) {
    html += por_cedula.map(grupo => `
      <div class="card-item">
        <div class="card-row">
          <span class="tag tag-danger">Cédula ${grupo[0].cedula}</span>
          <span class="card-sub">${grupo.length} registros</span>
        </div>
        ${grupo.map(m => `
          <div class="detalle-row" style="margin-top:4px">
            <div style="flex:1;min-width:0">
              <span class="detalle-nombre">${m.nombre}</span>
              <span class="detalle-lab">Vendedor: ${m.vendedor || '—'} <span style="color:var(--text-dim)">(#${m.cve_vendedor})</span></span>
            </div>
            <span class="card-sub">#${m.cve_medico}</span>
          </div>
        `).join('')}
      </div>
    `).join('');
  } else {
    html += '<div class="empty-state" style="padding:12px">Sin duplicados por cédula</div>';
  }

  // Posibles duplicados por nombre
  html += `<div class="section-title mt">Posibles duplicados · mismo nombre · ${por_nombre.length}</div>`;
  if (por_nombre.length) {
    html += por_nombre.map(grupo => `
      <div class="card-item">
        <div class="card-row">
          <span class="card-title" style="font-size:14px">${grupo[0].nombre}</span>
          <span class="tag tag-warn">${grupo.length} registros</span>
        </div>
        ${grupo.map(m => `
          <div class="detalle-row" style="margin-top:4px">
            <div style="flex:1;min-width:0">
              <span class="detalle-lab">Vendedor: ${m.vendedor || '—'} <span style="color:var(--text-dim)">(#${m.cve_vendedor})</span></span>
              ${m.cedula ? `<span class="detalle-lab">Cédula: <strong style="color:var(--blue-mid)">${m.cedula}</strong></span>` : '<span class="detalle-lab" style="color:var(--text-dim)">Sin cédula</span>'}
            </div>
            <span class="card-sub">#${m.cve_medico}</span>
          </div>
        `).join('')}
      </div>
    `).join('');
  } else {
    html += '<div class="empty-state" style="padding:12px">Sin duplicados por nombre</div>';
  }

  el.innerHTML = html;
}

// ── Cards de sucursal (Inicio = resumen con métricas, Inventario = stock) ─────
function renderSucursalCards(containerId, sucursales, modo) {
  const el = document.getElementById(containerId);
  if (!el) return;

  el.innerHTML = sucursales.map(s => {
    const lotesCad  = s.lotes_por_caducar || 0;
    const sinStock  = s.sin_stock || 0;
    const claseCad  = lotesCad > 10 ? 'tag-danger' : lotesCad > 0 ? 'tag-warn' : 'tag-success';

    return `
      <div class="card-item sucursal-card" data-cve="${s.cve_sucursal}" data-modo="${modo}">
        <div class="card-row sucursal-header">
          <span class="card-title">${s.sucursal}</span>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="tag ${claseCad}">
              ${lotesCad > 0 ? `${fmtNum(lotesCad)} lotes por caducar` : 'Sin caducidades'}
            </span>
            <span class="chevron">›</span>
          </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="card-sub">${fmtNum(sinStock)} productos sin existencia</span>
          <button class="ia-flash-btn" onclick="event.stopPropagation();iaFlashInventario(${s.cve_sucursal},'${s.sucursal.replace(/'/g,"\\'")}',this)" aria-label="Alerta IA">✦ IA</button>
        </div>
        <div class="sucursal-detalle" id="detalle-${modo}-${s.cve_sucursal}"></div>
      </div>
    `;
  }).join('');

  el.querySelectorAll('.sucursal-card').forEach(card => {
    card.addEventListener('click', () => toggleDetalle(card));
  });
}

// ── Expandir / colapsar card de sucursal ──────────────────────────────────────
async function toggleDetalle(card) {
  const cve    = card.dataset.cve;
  const modo   = card.dataset.modo;
  const detId  = `detalle-${modo}-${cve}`;
  const detalle = document.getElementById(detId);
  const chevron = card.querySelector('.chevron');
  const abierto = card.classList.contains('open');

  // Cerrar todos en el mismo contenedor
  card.closest('.card-list').querySelectorAll('.sucursal-card.open').forEach(c => {
    c.classList.remove('open');
    c.querySelector('.chevron').textContent = '›';
    const d = c.querySelector('.sucursal-detalle');
    if (d) d.innerHTML = '';
  });

  if (abierto) return;

  card.classList.add('open');
  chevron.textContent = '⌄';
  detalle.innerHTML = '<div class="detalle-loading"><div class="spinner spinner-sm"></div></div>';

  try {
    if (modo === 'inicio') {
      await cargarResumenSucursal(cve, detalle);
    } else {
      await cargarStockSucursal(cve, detalle);
    }
  } catch {
    detalle.innerHTML = '<div class="detalle-ok" style="color:var(--danger)">Error al cargar</div>';
  }
}

// ── Detalle Inicio: ventas + top productos + pedidos pendientes ───────────────
async function cargarResumenSucursal(cve, detalle) {
  const data = await authFetch(`/api/sucursal/${cve}/resumen`).then(r => r.json());

  const ventasAyer  = data.ventas_ayer  || {};
  const topProd     = data.top_productos || [];
  const pedidosPend = data.pedidos_pendientes || 0;

  // Fechas legibles desde el servidor (respeta TEST_DATE)
  const hoyDate  = data.fecha_hoy ? new Date(data.fecha_hoy + 'T12:00:00') : new Date();
  const ayerDate = new Date(hoyDate); ayerDate.setDate(ayerDate.getDate() - 1);
  const fmtCorto = d => d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' });
  const fmtMes   = d => d.toLocaleDateString('es-MX', { month: 'long', year: 'numeric' });

  const fmtCorto2 = d => d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short' });
  let html = `
    <div class="resumen-grid">
      <div class="resumen-kpi">
        <span class="resumen-val">${fmtMXN_corto(ventasAyer.importe_total)}</span>
        <span class="resumen-lbl">Ventas · ${fmtCorto2(ayerDate)}</span>
      </div>
      <div class="resumen-kpi">
        <span class="resumen-val">${fmtNum(ventasAyer.total_facturas || 0)}</span>
        <span class="resumen-lbl">Facturas · ${fmtCorto2(ayerDate)}</span>
      </div>
      <div class="resumen-kpi">
        <span class="resumen-val">${fmtNum(pedidosPend)}</span>
        <span class="resumen-lbl">Pedidos activos</span>
      </div>
    </div>
  `;

  if (topProd.length) {
    html += `<div class="detalle-seccion" style="margin-top:10px">Top productos · ${fmtMes(hoyDate)}</div>`;
    html += topProd.map((p, i) => `
      <div class="detalle-row">
        <div style="flex:1;min-width:0;display:flex;align-items:center;gap:4px">
          <span class="rank-number ${i === 0 ? 'top' : ''}" style="font-size:13px;min-width:22px">#${i+1}</span>
          <span class="detalle-nombre" style="flex:1;min-width:0">${p.producto || '—'}</span>
        </div>
        <span class="detalle-importe" style="flex-shrink:0;margin-left:8px">${fmtMXN(p.importe)}</span>
      </div>
    `).join('');
  }

  detalle.innerHTML = html;
}

// ── Detalle Inventario: productos sin stock + caducidades ─────────────────────
async function cargarStockSucursal(cve, detalle) {
  const data = await authFetch(`/api/stock/${cve}`).then(r => r.json());
  let html = '';

  // ── Caducidades próximas (primero — más urgente) ──────────────
  if (data.caducidades?.length) {
    html += `<div class="detalle-seccion">Por caducar · próximos 90 días</div>`;
    html += data.caducidades.map(l => `
      <div class="detalle-row">
        <div style="flex:1;min-width:0">
          <span class="detalle-nombre">${l.producto || '—'}</span>
          <span class="detalle-lab">Lote ${l.lote} · ${fmtNum(l.existencia_lote)} pzas</span>
        </div>
        ${fmtDias(l.dias_para_caducar)}
      </div>
    `).join('');
  } else {
    html += `<div class="detalle-ok">✓ Sin lotes por caducar en 90 días</div>`;
  }

  // ── Top productos con más existencia ─────────────────────────
  if (data.top_stock?.length) {
    const maxExist = data.top_stock[0].existencia_total || 1;
    html += `<div class="detalle-seccion" style="margin-top:12px">Mayor existencia</div>`;
    html += data.top_stock.map(p => {
      const pct = Math.round(p.existencia_total / maxExist * 100);
      return `
        <div class="detalle-row" style="flex-direction:column;align-items:stretch;gap:3px">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span class="detalle-nombre">${p.producto}</span>
            <span class="detalle-importe">${fmtNum(p.existencia_total)} pzas</span>
          </div>
          <div class="stock-bar-track">
            <div class="stock-bar-fill bar-ok" style="width:${pct}%"></div>
          </div>
        </div>
      `;
    }).join('');
  }

  // ── Sin existencia ────────────────────────────────────────────
  if (data.sin_stock?.length) {
    html += `<div class="detalle-seccion" style="margin-top:12px">Sin existencia · ${data.sin_stock.length}</div>`;
    html += data.sin_stock.map(p => `
      <div class="detalle-row">
        <div style="flex:1;min-width:0">
          <span class="detalle-nombre">${p.producto || '—'}</span>
          <span class="detalle-lab">${p.laboratorio || ''}</span>
        </div>
      </div>
    `).join('');
  }

  detalle.innerHTML = html || '<div class="detalle-ok">Sin datos</div>';
}

// ── IA Flash — panel compartido ──────────────────────────────────────────────
(function _initIAPanel() {
  function cerrarIAPanel() {
    document.getElementById('ia-panel').classList.add('hidden');
    document.getElementById('ia-overlay').classList.add('hidden');
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('ia-panel-close').addEventListener('click', cerrarIAPanel);
    document.getElementById('ia-overlay').addEventListener('click', cerrarIAPanel);
  });
})();

function _abrirIAPanel(titulo) {
  document.getElementById('ia-panel-titulo').textContent = titulo;
  document.getElementById('ia-panel-loading').classList.remove('hidden');
  const panelTexto = document.getElementById('ia-panel-texto');
  panelTexto.classList.add('hidden');
  panelTexto.textContent = '';
  const btnAnterior = document.getElementById('ia-panel-actualizar');
  if (btnAnterior) btnAnterior.remove();
  document.getElementById('ia-panel').classList.remove('hidden');
  document.getElementById('ia-overlay').classList.remove('hidden');
}

function _mostrarIATexto(texto, opcionActualizar) {
  document.getElementById('ia-panel-loading').classList.add('hidden');
  const el = document.getElementById('ia-panel-texto');
  el.textContent = texto;
  el.classList.remove('hidden');

  // Botón "Actualizar" — solo aparece cuando hay cache (segunda visita)
  const existente = document.getElementById('ia-panel-actualizar');
  if (existente) existente.remove();

  if (opcionActualizar) {
    const btn = document.createElement('button');
    btn.id        = 'ia-panel-actualizar';
    btn.className = 'ia-panel-actualizar-btn';
    btn.textContent = '🔄 Actualizar (consume 1 consulta)';
    btn.onclick   = opcionActualizar;
    el.after(btn);
  }
}

// ── Cache diario para IA Flash ────────────────────────────────────────────────
// Cada resultado se guarda en localStorage con clave tipo_id_fecha.
// Al día siguiente la clave cambia → se regenera automáticamente.
function _iaHoy() {
  return new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"
}
function _iaCacheKey(tipo, id) {
  return `ia_flash_${tipo}_${id}_${_iaHoy()}`;
}
function _iaCacheGet(tipo, id) {
  return localStorage.getItem(_iaCacheKey(tipo, id));
}
function _iaCacheSet(tipo, id, texto) {
  // Limpiar claves viejas del mismo tipo/id antes de guardar
  for (const k of Object.keys(localStorage)) {
    if (k.startsWith(`ia_flash_${tipo}_${id}_`) && k !== _iaCacheKey(tipo, id)) {
      localStorage.removeItem(k);
    }
  }
  localStorage.setItem(_iaCacheKey(tipo, id), texto);
}

async function _iaFlashPanel(tipo, endpoint, titulo, cve, nombre, btn) {
  btn.classList.add('loading');
  _abrirIAPanel(`${titulo} · ${nombre}`);

  const cached = _iaCacheGet(tipo, cve);
  if (cached) {
    _mostrarIATexto(cached, async () => {
      const b = document.getElementById('ia-panel-actualizar');
      if (b) b.disabled = true;
      try {
        const data = await authFetch(`/api/ia/${endpoint}/${cve}?regenerar=1`).then(r => r.json());
        const nuevo = data.texto || 'Sin respuesta.';
        _iaCacheSet(tipo, cve, nuevo);
        _mostrarIATexto(nuevo);
      } catch {
        _mostrarIATexto('Error al actualizar. Intenta de nuevo.');
      }
    });
    btn.classList.remove('loading');
    return;
  }

  try {
    const data = await authFetch(`/api/ia/${endpoint}/${cve}`).then(r => r.json());
    const texto = data.texto || 'Sin respuesta.';
    _iaCacheSet(tipo, cve, texto);
    _mostrarIATexto(texto);
  } catch {
    _mostrarIATexto('Error al obtener el resumen. Intenta de nuevo.');
  } finally {
    btn.classList.remove('loading');
  }
}

function iaFlashSucursal(cve, nombre, btn)   { _iaFlashPanel('suc', 'sucursal',   'Resumen',    cve, nombre, btn); }
function iaFlashInventario(cve, nombre, btn) { _iaFlashPanel('inv', 'inventario', 'Inventario', cve, nombre, btn); }

async function iaFlashMedicos() {
  const banner = document.getElementById('ia-medicos-banner');
  if (!banner) return;

  const cached = _iaCacheGet('med', 'global');
  if (cached) {
    banner.innerHTML = `<span class="ia-medicos-icono">✦</span><span class="ia-medicos-texto">${cached}</span>`;
    return;
  }

  try {
    const data = await authFetch('/api/ia/medicos').then(r => r.json());
    if (!data.texto) { banner.remove(); return; }
    _iaCacheSet('med', 'global', data.texto);
    banner.innerHTML = `
      <span class="ia-medicos-icono">✦</span>
      <span class="ia-medicos-texto">${data.texto}</span>`;
  } catch {
    banner.remove();
  }
}

// ── Carga inicial ─────────────────────────────────────────────────────────────
async function cargarDatos() {
  if (!getToken()) { mostrarLogin(); return; }

  document.getElementById('loading-state').style.display = 'flex';
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));

  try {
    const [resSucursales, resStock, resMedicos] = await Promise.all([
      authFetch('/api/sucursales').then(r => r.json()),
      authFetch('/api/stock').then(r => r.json()),
      authFetch('/api/medicos/duplicados').then(r => r.json()),
    ]);

    state.sucursales = resSucursales;
    state.stock      = resStock;
    state.medicos    = resMedicos;

    renderInicio();
    renderInventario();
    renderMedicos();

  } catch (err) {
    console.error('Error:', err);
  } finally {
    document.getElementById('loading-state').style.display = 'none';
    showView('inicio');
  }
}

// ── Chat — estado ─────────────────────────────────────────────────────────────
const chat = {
  conversacionId: null,   // ID de la conversación activa
  enviando: false,        // bloquea doble envío
};

// ── Chat — render de burbuja ──────────────────────────────────────────────────
function renderMarkdown(text) {
  // Separar bloques de tabla del resto del texto
  const parts = text.split(/((?:^\|.+\|\n?)+)/gm);
  let html = '';

  for (const part of parts) {
    if (part.match(/^\|.+\|/m)) {
      // Es una tabla Markdown — convertir a HTML
      const lines = part.trim().split('\n').filter(l => l.trim());
      const isHeader = lines.length > 1 && lines[1].match(/^\|[\s\-:|]+\|/);
      let table = '<div class="chat-table-wrap"><table class="chat-table">';

      lines.forEach((line, i) => {
        if (i === 1 && isHeader) return; // saltar línea separadora
        const cells = line.split('|').filter((_, ci) => ci > 0 && ci < line.split('|').length - 1);
        const tag   = (i === 0 && isHeader) ? 'th' : 'td';
        const row   = cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('');
        table += `<tr>${row}</tr>`;
      });

      table += '</table></div>';
      // Botón para expandir la tabla
      const id = 'tbl-' + Math.random().toString(36).slice(2, 7);
      html += `<div class="chat-table-outer" id="${id}"><div class="chat-table-toolbar"><button class="chat-table-expand" onclick="expandirTabla('${id}')" aria-label="Expandir tabla">⤢</button></div>${table}</div>`;
    } else {
      // Texto normal — Markdown básico
      html += part
        .replace(/^###\s+(.+)$/gm, '<strong>$1</strong>')
        .replace(/^##\s+(.+)$/gm,  '<strong>$1</strong>')
        .replace(/^#\s+(.+)$/gm,   '<strong>$1</strong>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
        .replace(/\n/g, '<br>');
    }
  }
  return html;
}

function expandirTabla(id) {
  const src = document.getElementById(id)?.querySelector('.chat-table-wrap');
  if (!src) return;
  document.getElementById('tabla-modal-body').innerHTML = src.outerHTML;
  document.getElementById('tabla-modal-overlay').classList.remove('hidden');
}

(function _initTablaModal() {
  function cerrarTablaModal() {
    document.getElementById('tabla-modal-overlay').classList.add('hidden');
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('tabla-modal-close').addEventListener('click', cerrarTablaModal);
    document.getElementById('tabla-modal-overlay').addEventListener('click', e => {
      if (e.target.id === 'tabla-modal-overlay') cerrarTablaModal();
    });
  });
})();

function appendBubble(text, role) {
  const messages = document.getElementById('chat-messages');
  const wrap     = document.createElement('div');
  wrap.className = `chat-bubble chat-bubble-${role === 'user' ? 'user' : 'bot'}`;

  const content = document.createElement('div');
  content.innerHTML = renderMarkdown(text);
  wrap.appendChild(content);

  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const messages = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-typing';
  div.id = 'typing-indicator';
  div.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function hideTyping() { document.getElementById('typing-indicator')?.remove(); }

// ── Chat — enviar mensaje ─────────────────────────────────────────────────────
async function enviarMensaje() {
  if (chat.enviando) return;
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  chat.enviando = true;
  appendBubble(text, 'user');
  showTyping();

  try {
    const res  = await authFetch('/api/chat/mensaje', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ mensaje: text, conversacion_id: chat.conversacionId }),
    });
    const data = await res.json();
    hideTyping();

    if (res.status === 429) {
      appendBubble(data.detail || 'Límite de consultas alcanzado.', 'bot');
      return;
    }

    const reply = data.respuesta || data.detail || 'Sin respuesta.';
    appendBubble(reply, 'bot');

    // Si era nueva conversación, guardar el ID y actualizar título
    if (!chat.conversacionId && data.conversacion_id) {
      chat.conversacionId = data.conversacion_id;
      document.getElementById('chat-titulo-actual').textContent =
        text.length > 40 ? text.slice(0, 40) + '…' : text;
    }
  } catch {
    hideTyping();
    appendBubble('Error al conectar con el asistente. Intenta de nuevo.', 'bot');
  } finally {
    chat.enviando = false;
  }
}

// ── Chat — nueva conversación ─────────────────────────────────────────────────
function nuevaConversacion() {
  chat.conversacionId = null;
  document.getElementById('chat-titulo-actual').textContent = 'Nueva conversación';
  document.getElementById('chat-messages').innerHTML = `
    <div class="chat-bubble chat-bubble-bot">
      <p>Hola, soy tu asistente analítico.</p>
      <p>Pregúntame lo que necesites sobre el negocio:</p>
      <ul>
        <li>¿Cuánto vendimos este mes?</li>
        <li>¿Qué sucursal tiene más pedidos pendientes?</li>
        <li>¿Cuál es el producto más vendido?</li>
      </ul>
    </div>`;
  cerrarHistorial();
  document.getElementById('chat-input').focus();
}

// ── Chat — historial de conversaciones ────────────────────────────────────────
function abrirHistorial() {
  cargarConversaciones();
  document.getElementById('historial-drawer').classList.remove('hidden');
  document.getElementById('historial-overlay').classList.remove('hidden');
}

function cerrarHistorial() {
  document.getElementById('historial-drawer').classList.add('hidden');
  document.getElementById('historial-overlay').classList.add('hidden');
}

async function cargarConversaciones() {
  const lista = document.getElementById('historial-lista');
  try {
    const res  = await authFetch('/api/chat/conversaciones');
    const data = await res.json();
    const convs = data.conversaciones || [];

    if (!convs.length) {
      lista.innerHTML = '<div class="historial-empty">Sin conversaciones anteriores</div>';
      return;
    }

    // Agrupar por fecha
    const hoy  = new Date().toDateString();
    const ayer = new Date(Date.now() - 86400000).toDateString();
    const grupos = { 'Hoy': [], 'Ayer': [], 'Anteriores': [] };

    convs.forEach(c => {
      const f = new Date(c.creado_en).toDateString();
      if (f === hoy)       grupos['Hoy'].push(c);
      else if (f === ayer) grupos['Ayer'].push(c);
      else                 grupos['Anteriores'].push(c);
    });

    lista.innerHTML = Object.entries(grupos)
      .filter(([, items]) => items.length)
      .map(([grupo, items]) => `
        <div class="historial-grupo-label">${grupo}</div>
        ${items.map(c => `
          <div class="historial-item ${c.id === chat.conversacionId ? 'active' : ''}"
               data-id="${c.id}">
            <div class="historial-item-texto">
              <span class="historial-item-titulo">${c.titulo}</span>
              ${c.ultimo_msg
                ? `<span class="historial-item-sub">${c.ultimo_msg}</span>`
                : ''}
            </div>
            <button class="historial-item-del" data-id="${c.id}" aria-label="Eliminar">×</button>
          </div>
        `).join('')}
      `).join('');

    // Click en item → cargar conversación
    lista.querySelectorAll('.historial-item').forEach(el => {
      el.addEventListener('click', () => cargarConversacion(parseInt(el.dataset.id)));
    });
    // Click en eliminar
    lista.querySelectorAll('.historial-item-del').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        eliminarConversacion(parseInt(btn.dataset.id));
      });
    });

  } catch {
    lista.innerHTML = '<div class="historial-empty">Error al cargar el historial</div>';
  }
}

async function cargarConversacion(id) {
  try {
    const res  = await authFetch(`/api/chat/conversaciones/${id}`);
    const data = await res.json();

    chat.conversacionId = id;
    document.getElementById('chat-titulo-actual').textContent = data.conversacion.titulo;

    const msgs = document.getElementById('chat-messages');
    msgs.innerHTML = '';
    (data.mensajes || []).forEach(m => appendBubble(m.contenido, m.rol === 'user' ? 'user' : 'bot'));
    msgs.scrollTop = msgs.scrollHeight;

    cerrarHistorial();
  } catch {
    cerrarHistorial();
  }
}

async function eliminarConversacion(id) {
  try {
    await authFetch(`/api/chat/conversaciones/${id}`, { method: 'DELETE' });
    if (chat.conversacionId === id) nuevaConversacion();
    else cargarConversaciones();
  } catch {}
}

// ── Chat — micrófono ──────────────────────────────────────────────────────────
let _recognition = null;

function toggleMicrofono() {
  const btn = document.getElementById('mic-btn');

  if (_recognition) {
    _recognition.stop();
    return;
  }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    alert('Tu navegador no soporta el reconocimiento de voz.\nUsa Chrome o Safari actualizado.');
    return;
  }

  _recognition = new SR();
  _recognition.lang           = 'es-MX';
  _recognition.continuous     = false;
  _recognition.interimResults = false;

  _recognition.onstart = () => btn.classList.add('recording');

  _recognition.onresult = (e) => {
    const texto = e.results[0][0].transcript;
    document.getElementById('chat-input').value = texto;
  };

  _recognition.onend = () => {
    btn.classList.remove('recording');
    _recognition = null;
  };

  _recognition.onerror = () => {
    btn.classList.remove('recording');
    _recognition = null;
  };

  _recognition.start();
}

// ── Init ──────────────────────────────────────────────────────────────────────
// ── Banner de instalación PWA ─────────────────────────────────────────────────
(function () {
  const DISMISSED_KEY = 'suite_install_dismissed_v3';
  let deferredPrompt = null;

  const esStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  const esIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);

  function cerrarBanner(guardar = false) {
    document.getElementById('install-banner').classList.add('hidden');
    if (guardar) localStorage.setItem(DISMISSED_KEY, '1');
  }

  function mostrarBanner() {
    if (localStorage.getItem(DISMISSED_KEY)) return;
    if (esStandalone()) return;

    const banner = document.getElementById('install-banner');
    const sub    = document.getElementById('install-banner-sub');
    const btn    = document.getElementById('install-banner-btn');

    if (deferredPrompt) {
      // Android Chrome — tenemos el prompt nativo
      sub.textContent = 'Instala la app para acceso rápido';
      btn.textContent = 'Instalar';
      btn.onclick = async () => {
        banner.classList.add('hidden');
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        deferredPrompt = null;
        if (outcome === 'accepted') cerrarBanner(true);
      };
    } else if (esIOS()) {
      // iOS Safari — instrucción manual
      sub.textContent = 'Toca Compartir → "Añadir a inicio"';
      btn.textContent = 'Entendido';
      btn.onclick = () => cerrarBanner(true);
    } else {
      // Android / otro — sin prompt nativo, instrucción manual
      sub.textContent = 'Menú (⋮) → "Añadir a pantalla de inicio"';
      btn.textContent = 'Entendido';
      btn.onclick = () => cerrarBanner(true);
    }

    banner.classList.remove('hidden');
    document.getElementById('install-banner-close').onclick = () => cerrarBanner(true);
  }

  // Android Chrome — mostrar banner EN CUANTO llega el prompt nativo
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    mostrarBanner();
  });

  // Fallback: si tras 4s no llegó el prompt nativo (iOS u otros), mostrar manual
  window.addEventListener('load', () => {
    setTimeout(() => {
      if (!deferredPrompt) mostrarBanner();
    }, 4000);
  });
})();

// ── Actualización PWA ─────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data?.type === 'SW_UPDATED') {
      const banner = document.getElementById('update-banner');
      if (banner) {
        banner.classList.remove('hidden');
        document.getElementById('update-banner-btn').onclick = () => window.location.reload();
      }
    }
  });
}

// ── Tema (dark / light / auto) ────────────────────────────────────────────────
const TEMA_KEY = 'suite_tema';

function aplicarTema(tema) {
  document.documentElement.classList.remove('dark', 'light');
  if (tema === 'dark')  document.documentElement.classList.add('dark');
  if (tema === 'light') document.documentElement.classList.add('light');
}

function esTemaOscuro() {
  const guardado = localStorage.getItem(TEMA_KEY);
  if (guardado === 'dark')  return true;
  if (guardado === 'light') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

(function () {
  const t = localStorage.getItem(TEMA_KEY);
  if (t) aplicarTema(t);
})();

// ── Perfil / Cerrar sesión ────────────────────────────────────────────────────
function renderAvatarHeader(nombre, fotoPerfil) {
  const av = document.getElementById('app-avatar');
  if (fotoPerfil) {
    av.innerHTML = `<img src="${fotoPerfil}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
  } else {
    av.textContent = (nombre || '?').charAt(0).toUpperCase();
  }
  const primerNombre = (nombre || '').split(' ')[0];
  const el = document.getElementById('header-nombre');
  if (el) el.textContent = `Hola, ${primerNombre}`;
}

function abrirPerfil() {
  const u = state.usuario || {};
  // Nombre y rol
  document.getElementById('perfil-nombre').textContent = u.nombre || '—';
  const roles = { admin: 'Administrador', supervisor: 'Supervisor', usuario: 'Usuario' };
  document.getElementById('perfil-rol').textContent = roles[u.rol] || u.rol || '—';
  // Avatar
  const avatarEl = document.getElementById('perfil-inicial');
  if (u.foto_perfil) {
    avatarEl.innerHTML = `<img src="${u.foto_perfil}">`;
  } else {
    avatarEl.textContent = (u.nombre || '?').charAt(0).toUpperCase();
    avatarEl.innerHTML = avatarEl.textContent; // clear img if any
  }
  // Toggle tema
  document.getElementById('perfil-tema-toggle').checked = esTemaOscuro();
  // Mostrar vista principal
  document.getElementById('perfil-vista-main').classList.remove('hidden');
  document.getElementById('perfil-vista-editar').classList.add('hidden');
  document.getElementById('perfil-sheet').classList.remove('hidden');
  document.getElementById('perfil-overlay').classList.remove('hidden');
}

function cerrarPerfil() {
  document.getElementById('perfil-sheet').classList.add('hidden');
  document.getElementById('perfil-overlay').classList.add('hidden');
}

function cerrarSesion() {
  clearToken();
  cerrarPerfil();
  state.usuario = null;
  document.getElementById('app-avatar').textContent = '?';
  document.getElementById('login-screen').classList.remove('hidden');
}

async function guardarNombre() {
  const input  = document.getElementById('perfil-nombre-input');
  const errorEl = document.getElementById('perfil-editar-error');
  const nombre  = input.value.trim();
  if (!nombre) { errorEl.textContent = 'El nombre no puede estar vacío.'; errorEl.classList.remove('hidden'); return; }
  errorEl.classList.add('hidden');

  const btn = document.getElementById('perfil-guardar-btn');
  btn.disabled = true; btn.textContent = 'Guardando...';
  try {
    const res  = await authFetch('/auth/perfil', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nombre }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error');
    state.usuario.nombre = data.nombre;
    renderAvatarHeader(data.nombre, state.usuario.foto_perfil);
    document.getElementById('perfil-nombre').textContent = data.nombre;
    document.getElementById('perfil-vista-editar').classList.add('hidden');
    document.getElementById('perfil-vista-main').classList.remove('hidden');
  } catch (e) {
    errorEl.textContent = e.message; errorEl.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Guardar';
  }
}

async function subirFoto(file) {
  const reader = new FileReader();
  reader.onload = async (e) => {
    // Redimensionar a 200x200 antes de subir
    const img = new Image();
    img.onload = async () => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 200;
      const ctx = canvas.getContext('2d');
      const min = Math.min(img.width, img.height);
      const sx  = (img.width  - min) / 2;
      const sy  = (img.height - min) / 2;
      ctx.drawImage(img, sx, sy, min, min, 0, 0, 200, 200);
      const dataUrl = canvas.toDataURL('image/jpeg', 0.8);

      const res  = await authFetch('/auth/perfil', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ foto_perfil: dataUrl }) });
      const data = await res.json();
      if (res.ok) {
        state.usuario.foto_perfil = data.foto_perfil;
        renderAvatarHeader(state.usuario.nombre, data.foto_perfil);
        // Actualizar avatar en el sheet
        const avatarEl = document.getElementById('perfil-inicial');
        avatarEl.innerHTML = `<img src="${data.foto_perfil}">`;
      }
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

document.addEventListener('DOMContentLoaded', async () => {
  mostrarAvisoDispositivo();

  // Navegación
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => showView(btn.dataset.view));
  });

  // Perfil — sheet principal
  document.getElementById('app-avatar').addEventListener('click', abrirPerfil);
  document.getElementById('perfil-overlay').addEventListener('click', cerrarPerfil);
  document.getElementById('perfil-cerrar-btn').addEventListener('click', cerrarPerfil);
  document.getElementById('perfil-logout-btn').addEventListener('click', cerrarSesion);

  // Perfil — foto
  document.getElementById('perfil-avatar-btn').addEventListener('click', () =>
    document.getElementById('perfil-foto-input').click()
  );
  document.getElementById('perfil-foto-input').addEventListener('change', (e) => {
    if (e.target.files[0]) subirFoto(e.target.files[0]);
  });

  // Perfil — editar nombre
  document.getElementById('perfil-editar-btn').addEventListener('click', () => {
    document.getElementById('perfil-nombre-input').value = state.usuario?.nombre || '';
    document.getElementById('perfil-editar-error').classList.add('hidden');
    document.getElementById('perfil-vista-main').classList.add('hidden');
    document.getElementById('perfil-vista-editar').classList.remove('hidden');
  });
  document.getElementById('perfil-editar-back').addEventListener('click', () => {
    document.getElementById('perfil-vista-editar').classList.add('hidden');
    document.getElementById('perfil-vista-main').classList.remove('hidden');
  });
  document.getElementById('perfil-guardar-btn').addEventListener('click', guardarNombre);
  document.getElementById('perfil-nombre-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') guardarNombre();
  });

  // Perfil — toggle tema
  document.getElementById('perfil-tema-toggle').addEventListener('change', (e) => {
    const tema = e.target.checked ? 'dark' : 'light';
    localStorage.setItem(TEMA_KEY, tema);
    aplicarTema(tema);
  });

  // Chat — envío
  document.getElementById('chat-send')?.addEventListener('click', enviarMensaje);
  document.getElementById('chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviarMensaje(); }
  });

  // Chat — historial y nueva conversación
  document.getElementById('btn-historial')?.addEventListener('click', abrirHistorial);
  document.getElementById('btn-nueva-conv')?.addEventListener('click', nuevaConversacion);
  document.getElementById('historial-nueva-btn')?.addEventListener('click', nuevaConversacion);
  document.getElementById('historial-close')?.addEventListener('click', cerrarHistorial);
  document.getElementById('historial-overlay')?.addEventListener('click', cerrarHistorial);

  // Chat — micrófono
  document.getElementById('mic-btn')?.addEventListener('click', toggleMicrofono);

  // Login
  document.getElementById('login-btn').addEventListener('click', iniciarSesion);
  document.getElementById('login-password').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') iniciarSesion();
  });

  // Si ya hay token, verificar y redirigir según dispositivo
  if (getToken()) {
    try {
      const res  = await fetch('/auth/me', { headers: { Authorization: `Bearer ${getToken()}` } });
      const data = await res.json();
      if (res.ok) {
        state.usuario = { nombre: data.nombre, rol: data.rol, foto_perfil: data.foto_perfil || null };
        renderAvatarHeader(data.nombre, data.foto_perfil);
        ocultarLogin();
        cargarDatos();
      } else {
        clearToken();
      }
    } catch {
      clearToken();
    }
  }
});
