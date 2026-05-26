/* ============================================================
   Chat Module — Streaming Q&A with context cards
   ============================================================ */

const CHAT_API = '/api/v1/ask/stream';
let isStreaming = false;
let _chatInit = false;

function scrollChatBottom() {
  const el = document.getElementById('chat-messages');
  if (el) el.scrollTop = el.scrollHeight;
}

function showChatEmpty() {
  const el = document.getElementById('chat-messages');
  el.innerHTML = `
    <div class="chat-empty">
      <div class="ce-icon">&Sigma;</div>
      <h2>工业机器人知识问答</h2>
      <p>基于结构化知识的检索增强回答</p>
      <div class="ce-examples">
        <button onclick="chatExample(this)">FANUC M-20iA 负载与精度？</button>
        <button onclick="chatExample(this)">ABB 焊接机器人型号？</button>
        <button onclick="chatExample(this)">KUKA 机器人控制系统？</button>
        <button onclick="chatExample(this)">对比 FANUC 与 ABB 六轴机器人</button>
        <button onclick="chatExample(this)">RV减速器 vs 谐波减速器？</button>
      </div>
    </div>`;
}

function chatExample(btn) {
  document.getElementById('chatInput').value = btn.textContent.trim();
  chatSend();
}

function chatAddMsg(role, content) {
  const el = document.getElementById('chat-messages');
  const es = el.querySelector('.chat-empty'); if (es) es.remove();
  const row = document.createElement('div');
  row.className = 'msg-row ' + role;
  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = role === 'user' ? '提问' : '助手';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = content;
  row.appendChild(label);
  row.appendChild(bubble);
  el.appendChild(row);
  return row;
}

function chatAddContextCard(contextText) {
  const el = document.getElementById('chat-messages');
  const es = el.querySelector('.chat-empty'); if (es) es.remove();
  const card = document.createElement('div');
  card.className = 'context-card';
  card.innerHTML = '<div class="ctx-label">检索到的知识</div><pre>' + escapeHtml(contextText) + '</pre>';
  el.appendChild(card);
  return card;
}

async function chatSend() {
  if (isStreaming) return;
  const input = document.getElementById('chatInput');
  const btn = document.getElementById('chatSendBtn');
  const question = input.value.trim();
  if (!question) return;

  input.value = '';
  input.style.height = 'auto';
  isStreaming = true;
  btn.disabled = true;

  chatAddMsg('user', escapeHtml(question));
  scrollChatBottom();

  const row = document.createElement('div');
  row.className = 'msg-row assistant';
  const label = document.createElement('div');
  label.className = 'msg-label'; label.textContent = '助手';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  row.appendChild(label);
  row.appendChild(bubble);
  document.getElementById('chat-messages').appendChild(row);
  scrollChatBottom();

  const cursor = document.createElement('span');
  cursor.className = 'typing-cursor';
  bubble.appendChild(cursor);

  let fullText = '';
  let contextCard = null;

  try {
    const resp = await fetch(CHAT_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, top_k: 8 })
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const result = await reader.read();
      if (result.done) break;
      buf += decoder.decode(result.value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const json = line.slice(6);
        try {
          const data = JSON.parse(json);
          if (data.type === 'meta' && data.context && !contextCard) {
            contextCard = chatAddContextCard(data.context);
            scrollChatBottom();
          } else if (data.type === 'token') {
            fullText += data.content;
            bubble.innerHTML = '<p>' + simpleMd(escapeHtml(fullText)) + '</p>';
            bubble.appendChild(cursor);
            scrollChatBottom();
          } else if (data.type === 'done') {
            if (cursor.parentNode) cursor.remove();
          } else if (data.type === 'error') {
            bubble.innerHTML = '<p style="color:var(--err)">错误: ' + escapeHtml(data.message) + '</p>';
          }
        } catch (e) {}
      }
    }
  } catch (e) {
    bubble.innerHTML = '<p style="color:var(--err)">请求失败: ' + escapeHtml(e.message) + '</p>';
  }

  if (cursor.parentNode) cursor.remove();
  if (fullText) {
    bubble.innerHTML = '<p>' + simpleMd(escapeHtml(fullText)) + '</p>';
  } else if (!bubble.innerHTML.includes('Request failed') && !bubble.innerHTML.includes('Error:')) {
    bubble.innerHTML = '<p style="color:var(--text-dim)">无响应 — 检查后端</p>';
  }

  isStreaming = false;
  btn.disabled = false;
  scrollChatBottom();
}

// Auto-resize textarea
document.addEventListener('DOMContentLoaded', () => {
  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 130) + 'px';
    });
  }
});
