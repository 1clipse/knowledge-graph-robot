/* ============================================================
   Graph Module — D3 force graph, search, path finding, stats
   ============================================================ */

const LABEL_COLORS = {
  Robot: '#1E40AF', Manufacturer: '#D97706', Component: '#059669',
  Reducer: '#65A30D', ServoMotor: '#CA8A04', Controller: '#7C3AED',
  Sensor: '#DB2777', ApplicationScenario: '#EA580C', Process: '#0891B2',
  EndEffector: '#4F46E5', Standard: '#0D9488', Material: '#16A34A',
  Software: '#9333EA', IngestLog: '#94A3B8'
};
const LABEL_ZH = {
  Robot: '机器人', Manufacturer: '制造商', Component: '零部件',
  Reducer: '减速器', ServoMotor: '伺服电机', Controller: '控制器',
  Sensor: '传感器', ApplicationScenario: '应用场景', Process: '工艺',
  EndEffector: '末端执行器', Standard: '标准规范', Material: '加工材料',
  Software: '软件系统', IngestLog: '摄入日志'
};

let simulation, svg, g, linkGroup, nodeGroup, zoomBehavior;
let currentNodes = [], currentLinks = [], filteredTypes = new Set();
let allEntitiesCache = [];
let _graphInit = false;

function initGraph() {
  const container = document.getElementById('graph-container');
  const w = container.clientWidth;
  const h = container.clientHeight;

  svg = d3.select('#graph-container').append('svg').attr('width', w).attr('height', h);
  g = svg.append('g');
  linkGroup = g.append('g').attr('class', 'links');
  nodeGroup = g.append('g').attr('class', 'nodes');

  simulation = d3.forceSimulation()
    .force('link', d3.forceLink().id(d => d.id).distance(120))
    .force('charge', d3.forceManyBody().strength(-400))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide().radius(38));

  zoomBehavior = d3.zoom().scaleExtent([0.08, 5]).on('zoom', (event) => {
    g.attr('transform', event.transform);
  });
  svg.call(zoomBehavior);

  simulation.on('tick', () => {
    linkGroup.selectAll('line')
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeGroup.selectAll('g.node').attr('transform', d => `translate(${d.x},${d.y})`);
  });

  window.addEventListener('resize', () => {
    const cw = document.getElementById('graph-container').clientWidth;
    const ch = document.getElementById('graph-container').clientHeight;
    svg.attr('width', cw).attr('height', ch);
    simulation.force('center', d3.forceCenter(cw / 2, ch / 2));
  });

  buildLegend();
  document.getElementById('graphLoading').style.display = 'none';
}

function updateGraph(data) {
  document.getElementById('graphLoading').style.display = 'none';

  const rawNodes = (data.nodes || []).filter(n => {
    const lbl = (n.labels && n.labels[0]) || '未知';
    return filteredTypes.size === 0 || !filteredTypes.has(lbl);
  });

  currentNodes = rawNodes.map(n => {
    const id = n.id || ((n.labels && n.labels[0] || '未知') + '::' + ((n.properties && n.properties.name) || ''));
    return Object.assign({}, n, { id });
  });

  const nodeIds = new Set(currentNodes.map(n => n.id));
  currentLinks = (data.edges || []).filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map(e => ({ source: e.source, target: e.target, type: e.type || 'RELATED' }));

  // Links
  linkGroup.selectAll('line')
    .data(currentLinks, d => d.source + '-' + (d.type || '') + '-' + d.target)
    .join('line')
    .attr('stroke', '#CBD5E1').attr('stroke-width', 1.5).attr('stroke-opacity', 0.65);

  // Nodes
  const nodes = nodeGroup.selectAll('g.node').data(currentNodes, d => d.id);
  nodes.exit().remove();

  const nodesEnter = nodes.enter().append('g').attr('class', 'node').style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  nodesEnter.append('circle')
    .attr('r', 18)
    .attr('fill', d => LABEL_COLORS[d.labels && d.labels[0]] || '#94A3B8')
    .attr('stroke', '#FFFFFF').attr('stroke-width', 2)
    .on('mouseover', showTooltip)
    .on('mouseout', hideTooltip)
    .on('click', (event, d) => showNodeDetail(d));

  nodesEnter.append('text')
    .attr('dy', 30).attr('text-anchor', 'middle')
    .attr('fill', d => LABEL_COLORS[d.labels && d.labels[0]] || '#64748B')
    .attr('font-size', 11).attr('font-weight', 500)
    .attr('font-family', 'Fira Code, monospace')
    .text(d => {
      const name = (d.properties && d.properties.name) || d.id || '';
      return name.length > 14 ? name.substring(0, 13) + '…' : name;
    });

  nodes.select('circle').attr('fill', d => LABEL_COLORS[d.labels && d.labels[0]] || '#94A3B8');
  nodes.select('text').text(d => {
    const name = (d.properties && d.properties.name) || d.id || '';
    return name.length > 14 ? name.substring(0, 13) + '…' : name;
  });

  simulation.nodes(currentNodes);
  simulation.force('link').links(currentLinks);
  simulation.alpha(1).restart();
}

