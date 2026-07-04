/*
 * ARIA — ArchPi's conversational assistant, present on every page.
 *
 * A floating animated orb (ElevenLabs-style) opens a chat panel that works
 * by TEXT or by VOICE: the mic button listens (Web Speech API, understands
 * English / Hindi / Hinglish) and every ARIA reply is spoken aloud with a
 * natural English voice — a real back-and-forth conversation.
 *
 * Messages go to /api/assistant (Qwen2.5-72B → DeepSeek → Gemini) together
 * with the current page, the global active building and a snapshot of the
 * visible page data. The model answers in a strict JSON schema whose
 * "actions" this file executes: set the active building, navigate, launch
 * research, locate a structure on the globe, or start an FEA simulation.
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
    var MUTE_KEY = 'archpi.aria.muted';
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
        /* --- the orb (ElevenLabs-style living gradient sphere) --- */
        '#aria-fab{position:fixed;bottom:88px;right:24px;z-index:9000;width:64px;height:64px;border-radius:50%;',
        ' border:none;cursor:pointer;padding:0;background:transparent;filter:drop-shadow(0 10px 26px rgba(114,90,61,.45));',
        ' transition:transform .25s}',
        '#aria-fab:hover{transform:translateY(-3px) scale(1.06)}',
        '.aria-orb{position:relative;width:64px;height:64px;border-radius:50%;overflow:hidden;',
        ' background:radial-gradient(circle at 32% 28%,#fff8f4 0%,#e1c19d 22%,#bfa17f 48%,#725a3d 78%,#26221e 100%)}',
        '.aria-orb::before{content:"";position:absolute;inset:-40%;border-radius:50%;',
        ' background:conic-gradient(from 0deg,transparent 0deg,rgba(255,248,244,.55) 60deg,transparent 140deg,rgba(225,193,157,.5) 240deg,transparent 330deg);',
        ' animation:orbSwirl 6s linear infinite}',
        '.aria-orb::after{content:"";position:absolute;inset:0;border-radius:50%;',
        ' background:radial-gradient(circle at 66% 72%,rgba(38,34,30,.55) 0%,transparent 55%),',
        '  radial-gradient(circle at 30% 26%,rgba(255,255,255,.8) 0%,transparent 34%);',
        ' animation:orbBreathe 3.4s ease-in-out infinite}',
        '@keyframes orbSwirl{to{transform:rotate(360deg)}}',
        '@keyframes orbBreathe{0%,100%{opacity:.85;transform:scale(1)}50%{opacity:1;transform:scale(1.05)}}',
        /* orb state rings */
        '#aria-fab .ring{position:absolute;inset:-6px;border-radius:50%;border:2px solid transparent;pointer-events:none}',
        '#aria-fab.listening .ring{border-color:#c0392b;animation:ringPulse 1.1s ease-out infinite}',
        '#aria-fab.speaking .ring{border-color:#e1c19d;animation:ringPulse 1.5s ease-out infinite}',
        '#aria-fab.listening .aria-orb::before{animation-duration:1.6s}',
        '#aria-fab.speaking .aria-orb::before{animation-duration:2.6s}',
        '@keyframes ringPulse{0%{transform:scale(.92);opacity:.9}100%{transform:scale(1.28);opacity:0}}',
        /* --- panel --- */
        '#aria-panel{position:fixed;bottom:164px;right:24px;z-index:9001;width:396px;max-width:calc(100vw - 32px);',
        ' height:560px;max-height:calc(100vh - 190px);background:#fff8f4;border:1px solid #d1c4b8;border-radius:18px;',
        ' box-shadow:0 30px 70px -20px rgba(30,27,25,.5);display:none;flex-direction:column;overflow:hidden;',
        ' font-family:Inter,system-ui,sans-serif}',
        '#aria-panel.open{display:flex;animation:ariaIn .25s ease}',
        '@keyframes ariaIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}',
        '#aria-head{background:#26221e;color:#f7efeb;padding:13px 16px;display:flex;align-items:center;gap:11px}',
        '#aria-head .mini-orb{width:30px;height:30px;border-radius:50%;flex:none;',
        ' background:radial-gradient(circle at 32% 28%,#fff8f4 0%,#e1c19d 25%,#bfa17f 55%,#725a3d 100%);',
        ' animation:orbBreathe 3.4s ease-in-out infinite}',
        '#aria-head b{font-size:14px;letter-spacing:.04em;display:block}',
        '#aria-head small{color:#bfa17f;font-size:10px;letter-spacing:.12em;text-transform:uppercase}',
        '#aria-head .spacer{flex:1}',
        '#aria-mute{background:none;border:1px solid #4a423a;color:#bfa17f;border-radius:8px;width:32px;height:32px;',
        ' cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:15px}',
        '#aria-mute:hover{border-color:#bfa17f}',
        '#aria-state{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#e1c19d;min-height:13px}',
        '#aria-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:#faf2ee}',
        '.aria-m{max-width:86%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.55;white-space:pre-wrap;word-wrap:break-word}',
        '.aria-m.user{align-self:flex-end;background:#725a3d;color:#fff;border-bottom-right-radius:4px}',
        '.aria-m.bot{align-self:flex-start;background:#fff;border:1px solid #e2d5c6;color:#1e1b19;border-bottom-left-radius:4px}',
        '.aria-m.bot .act{display:inline-block;margin-top:6px;font-size:10.5px;font-weight:700;letter-spacing:.1em;',
        ' text-transform:uppercase;color:#725a3d;background:#f2e6d6;border:1px solid #e1c19d;border-radius:999px;padding:3px 10px;margin-right:4px}',
        '#aria-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid #e2d5c6;background:#fff8f4;align-items:center}',
        '#aria-input{flex:1;border:1px solid #d1c4b8;border-radius:12px;padding:10px 12px;font-size:13.5px;outline:none;',
        ' font-family:inherit;background:#fff;color:#1e1b19;min-width:0}',
        '#aria-input:focus{border-color:#725a3d}',
        '#aria-mic{width:42px;height:42px;flex:none;border-radius:50%;border:1px solid #d1c4b8;background:#fff;color:#725a3d;',
        ' cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .2s}',
        '#aria-mic:hover{border-color:#725a3d}',
        '#aria-mic.rec{background:#c0392b;border-color:#c0392b;color:#fff;animation:micPulse 1.1s infinite}',
        '@keyframes micPulse{0%,100%{box-shadow:0 0 0 0 rgba(192,57,43,.5)}50%{box-shadow:0 0 0 9px rgba(192,57,43,0)}}',
        '#aria-mic svg{width:19px;height:19px}',
        '#aria-send{background:#725a3d;color:#fff;border:none;border-radius:12px;padding:0 16px;height:42px;cursor:pointer;font-size:13px;font-weight:600}',
        '#aria-send:disabled{opacity:.5;cursor:default}',
        '.aria-typing{align-self:flex-start;display:flex;gap:4px;padding:12px 16px;background:#fff;border:1px solid #e2d5c6;border-radius:14px}',
        '.aria-typing i{width:7px;height:7px;border-radius:50%;background:#bfa17f;animation:ariaB 1.2s infinite}',
        '.aria-typing i:nth-child(2){animation-delay:.18s}.aria-typing i:nth-child(3){animation-delay:.36s}',
        '@keyframes ariaB{0%,80%,100%{transform:scale(.7);opacity:.5}40%{transform:scale(1.15);opacity:1}}',
        /* --- toast --- */
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

    /* ================= voice: output (TTS) ================= */
    function isMuted() { try { return localStorage.getItem(MUTE_KEY) === '1'; } catch (e) { return false; } }
    function setMuted(m) { try { localStorage.setItem(MUTE_KEY, m ? '1' : ''); } catch (e) {} }

    var fabEl = null, stateEl = null;
    function setOrbState(state, label) {
        if (fabEl) { fabEl.classList.remove('listening', 'speaking'); if (state) fabEl.classList.add(state); }
        if (stateEl) stateEl.textContent = label || '';
    }

    function pickVoice() {
        var voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
        return voices.find(function (v) { return /en[-_]IN/i.test(v.lang) && /female|neural|natural|online/i.test(v.name); })
            || voices.find(function (v) { return /en[-_]IN/i.test(v.lang); })
            || voices.find(function (v) { return /en[-_](GB|US)/i.test(v.lang) && /natural|neural|online/i.test(v.name); })
            || voices.find(function (v) { return /^en/i.test(v.lang); }) || null;
    }

    function speak(text) {
        if (isMuted() || !('speechSynthesis' in window) || !text) return;
        window.speechSynthesis.cancel();
        // strip emoji/symbols so the voice sounds human, not like it's reading a keyboard
        var clean = text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}⚡]/gu, '').replace(/\s+/g, ' ').trim();
        if (!clean) return;
        var u = new SpeechSynthesisUtterance(clean);
        u.rate = 1.02; u.pitch = 1.04;
        var v = pickVoice();
        if (v) u.voice = v;
        u.onstart = function () { setOrbState('speaking', 'Speaking…'); };
        u.onend = function () { setOrbState('', ''); };
        u.onerror = function () { setOrbState('', ''); };
        window.speechSynthesis.speak(u);
    }
    if ('speechSynthesis' in window) window.speechSynthesis.getVoices(); // warm the voice list

    /* ================= voice: input (speech recognition) ================= */
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var recog = null, recActive = false;

    function startListening() {
        if (!SR) return;
        if (recActive) { stopListening(); return; }
        window.speechSynthesis && window.speechSynthesis.cancel();
        recog = new SR();
        recog.lang = 'en-IN';            // handles English, Hindi and Hinglish speech
        recog.interimResults = true;
        recog.continuous = false;
        var input = document.getElementById('aria-input');
        var mic = document.getElementById('aria-mic');
        recActive = true;
        mic.classList.add('rec');
        setOrbState('listening', 'Listening…');
        input.placeholder = 'Listening… bol kar poochiye';

        var finalText = '';
        recog.onresult = function (ev) {
            var interim = '';
            for (var i = ev.resultIndex; i < ev.results.length; i++) {
                if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
                else interim += ev.results[i][0].transcript;
            }
            input.value = (finalText + ' ' + interim).trim();
        };
        recog.onend = function () {
            recActive = false;
            mic.classList.remove('rec');
            setOrbState('', '');
            input.placeholder = 'Ask anything — English ya Hindi…';
            if (input.value.trim()) send();   // spoken request goes straight to ARIA
        };
        recog.onerror = function (ev) {
            recActive = false;
            mic.classList.remove('rec');
            setOrbState('', '');
            if (ev.error === 'not-allowed') {
                bubble('bot', 'I need microphone permission for voice — click the mic icon in the address bar and allow it.');
            }
        };
        try { recog.start(); } catch (e) { recActive = false; mic.classList.remove('rec'); }
    }
    function stopListening() { if (recog) try { recog.stop(); } catch (e) {} }

    /* ================= UI ================= */
    function build() {
        fabEl = document.createElement('button');
        fabEl.id = 'aria-fab';
        fabEl.title = 'ARIA — talk to your ArchPi assistant';
        fabEl.innerHTML = '<span class="aria-orb"></span><span class="ring"></span>';
        document.body.appendChild(fabEl);

        var panel = document.createElement('div');
        panel.id = 'aria-panel';
        panel.innerHTML =
            '<div id="aria-head"><span class="mini-orb"></span>' +
            '<div><b>ARIA</b><small>Voice + chat · understands हिंदी & English</small>' +
            '<div id="aria-state"></div></div>' +
            '<span class="spacer"></span>' +
            '<button id="aria-mute" title="Toggle voice replies"></button></div>' +
            '<div id="aria-msgs"></div>' +
            '<div id="aria-input-row">' +
            '<button id="aria-mic" title="Speak to ARIA">' +
            '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/></svg></button>' +
            '<input id="aria-input" placeholder="Ask anything — English ya Hindi…" autocomplete="off">' +
            '<button id="aria-send">Send</button></div>';
        document.body.appendChild(panel);
        stateEl = panel.querySelector('#aria-state');

        function refreshMuteIcon() {
            document.getElementById('aria-mute').textContent = isMuted() ? '🔇' : '🔊';
        }
        refreshMuteIcon();
        document.getElementById('aria-mute').addEventListener('click', function () {
            setMuted(!isMuted());
            refreshMuteIcon();
            if (isMuted()) window.speechSynthesis && window.speechSynthesis.cancel();
        });

        fabEl.addEventListener('click', function () {
            var open = panel.classList.toggle('open');
            try { localStorage.setItem(OPEN_KEY, open ? '1' : ''); } catch (e) {}
            if (open) {
                renderHistory();
                document.getElementById('aria-input').focus();
            } else {
                window.speechSynthesis && window.speechSynthesis.cancel();
                stopListening();
            }
        });
        document.getElementById('aria-send').addEventListener('click', send);
        document.getElementById('aria-mic').addEventListener('click', startListening);
        if (!SR) document.getElementById('aria-mic').style.display = 'none';
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
        if (!msgs) return null;
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
            bubble('bot', 'Good day — ARIA online. Tap the mic and just talk to me, in English or Hindi: "research Hawa Mahal", "Taj Mahal par earthquake simulation chalao" — I\'ll handle the rest.');
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
        // Give the spoken reply a moment before the page changes
        if (nav) setTimeout(function () { window.location.href = nav; }, 1800);
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
        setOrbState('', 'Thinking…');

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
            speak(reply);
            execute(actions);
        })
        .catch(function (e) {
            typing.remove();
            bubble('bot', 'Connection problem: ' + e.message + '. Is the main server running?');
        })
        .finally(function () {
            busy = false;
            setOrbState('', '');
            document.getElementById('aria-send').disabled = false;
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
    else build();
})();
