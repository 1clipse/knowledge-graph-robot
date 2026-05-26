/* ============================================================
   Ingest Module — Text / file upload, log management, delete
   ============================================================ */

const INGEST_API = '/api/v1/ingest';
let _knownFiles = [];

async function ingestText() {
  const textarea = document.getElementById('ingestText');
  const statusEl = document.getElementById('ingestStatus');
  const text = textarea.value.trim();
  if (!text) { statusEl.className = 'ingest-status error'; statusEl.textContent = '请输入文本'; return; }
  if (text.length < 10) { statusEl.className = 'ingest-status error'; statusEl.textContent = '文本太短 (最少10个字符)'; return; }

  statusEl.className = 'ingest-status'; statusEl.textContent = '分析中...';
  try {
    const resp = await fetch(`${INGEST_API}/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, use_llm: true, use_rule_fallback: true })
    });
    const data = await resp.json();
    if (data.status === 'success') {
      statusEl.className = 'ingest-status success';
      statusEl.textContent = `成功 — ${data.entities_count} 实体, ${data.relations_count} 关系`;
      textarea.value = '';
      loadStats();
      setTimeout(loadFullGraph, 500);
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = '失败: ' + (data.message || '未知');
    }
  } catch (e) {
    statusEl.className = 'ingest-status error'; statusEl.textContent = '请求失败: ' + e.message;
  }
}

async function ingestFile(input) {
  const statusEl = document.getElementById('ingestStatus');
  const files = Array.from(input.files);
  if (files.length === 0) return;

  const useLLM = document.getElementById('ingestUseLLM').checked;

  // Batch upload for multiple files
  if (files.length > 1) {
    // Dedup: warn about already-uploaded files
    const dupNames = files.filter(f => _knownFiles.includes(f.name)).map(f => f.name);

    if (dupNames.length > 0) {
      const ok = confirm(
        `${dupNames.length} 个文件已上传过：\n${dupNames.join('\n')}\n\n是否继续上传？`
      );
      if (!ok) { statusEl.className = 'ingest-status'; statusEl.textContent = '已取消'; input.value = ''; return; }
    }

    statusEl.className = 'ingest-status';
    statusEl.textContent = `批量上传 ${files.length} 个文件...`;

    const formData = new FormData();
    files.forEach(f => formData.append('files', f));

    const startTime = Date.now();
    try {
      const resp = await fetch(`${INGEST_API}/batch?use_llm=${useLLM}`, {
        method: 'POST', body: formData
      });
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      const data = await resp.json();
      if (data.status === 'success') {
        statusEl.className = 'ingest-status success';
        statusEl.textContent = `完成 (${elapsed}秒) — ${data.success_count}/${data.total_files} 成功, ${data.total_entities} 实体, ${data.total_relations} 关系`;
        if (data.failed_count > 0) {
          const failed = data.details.filter(d => d.status === 'failed').map(d => d.filename);
          statusEl.textContent += ` | 失败: ${failed.join(', ')}`;
        }
        input.value = '';
        loadStats();
        loadIngestLogs();
        setTimeout(loadFullGraph, 500);
      } else {
        statusEl.className = 'ingest-status error';
        statusEl.textContent = '批量上传失败';
      }
    } catch (e) {
      statusEl.className = 'ingest-status error';
      statusEl.textContent = '请求失败: ' + e.message;
    }
    return;
  }

  // Single file — existing logic
  const file = files[0];

  if (_knownFiles.length > 0 && _knownFiles.indexOf(file.name) !== -1) {
    const ok = confirm('文件 "' + file.name + '" 已上传过，是否继续上传？');
    if (!ok) { statusEl.className = 'ingest-status'; statusEl.textContent = '已取消'; input.value = ''; return; }
  }

  const prefix = useLLM ? '处理中' : '快速处理';
  statusEl.className = 'ingest-status'; statusEl.textContent = prefix + ' ' + file.name + ' (0秒)...';

  const startTime = Date.now();
  const timerId = setInterval(() => {
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    statusEl.textContent = prefix + ' ' + file.name + ' (' + elapsed + '秒)...';
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
      statusEl.textContent = `完成 (${elapsed}秒) — ${data.entities_count} 实体, ${data.relations_count} 关系`;
      input.value = '';
      loadStats();
      loadIngestLogs();
      setTimeout(loadFullGraph, 500);
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = '失败: ' + (data.message || '未知');
    }
  } catch (e) {
    clearTimeout(timeoutId);
    clearInterval(timerId);
    if (e.name === 'AbortError') {
      statusEl.className = 'ingest-status error'; statusEl.textContent = '超时 (>5分钟)。尝试关闭AI提取。';
    } else {
      statusEl.className = 'ingest-status error'; statusEl.textContent = '上传失败: ' + (e.message || '网络错误');
    }
  }
}

// ---- Ingest Logs ----
async function loadIngestLogs() {
  const el = document.getElementById('logList');
  el.innerHTML = '<div style="font-size:11px;color:var(--text-dim);padding:6px">加载中...</div>';
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
    el.innerHTML = '<div style="font-size:11px;color:var(--err);padding:6px">加载失败 — 检查后端</div>';
    return;
  }
  if (items.length === 0) {
    el.innerHTML = '<div style="font-size:11px;color:var(--text-dim);padding:6px">暂无上传记录</div>';
    return;
  }

  el.innerHTML = '';
  items.forEach(log => {
    const row = document.createElement('div');
    row.className = 'log-item';
    const fn = log.filename || 'Unknown';
    const ts = log.timestamp ? log.timestamp.replace('T', ' ').substring(0, 19) : '';
    row.innerHTML = `<div class="log-info"><div class="log-fn">${escapeHtml(fn)}</div><div class="log-meta">${ts || '历史'} · entities ${log.entities_count || 0} rels ${log.relations_count || 0}</div></div>`;
    const delBtn = document.createElement('button');
    delBtn.className = 'log-del';
    delBtn.textContent = 'DEL';
    delBtn.onclick = () => { if (confirm('删除 "' + fn + '" 的所有数据？此操作不可撤销。')) deleteByFile(fn); };
    row.appendChild(delBtn);
    el.appendChild(row);
  });
  _knownFiles = items.map(x => x.filename);
}

async function deleteByFile(filename) {
  try {
    const resp = await fetch(`${INGEST_API}/file/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    const data = await resp.json();
    alert('已删除 ' + (data.nodes_removed || 0) + ' 个节点');
    loadIngestLogs();
    loadStats();
    loadFullGraph();
  } catch (e) { alert('删除失败: ' + e.message); }
}

async function deleteNode(label, name) {
  if (!confirm('删除节点 "' + name + '" (' + label + ') 及其所有关系？')) return;
  try {
    const resp = await fetch(`${API_BASE}/query/node/${encodeURIComponent(label)}/${encodeURIComponent(name)}`, { method: 'DELETE' });
    const data = await resp.json();
    document.getElementById('nodeDetail').innerHTML = `<div style="color:var(--text-dim);font-family:var(--font-mono)">Deleted (${data.nodes_removed || 0} nodes)</div>`;
    loadStats();
    if (currentNodes.length > 0 && currentLinks.length > 0) updateGraph({ nodes: currentNodes, edges: currentLinks });
  } catch (e) { alert('删除失败: ' + e.message); }
}