// ---- Tooltip ----
function showTooltip(event, d) {
  const tt = document.getElementById('tooltip');
  const container = document.getElementById('graph-view');
  const rect = container.getBoundingClientRect();
  const props = d.properties || {};
  const label = (d.labels && d.labels[0]) || '未知';
  const labelZh = LABEL_ZH[label] || label;
  const color = LABEL_COLORS[label] || '#94A3B8';
  let html = `<strong style="color:${color}">${labelZh} / ${label}</strong><br>`;
  html += `<strong style="font-size:14px">${escapeHtml(props.name || d.id)}</strong><br>`;
  Object.entries(props).slice(0, 5).forEach(([k, v]) => {
    if (k !== 'name' && v != null && v !== '') {
      html += `<span style="color:#94A3B8">${k}:</span> ${escapeHtml(String(v))}<br>`;
    }
  });
  tt.innerHTML = html;
  tt.style.display = 'block';
  let left = event.pageX - rect.left + 14;
  let top = event.pageY - rect.top + 14;
  if (left + 320 > rect.width) left = rect.width - 320;
  if (top + 180 > rect.height) top = rect.height - 180;
  tt.style.left = Math.max(0, left) + 'px';
  tt.style.top = Math.max(0, top) + 'px';
}

function hideTooltip() { document.getElementById('tooltip').style.display = 'none'; }

// ---- Node detail ----
function showNodeDetail(d) {
  const detail = document.getElementById('nodeDetail');
  const props = d.properties || {};
  const label = (d.labels && d.labels[0]) || '未知';
  const labelZh = LABEL_ZH[label] || label;
  const color = LABEL_COLORS[label] || '#94A3B8';
  const nameVal = props.name || d.id || '';
  let html = `<div class="detail-name"><span class="detail-label" style="background:${color}">${labelZh}</span> ${escapeHtml(nameVal)}</div>`;
  html += `<button onclick="deleteNode('${label}','${nameVal.replace(/'/g, "\\'")}');event.stopPropagation()" style="margin-bottom:6px;padding:3px 10px;border-radius:2px;border:1px solid var(--err);background:transparent;color:var(--err);cursor:pointer;font-size:10px;font-family:var(--font-mono)">删除</button>`;
  Object.keys(props).forEach(k => {
    if (props[k] != null && props[k] !== '') {
      html += `<div class="detail-row"><span>${k}:</span> ${escapeHtml(String(props[k]))}</div>`;
    }
  });
  if (!Object.keys(props).length) html += '<div class="detail-row" style="color:var(--text-dim)">无属性</div>';

  // Fetch neighbors
  fetch(`${API_BASE}/query/neighbors/${label}/${encodeURIComponent(nameVal)}?limit=20`)
    .then(r => r.json()).then(data => {
      if (data && data.length > 0) {
        html += '<div class="neighbor-section"><strong style="color:var(--accent);font-size:11px">关联节点:</strong>';
        data.forEach(n => {
          html += `<div class="detail-row" style="margin-left:6px">&rarr; <span style="color:var(--accent-dim)">${n.relation_type || ''}</span>: ${n.node && n.node.name || '?'}</div>`;
        });
        html += '</div>';
      }
      // Confidence & source
      if (props._confidence !== undefined) html += `<div class="detail-row"><span>置信度:</span> ${(props._confidence * 100).toFixed(0)}%</div>`;
      if (props._source) html += `<div class="detail-row"><span>来源:</span> ${escapeHtml(props._source)}</div>`;
      if (props.valid_from || props.valid_to) html += `<div class="detail-row"><span>时段:</span> ${props.valid_from || '?'} ~ ${props.valid_to || '至今'}</div>`;
      detail.innerHTML = html;
    }).catch(() => { detail.innerHTML = html; });
}

