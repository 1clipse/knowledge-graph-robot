/* ============================================================
   Graph Module — D3 force graph, search, path finding, stats
   ============================================================ */

const DEFAULT_LABEL_COLORS = {
  Robot: '#1E40AF', Manufacturer: '#D97706', Component: '#059669',
  Reducer: '#65A30D', ServoMotor: '#CA8A04', Controller: '#7C3AED',
  Sensor: '#DB2777', ApplicationScenario: '#EA580C', Process: '#0891B2',
  EndEffector: '#4F46E5', Standard: '#0D9488', Material: '#16A34A',
  Software: '#9333EA', Drawing: '#2563EB', Part: '#0284C7', Assembly: '#7C3AED',
  Dimension: '#F97316', CADLayer: '#64748B', IngestLog: '#94A3B8'
};
const PALETTE = ['#1E40AF', '#D97706', '#059669', '#65A30D', '#CA8A04', '#7C3AED', '#DB2777', '#EA580C', '#0891B2', '#4F46E5', '#0D9488', '#16A34A', '#9333EA'];
let LABEL_COLORS = { ...DEFAULT_LABEL_COLORS };
let LABEL_ZH = {
  Robot: '机器人', Manufacturer: '制造商', Component: '零部件',
  Reducer: '减速器', ServoMotor: '伺服电机', Controller: '控制器',
  Sensor: '传感器', ApplicationScenario: '应用场景', Process: '工艺',
  EndEffector: '末端执行器', Standard: '标准规范', Material: '加工材料',
  Software: '软件系统', Drawing: 'CAD图纸', Part: '机械零件', Assembly: '装配体',
  Dimension: '尺寸标注', CADLayer: 'CAD图层', IngestLog: '摄入日志'
};

let simulation, svg, g, linkGroup, linkLabelGroup, nodeGroup, zoomBehavior, defs;
let currentNodes = [], currentLinks = [], filteredTypes = new Set();
let allEntitiesCache = [];
let _graphInit = false;
let _lastCompareData = null;  // stores full CompareResponse for export

// 关系类型 → 中文显示名（启动时会优先使用本地 schema metadata 覆盖）
let REL_ZH = {
  manufactures: '生产',
  supplies_component: '供应零部件',
  uses_component: '使用零部件',
  uses_reducer: '使用减速器',
  uses_servo: '使用伺服',
  uses_controller: '使用控制器',
  uses_sensor: '使用传感器',
  uses_end_effector: '使用末端执行器',
  applied_in: '应用于',
  performs_process: '执行工艺',
  process_requires: '工艺需要',
  process_material: '加工材料',
  scenario_includes: '场景包含',
  complies_with: '符合标准',
  uses_software: '使用软件',
  component_compatible: '零部件兼容',
  contains: '包含',
  competitor_of: '竞争关系',
  subsidiary_of: '子公司',
  drawing_defines: '图纸定义',
  drawing_defines_assembly: '图纸定义装配体',
  assembly_contains_part: '装配包含零件',
  assembly_contains_sub: '包含子装配体',
  part_has_dimension: '零件尺寸',
  drawing_has_layer: '图纸包含图层',
  part_made_of: '零件材料',
  robot_has_part: '机器人包含零件',
  RELATED: '关联',
  DERIVED_FROM: '来源于'
};

function relLabel(t) { return REL_ZH[t] || REL_ZH[String(t || '').toLowerCase()] || t || ''; }

async function loadGraphSchemaMetadata() {
  try {
    const resp = await fetch(`${API_BASE}/subgraph/schema`);
    if (!resp.ok) throw new Error(`schema metadata ${resp.status}`);
    const schema = await resp.json();
    const entityTypes = schema.entity_types || {};
    Object.entries(entityTypes).forEach(([label, meta], idx) => {
      LABEL_ZH[label] = meta.label_zh || label;
      if (!LABEL_COLORS[label]) LABEL_COLORS[label] = PALETTE[idx % PALETTE.length];
    });
    const relationTypes = schema.relation_types || {};
    Object.entries(relationTypes).forEach(([type, meta]) => {
      REL_ZH[type] = meta.label_zh || type;
      if (meta.label_en) REL_ZH[meta.label_en] = meta.label_zh || type;
    });
  } catch (e) {
    console.warn('Graph schema metadata unavailable, using local fallback labels:', e);
  }
}

