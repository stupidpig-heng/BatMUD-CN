/**
 * BatMUD CN - Web Terminal Client
 * WebSocket communication + HTML rendering + command input
 */

(function() {
  'use strict';

  // DOM
  const outputEl = document.getElementById('output');
  const inputEl = document.getElementById('cmd-input');
  const terminalEl = document.getElementById('terminal');
  const statusInd = document.getElementById('status-indicator');
  const statusText = document.getElementById('status-text');
  const statusInfo = document.getElementById('status-info');
  const debugPanel = document.getElementById('debug-panel');
  const debugOutput = document.getElementById('debug-output');
  const debugToggle = document.getElementById('debug-toggle');
  const debugClear = document.getElementById('debug-clear');
  const statusHud = document.getElementById('status-hud');
  const hudContent = document.getElementById('hud-content');

  // State
  let ws = null;
  let connected = false;
  let reconnectTimer = null;
  let reconnectDelay = 2000;

  // Command history
  const history = [];
  let historyIdx = -1;

  // ---- WebSocket ----

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    setStatus('connecting', 'Connecting...');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws`;

    try { ws = new WebSocket(url); }
    catch (e) { setStatus('error', 'Not supported'); scheduleReconnect(); return; }

    ws.onopen = () => {
      connected = true;
      setStatus('connected', 'Connected');
      reconnectDelay = 2000;
      statusInfo.textContent = 'BatMUD CN | ' + location.host;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (e) {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      connected = false;
      setStatus('error', 'Disconnected');
      scheduleReconnect();
    };

    ws.onerror = () => { /* onclose fires next */ };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    statusInfo.textContent = 'Reconnecting in ' + (reconnectDelay / 1000) + 's...';
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
      connect();
    }, reconnectDelay);
  }

  function setStatus(cls, text) {
    statusInd.className = 'status-' + cls;
    statusText.textContent = text;
  }

  // ---- Message handler ----

  function handleMessage(msg) {
    switch (msg.type) {
      case 'html':
        if (msg.data) appendHtml(msg.data);
        break;

      case 'debug':
        if (msg.data) appendDebug(msg.data);
        break;

      case 'status':
        if (msg.data === 'connected') {
          setStatus('connected', 'Connected');
        } else if (msg.data === 'disconnected') {
          setStatus('error', 'Server disconnected');
        }
        break;

      case 'text':
        if (msg.data) appendPlain(msg.data);
        break;

      case 'prompt':
        if (msg.data) updateStatusHud(msg.data);
        break;

      case 'error':
        appendPlain('\n[ERROR] ' + msg.data + '\n');
        break;
    }
  }

  // ---- Output ----

  function appendHtml(html) {
    if (!html) return;
    const line = document.createElement('div');
    line.className = 'line';
    line.innerHTML = html;
    outputEl.appendChild(line);
    scrollToBottom();
  }

  function appendPlain(text) {
    const line = document.createElement('div');
    line.className = 'line';
    line.textContent = text;
    outputEl.appendChild(line);
    scrollToBottom();
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      terminalEl.scrollTop = terminalEl.scrollHeight;
    });
  }

  // ---- Status HUD ----

  function updateStatusHud(promptText) {
    if (!promptText || !statusHud || !hudContent) return;

    // Parse stats from prompt: "Hp:318/318 Sp:25/25 Ep:183/183 Exp:356 >"
    var stats = [];
    var patterns = [
      { re: /Hp[:\s]*(\d+)\/(\d+)/i, label: 'HP', cls: 'hp' },
      { re: /Sp[:\s]*(\d+)\/(\d+)/i, label: 'SP', cls: 'sp' },
      { re: /Ep[:\s]*(\d+)\/(\d+)/i, label: 'EP', cls: 'ep' },
      { re: /Exp[:\s]*(\d+)/i,   label: 'EXP', cls: 'exp', maxOnly: true },
      { re: /Tnl[:\s]*(\d+)/i,   label: 'TNL', cls: 'tnl', maxOnly: true },
    ];

    patterns.forEach(function(p) {
      var m = promptText.match(p.re);
      if (m) {
        var cur = parseInt(m[1], 10);
        var max = p.maxOnly ? cur : (m[2] ? parseInt(m[2], 10) : cur);
        stats.push({ label: p.label, cls: p.cls, current: cur, max: max, maxOnly: p.maxOnly });
      }
    });

    if (stats.length === 0) {
      statusHud.classList.remove('visible');
      return;
    }

    var html = '';
    stats.forEach(function(s) {
      var pct = s.max > 0 ? s.current / s.max : 0;
      var barCls = s.cls;
      if (!s.maxOnly && pct <= 0.3) barCls = 'danger';
      else if (!s.maxOnly && pct <= 0.6) barCls = 'warn';

      html += '<div class="hud-stat">';
      html += '<span class="hud-label ' + s.cls + '">' + s.label + '</span>';
      if (s.maxOnly) {
        html += '<span class="hud-value">' + s.current.toLocaleString() + '</span>';
      } else {
        html += '<span class="hud-value">' + s.current + '/' + s.max + '</span>';
        html += '<div class="hud-bar"><div class="hud-bar-fill ' + barCls + '" style="width:' + (pct * 100).toFixed(0) + '%"></div></div>';
      }
      html += '</div>';
    });

    hudContent.innerHTML = html;
    statusHud.classList.add('visible');
  }

  // ---- Debug Panel ----

  let debugEnabled = false;
  const MAX_DEBUG_ENTRIES = 200;

  debugToggle.addEventListener('click', () => {
    debugEnabled = !debugEnabled;
    debugPanel.style.display = debugEnabled ? 'flex' : 'none';
    debugToggle.classList.toggle('active', debugEnabled);
    // 打开时滚到底部
    if (debugEnabled) {
      debugOutput.scrollTop = debugOutput.scrollHeight;
    }
  });

  debugClear.addEventListener('click', () => {
    debugOutput.innerHTML = '';
  });

  function appendDebug(d) {
    // 始终缓存条目，不受 toggle 状态影响
    const entry = document.createElement('div');
    entry.className = 'debug-entry';

    let html = '';

    // Mode tag
    const modeLabel = d.mode === 'passthrough' ? '透传' : '翻译';
    const modeColor = d.mode === 'passthrough' ? '#666' : '#44cc44';
    html += `<span class="debug-mode" style="color:${modeColor}">[${modeLabel}]</span> `;

    // Skip reason
    if (d.skip) {
      const reasons = [];
      if (d.is_prompt) reasons.push('prompt');
      if (d.text && d.text.trim().length < 4) reasons.push('short');
      html += `<span class="debug-skip">跳过:${reasons.join(',')}</span> `;
    }

    // ANSI count
    html += `<span class="debug-ansi">ANSI:${d.ansi_count}</span> `;

    // Primary style
    if (d.primary_style) {
      const ps = d.primary_style;
      html += `<span class="debug-style">主色: fg=${ps.fg}`;
      if (ps.bg) html += ` bg=${ps.bg}`;
      if (ps.bold) html += ' B';
      if (ps.underline) html += ' U';
      html += ` css="${ps.css_class || '(none)'}"`;
      html += `</span> `;
    }

    // Text preview
    if (d.text) {
      const preview = d.text.length > 40 ? d.text.substring(0, 40) + '...' : d.text;
      html += `<span class="debug-text" title="${escapeHtml(d.text)}">"${escapeHtml(preview)}"</span> `;
    }

    // Translation
    if (d.translated) {
      const tpreview = d.translated.length > 30 ? d.translated.substring(0, 30) + '...' : d.translated;
      html += `<span class="debug-trans">→ "${escapeHtml(tpreview)}"</span> `;
    }

    // Raw hex (abbreviated)
    if (d.raw_hex && d.raw_hex.length > 80) {
      html += `<span class="debug-raw" title="${escapeHtml(d.raw_hex)}">hex:${escapeHtml(d.raw_hex.substring(0, 80))}...</span>`;
    } else if (d.raw_hex) {
      html += `<span class="debug-raw" title="${escapeHtml(d.raw_hex)}">hex:${escapeHtml(d.raw_hex)}</span>`;
    }

    // HTML output preview
    if (d.html) {
      const hpreview = d.html.length > 60 ? d.html.substring(0, 60) + '...' : d.html;
      html += `<div class="debug-html" title="${escapeHtml(d.html)}">→ ${escapeHtml(hpreview)}</div>`;
    }

    entry.innerHTML = html;
    debugOutput.appendChild(entry);

    // Limit entries
    while (debugOutput.children.length > MAX_DEBUG_ENTRIES) {
      debugOutput.firstChild.remove();
    }

    // Auto-scroll debug panel (only when visible)
    if (debugEnabled) {
      debugOutput.scrollTop = debugOutput.scrollHeight;
    }
  }

  // ---- Input ----

  function sendCommand(cmd) {
    if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
      appendPlain('\n[Offline - cannot send]\n');
      return;
    }

    ws.send(JSON.stringify({ type: 'cmd', data: cmd + '\r\n' }));

    // Echo locally
    const echoLine = document.createElement('div');
    echoLine.className = 'line';
    echoLine.innerHTML = '<span style="color:#4da6ff">&gt;</span> ' + escapeHtml(cmd);
    outputEl.appendChild(echoLine);
    scrollToBottom();
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = inputEl.value;
      if (cmd.trim()) {
        sendCommand(cmd);
        history.push(cmd);
        if (history.length > 500) history.shift();
        historyIdx = history.length;
      }
      inputEl.value = '';
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length === 0) return;
      if (historyIdx === history.length) history.push(inputEl.value);
      if (historyIdx > 0) { historyIdx--; inputEl.value = history[historyIdx]; }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIdx < history.length - 1) {
        historyIdx++;
        inputEl.value = history[historyIdx];
      } else {
        historyIdx = history.length;
        inputEl.value = '';
      }
    }
  });

  // Focus
  terminalEl.addEventListener('click', () => inputEl.focus());
  inputEl.focus();

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'l') { e.preventDefault(); outputEl.innerHTML = ''; }
    if (document.activeElement !== inputEl &&
        e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      inputEl.focus();
    }
  });

  // Start
  connect();
})();