// ---- Search ----
async function searchEntities() {
  const keyword = document.getElementById('searchInput').value.trim();
  if (!keyword) return;
  document.getElementById('graphLoading').style.display = 'flex';
  try {
    const resp = await fetch(`${API_BASE}/subgraph/search/${encodeURIComponent(keyword)}?depth=2&limit=200`);
    const data = await resp.json();
    updateGraph(data);
    const resultsDiv = document.getElementById('searchResults');
    resultsDiv.innerHTML = '';
    const nodes = data.nodes || [];
    if (nodes.length === 0) {
      resultsDiv.innerHTML = '<div style="font-size:12px;color:var(--text-dim);padding:6px">无匹配结果</div>';
    } else {
      nodes.slice(0, 25).forEach(n => {
        const label = (n.labels && n.labels[0]) || '未知';
        const labelZh = LABEL_ZH[label] || label;
        const color = LABEL_COLORS[label] || '#94A3B8';
        const item = document.createElement('div');
        item.className = 'node-item';
        item.innerHTML = `<span class="node-dot" style="background:${color}"></span>${escapeHtml(n.properties && n.properties.name || n.id)} <span style="font-size:10px;color:var(--text-dim)">${labelZh}</span>`;
        item.onclick = () => showNodeDetail(n);
        resultsDiv.appendChild(item);
      });
    }
  } catch (e) { console.error('Search failed:', e); }
  document.getElementById('graphLoading').style.display = 'none';
}

// ---- Full graph load ----
async function loadFullGraph() {
  document.getElementById('graphLoading').style.display = 'flex';
  filteredTypes = new Set();
  buildLegend();
  const keywords = ['Component', 'Robot', 'Manufacturer', 'FANUC', 'ABB', 'CHX'];
  let best = null;
  for (const kw of keywords) {
    const resp = await fetch(`${API_BASE}/subgraph/search/${encodeURIComponent(kw)}?depth=3&limit=300`);
    const data = await resp.json();
    if (!data.nodes || data.nodes.length === 0) continue;
    if (!best || (data.edges ? data.edges.length : 0) > (best.edges ? best.edges.length : 0)) {
      best = data;
    }
  }
  if (best && best.nodes && best.nodes.length > 0) updateGraph(best);
  document.getElementById('graphLoading').style.display = 'none';
}

// ---- Stats ----
async function loadStats() {
  try {
    const resp = await fetch(`${API_BASE}/query/stats`);
    const data = await resp.json();
    document.getElementById('statNodes').textContent = data.total_nodes || 0;
    document.getElementById('statRels').textContent = data.total_relations || 0;
    document.getElementById('statLabels').textContent = (data.node_labels || []).length;
    document.getElementById('statRelTypes').textContent = (data.relation_types || []).length;

    if (data.top_degree_nodes && data.top_degree_nodes.length > 0) {
      const src = document.getElementById('pathSource');
      const tgt = document.getElementById('pathTarget');
      [src, tgt].forEach(sel => {
        while (sel.options.length > 1) sel.remove(1);
        data.top_degree_nodes.forEach(n => {
          const opt = document.createElement('option');
          opt.value = n.name || '';
          opt.textContent = (n.name || '') + ' (' + (n.label || '?') + ')';
          opt.setAttribute('data-label', n.label || '');
          sel.appendChild(opt);
        });
      });
    }
  } catch (e) { console.error('Stats failed:', e); }
}

// ---- Path finding ----
function updatePathTarget() {}