function initGraph() {
  const container = document.getElementById('graph-container');
  const w = container.clientWidth;
  const h = container.clientHeight;

  svg = d3.select('#graph-container').append('svg').attr('width', w).attr('height', h);
  defs = svg.append('defs');
  // 箭头标记
  defs.append('marker')
    .attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
    .attr('refX', 26).attr('refY', 0)
    .attr('markerWidth', 7).attr('markerHeight', 7)
    .attr('orient', 'auto')
    .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', '#94A3B8');

  g = svg.append('g');
  linkGroup = g.append('g').attr('class', 'links');
  linkLabelGroup = g.append('g').attr('class', 'link-labels');
  nodeGroup = g.append('g').attr('class', 'nodes');

  simulation = d3.forceSimulation()
    .force('link', d3.forceLink().id(d => d.id).distance(180).strength(0.6))
    .force('charge', d3.forceManyBody().strength(-900).distanceMax(800))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide().radius(50))
    .force('x', d3.forceX(w / 2).strength(0.04))
    .force('y', d3.forceY(h / 2).strength(0.04));

  zoomBehavior = d3.zoom().scaleExtent([0.08, 5]).on('zoom', (event) => {
    g.attr('transform', event.transform);
  });
  svg.call(zoomBehavior);

  simulation.on('tick', () => {
    linkGroup.selectAll('line')
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    linkLabelGroup.selectAll('g.link-label')
      .attr('transform', d => {
        const x = (d.source.x + d.target.x) / 2;
        const y = (d.source.y + d.target.y) / 2;
        let ang = Math.atan2(d.target.y - d.source.y, d.target.x - d.source.x) * 180 / Math.PI;
        if (ang > 90) ang -= 180; else if (ang < -90) ang += 180;
        return `translate(${x},${y}) rotate(${ang})`;
      });
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

  // 计算度数 → 节点大小
  const deg = {};
  currentLinks.forEach(l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    deg[s] = (deg[s] || 0) + 1;
    deg[t] = (deg[t] || 0) + 1;
  });
  const radiusOf = d => Math.max(14, Math.min(34, 14 + Math.sqrt(deg[d.id] || 0) * 4));

  // Links
  const links = linkGroup.selectAll('line')
    .data(currentLinks, d => d.source + '-' + (d.type || '') + '-' + d.target);
  links.exit().remove();
  links.enter().append('line')
    .attr('stroke', '#94A3B8').attr('stroke-width', 1.2).attr('stroke-opacity', 0.55)
    .attr('marker-end', 'url(#arrow)');

  // Link labels (关系名贴在线条中点)
  const linkLabels = linkLabelGroup.selectAll('g.link-label')
    .data(currentLinks, d => d.source + '-' + (d.type || '') + '-' + d.target);
  linkLabels.exit().remove();
  const linkLabelsEnter = linkLabels.enter().append('g').attr('class', 'link-label');
  linkLabelsEnter.append('rect')
    .attr('fill', '#FFFFFF').attr('fill-opacity', 0.92)
    .attr('rx', 2).attr('ry', 2);
  linkLabelsEnter.append('text')
    .attr('text-anchor', 'middle').attr('dy', 3)
    .attr('font-size', 10).attr('font-family', 'Fira Code, monospace')
    .attr('fill', '#475569')
    .text(d => relLabel(d.type));
  // 给每个 label 的 rect 设置基于文本的尺寸
  linkLabelGroup.selectAll('g.link-label').each(function() {
    const t = d3.select(this).select('text').node();
    if (!t) return;
    const bb = t.getBBox();
    d3.select(this).select('rect')
      .attr('x', bb.x - 3).attr('y', bb.y - 1)
      .attr('width', bb.width + 6).attr('height', bb.height + 2);
  });

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
    .attr('r', radiusOf)
    .attr('fill', d => LABEL_COLORS[d.labels && d.labels[0]] || '#94A3B8')
    .attr('stroke', '#FFFFFF').attr('stroke-width', 2.5)
    .on('mouseover', showTooltip)
    .on('mouseout', hideTooltip)
    .on('click', (event, d) => showNodeDetail(d));

  // 节点标签：只显示文字，不再绘制白底胶囊，避免空标签产生无用白框
  const labelG = nodesEnter.append('g').attr('class', 'node-label').attr('pointer-events', 'none');
  labelG.append('text')
    .attr('text-anchor', 'middle')
    .attr('font-size', 11).attr('font-weight', 700)
    .attr('font-family', 'Fira Sans, Microsoft YaHei, sans-serif')
    .attr('fill', '#0F172A')
    .attr('paint-order', 'stroke')
    .attr('stroke', 'rgba(255,255,255,0.9)')
    .attr('stroke-width', 3)
    .attr('stroke-linejoin', 'round')
    .text(d => {
      const name = (d.properties && d.properties.name) || d.id || '';
      return name.length > 12 ? name.substring(0, 11) + '…' : name;
    });

  nodes.select('circle')
    .attr('r', radiusOf)
    .attr('fill', d => LABEL_COLORS[d.labels && d.labels[0]] || '#94A3B8');
  nodes.select('g.node-label text').text(d => {
    const name = (d.properties && d.properties.name) || d.id || '';
    return name.length > 12 ? name.substring(0, 11) + '…' : name;
  });

  // 重新计算节点 label 偏移（圆下方）
  nodeGroup.selectAll('g.node').each(function(d) {
    const sel = d3.select(this);
    const t = sel.select('g.node-label text').node();
    if (!t) return;
    const r = radiusOf(d);
    const dy = r + 16;
    sel.select('g.node-label text').attr('y', dy);
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
  try {
    const resp = await fetch(`${API_BASE}/subgraph?limit=5000`);
    const data = await resp.json();
    updateGraph(data);
  } catch (e) {
    console.error('Full graph load failed:', e);
  }
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
      _lastCompareData = data;
      el.innerHTML = `<div style="white-space:pre-wrap;font-size:12px;line-height:1.8;font-family:var(--font-mono)">${simpleMd(data.comparison || 'No result')}</div>`;
    } else {
      _lastCompareData = null;
      el.innerHTML = '<div style="color:var(--err)">对比失败</div>';
    }
  } catch (e) {
    _lastCompareData = null;
    el.innerHTML = `<div style="color:var(--err)">请求失败: ${e.message}</div>`;
  }
}

