/* ════════════════════════════════════════════════════════════
   ERP Demo — Frontend (Odoo Enterprise-like)
   ════════════════════════════════════════════════════════════ */

/* ── Utilidades ─────────────────────────────────────────────── */

function fmtCurrency(val) {
  if (val == null || val === '') return '—';
  try {
    return new Intl.NumberFormat('es-MX', {
      style: 'currency', currency: 'MXN', maximumFractionDigits: 0,
    }).format(Number(val));
  } catch (_) {
    return '$' + new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Number(val));
  }
}

function fmtNum(val) {
  if (val == null || val === '') return '—';
  try {
    return new Intl.NumberFormat('es-MX').format(Number(val));
  } catch (_) {
    return String(val);
  }
}

function fmtDate(val) {
  if (!val) return '—';
  return String(val).substring(0, 10);
}

function nowTime() {
  return new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/\n/g, '<br>');
}

function initials(name) {
  if (!name) return '?';
  const parts = String(name).split(' ');
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
}

/* ── Sidebar toggle ─────────────────────────────────────────── */
document.getElementById('sidebar-toggle').addEventListener('click', () => {
  document.getElementById('main-layout').classList.toggle('sidebar-collapsed');
});

/* ── Navegación ─────────────────────────────────────────────── */
const SECTION_NAMES = {
  dashboard: 'Dashboard',
  ventas:    'Ventas',
  empleados: 'Empleados',
  inventario:'Inventario',
  finanzas:  'Finanzas',
};

document.querySelectorAll('.o-sidebar-item[data-section]').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    document.querySelectorAll('.o-sidebar-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    const section = item.dataset.section;
    document.getElementById('breadcrumb-title').textContent = SECTION_NAMES[section] || section;
    loadSection(section);
  });
});

function loadSection(section) {
  const loading = document.getElementById('loading');
  const content = document.getElementById('content');
  loading.style.display = 'flex';
  content.classList.add('d-none');

  fetch(`/api/${section}`)
    .then(r => {
      if (!r.ok) return r.text().then(t => { throw new Error(t.substring(0, 120)); });
      return r.json();
    })
    .then(data => {
      loading.style.display = 'none';
      content.classList.remove('d-none');
      ({ dashboard: renderDashboard, ventas: renderVentas,
         empleados: renderEmpleados, inventario: renderInventario,
         finanzas: renderFinanzas })[section]?.(data);
    })
    .catch(err => {
      loading.style.display = 'none';
      content.classList.remove('d-none');
      content.innerHTML = `<div style="margin:20px;padding:16px;background:#fff8f8;border:1px solid #f5c6cb;border-radius:6px;color:#721c24;font-size:.82rem">
        <strong>Error al cargar datos</strong><br>${escapeHtml(err.message)}</div>`;
    });
}

/* ── Helpers HTML ───────────────────────────────────────────── */

function statBtn(label, value, icon) {
  return `
  <button class="o-stat-btn">
    <div class="o-stat-icon"><i class="bi ${icon}"></i></div>
    <div class="o-stat-value">${value}</div>
    <div class="o-stat-label">${label}</div>
  </button>`;
}

function viewToolbar(title, count, icon) {
  return `
  <div class="o-view-toolbar">
    <button class="o-btn-new"><i class="bi bi-plus-lg"></i> Nuevo</button>
    <button class="o-btn-secondary"><i class="bi bi-upload" style="font-size:.75rem"></i> Importar</button>
    <span class="o-record-count">${fmtNum(count)} registros</span>
    <div class="o-view-types" style="margin-left:auto">
      <button class="o-view-type-btn active" title="Lista"><i class="bi bi-list-ul"></i></button>
      <button class="o-view-type-btn" title="Kanban"><i class="bi bi-grid"></i></button>
      <button class="o-view-type-btn" title="Gráfico"><i class="bi bi-bar-chart-line"></i></button>
    </div>
  </div>`;
}

