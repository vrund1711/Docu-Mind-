// ── Upload ─────────────────────────────────────────────────────────────────
  const fileInput  = document.getElementById('fileInput');
  const uploadZone = document.getElementById('uploadZone');

  uploadZone.addEventListener('dragover',  e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
  uploadZone.addEventListener('dragleave', ()  => uploadZone.classList.remove('drag-over'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    handleFiles(e.dataTransfer.files);
  });

  fileInput.addEventListener('change', () => handleFiles(fileInput.files));

  async function handleFiles(files) {
    for (const file of files) {
      await uploadFile(file);
    }
  }

  async function uploadFile(file) {
    showProgress(true);
    const fd = new FormData();
    fd.append('file', file);

    try {
      const res = await fetch('/upload', { method: 'POST', body: fd });
      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error('The server stopped before finishing this upload. The PDF may be too large or complex; try a smaller or text-based PDF.');
      }
      if (!res.ok) throw new Error(data.error || `Upload failed (HTTP ${res.status})`);
      if (data.error) throw new Error(data.error);
      addDocToList(data.doc);
      showToast(`✅ "${data.doc.name}" ingested — ${data.doc.chunks} chunks`, 'success');
      hideWelcome();
    } catch (err) {
      showToast(`❌ ${err.message || 'Upload failed. Please try again.'}`, 'error');
    } finally {
      showProgress(false);
      fileInput.value = '';
    }
  }

  function showProgress(on) {
    document.getElementById('uploadProgress').style.display = on ? 'block' : 'none';
  }

  function addDocToList(doc) {
    const list = document.getElementById('docList');
    const noDocs = list.querySelector('.no-docs');
    if (noDocs) noDocs.remove();

    const ext = doc.name.split('.').pop().toUpperCase();
    const icon = ext === 'PDF' ? '📄' : '📝';

    const el = document.createElement('div');
    el.className = 'doc-item';
    el.dataset.id = doc.id;
    el.innerHTML = `
      <span class="doc-icon">${icon}</span>
      <div class="doc-info">
        <div class="doc-name" title="${doc.name}">${doc.name}</div>
        <div class="doc-meta">${doc.pages} page${doc.pages !== 1 ? 's' : ''} · ${doc.chunks} chunks · ${doc.size_kb} KB</div>
      </div>
      <span class="doc-badge">indexed</span>`;
    list.appendChild(el);
  }

  // ── Textarea auto-resize + char count ─────────────────────────────────────
  const textarea  = document.getElementById('question');
  const charCount = document.getElementById('charCount');

  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 140) + 'px';
    charCount.textContent = `${textarea.value.length}/1000`;
  });

  textarea.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });

  // ── Chat ───────────────────────────────────────────────────────────────────
  let isStreaming = false;

  function hideWelcome() {
    const w = document.getElementById('welcome');
    if (w) w.remove();
  }

  function useHint(el) {
    textarea.value = el.textContent;
    textarea.dispatchEvent(new Event('input'));
    textarea.focus();
  }

  function addMessage(role, text = '') {
    hideWelcome();
    const msgs = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = `msg ${role}`;

    const avatarEmoji = role === 'user' ? '👤' : '🤖';
    const roleLabel   = role === 'user' ? 'You' : 'DocuMind';

    div.innerHTML = `
      <div class="msg-avatar">${avatarEmoji}</div>
      <div class="msg-body">
        <div class="msg-role">${roleLabel}</div>
        <div class="msg-bubble" id="bubble-${Date.now()}">${text}</div>
      </div>`;

    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div.querySelector('.msg-bubble');
  }

  function addError(msg) {
    const msgs = document.getElementById('messages');
    const el = document.createElement('div');
    el.className = 'msg-error';
    el.textContent = msg;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function sendQuestion() {
    if (isStreaming) return;
    const q = textarea.value.trim();
    if (!q) return;

    textarea.value = '';
    textarea.style.height = 'auto';
    charCount.textContent = '0/1000';

    addMessage('user', q);

    isStreaming = true;
    document.getElementById('sendBtn').disabled = true;

    const bubble = addMessage('bot');
    bubble.innerHTML = '<span class="cursor"></span>';

    let fullText = '';
    let sourceText = '';
    let streamError = '';

    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Server error');
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (raw === '[DONE]') continue;

          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            continue;
          }
          if (parsed.error) {
            streamError = parsed.error;
            continue;
          }
          if (parsed.token) {
            const token = parsed.token;
            // Detect sources line
            if (token.includes('📎 **Sources:**')) {
              sourceText += token;
            } else {
              fullText += token;
            }
            renderBubble(bubble, fullText, sourceText);
          }
        }
      }

      if (streamError) throw new Error(streamError);
    } catch (err) {
      bubble.remove();
      addError(err.message);
    } finally {
      // Remove cursor
      const cursor = bubble.querySelector('.cursor');
      if (cursor) cursor.remove();
      isStreaming = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('messages').scrollTop = 99999;
    }
  }

  function renderBubble(bubble, text, sources) {
    let html = escapeHtml(text).replace(/\n/g, '<br>');
    if (sources) {
      const srcClean = sources.replace('📎 **Sources:**', '').trim();
      html += `<div class="sources">📎 Sources: ${escapeHtml(srcClean)}</div>`;
    }
    html += '<span class="cursor"></span>';
    bubble.innerHTML = html;
    document.getElementById('messages').scrollTop = 99999;
  }

  function escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Sidebar actions ────────────────────────────────────────────────────────
  async function clearHistory() {
    await fetch('/clear', { method: 'POST' });
    document.getElementById('messages').innerHTML = '';
    const welcomeDiv = document.createElement('div');
    welcomeDiv.className = 'welcome';
    welcomeDiv.id = 'welcome';
    welcomeDiv.innerHTML = `
      <div class="welcome-icon">🧠</div>
      <div class="welcome-title">DocuMind</div>
      <p class="welcome-text">Conversation cleared. Your documents are still loaded — ask away.</p>`;
    document.getElementById('messages').appendChild(welcomeDiv);
    showToast('🔄 Chat history cleared', 'success');
  }

  async function deleteAll() {
    if (!confirm('Delete all documents and clear everything?')) return;
    await fetch('/delete-all', { method: 'POST' });
    document.getElementById('docList').innerHTML = '<div class="no-docs">No documents yet.<br/>Upload a file to begin.</div>';
    document.getElementById('messages').innerHTML = '';
    const welcomeDiv = document.createElement('div');
    welcomeDiv.className = 'welcome';
    welcomeDiv.id = 'welcome';
    welcomeDiv.innerHTML = `
      <div class="welcome-icon">🧠</div>
      <div class="welcome-title">DocuMind</div>
      <p class="welcome-text">All documents removed. Upload a new file to begin.</p>`;
    document.getElementById('messages').appendChild(welcomeDiv);
    showToast('🗑 All documents removed', 'success');
  }

  // ── Toast ──────────────────────────────────────────────────────────────────
  function showToast(msg, type = '') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type} show`;
    setTimeout(() => t.classList.remove('show'), 3500);
  }

  // Load existing docs on page refresh
  window.addEventListener('DOMContentLoaded', async () => {
    try {
      const res = await fetch('/docs');
      const data = await res.json();
      if (data.docs && data.docs.length) {
        data.docs.forEach(addDocToList);
        hideWelcome();
      }
    } catch {}
  });