/* ---- Export: PDF (backend download) ---- */
async function exportPdf() {
  if (!_lastCompareData) { alert('请先生成对比报告'); return; }

  const btn = document.getElementById('compare-export-pdf');
  if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }

  try {
    const resp = await fetch(`${API_BASE}/compare/export/pdf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entity_a: _lastCompareData.entity_a,
        entity_b: _lastCompareData.entity_b,
        common_relations: _lastCompareData.common_relations,
        comparison: _lastCompareData.comparison
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: '未知错误' }));
      alert('导出失败: ' + (err.detail || resp.statusText));
      return;
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const disp = resp.headers.get('Content-Disposition');
    let filename = 'comparison_report.pdf';
    if (disp) {
      const match = disp.match(/filename="?(.+?)"?$/);
      if (match) filename = match[1];
    }
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  } catch (e) {
    alert('导出请求失败: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '导出 PDF'; }
  }
}

/* ---- Export: DOCX ---- */
async function exportDocx() {
  if (!_lastCompareData) { alert('请先生成对比报告'); return; }

  const btn = document.getElementById('compare-export-docx');
  if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }

  try {
    const resp = await fetch(`${API_BASE}/compare/export/docx`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entity_a: _lastCompareData.entity_a,
        entity_b: _lastCompareData.entity_b,
        common_relations: _lastCompareData.common_relations,
        comparison: _lastCompareData.comparison
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: '未知错误' }));
      alert('导出失败: ' + (err.detail || resp.statusText));
      return;
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const disp = resp.headers.get('Content-Disposition');
    let filename = 'comparison_report.docx';
    if (disp) {
      const match = disp.match(/filename="?(.+?)"?$/);
      if (match) filename = match[1];
    }
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  } catch (e) {
    alert('导出请求失败: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '导出 DOCX'; }
  }
}
