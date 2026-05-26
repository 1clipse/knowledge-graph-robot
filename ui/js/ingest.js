/* ============================================================
   Ingest Module — Text / file upload, log management, delete
   ============================================================ */

const INGEST_API = '/api/v1/ingest';
let _knownFiles = [];

async function ingestText() {
  const textarea = document.getElementById('ingestText');
  const statusEl = document.getElementById('ingestStatus');
  const text = textarea.value.trim();
  if (!text) { statusEl.className = 'ingest-status error'; statusEl.textContent = 'Please enter text'; return; }
  if (text.length < 10) { statusEl.className = 'ingest-status error'; statusEl.textContent = 'Text too short (min 10 chars)'; return; }

  statusEl.className = 'ingest-status'; statusEl.textContent = 'Analyzing...';
  try {
    const resp = await fetch(`${INGEST_API}/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, use_llm: true, use_rule_fallback: true })
    });
    const data = await resp.json();
    if (data.status === 'success') {
      statusEl.className = 'ingest-status success';
      statusEl.textContent = `OK — ${data.entities_count} entities, ${data.relations_count} relations`;
      textarea.value = '';
      loadStats();
      setTimeout(loadFullGraph, 500);
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = 'Failed: ' + (data.message || 'Unknown');
    }
  } catch (e) {
    statusEl.className = 'ingest-status error'; statusEl.textContent = 'Request failed: ' + e.message;
  }
}

async function ingestFile(input) {
  const statusEl = document.getElementById('ingestStatus');
  const file = input.files[0];
  if (!file) return;

  if (_knownFiles.length > 0 && _knownFiles.indexOf(file.name) !== -1) {
    const ok = confirm('File "' + file.name + '" already uploaded. Continue anyway?');
    if (!ok) { statusEl.className = 'ingest-status'; statusEl.textContent = 'Cancelled'; input.value = ''; return; }
  }

  const useLLM = document.getElementById('ingestUseLLM').checked;
  const prefix = useLLM ? 'Processing' : 'Fast processing';
  statusEl.className = 'ingest-status'; statusEl.textContent = prefix + ' ' + file.name + ' (0s)...';

  const startTime = Date.now();
  const timerId = setInterval(() => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    statusEl.textContent = prefix + ' ' + file.name + ' (' + elapsed + 's)...';
  }, 1000);

  const formData = new FormData();
  formData.append('file', file);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 300000);

  try {
    const resp = await fetch(`${INGEST_API}/file?use_llm=${useLLM}`, {
      method: 'POST', body: formData, signal: controller.signal
    });
    clearTimeout(timeoutId);
    clearInterval(timerId);

    const elapsed = Math.round((Date.now() - startTime) / 1000);
    const data = await resp.json();
    if (data.status === 'success') {
      statusEl.className = 'ingest-status success';
      statusEl.textContent = `Done (${elapsed}s) — ${data.entities_count} entities, ${data.relations_count} relations`;
      input.value = '';
      loadStats();
      setTimeout(loadFullGraph, 500);
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = 'Failed: ' + (data.message || 'Unknown');
    }
  } catch (e) {
    clearTimeout(timeoutId);
    clearInterval(timerId);
    if (e.name === 'AbortError') {
      statusEl.className = 'ingest-status error'; statusEl.textContent = 'Timeout (>5min). Try without AI extraction.';
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = 'Upload failed: ' + (e.message || 'Network error');
    }
  }
}

// ---- Ingest Logs ----
async function loadIngestLogs() {
  const el = document.getElementById('logList');
  el.innerHTML = '<div style="font-size:11px;color:var(--text-dim);padding:6px">Loading...</div>';
  const items = [];
  let errors = 0;

  try {
    const r1 = await fetch(`${INGEST_API}/files`);
    if (r1.ok) {
      const files = await r1.json();
      for (const f of files) {
        items.push({ filename: f.filename, entities_count: f.cnt, relations_count: 0, timestamp: '', success: true, source: 'file' });
      }
    } else errors++;
  } catch (e) { errors++; }

  try {
    const r2 = await fetch(`${INGEST_API}/logs?limit=30`);
    if (r2.ok) {
      const logs = await r2.json();
      for (const log of logs) {
        const dup = items.some(item => item.filename === log.filename);
        if (dup) {
          const existing = items.find(item => item.filename === log.filename);
          if (existing) existing.timestamp = existing.timestamp || log.timestamp;
        } else if (log.filename) {
          items.push({
            filename: log.filename, entities_count: log.entities_count || 0,
            relations_count: log.relations_count || 0, timestamp: log.timestamp || '',
            success: log.success, source: log.source || ''
          });
        }
      }
    } else errors++;
  } catch (e) { errors++; }

  if (errors >= 2) {
    el.innerHTML = '<div style="font-size:11px;color:var(--err);padding:6px">Load failed — check backend</div>';
    return;
  }
  if (items.length === 0) {
    el.innerHTML = '<div style="font-size:11px;color:var(--text-dim);padding:6px">No uploads yet</div>';
    return;
  }

  el.innerHTML = '';
  items.forEach(log => {
    const row = document.createElement('div');
    row.className = 'log-item';
    const fn = log.filename || 'Unknown';
    const ts = log.timestamp ? log.timestamp.replace('T', ' ').substring(0, 19) : '';
    row.innerHTML = `<div class="log-info"><div class="log-fn">${escapeHtml(fn)}</div><div class="log-meta">${ts || 'history'} · entities ${log.entities_count || 0} rels ${log.relations_count || 0}</div></div>`;
    const delBtn = document.createElement('button');
    delBtn.className = 'log-del';
    delBtn.textContent = 'DEL';
    delBtn.onclick = () => { if (confirm('Delete all data for "' + fn + '"? This cannot be undone.')) deleteByFile(fn); };
    row.appendChild(delBtn);
    el.appendChild(row);
  });
  _knownFiles = items.map(x => x.filename);
}

async function deleteByFile(filename) {
  try {
    const resp = await fetch(`${INGEST_API}/file/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    const data = await resp.json();
    alert('Deleted ' + (data.nodes_removed || 0) + ' nodes');
    loadIngestLogs();
    loadStats();
    loadFullGraph();
  } catch (e) { alert('Delete failed: ' + e.message); }
}

async function deleteNode(label, name) {
  if (!confirm('Delete node "' + name + '" (' + label + ') and all relations?')) return;
  try {
    const resp = await fetch(`${API_BASE}/query/node/${encodeURIComponent(label)}/${encodeURIComponent(name)}`, { method: 'DELETE' });
    const data = await resp.json();
    document.getElementById('nodeDetail').innerHTML = `<div style="color:var(--text-dim);font-family:var(--font-mono)">Deleted (${data.nodes_removed || 0} nodes)</div>`;
    loadStats();
    if (currentNodes.length > 0 && currentLinks.length > 0) updateGraph({ nodes: currentNodes, edges: currentLinks });
  } catch (e) { alert('Delete failed: ' + e.message); }
}
