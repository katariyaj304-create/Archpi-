/*
 * ARIA — ArchPi's conversational assistant, present on every page.
 *
 * A floating button opens a chat panel. Messages go to /api/assistant
 * (Qwen2.5-72B → DeepSeek → Gemini) together with the current page name,
 * the global active building and a trimmed snapshot of the visible page
 * data — so ARIA can answer questions about what's on screen. The model
 * answers in a strict JSON schema whose "actions" this file executes:
 * setting the active building, navigating, launching research, locating
 * a structure on the globe, or starting an FEA simulation.
 *
 * Also ships window.ArchPiToast + a fetch interceptor that shows a
 * "loading live data" pill whenever backend calls run long, so the slow
 * soil / weather / forensic fetches never look like a frozen page.
 */
(function () {
    'use strict';
    if (window.__ariaLoaded) return;
    window.__ariaLoaded = true;

    var HISTORY_KEY = 'archpi.aria.history';
    var OPEN_KEY = 'archpi.aria.open';
    var PAGE = (location.pathname.split('/').pop() || 'index.html').replace('.html', '') || 'index';

    /* ================= loading toast + fetch interceptor ================= */
    var toastEl = null, toastTimer = null, pendingFetches = 0;

    window.ArchPiToast = {
        show: function (msg) {
            if (!toastEl) {
                toastEl = document.createElement('div');
                toastEl.id = 'archpi-toast';
                toastEl.innerHTML = '<span class="aria-spin"></span><span id="archpi-toast-msg"></span>';
                document.body.appendChild(toastEl);
            }
            toastEl.querySelector('#archpi-toast-msg').textContent = msg || 'Loading live data…';
            toastEl.classList.add('on');
        },
        hide: function () { if (toastEl) toastEl.classList.remove('on'); }
    };

    var origFetch = window.fetch;
    window.fetch = function () {
        var url = String(arguments[0] || '');
        var tracked = /localhost:8000|localhost:8010|\/api\//.test(url) && !/\/api\/assistant/.test(url);
        if (tracked) {
            pendingFetches++;
            if (!toastTimer) {
                toastTimer = setTimeout(function () {
                    if (pendingFetches > 0) window.ArchPiToast.show('Fetching live data — the page is working, not frozen…');
                }, 800);
            }
        }
        var p = origFetch.apply(window, arguments);
        if (tracked && p && p.finally) {
            p.finally(function () {
                pendingFetches = Math.max(0, pendingFetches - 1);
                if (pendingFetches === 0) {
                    clearTimeout(toastTimer); toastTimer = null;
                    window.ArchPiToast.hide();
                }
            });
        }
        return p;
    };

    /* ================= styles ================= */
    var css = [
        '#aria-fab{position:fixed;bottom:88px;right:24px;z-index:9000;width:56px;height:56px;border-radius:50%;',
        ' background:#725a3d;color:#fff;border:2px solid #e1c19d;box-shadow:0 10px 30px rgba(30,27,25,.35);',
        ' cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .2s, box-shadow .2s}',
        '#aria-fab:hover{transform:translateY(-3px) scale(1.05);box-shadow:0 16px 36px rgba(30,27,25,.45)}',
        '#aria-fab svg{width:26px;height:26px}',
        '#aria-panel{position:fixed;bottom:156px;right:24px;z-index:9001;width:390px;max-width:calc(100vw - 32px);',
        ' height:540px;max-height:calc(100vh - 180px);background:#fff8f4;border:1px solid #d1c4b8;border-radius:16px;',
        ' box-shadow:0 30px 70px -20px rgba(30,27,25,.5);display:none;flex-direction:column;overflow:hidden;',
        ' font-family:Inter,system-ui,sans-serif}',
        '#aria-panel.open{display:flex;animation:ariaIn .25s ease}',
        '@keyframes ariaIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}',
        '#aria-head{background:#26221e;color:#f7efeb;padding:14px 18px;display:flex;align-items:center;gap:10px}',
        '#aria-head .dot{width:9px;height:9px;border-radius:50%;background:#7fbf7f;animation:ariaPulse 2s infinite}',
        '@keyframes ariaPulse{0%,100%{opacity:1}50%{opacity:.35}}',
        '#aria-head b{font-size:14px;letter-spacing:.04em}',
        '#aria-head small{color:#bfa17f;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;display:block}',
        '#aria-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:#faf2ee}',
        '.aria-m{max-width:86%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.55;white-space:pre-wrap;word-wrap:break-word}',
        '.aria-m.user{align-self:flex-end;background:#725a3d;color:#fff;border-bottom-right-radius:4px}',
        '.aria-m.bot{align-self:flex-start;background:#fff;border:1px solid #e2d5c6;color:#1e1b19;border-bottom-left-radius:4px}',
        '.aria-m.bot .act{display:inline-block;margin-top:6px;font-size:10.5px;font-weight:700;letter-spacing:.1em;',
        ' text-transform:uppercase;color:#725a3d;background:#f2e6d6;border:1px solid #e1c19d;border-radius:999px;padding:3px 10px;margin-right:4px}',
        '#aria-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid #e2d5c6;background:#fff8f4}',
        '#aria-input{flex:1;border:1px solid #d1c4b8;border-radius:10px;padding:10px 12px;font-size:13.5px;outline:none;',
        ' font-family:inherit;background:#fff;color:#1e1b19}',
        '#aria-input:focus{border-color:#725a3d}',
        '#aria-send{background:#725a3d;color:#fff;border:none;border-radius:10px;padding:0 16px;cursor:pointer;font-size:13px;font-weight:600}',
        '#aria-send:disabled{opacity:.5;cursor:default}',
        '.aria-typing{align-self:flex-start;display:flex;gap:4px;padding:12px 16px;background:#fff;border:1px solid #e2d5c6;border-radius:14px}',
        '.aria-typing i{width:7px;height:7px;border-radius:50%;background:#bfa17f;animation:ariaB 1.2s infinite}',
        '.aria-typing i:nth-child(2){animation-delay:.18s}.aria-typing i:nth-child(3){animation-delay:.36s}',
        '@keyframes ariaB{0%,80%,100%{transform:scale(.7);opacity:.5}40%{transform:scale(1.15);opacity:1}}',
        '#archpi-toast{position:fixed;top:76px;left:50%;transform:translate(-50%,-16px);z-index:9500;display:flex;',
        ' align-items:center;gap:10px;background:#26221e;color:#f7efeb;font:600 12.5px Inter,system-ui,sans-serif;',
        ' letter-spacing:.04em;padding:10px 18px;border-radius:999px;border:1px solid #725a3d;',
        ' box-shadow:0 12px 30px rgba(30,27,25,.4);opacity:0;pointer-events:none;transition:all .3s}',
        '#archpi-toast.on{opacity:1;transform:translate(-50%,0)}',
        '.aria-spin{width:14px;height:14px;border-radius:50%;border:2px solid #bfa17f;border-top-color:transparent;',
        ' animation:ariaSpin .8s linear infinite;display:inline-block}',
        '@keyframes ariaSpin{to{transform:rotate(360deg)}}'
    ].join('\n');
    var styleEl = document.createElement('style');
    styleEl.textContent = css;
    document.head.appendChild(styleEl);

    /* ================= UI ================= */
    function build() {
        var fab = document.createElement('button');
        fab.id = 'aria-fab';
        fab.title = 'ARIA — your ArchPi assistant';
        fab.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a2 2 0 0 1 2 2c0 .5-.2 1-.5 1.3L15 7h3a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3h3l1.5-1.7A2 2 0 0 1 12 2zm-4 9.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm8 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zM9 17h6a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2z"/></svg>';
        document.body.appendChild(fab);

        var panel = document.createElement('div');
        panel.id = 'aria-panel';
        panel.innerHTML =
            '<div id="aria-head"><span class="dot"></span><div><b>ARIA</b><small>ArchPi Assistant · Qwen 72B</small></div></div>' +
            '<div id="aria-msgs"></div>' +
            '<div id="aria-input-row">' +
            '<input id="aria-input" placeholder="Ask anything — English ya Hindi…" autocomplete="off">' +
            '<button id="aria-send">Send</button></div>';
        document.body.appendChild(panel);

        fab.addEventListener('click', function () {
            var open = panel.classList.toggle('open');
            try { localStorage.setItem(OPEN_KEY, open ? '1' : ''); } catch (e) {}
            if (open) {
                renderHistory();
                document.getElementById('aria-input').focus();
            }
        });
        document.getElementById('aria-send').addEventListener('click', send);
        document.getElementById('aria-input').addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
        });

        // Re-open automatically after an action navigated to a new page
        var wasOpen = '';
        try { wasOpen = localStorage.getItem(OPEN_KEY) || ''; } catch (e) {}
        if (wasOpen === '1') { panel.classList.add('open'); renderHistory(); }
    }

    /* ================= history ================= */
    function loadHistory() {
        try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch (e) { return []; }
    }
    function saveHistory(h) {
        try { localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(-16))); } catch (e) {}
    }

    function bubble(role, text, actions) {
        var msgs = document.getElementById('aria-msgs');
        var d = document.createElement('div');
        d.className = 'aria-m ' + (role === 'user' ? 'user' : 'bot');
        d.textContent = text;
        if (actions && actions.length) {
            var labels = { set_building: 'structure set', navigate: 'opening page', research: 'deep research',
                           find_on_globe: 'locating on globe', simulate: 'running simulation' };
            actions.forEach(function (a) {
                var chip = document.createElement('span');
                chip.className = 'act';
                chip.textContent = '⚡ ' + (labels[a.type] || a.type);
                d.appendChild(document.createElement('br'));
                d.appendChild(chip);
            });
        }
        msgs.appendChild(d);
        msgs.scrollTop = msgs.scrollHeight;
        return d;
    }

    function renderHistory() {
        var msgs = document.getElementById('aria-msgs');
        msgs.innerHTML = '';
        var h = loadHistory();
        if (!h.length) {
            bubble('bot', 'Good day — ARIA online. I operate all of ArchPi for you: say "research Hawa Mahal", "Taj Mahal par earthquake simulation chalao", or ask me about anything on this page.');
            return;
        }
        h.forEach(function (m) { bubble(m.role, m.content, m.actions); });
    }

    /* ================= action executor ================= */
    function execute(actions) {
        if (!actions || !actions.length) return;
        var nav = null;
        actions.forEach(function (a) {
            if (a.type === 'set_building' && a.name && window.ArchPiBuilding) ArchPiBuilding.set(a.name);
            if (a.type === 'navigate') nav = a.page + '.html';
            if (a.type === 'research' && a.name) {
                if (window.ArchPiBuilding) ArchPiBuilding.set(a.name);
                nav = 'analysis.html?q=' + encodeURIComponent(a.name);
            }
            if (a.type === 'find_on_globe' && a.name) {
                if (window.ArchPiBuilding) ArchPiBuilding.set(a.name);
                nav = 'globe.html?q=' + encodeURIComponent(a.name);
            }
            if (a.type === 'simulate' && a.name) {
                if (window.ArchPiBuilding) ArchPiBuilding.set(a.name);
                nav = 'simulation.html?q=' + encodeURIComponent(a.name) +
                      (a.disaster ? '&disaster=' + encodeURIComponent(a.disaster) : '');
            }
        });
        if (nav) setTimeout(function () { window.location.href = nav; }, 1100);
    }

    /* ================= send ================= */
    var busy = false;
    function send() {
        if (busy) return;
        var input = document.getElementById('aria-input');
        var text = input.value.trim();
        if (!text) return;
        input.value = '';
        busy = true;
        document.getElementById('aria-send').disabled = true;

        var history = loadHistory();
        history.push({ role: 'user', content: text });
        saveHistory(history);
        bubble('user', text);

        var msgs = document.getElementById('aria-msgs');
        var typing = document.createElement('div');
        typing.className = 'aria-typing';
        typing.innerHTML = '<i></i><i></i><i></i>';
        msgs.appendChild(typing);
        msgs.scrollTop = msgs.scrollHeight;

        var context = {
            page: PAGE,
            activeBuilding: (window.ArchPiBuilding && ArchPiBuilding.get()) || '',
            pageText: (document.body.innerText || '').replace(/\s+/g, ' ').substring(0, 2400)
        };

        origFetch('/api/assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: history.map(function (m) { return { role: m.role, content: m.content }; }), context: context })
        })
        .then(function (r) { return r.json(); })
        .then(function (d) {
            typing.remove();
            var reply = d.reply || d.error || 'Sorry — I could not reach my language models just now.';
            var actions = d.actions || [];
            history.push({ role: 'assistant', content: reply, actions: actions });
            saveHistory(history);
            bubble('bot', reply, actions);
            execute(actions);
        })
        .catch(function (e) {
            typing.remove();
            bubble('bot', 'Connection problem: ' + e.message + '. Is the main server running?');
        })
        .finally(function () {
            busy = false;
            document.getElementById('aria-send').disabled = false;
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
    else build();
})();