function listHeader(title, count, icon) {
  return `
  <div class="o-list-header-bar">
    <span class="o-list-title">
      <i class="bi ${icon}" style="color:var(--odoo-purple)"></i>
      ${title}
      <span class="o-list-count-badge">${fmtNum(count)}</span>
    </span>
  </div>`;
}

function rankBadge(i) {
  const cls = i === 0 ? 'o-rank-1' : i === 1 ? 'o-rank-2' : i === 2 ? 'o-rank-3' : '';
  return `<span class="o-rank ${cls}">${i + 1}</span>`;
}

/* ════════════════════════════════════════════════════════════
   DASHBOARD
   ════════════════════════════════════════════════════════════ */
let _dashCharts = {};

function renderDashboard(data) {
  const { kpis, ventas_mensuales } = data;
  document.getElementById('content').innerHTML = `

    <div class="o-stats-bar">
      ${statBtn('Ventas Totales',    fmtCurrency(kpis.ventas_totales), 'bi-graph-up-arrow')}
      ${statBtn('Pedidos',           fmtNum(kpis.total_pedidos),       'bi-cart3')}
      ${statBtn('Empleados Activos', fmtNum(kpis.empleados_activos),   'bi-people-fill')}
      ${statBtn('Clientes Activos',  fmtNum(kpis.clientes_activos),    'bi-buildings')}
    </div>

    <!-- Fila 1: Tendencia + Metas -->
    <div class="o-charts-row">
      <div class="o-chart-card o-chart-wide">
        <div class="o-chart-card-title">
          <i class="bi bi-graph-up-arrow"></i>
          Tendencia de Ventas &amp; Proyección
          <span class="o-chart-badge-ia"><i class="bi bi-cpu"></i> IA</span>
        </div>
        <canvas id="chartVentas"></canvas>
      </div>
      <div class="o-chart-card o-chart-narrow">
        <div class="o-chart-card-title">
          <i class="bi bi-bullseye"></i> Cumplimiento de Metas
        </div>
        <canvas id="chartMetas"></canvas>
        <div id="metas-leyenda" style="text-align:center;font-size:.72rem;margin-top:6px;line-height:1.8"></div>
      </div>
    </div>

    <!-- Fila 2: Top vendedores + Regiones -->
    <div class="o-charts-row" style="margin-top:0">
      <div class="o-chart-card o-chart-half">
        <div class="o-chart-card-title">
          <i class="bi bi-trophy"></i> Top 5 Vendedores
        </div>
        <canvas id="chartVendedores"></canvas>
      </div>
      <div class="o-chart-card o-chart-half">
        <div class="o-chart-card-title">
          <i class="bi bi-geo-alt"></i> Ventas por Región (año actual)
        </div>
        <canvas id="chartRegion"></canvas>
      </div>
    </div>

    ${viewToolbar('Ventas Mensuales', ventas_mensuales.length, 'bi-calendar3')}
    <div class="o-list-view" style="margin:0;border-radius:0;border-left:none;border-right:none;border-bottom:none">
      <table class="o-table">
        <thead>
          <tr>
            <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
            <th class="sortable">Período <i class="bi bi-arrow-down sort-icon"></i></th>
            <th class="sortable">Pedidos <i class="bi bi-chevron-expand sort-icon"></i></th>
            <th class="sortable">Ventas Totales <i class="bi bi-chevron-expand sort-icon"></i></th>
            <th>Ticket Promedio</th>
            <th>Descuentos</th>
            <th>Clientes</th>
            <th>Vendedores</th>
          </tr>
        </thead>
        <tbody>
          ${ventas_mensuales.map(r => `
          <tr>
            <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
            <td><span class="o-badge o-badge-purple">${r.periodo}</span></td>
            <td class="o-num">${fmtNum(r.total_pedidos)}</td>
            <td class="o-num-bold">${fmtCurrency(r.ventas_totales)}</td>
            <td class="o-num">${fmtCurrency(r.ticket_promedio)}</td>
            <td class="o-num" style="color:var(--danger)">${fmtCurrency(r.descuentos_totales)}</td>
            <td class="o-num">${fmtNum(r.clientes_unicos)}</td>
            <td class="o-num">${fmtNum(r.vendedores_activos)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;

  _drawDashboardCharts(data);
}

function _nextPeriod(p) {
  const [y, m] = p.split('-').map(Number);
  const d = new Date(y, m, 1);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
}

function _linReg(vals) {
  const n = vals.length, xBar = (n-1)/2;
  const yBar = vals.reduce((a,b)=>a+b,0)/n;
  let num=0, den=0;
  vals.forEach((y,i)=>{ num+=(i-xBar)*(y-yBar); den+=(i-xBar)**2; });
  const slope = den ? num/den : 0;
  return { slope, intercept: yBar - slope*xBar };
}

function _drawDashboardCharts({ ventas_mensuales, top_vendedores, ventas_region, metas_dist }) {
  Object.values(_dashCharts).forEach(c => c?.destroy());
  _dashCharts = {};

  const P  = '#875A7B';
  const PA = 'rgba(135,90,123,0.12)';
  const OG = '#fd7e14';
  const gridColor = 'rgba(0,0,0,0.05)';
  const tickFont  = { size: 10 };

  /* ── Chart 1: Tendencia + Proyección ── */
  const vmAsc     = [...ventas_mensuales].reverse();
  const histLabels = vmAsc.map(r => r.periodo);
  const histVals   = vmAsc.map(r => parseFloat(r.ventas_totales));
  const n = histVals.length;
  const reg = _linReg(histVals);
  let last = histLabels[n-1];
  const projLabels = [];
  for(let i=0;i<3;i++) { last = _nextPeriod(last); projLabels.push(last); }
  const projVals = [1,2,3].map(i => Math.round(Math.max(0, reg.slope*(n-1+i)+reg.intercept)));
  const allLabels = [...histLabels, ...projLabels];
  const histData  = [...histVals, null, null, null];
  const projData  = histVals.map((_,i) => i===n-1 ? histVals[n-1] : null).concat(projVals);

  _dashCharts.ventas = new Chart(document.getElementById('chartVentas'), {
    type: 'line',
    data: {
      labels: allLabels,
      datasets: [
        { label:'Ventas reales', data:histData, borderColor:P, backgroundColor:PA,
          fill:true, tension:0.35, pointRadius:2, pointHoverRadius:4,
          borderWidth:2, spanGaps:false },
        { label:'Proyección IA', data:projData, borderColor:OG,
          backgroundColor:'rgba(253,126,20,0.06)', fill:true, tension:0.35,
          pointRadius:4, pointStyle:'rectRot', borderWidth:2,
          borderDash:[5,3], spanGaps:false }
      ]
    },
    options: {
      responsive:true, aspectRatio:3.8,
      interaction:{mode:'index',intersect:false},
      layout:{padding:{top:4,bottom:2}},
      plugins: {
        legend:{labels:{font:{size:10},usePointStyle:true,boxWidth:8,padding:12}},
        tooltip:{callbacks:{label:ctx=>` ${ctx.dataset.label}: ${fmtCurrency(ctx.parsed.y)}`}}
      },
      scales: {
        y:{ ticks:{callback:v=>v>=1e6?'$'+(v/1e6).toFixed(1)+'M':'$'+(v/1000).toFixed(0)+'K',
              font:tickFont, maxTicksLimit:5}, grid:{color:gridColor} },
        x:{ ticks:{font:tickFont, maxRotation:0, maxTicksLimit:8}, grid:{display:false} }
      }
    }
  });

  /* ── Chart 2: Doughnut metas ── */
  const { sobre_meta, en_meta, bajo_meta } = metas_dist || {};
  _dashCharts.metas = new Chart(document.getElementById('chartMetas'), {
    type:'doughnut',
    data:{
      labels:['≥ 100%','80 – 99%','< 80%'],
      datasets:[{
        data:[sobre_meta||0, en_meta||0, bajo_meta||0],
        backgroundColor:['#28a745', P, '#dc3545'],
        borderWidth:2, borderColor:'#fff'
      }]
    },
    options:{
      responsive:true, aspectRatio:1.6, cutout:'68%',
      layout:{padding:4},
      plugins:{
        legend:{display:false},
        tooltip:{callbacks:{label:ctx=>`${ctx.label}: ${ctx.raw} vendedores`}}
      }
    }
  });
  const total = (sobre_meta||0)+(en_meta||0)+(bajo_meta||0);
  document.getElementById('metas-leyenda').innerHTML =
    `<span style="color:#28a745">●</span> Sobre meta: <b>${sobre_meta||0}</b>&nbsp;&nbsp;`+
    `<span style="color:${P}">●</span> En meta: <b>${en_meta||0}</b>&nbsp;&nbsp;`+
    `<span style="color:#dc3545">●</span> Bajo meta: <b>${bajo_meta||0}</b><br>`+
    `<span style="color:#888">Equipo de ${total} vendedores</span>`;

  /* ── Chart 3: Top 5 vendedores (horizontal bar) ── */
  const vNames  = (top_vendedores||[]).map(v => {
    const p = v.vendedor.split(' ');
    return p[0] + (p.length>1?' '+p[p.length-1]:'');
  });
  const vVentas = (top_vendedores||[]).map(v => parseFloat(v.ventas_totales));
  const alphas  = [0.85,0.70,0.55,0.42,0.30];
  _dashCharts.vendedores = new Chart(document.getElementById('chartVendedores'), {
    type:'bar',
    data:{
      labels:vNames,
      datasets:[{
        label:'Ventas', data:vVentas,
        backgroundColor:alphas.map(a=>`rgba(135,90,123,${a})`),
        borderRadius:3, borderSkipped:false, barThickness:14
      }]
    },
    options:{
      indexAxis:'y', responsive:true, aspectRatio:2.2,
      layout:{padding:{right:8}},
      plugins:{
        legend:{display:false},
        tooltip:{callbacks:{label:ctx=>` ${fmtCurrency(ctx.parsed.x)}`}}
      },
      scales:{
        x:{ ticks:{callback:v=>'$'+(v/1e6).toFixed(1)+'M', font:tickFont, maxTicksLimit:5},
            grid:{color:gridColor} },
        y:{ ticks:{font:{size:10}}, grid:{display:false} }
      }
    }
  });

  /* ── Chart 4: Ventas por región (bar) ── */
  const regColors = [P,'#a57ba0','#28a745','#17a2b8','#fd7e14','#dc3545'];
  const regLabels = (ventas_region||[]).map(r => r.region);
  const regVals   = (ventas_region||[]).map(r => parseFloat(r.total_ventas));
  _dashCharts.region = new Chart(document.getElementById('chartRegion'), {
    type:'bar',
    data:{
      labels:regLabels,
      datasets:[{
        label:'Ventas', data:regVals,
        backgroundColor:regColors.slice(0, regLabels.length),
        borderRadius:3, borderSkipped:false, barThickness:28
      }]
    },
    options:{
      responsive:true, aspectRatio:2.2,
      layout:{padding:{top:4}},
      plugins:{
        legend:{display:false},
        tooltip:{callbacks:{label:ctx=>` ${fmtCurrency(ctx.parsed.y)}`}}
      },
      scales:{
        y:{ ticks:{callback:v=>'$'+(v/1e6).toFixed(1)+'M', font:tickFont, maxTicksLimit:5},
            grid:{color:gridColor} },
        x:{ ticks:{font:tickFont}, grid:{display:false} }
      }
    }
  });
}

/* ════════════════════════════════════════════════════════════
   VENTAS
   ════════════════════════════════════════════════════════════ */
function renderVentas(data) {
  const { vendedores, clientes_top } = data;
  document.getElementById('content').innerHTML = `

    ${viewToolbar('Ventas', vendedores.length + clientes_top.length, 'bi-graph-up')}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0;margin:16px 20px">

      <!-- Vendedores -->
      <div class="o-list-view" style="margin:0;border-radius:6px 0 0 6px">
        ${listHeader('Top Vendedores', vendedores.length, 'bi-trophy')}
        <table class="o-table">
          <thead>
            <tr>
              <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
              <th>#</th>
              <th class="sortable">Vendedor <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th>Zona</th>
              <th class="sortable">Ventas <i class="bi bi-arrow-down sort-icon"></i></th>
              <th>% Meta</th>
            </tr>
          </thead>
          <tbody>
            ${vendedores.map((r, i) => `
            <tr>
              <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
              <td>${rankBadge(i)}</td>
              <td>
                <div style="display:flex;align-items:center;gap:7px">
                  <span class="o-avatar">${initials(r.vendedor)}</span>
                  <div>
                    <div class="o-cell-main">${r.vendedor}</div>
                    <div class="o-cell-sub">${r.cargo || ''}</div>
                  </div>
                </div>
              </td>
              <td><span class="o-badge o-badge-gray">${r.zona || '—'}</span></td>
              <td class="o-num-bold">${fmtCurrency(r.ventas_totales)}</td>
              <td>
                <div style="display:flex;align-items:center;gap:6px">
                  <div class="o-progress" style="width:52px">
                    <div class="o-progress-bar ${(r.pct_meta_mensual||0)>=100?'full':''}"
                         style="width:${Math.min(r.pct_meta_mensual||0,100)}%"></div>
                  </div>
                  <small class="o-num" style="min-width:32px">${r.pct_meta_mensual||0}%</small>
                </div>
              </td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>

      <!-- Clientes Top -->
      <div class="o-list-view" style="margin:0;border-radius:0 6px 6px 0;border-left:none">
        ${listHeader('Clientes Top', clientes_top.length, 'bi-star-fill')}
        <table class="o-table">
          <thead>
            <tr>
              <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
              <th>#</th>
              <th class="sortable">Cliente <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th class="sortable">Facturación <i class="bi bi-arrow-down sort-icon"></i></th>
              <th>Pedidos</th>
              <th>Último Pedido</th>
            </tr>
          </thead>
          <tbody>
            ${clientes_top.map((r, i) => `
            <tr>
              <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
              <td>${rankBadge(i)}</td>
              <td>
                <div class="o-cell-main">${r.razon_social}</div>
                <div class="o-cell-sub">${r.segmento} · ${r.ciudad}</div>
              </td>
              <td class="o-num-bold">${fmtCurrency(r.facturacion_total)}</td>
              <td class="o-num">${fmtNum(r.total_pedidos)}</td>
              <td><small style="color:var(--muted)">${fmtDate(r.ultimo_pedido)}</small></td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

/* ════════════════════════════════════════════════════════════
   EMPLEADOS
   ════════════════════════════════════════════════════════════ */
function renderEmpleados(data) {
  const { kpis, empleados, antiguedad } = data;
  const NIVEL_BADGE = {
    director: 'o-badge-danger', gerente: 'o-badge-warning',
    lead: 'o-badge-purple', senior: 'o-badge-info',
    mid: 'o-badge-success', junior: 'o-badge-gray',
  };
  document.getElementById('content').innerHTML = `

    <div class="o-stats-bar">
      ${statBtn('Empleados Activos',  fmtNum(kpis.total_activos),         'bi-person-check')}
      ${statBtn('Salario Promedio',   fmtCurrency(kpis.salario_promedio), 'bi-cash')}
      ${statBtn('Departamentos',      fmtNum(kpis.total_departamentos),   'bi-diagram-3')}
      ${statBtn('Sucursales',         fmtNum(kpis.total_sucursales),      'bi-geo-alt')}
    </div>

    ${viewToolbar('Empleados', kpis.total_activos, 'bi-people')}

    <div style="display:grid;grid-template-columns:1fr auto;gap:0;margin:16px 20px">

      <!-- Nómina -->
      <div class="o-list-view" style="margin:0;border-radius:6px 0 0 6px">
        ${listHeader('Nómina Activa', empleados.length, 'bi-person-badge')}
        <table class="o-table">
          <thead>
            <tr>
              <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
              <th class="sortable">Empleado <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th>Nivel</th>
              <th class="sortable">Departamento <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th>Sucursal</th>
              <th class="sortable">Salario <i class="bi bi-arrow-down sort-icon"></i></th>
              <th>Antigüedad</th>
            </tr>
          </thead>
          <tbody>
            ${empleados.map(r => `
            <tr>
              <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
              <td>
                <div style="display:flex;align-items:center;gap:8px">
                  <span class="o-avatar">${initials(r.nombre_completo)}</span>
                  <div>
                    <div class="o-cell-main">${r.nombre_completo}</div>
                    <div class="o-cell-sub">${r.numero_empleado} · ${r.cargo}</div>
                  </div>
                </div>
              </td>
              <td><span class="o-badge ${NIVEL_BADGE[r.nivel] || 'o-badge-gray'}">${r.nivel}</span></td>
              <td>${r.departamento}</td>
              <td>
                <div class="o-cell-main">${r.sucursal}</div>
                <div class="o-cell-sub">${r.ciudad}</div>
              </td>
              <td class="o-num-bold">${fmtCurrency(r.salario_mensual)}</td>
              <td class="o-num">${r.anos_en_empresa} años</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>

      <!-- Antigüedad -->
      <div class="o-list-view" style="margin:0;border-radius:0 6px 6px 0;border-left:none;width:280px">
        ${listHeader('Más Antigüos', antiguedad.length, 'bi-award')}
        <table class="o-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Empleado</th>
              <th>Años</th>
            </tr>
          </thead>
          <tbody>
            ${antiguedad.map((r, i) => `
            <tr>
              <td>${rankBadge(i)}</td>
              <td>
                <div class="o-cell-main">${r.nombre}</div>
                <div class="o-cell-sub">${r.cargo} · ${fmtDate(r.fecha_ingreso)}</div>
              </td>
              <td>
                <span class="o-badge o-badge-purple">${r.anos}</span>
              </td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

/* ════════════════════════════════════════════════════════════
   INVENTARIO
   ════════════════════════════════════════════════════════════ */
const STOCK_BADGE = {
  sin_stock:  'o-badge-danger',
  critico:    'o-badge-warning',
  sobrestock: 'o-badge-info',
  normal:     'o-badge-success',
};

function renderInventario(data) {
  const { kpis, inventario, mas_vendidos } = data;
  document.getElementById('content').innerHTML = `

    <div class="o-stats-bar">
      ${statBtn('Sin Stock',        fmtNum(kpis.sin_stock),             'bi-exclamation-triangle')}
      ${statBtn('Stock Crítico',    fmtNum(kpis.critico),               'bi-exclamation-circle')}
      ${statBtn('Valor Inventario', fmtCurrency(kpis.valor_inventario), 'bi-currency-dollar')}
      ${statBtn('Total Productos',  fmtNum(kpis.total_productos),       'bi-box2')}
    </div>

    ${viewToolbar('Inventario', kpis.total_productos, 'bi-box-seam')}

    <div style="display:grid;grid-template-columns:1fr auto;gap:0;margin:16px 20px">

      <!-- Estado inventario -->
      <div class="o-list-view" style="margin:0;border-radius:6px 0 0 6px">
        ${listHeader('Estado del Inventario', inventario.length, 'bi-clipboard-data')}
        <table class="o-table">
          <thead>
            <tr>
              <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
              <th class="sortable">Producto <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th>Categoría</th>
              <th>Proveedor</th>
              <th class="sortable">Stock <i class="bi bi-chevron-expand sort-icon"></i></th>
              <th>Estado</th>
              <th>Margen</th>
              <th class="sortable">Valor <i class="bi bi-chevron-expand sort-icon"></i></th>
            </tr>
          </thead>
          <tbody>
            ${inventario.map(r => `
            <tr>
              <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
              <td>
                <div class="o-cell-main">${r.producto}</div>
                <div class="o-cell-sub">${r.sku}</div>
              </td>
              <td>${r.categoria}</td>
              <td style="color:var(--muted);font-size:.78rem">${r.proveedor}</td>
              <td>
                <span class="o-num">${fmtNum(r.stock_actual)}</span>
                <span style="color:var(--muted);font-size:.72rem"> / ${fmtNum(r.stock_minimo)}</span>
              </td>
              <td><span class="o-badge ${STOCK_BADGE[r.estado_stock] || 'o-badge-gray'}">${r.estado_stock}</span></td>
              <td class="o-num">${r.margen_pct}%</td>
              <td class="o-num">${fmtCurrency(r.valor_inventario)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>

      <!-- Más vendidos -->
      <div class="o-list-view" style="margin:0;border-radius:0 6px 6px 0;border-left:none;width:310px">
        ${listHeader('Más Vendidos', mas_vendidos.length, 'bi-fire')}
        <table class="o-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Producto</th>
              <th class="sortable">Unidades <i class="bi bi-arrow-down sort-icon"></i></th>
            </tr>
          </thead>
          <tbody>
            ${mas_vendidos.map((r, i) => `
            <tr>
              <td>${rankBadge(i)}</td>
              <td>
                <div class="o-cell-main">${r.producto}</div>
                <div class="o-cell-sub">${r.categoria}</div>
              </td>
              <td class="o-num-bold">${fmtNum(r.unidades_vendidas)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

/* ════════════════════════════════════════════════════════════
   FINANZAS
   ════════════════════════════════════════════════════════════ */
function renderFinanzas(data) {
  const { kpis, pagos } = data;
  document.getElementById('content').innerHTML = `

    <div class="o-stats-bar">
      ${statBtn('Pagos del Mes',       fmtCurrency(kpis.pagos_mes),          'bi-credit-card')}
      ${statBtn('Total Pagos',         fmtNum(kpis.total_pagos),             'bi-receipt')}
      ${statBtn('Facturas Pendientes', fmtNum(kpis.facturas_pendientes),     'bi-file-earmark-text')}
      ${statBtn('Confirmados',         fmtNum(kpis.pagos_confirmados),       'bi-check-circle')}
    </div>

    ${viewToolbar('Pagos', pagos.length, 'bi-cash-stack')}

    <div class="o-list-view" style="margin:16px 20px">
      ${listHeader('Pagos Recientes', pagos.length, 'bi-clock-history')}
      <table class="o-table">
        <thead>
          <tr>
            <th class="o-col-check"><input type="checkbox" class="o-checkbox"></th>
            <th class="sortable">Fecha <i class="bi bi-arrow-down sort-icon"></i></th>
            <th>Folio Pedido</th>
            <th class="sortable">Cliente <i class="bi bi-chevron-expand sort-icon"></i></th>
            <th>Región</th>
            <th>Método</th>
            <th class="sortable">Monto <i class="bi bi-chevron-expand sort-icon"></i></th>
            <th>Estado</th>
            <th>Factura</th>
          </tr>
        </thead>
        <tbody>
          ${pagos.map(r => `
          <tr>
            <td class="o-col-check"><input type="checkbox" class="o-checkbox"></td>
            <td><span style="color:var(--muted);font-size:.78rem">${fmtDate(r.fecha_pago)}</span></td>
            <td><span class="o-badge o-badge-gray" style="font-family:monospace">${r.folio_pedido}</span></td>
            <td>
              <div style="display:flex;align-items:center;gap:6px">
                <span class="o-avatar" style="width:24px;height:24px;font-size:.65rem">${initials(r.cliente)}</span>
                <div class="o-cell-main">${r.cliente}</div>
              </div>
            </td>
            <td style="color:var(--muted);font-size:.78rem">${r.region}</td>
            <td><span class="o-badge o-badge-info">${r.metodo_pago}</span></td>
            <td class="o-num-bold">${fmtCurrency(r.monto)}</td>
            <td>
              ${r.confirmado
                ? '<span class="o-badge o-badge-success"><i class="bi bi-check2"></i> Confirmado</span>'
                : '<span class="o-badge o-badge-warning"><i class="bi bi-clock"></i> Pendiente</span>'}
            </td>
            <td>
              ${r.factura_pagada
                ? '<span class="o-badge o-badge-success">Pagada</span>'
                : '<span class="o-badge o-badge-warning">Por pagar</span>'}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

/* ════════════════════════════════════════════════════════════
   CHAT
   ════════════════════════════════════════════════════════════ */
let chatHistory = [];
let chatOpen    = false;

const chatToggle   = document.getElementById('chat-toggle');
const chatPanel    = document.getElementById('chat-panel');
const chatCloseBtn = document.getElementById('chat-close');
const chatInput    = document.getElementById('chat-input');
const chatSend     = document.getElementById('chat-send');
const chatMessages = document.getElementById('chat-messages');
const chatTyping   = document.getElementById('chat-typing');

chatToggle.addEventListener('click', () => {
  chatOpen = !chatOpen;
  chatPanel.classList.toggle('open', chatOpen);
  chatToggle.innerHTML = chatOpen ? '<i class="bi bi-x-lg"></i>' : '<i class="bi bi-robot"></i>';
  if (chatOpen) { chatInput.focus(); scrollBottom(); }
});
chatCloseBtn.addEventListener('click', () => {
  chatOpen = false;
  chatPanel.classList.remove('open');
  chatToggle.innerHTML = '<i class="bi bi-robot"></i>';
});
chatSend.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || chatSend.disabled) return;
  chatInput.value = '';

  const historyToSend = [...chatHistory];
  appendMsg('user', text);
  setInputEnabled(false);
  chatTyping.classList.add('visible');
  scrollBottom();

  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, history: historyToSend }),
  })
    .then(r => r.json())
    .then(data => {
      chatTyping.classList.remove('visible');
      setInputEnabled(true);
      chatInput.focus();
      if (data.error) {
        appendMsg('bot', `Error: ${data.error}`);
      } else {
        chatHistory.push({ role: 'user',      content: text });
        chatHistory.push({ role: 'assistant', content: data.response });
        appendMsg('bot', data.response);
      }
      scrollBottom();
    })
    .catch(err => {
      chatTyping.classList.remove('visible');
      setInputEnabled(true);
      appendMsg('bot', `Error de conexión: ${err.message}`);
      scrollBottom();
    });
}

function appendMsg(role, text) {
  const isUser = role === 'user';
  const div = document.createElement('div');
  div.className = `msg msg-${isUser ? 'user' : 'bot'}`;
  div.innerHTML = `
    <div class="msg-bubble">${escapeHtml(text)}</div>
    <div class="msg-time">${nowTime()}</div>`;
  chatMessages.appendChild(div);
}

function setInputEnabled(on) {
  chatInput.disabled = !on;
  chatSend.disabled  = !on;
}

function scrollBottom() {
  setTimeout(() => { chatMessages.scrollTop = chatMessages.scrollHeight; }, 40);
}

/* ── Arranque ───────────────────────────────────────────────── */
loadSection('dashboard');