async function findPath() {
  const srcSel = document.getElementById('pathSource');
  const tgtSel = document.getElementById('pathTarget');
  const srcName = srcSel.value;
  const tgtName = tgtSel.value;
  if (!srcName || !tgtName) { alert('请选择起点和终点实体'); return; }
  if (srcName === tgtName) { alert('起点和终点不能相同'); return; }

  const srcLabel = srcSel.selectedOptions[0] ? srcSel.selectedOptions[0].getAttribute('data-label') || '' : '';
  const tgtLabel = tgtSel.selectedOptions[0] ? tgtSel.selectedOptions[0].getAttribute('data-label') || '' : '';

  document.getElementById('graphLoading').style.display = 'flex';
  try {
    const url = `${API_BASE}/query/shortest-path?source_label=${encodeURIComponent(srcLabel)}&source_name=${encodeURIComponent(srcName)}&target_label=${encodeURIComponent(tgtLabel)}&target_name=${encodeURIComponent(tgtName)}&max_depth=5`;
    const resp = await fetch(url);
    const data = await resp.json();
    if (!data || data.length === 0) { alert('未找到路径'); document.getElementById('graphLoading').style.display = 'none'; return; }
    const path = data[0];
    const nodes = (path.nodes || []).map((n, i) => ({
      id: (n.labels && n.labels[0] || 'Node') + '::' + ((n.properties && n.properties.name) || 'node_' + i),
      labels: n.labels || ['未知'],
      properties: n.properties || {}
    }));
    const nameToLabel = {};
    nodes.forEach(n => { const nm = (n.properties && n.properties.name) || ''; nameToLabel[nm] = n.labels && n.labels[0] || '未知'; });
    const edges = (path.edges || []).map(e => ({
      source: (nameToLabel[e.start] || '未知') + '::' + (e.start || ''),
      target: (nameToLabel[e.end] || '未知') + '::' + (e.end || ''),
      type: e.type || 'RELATED'
    }));
    updateGraph({ nodes, edges });
  } catch (e) { console.error('Path find failed:', e); }
  document.getElementById('graphLoading').style.display = 'none';
}

// ---- Legend ----
function buildLegend() {
  const legend = document.getElementById('legend');
  legend.innerHTML = '';
  Object.entries(LABEL_COLORS).forEach(([label, color]) => {
    const isFiltered = filteredTypes.has(label);
    const div = document.createElement('div');
    div.className = 'legend-item' + (isFiltered ? ' filtered' : '');
    div.innerHTML = `<span class="legend-dot" style="background:${color}"></span>${LABEL_ZH[label] || label}`;
    div.onclick = () => {
      if (filteredTypes.has(label)) filteredTypes.delete(label); else filteredTypes.add(label);
      buildLegend();
      if (currentNodes.length > 0 && currentLinks.length > 0) updateGraph({ nodes: currentNodes, edges: currentLinks });
    };
    legend.appendChild(div);
  });
}

// ---- Zoom ----
function zoomIn()  { svg.transition().duration(300).call(zoomBehavior.scaleBy, 1.4); }
function zoomOut() { svg.transition().duration(300).call(zoomBehavior.scaleBy, 0.7); }
function resetZoom(){ svg.transition().duration(400).call(zoomBehavior.transform, d3.zoomIdentity); }

// ---- Quality ----
async function loadQuality() {
  try {
    const resp = await fetch(`${API_BASE}/quality`);
    const data = await resp.json();
    let html = `<div style="padding:6px"><strong style="font-family:var(--font-mono);font-size:12px">质量: ${data.quality_score || 0}/100</strong><br>`;
    html += `孤立节点: ${data.orphan_nodes ? data.orphan_nodes.length : 0}<br>`;
    html += `缺失属性: ${data.missing_key_props ? data.missing_key_props.length : 0}<br>`;
    html += `潜在重复: ${data.potential_duplicates ? data.potential_duplicates.length : 0}<br>`;
    html += `低置信度: ${data.low_confidence_facts ? data.low_confidence_facts.length : 0}</div>`;
    document.getElementById('nodeDetail').innerHTML = html;
  } catch (e) { alert('质量报告失败: ' + e.message); }
}

// ---- Compare ----
async function compareEntities() {
  const a = document.getElementById('compareA').value.trim();
  const b = document.getElementById('compareB').value.trim();
  if (!a || !b) { alert('请输入两个实体名称'); return; }
  const el = document.getElementById('compareResult');
  el.innerHTML = '生成中...';
  try {
    const resp = await fetch(`${API_BASE}/compare`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entity_a: a, entity_b: b })
    });
    const data = await resp.json();
    if (data.status === 'success') {
      el.innerHTML = `<div style="white-space:pre-wrap;font-size:12px;line-height:1.8;font-family:var(--font-mono)">${simpleMd(data.comparison || 'No result')}</div>`;
    } else {
      el.innerHTML = '<div style="color:var(--err)">对比失败</div>';
    }
  } catch (e) { el.innerHTML = `<div style="color:var(--err)">请求失败: ${e.message}</div>`; }
}
