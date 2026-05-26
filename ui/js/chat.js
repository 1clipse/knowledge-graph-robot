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
      <h2>Industrial Robot Knowledge Q&A</h2>
      <p>Retrieval-augmented answers from structured knowledge</p>
      <div class="ce-examples">
        <button onclick="chatExample(this)">FANUC M-20iA load capacity and precision?</button>
        <button onclick="chatExample(this)">ABB welding robot models?</button>
        <button onclick="chatExample(this)">KUKA robot control systems?</button>
        <button onclick="chatExample(this)">Compare FANUC vs ABB 6-axis robots</button>
        <button onclick="chatExample(this)">RV reducer vs harmonic reducer?</button>
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
  label.textContent = role === 'user' ? 'ASK' : 'ASSISTANT';
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
  card.innerHTML = '<div class="ctx-label">Retrieved Knowledge</div><pre>' + escapeHtml(contextText) + '</pre>';
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
  label.className = 'msg-label'; label.textContent = 'ASSISTANT';
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
            bubble.innerHTML = '<p style="color:var(--err)">Error: ' + escapeHtml(data.message) + '</p>';
          }
        } catch (e) {}
      }
    }
  } catch (e) {
    bubble.innerHTML = '<p style="color:var(--err)">Request failed: ' + escapeHtml(e.message) + '</p>';
  }

  if (cursor.parentNode) cursor.remove();
  if (fullText) {
    bubble.innerHTML = '<p>' + simpleMd(escapeHtml(fullText)) + '</p>';
  } else if (!bubble.innerHTML.includes('Request failed') && !bubble.innerHTML.includes('Error:')) {
    bubble.innerHTML = '<p style="color:var(--text-dim)">No response — check backend</p>';
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
