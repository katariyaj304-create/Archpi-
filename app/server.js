const express = require('express');
const cors = require('cors');
const path = require('path');
const dns = require('node:dns');
dns.setDefaultResultOrder('ipv4first');

require('dotenv').config();

// =========================================================
// BATCH IMAGE FETCHER — DuckDuckGo image search (Python subprocess)
// Takes an array of queries, returns { query: bestImageUrl, "query__all": [...] }
// =========================================================
function fetchImages(queries) {
    return new Promise((resolve) => {
        const { execFile } = require('child_process');
        execFile('python', ['image_search_agent.py', JSON.stringify(queries)],
            { timeout: 120000, maxBuffer: 4 * 1024 * 1024 }, (error, stdout, stderr) => {
            const start = Date.now();
            if (error) {
                console.error("Image Fetch Error:", error.message);
                logApiCall('DuckDuckGo', '/images', queries.join(' | '), 0, Date.now() - start, false);
                return resolve({});
            }
            try {
                const results = JSON.parse(stdout);
                if (results.error) {
                    console.error("Image Fetch Agent Error:", results.error);
                    return resolve({});
                }
                const found = Object.keys(results).filter(k => !k.endsWith('__all') && results[k]).length;
                console.log(`[Images] DuckDuckGo found ${found}/${queries.length} images`);
                logApiCall('DuckDuckGo', '/images', queries.join(' | '), stdout.length, Date.now() - start, true);
                resolve(results);
            } catch (e) {
                console.error("Image Fetch JSON Error:", e);
                resolve({});
            }
        });
    });
}

// Attach theme-relevant DuckDuckGo images to a research result:
// each material gets a shot of that material on/in the building, each
// timeline phase a period-correct photo, plus blueprint + hero shots.
async function attachResearchImages(result, buildingName) {
    const matQuery = (m) => (m.name + ' ' + buildingName + ' material texture closeup').replace(/\s+/g, ' ').trim();
    const timeQuery = (t) => {
        const year = (t.year && /\d{3,4}/.test(String(t.year))) ? String(t.year) : '';
        return (buildingName + ' ' + t.title + ' ' + year + ' historical photo').replace(/\s+/g, ' ').trim();
    };
    const blueprintQuery = buildingName + ' architectural drawing elevation blueprint';
    const heroQuery = buildingName + ' landmark iconic photograph';

    const imgQueries = [];
    (result.materials || []).forEach(m => imgQueries.push(matQuery(m)));
    (result.timeline || []).forEach(t => imgQueries.push(timeQuery(t)));
    imgQueries.push(blueprintQuery, heroQuery);

    const imageResults = await fetchImages(imgQueries);

    (result.materials || []).forEach(m => {
        m.image = imageResults[matQuery(m)] || '';
        m.imageAlternates = imageResults[matQuery(m) + '__all'] || [];
    });
    (result.timeline || []).forEach(t => {
        t.image = imageResults[timeQuery(t)] || '';
        t.imageAlternates = imageResults[timeQuery(t) + '__all'] || [];
    });
    if (result.blueprint) result.blueprint.image = imageResults[blueprintQuery] || '';
    result.heroImage = imageResults[heroQuery] || imageResults[blueprintQuery] || '';
    return result;
}

// =========================================================
// DUCKDUCKGO TEXT SEARCH (Python subprocess)
// =========================================================
function duckDuckGoTextSearch(queries) {
    return new Promise((resolve) => {
        // execFile passes the JSON as a raw argv entry — no shell, no quote
        // escaping, no injection via building names
        const { execFile } = require('child_process');
        execFile('python', ['search_agent.py', JSON.stringify(queries)], { timeout: 30000 }, (error, stdout, stderr) => {
            if (error) {
                console.error("DDG Search Error:", error.message);
                return resolve({});
            }
            try {
                const results = JSON.parse(stdout);
                resolve(results);
            } catch (e) {
                console.error("DDG Parse Error:", e, stdout);
                resolve({});
            }
        });
    });
}

const app = express();
app.use(cors());
app.use(express.json({ limit: '25mb' })); // photo uploads arrive as base64 JSON
app.use(express.static(path.join(__dirname, 'public')));

const TAVILY_API_KEY = process.env.TAVILY_API_KEY || '';
const LANGCHAIN_URL = 'http://127.0.0.1:5001';

// =========================================================
// API USAGE TRACKING — persisted to usage_data.json so counters
// and history survive server restarts (quota windows are real
// calendar days/months, not process lifetimes)
// =========================================================
const fs = require('fs');
const USAGE_FILE = path.join(__dirname, 'usage_data.json');

let usageData = { days: {}, months: {}, lastErrors: {}, log: [], totals: {} };
try {
    const loaded = JSON.parse(fs.readFileSync(USAGE_FILE, 'utf8'));
    usageData = Object.assign(usageData, loaded);
} catch (e) { /* first run — start fresh */ }

const apiUsageLog = usageData.log;
let totalTavilyCalls = usageData.totals.Tavily || 0;
let totalTokensEstimated = usageData.totals.tokens || 0;
let totalAnalyses = usageData.totals.analyses || 0;
let totalHfCalls = usageData.totals.HuggingFace || 0;
let totalDdgCalls = usageData.totals.DuckDuckGo || 0;

let usageSaveTimer = null;
function saveUsageData() {
    if (usageSaveTimer) return;
    usageSaveTimer = setTimeout(() => {
        usageSaveTimer = null;
        usageData.totals = {
            Tavily: totalTavilyCalls, HuggingFace: totalHfCalls, DuckDuckGo: totalDdgCalls,
            tokens: totalTokensEstimated, analyses: totalAnalyses
        };
        fs.writeFile(USAGE_FILE, JSON.stringify(usageData), (err) => {
            if (err) console.error('[Usage] persist failed:', err.message);
        });
    }, 2000);
}

function logApiCall(service, endpoint, query, responseSize, durationMs, success, errorMsg) {
    if (service === 'HuggingFace') totalHfCalls++;
    else if (service === 'DuckDuckGo') totalDdgCalls++;
    else if (service === 'Tavily') totalTavilyCalls++;
    totalTokensEstimated += Math.round(responseSize / 4);

    const day = new Date().toISOString().slice(0, 10);
    const month = day.slice(0, 7);
    const d = usageData.days[day] = usageData.days[day] || {};
    d[service] = (d[service] || 0) + 1;
    const m = usageData.months[month] = usageData.months[month] || {};
    m[service] = (m[service] || 0) + 1;
    if (!success) {
        usageData.lastErrors[service] = {
            message: String(errorMsg || 'request failed').substring(0, 200),
            at: new Date().toISOString()
        };
    }

    apiUsageLog.push({
        timestamp: new Date().toISOString(),
        service: service,
        endpoint: endpoint,
        query: query.substring(0, 80),
        responseSize: responseSize,
        durationMs: durationMs,
        success: success
    });
    if (apiUsageLog.length > 500) apiUsageLog.shift();
    saveUsageData();
}

// =========================================================
// TAVILY SEARCH HELPER
// =========================================================
async function tavilySearch(query) {
    const start = Date.now();
    try {
        const res = await fetch('https://api.tavily.com/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: TAVILY_API_KEY,
                query: query,
                search_depth: "basic",
                include_answer: true,
                max_results: 5
            })
        });
        const duration = Date.now() - start;
        if (!res.ok) {
            const errText = await res.text();
            logApiCall('Tavily', '/search', query, errText.length, duration, false);
            console.error('Tavily error:', errText);
            return null;
        }
        const data = await res.json();
        const size = JSON.stringify(data).length;
        logApiCall('Tavily', '/search', query, size, duration, true);
        return data;
    } catch (e) {
        logApiCall('Tavily', '/search', query, 0, Date.now() - start, false);
        console.error('Tavily fetch error:', e.message);
        return null;
    }
}

// =========================================================
// LANGCHAIN AGENT HELPERS
// =========================================================
async function callLangChainAgent(buildingName, researchText) {
    const start = Date.now();
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 330000); // deep-report generation can take minutes

        const res = await fetch(LANGCHAIN_URL + '/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                buildingName: buildingName,
                researchText: researchText.substring(0, 14000)
            }),
            signal: controller.signal
        });
        clearTimeout(timeout);

        const duration = Date.now() - start;

        if (!res.ok) {
            logApiCall('HuggingFace', '/extract', buildingName, 0, duration, false);
            console.log('LangChain agent returned status:', res.status);
            return null;
        }

        const data = await res.json();
        logApiCall('HuggingFace', '/extract', buildingName, JSON.stringify(data).length, duration, true);
        return data;
    } catch (e) {
        const duration = Date.now() - start;
        logApiCall('HuggingFace', '/extract', buildingName, 0, duration, false);
        console.log('LangChain agent unavailable:', e.message);
        return null;
    }
}

async function callLLMKnowledge(buildingName) {
    const start = Date.now();
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 240000);

        const res = await fetch(LANGCHAIN_URL + '/knowledge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ buildingName }),
            signal: controller.signal
        });
        clearTimeout(timeout);

        const duration = Date.now() - start;
        if (!res.ok) {
            logApiCall('HuggingFace', '/knowledge', buildingName, 0, duration, false);
            return null;
        }
        const data = await res.json();
        logApiCall('HuggingFace', '/knowledge', buildingName, JSON.stringify(data).length, duration, true);
        return data;
    } catch (e) {
        logApiCall('HuggingFace', '/knowledge', buildingName, 0, Date.now() - start, false);
        console.log('LLM Knowledge unavailable:', e.message);
        return null;
    }
}

async function callLLMReason(buildingName, findings) {
    const start = Date.now();
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 240000);

        const res = await fetch(LANGCHAIN_URL + '/reason', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ buildingName, findings: findings.substring(0, 6000) }),
            signal: controller.signal
        });
        clearTimeout(timeout);

        const duration = Date.now() - start;
        if (!res.ok) {
            logApiCall('HuggingFace', '/reason', buildingName, 0, duration, false);
            return null;
        }
        const data = await res.json();
        logApiCall('HuggingFace', '/reason', buildingName, JSON.stringify(data).length, duration, true);
        return data;
    } catch (e) {
        logApiCall('HuggingFace', '/reason', buildingName, 0, Date.now() - start, false);
        console.log('LLM Reason unavailable:', e.message);
        return null;
    }
}

async function getModelStatus() {
    try {
        const res = await fetch(LANGCHAIN_URL + '/status');
        if (res.ok) return await res.json();
    } catch (e) { /* agent not running */ }
    return { active: false, model: 'none', status: 'unavailable' };
}

// =========================================================
// BUILDING IMAGE ENDPOINT (PROXY TO AVOID CORS)
// =========================================================
app.get('/api/building-image', async (req, res) => {
    const bldg = req.query.q;
    if (!bldg) return res.status(400).json({ error: 'q parameter required' });
    try {
        const query = encodeURIComponent(bldg);
        const url = `https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch=${query}&gsrlimit=1&prop=pageimages&format=json&pithumbsize=1000`;
        // Wikimedia rejects requests without a User-Agent (403 robot policy)
        const wikiRes = await fetch(url, { headers: { 'User-Agent': 'ArchPi/1.0 (architectural research tool)' } });
        const data = await wikiRes.json();
        
        let imgUrl = null;
        if (data.query && data.query.pages) {
            const pages = data.query.pages;
            const pageId = Object.keys(pages)[0];
            if (pages[pageId].thumbnail) {
                imgUrl = pages[pageId].thumbnail.source;
            }
        }
        
        if(!imgUrl) return res.status(404).send('Not found');
        
        // Proxy the image to bypass canvas CORS issues
        const https = require('https');
        const http = require('http');
        const client = imgUrl.startsWith('https') ? https : http;

        client.get(imgUrl, { headers: { 'User-Agent': 'ArchPi/1.0 (architectural research tool)', 'Accept': 'image/*,*/*;q=0.8' } }, (proxyRes) => {
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
            proxyRes.on('error', (e) => res.status(500).end());
        }).on('error', (e) => res.status(500).json({error: e.message}));

    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Cache image-agent lookups per building — avoids re-spawning Python and
// hammering the Wikipedia API (which rate-limits bursts) on every page load / retry
const blueprintCache = new Map();
const BLUEPRINT_CACHE_TTL = 60 * 60 * 1000; // 1 hour

app.get('/api/building-blueprint-image', async (req, res) => {
    const bldg = req.query.q;
    const idx = parseInt(req.query.idx || '0', 10); // optional: index into fallback URLs
    if (!bldg) return res.status(400).json({ error: 'q parameter required' });
    try {
        console.log(`[Blueprint] Searching image for: "${bldg}" (index=${idx})`);

        const cacheKey = bldg.toLowerCase().trim();
        const cached = blueprintCache.get(cacheKey);
        let imgResult;
        if (cached && (Date.now() - cached.at) < BLUEPRINT_CACHE_TTL && (cached.result.all || []).length > 0) {
            imgResult = cached.result;
            console.log(`[Blueprint] Cache hit (${imgResult.all.length} URLs)`);
        } else {
            // If the AI structural profile is already cached, use its canonical
            // name and category to sharpen the image search query
            const prof = profileCache.get(cacheKey);
            const category = (prof && prof.data && prof.data.category && prof.data.category !== 'other')
                ? prof.data.category : '';
            const searchName = (prof && prof.data && prof.data.name) ? prof.data.name : bldg;

            const runAgent = (script) => new Promise((resolve) => {
                const { execFile } = require('child_process');
                const args = script === 'sketch_agent.py' ? [script, searchName, category] : [script, bldg];
                execFile('python', args, { timeout: 35000 }, (error, stdout) => {
                    if (error) {
                        console.error(`[Blueprint] ${script} error:`, error.message);
                        return resolve({ url: '', all: [] });
                    }
                    try {
                        resolve(JSON.parse(stdout));
                    } catch (e) {
                        console.error(`[Blueprint] ${script} JSON parse error:`, e.message);
                        resolve({ url: '', all: [] });
                    }
                });
            });

            // Architectural sketch/elevation drawings first (Tavily + DuckDuckGo),
            // Wikipedia photos as fallback candidates after them
            const [sketch, wiki] = await Promise.all([
                runAgent('sketch_agent.py'),
                runAgent('image_agent.py'),
            ]);
            const merged = [...(sketch.all || []), ...(wiki.all || [])];
            imgResult = { url: merged[0] || '', all: merged.slice(0, 12) };
            console.log(`[Blueprint] ${sketch.all?.length || 0} sketches + ${wiki.all?.length || 0} photos found`);

            if (imgResult.all.length > 0) {
                blueprintCache.set(cacheKey, { result: imgResult, at: Date.now() });
            }
        }
        
        // Select URL based on index (for fallback rotation)
        let imgUrl = '';
        if (imgResult.all && imgResult.all.length > 0) {
            imgUrl = imgResult.all[Math.min(idx, imgResult.all.length - 1)];
        } else {
            imgUrl = imgResult.url;
        }
        
        if (!imgUrl) {
            return res.status(404).json({ error: 'No image found', searched: bldg });
        }
        
        console.log(`[Blueprint] Proxying image: ${imgUrl.substring(0, 100)}...`);
        
        // Proxy the image to bypass canvas CORS issues
        const urlParser = require('url');
        const parsedUrl = urlParser.parse(imgUrl);
        const protocol = parsedUrl.protocol === 'https:' ? require('https') : require('http');
        
        const options = {
            hostname: parsedUrl.hostname,
            path: parsedUrl.path,
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
            },
            timeout: 12000
        };

        const proxyReq = protocol.get(options, (proxyRes) => {
            // Handle redirects (up to 2 levels)
            if (proxyRes.statusCode >= 300 && proxyRes.statusCode < 400 && proxyRes.headers.location) {
                const redirectUrl = proxyRes.headers.location;
                console.log(`[Blueprint] Redirect → ${redirectUrl.substring(0, 80)}...`);
                const redirectParsed = urlParser.parse(redirectUrl);
                const redirectProtocol = redirectParsed.protocol === 'https:' ? require('https') : require('http');
                redirectProtocol.get({
                    hostname: redirectParsed.hostname,
                    path: redirectParsed.path,
                    headers: options.headers,
                    timeout: 12000
                }, (redRes) => {
                    res.writeHead(redRes.statusCode, redRes.headers);
                    redRes.pipe(res);
                }).on('error', (e) => res.status(500).end());
                return;
            }
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
        });
        
        proxyReq.on('error', (e) => {
            console.error(`[Blueprint] Proxy error: ${e.message}`);
            res.status(500).json({ error: e.message });
        });

    } catch (e) {
        console.error(`[Blueprint] Endpoint error: ${e.message}`);
        res.status(500).json({ error: e.message });
    }
});

// =========================================================
// API USAGE ENDPOINT
// =========================================================
app.get('/api/usage', (req, res) => {
    const successCalls = apiUsageLog.filter(l => l.success).length;
    const failedCalls = apiUsageLog.filter(l => !l.success).length;
    const avgDuration = apiUsageLog.length > 0
        ? Math.round(apiUsageLog.reduce((s, l) => s + l.durationMs, 0) / apiUsageLog.length)
        : 0;
    const totalDataProcessed = apiUsageLog.reduce((s, l) => s + l.responseSize, 0);

    res.json({
        summary: {
            totalApiCalls: totalTavilyCalls + totalHfCalls + totalDdgCalls,
            totalTavilyCalls: totalTavilyCalls,
            totalHuggingFaceCalls: totalHfCalls,
            totalDuckDuckGoCalls: totalDdgCalls,
            remainingCalls: 1000 - totalTavilyCalls,
            successfulCalls: successCalls,
            failedCalls: failedCalls,
            totalAnalyses: totalAnalyses,
            avgResponseTime: avgDuration + 'ms',
            totalDataProcessed: (totalDataProcessed / 1024).toFixed(1) + ' KB',
            estimatedTokens: totalTokensEstimated,
            uptime: Math.round(process.uptime()) + 's'
        },
        recentCalls: apiUsageLog.slice(-50).reverse()
    });
});

// =========================================================
// USAGE LIMITS & QUOTAS — per-provider consumption vs known
// free-tier limits, plus live OpenRouter credit balance
// =========================================================
const PROVIDER_QUOTAS = {
    Tavily:       { label: 'Tavily Web Search',            period: 'month', limit: 1000, unit: 'searches',   plan: 'Free plan — 1,000 API credits/month' },
    Gemini:       { label: 'Gemini 2.5 Flash (text+vision)', period: 'day', limit: 250,  unit: 'requests',   plan: 'Free tier — 250 requests/day, 10 req/min' },
    GeminiImage:  { label: 'Gemini Image Generation',      period: 'day',   limit: 0,    unit: 'images',     plan: 'Paid feature — zero quota on free tier' },
    HuggingFace:  { label: 'HF Router (DeepSeek / FLUX)',  period: 'month', limit: null, unit: 'calls',      plan: '~$0.10 included credits/month, then HTTP 402' },
    OpenRouter:   { label: 'OpenRouter (image model)',     period: 'live',  limit: null, unit: 'credits',    plan: 'Prepaid credits — live balance below' },
    DuckDuckGo:   { label: 'DuckDuckGo Search',            period: 'day',   limit: null, unit: 'searches',   plan: 'Free — soft rate limits only' },
    Pollinations: { label: 'Pollinations FLUX (fallback)', period: 'day',   limit: null, unit: 'images',     plan: 'Free public endpoint — no key, no hard quota' },
};

// OpenRouter live balance, cached so the dashboard doesn't burn requests
let openRouterCredits = { fetchedAt: 0, data: null };
async function getOpenRouterCredits() {
    if (!OPENROUTER_API_KEY) return null;
    if (Date.now() - openRouterCredits.fetchedAt < 5 * 60 * 1000) return openRouterCredits.data;
    try {
        const r = await fetch('https://openrouter.ai/api/v1/credits', {
            headers: { 'Authorization': `Bearer ${OPENROUTER_API_KEY}` },
            signal: AbortSignal.timeout(10000)
        });
        if (r.ok) {
            const d = await r.json();
            openRouterCredits = {
                fetchedAt: Date.now(),
                data: {
                    totalCredits: d.data?.total_credits ?? null,
                    totalUsage: d.data?.total_usage ?? null,
                    remaining: (d.data?.total_credits != null && d.data?.total_usage != null)
                        ? +(d.data.total_credits - d.data.total_usage).toFixed(4) : null
                }
            };
        }
    } catch (e) { /* keep stale/null data */ }
    return openRouterCredits.data;
}

app.get('/api/usage-limits', async (req, res) => {
    const day = new Date().toISOString().slice(0, 10);
    const month = day.slice(0, 7);
    const today = usageData.days[day] || {};
    const thisMonth = usageData.months[month] || {};
    const orCredits = await getOpenRouterCredits();

    const providers = Object.entries(PROVIDER_QUOTAS).map(([key, cfg]) => {
        const usedToday = today[key] || 0;
        const usedMonth = thisMonth[key] || 0;
        const used = cfg.period === 'day' ? usedToday : usedMonth;
        const lastErr = usageData.lastErrors[key] || null;
        // Quota exhaustion: a 402/429-style failure within the last 24h
        const exhausted = lastErr
            && /402|429|quota|credit|depleted|exceeded/i.test(lastErr.message)
            && (Date.now() - new Date(lastErr.at).getTime()) < 24 * 3600 * 1000;
        return {
            key,
            label: cfg.label,
            plan: cfg.plan,
            period: cfg.period,
            unit: cfg.unit,
            usedToday,
            usedThisMonth: usedMonth,
            limit: cfg.limit,
            remaining: cfg.limit != null ? Math.max(0, cfg.limit - used) : null,
            percentUsed: cfg.limit ? Math.min(100, Math.round(used / cfg.limit * 100)) : null,
            status: exhausted ? 'exhausted' : 'ok',
            lastError: lastErr,
            ...(key === 'OpenRouter' && orCredits ? { liveCredits: orCredits } : {})
        };
    });

    // Daily history, most recent 14 days that have any traffic
    const history = Object.keys(usageData.days).sort().slice(-14)
        .map(d => ({ date: d, services: usageData.days[d] }));

    res.json({ generatedAt: new Date().toISOString(), providers, history });
});

// =========================================================
// ARIA — CONVERSATIONAL ASSISTANT
// Qwen2.5-72B first (the 14B-1M variant is not offered by any
// HF router provider), DeepSeek second, Gemini as last resort.
// The model must answer with STRICT JSON: a reply plus a list of
// app actions the client executes (navigate, research, simulate…).
// =========================================================
const ASSISTANT_MODELS = ['Qwen/Qwen2.5-72B-Instruct', 'deepseek-ai/DeepSeek-V3.2'];

const ASSISTANT_SYSTEM = `You are ARIA (ArchPi Reasoning & Intelligence Assistant) — the built-in AI operator of ArchPi, an architectural-engineering diagnostic platform. Behave like a calm, ultra-competent personal assistant (think JARVIS): brief, precise, warm, never robotic filler.

LANGUAGE RULES
- Users may write in English, Hindi, or Hinglish. You must UNDERSTAND all three.
- You must ALWAYS answer in English only, no matter the input language.

THE APP YOU OPERATE (pages and what they do)
- index: home page with the main search box
- globe: world map; locates any structure by name
- analysis: autonomous deep research on the active building (web search + AI dossier)
- materials: the active building's materials list with images
- material-efficiency: stress-tests materials, including the active building's researched materials
- history: the active building's construction timeline
- simulation: real FEA physics stress-test of the active building (seismic / wind)
- soil: soil diagnostics at the active building's location (bearing capacity, seismic zone)
- weather: live weather stress at the active building's location (wind shear, carbonation)
- forensic: forensic materials lab (XRD, radiocarbon, dendro, hazards) for curated dossier sites
- api-usage: quotas, live credits and database storage dashboard

ONE GLOBAL SELECTION: the "active building" follows the user across every page. Setting it re-points the whole app at that structure.

ACTIONS YOU MAY EMIT (these are your hands — the client executes them in order)
1 {"type":"set_building","name":"<building name>"}        — set the global active structure
2 {"type":"navigate","page":"<one page id from the list above>","highlight":"<optional: exact 3-12 word phrase to highlight on that page>"}
3 {"type":"research","name":"<building name>"}            — set building + run deep research (analysis page)
4 {"type":"find_on_globe","name":"<building name>"}       — set building + locate it on the globe
5 {"type":"simulate","name":"<building name>","disaster":"seismic"|"wind"|""}  — set building + open the FEA simulation

POINTING AT THE SOURCE (very important)
When the user asks a question whose answer exists in the research data — CURRENT CONTEXT page data or the RESEARCH DOSSIER — do BOTH:
(a) answer it briefly in "reply", and
(b) emit {"type":"navigate","page":"<the page holding that answer>","highlight":"<the EXACT short phrase copied verbatim from that data>"} so the app scrolls to and highlights the source text on screen.
The highlight is matched against the page text, so keep it SHORT and DISTINCTIVE: a single name, date, number or 3-6 word phrase (e.g. "White Makrana Marble", "1632", "20,000 workers") — never join separate fields with dashes or commas. Materials facts live on "materials", timeline/dates on "history", specs/overview on "analysis", soil numbers on "soil", weather numbers on "weather". If the answer is already visible on the current page, still emit navigate to the same page with the highlight.

STRICT OUTPUT SCHEMA — YOU MUST FOLLOW THIS EXACTLY
Respond with ONE valid JSON object and NOTHING else. No markdown fences, no commentary outside JSON:
{"reply":"<what you say to the user, English, 1-4 sentences>","actions":[<zero or more action objects from the catalog>]}

BEHAVIOUR RULES
- Fulfil requests through actions; never claim you did something without emitting the action.
- "Find X on the globe" => find_on_globe. "Research/analyse X" => research. "Simulate/earthquake/storm on X" => simulate with the right disaster. "Show me its materials/history/soil/weather" => set_building (if a name was given) then navigate.
- Use CURRENT CONTEXT (page, active building, page data) to answer questions about what is on screen — quote the real numbers you see there.
- If the user's request needs no app action (a question, a chat), return "actions":[].
- Never invent page ids or action types outside the catalog. Never modify the app; you only drive its existing features.
- If a building name is ambiguous or missing, ask one short clarifying question instead of guessing.`;

app.post('/api/assistant', async (req, res) => {
    try {
        const { messages, context } = req.body || {};
        if (!Array.isArray(messages) || !messages.length) {
            return res.status(400).json({ error: 'messages array required' });
        }
        const ctx = context || {};
        const contextBlock =
            `CURRENT CONTEXT\n- Current page: ${ctx.page || 'unknown'}\n` +
            `- Active building: ${ctx.activeBuilding || 'none selected'}\n` +
            `- Visible page data (trimmed):\n${String(ctx.pageText || '').substring(0, 2400)}` +
            (ctx.dossier ? `\n\nRESEARCH DOSSIER for the active building (quote phrases from here verbatim for highlights):\n${String(ctx.dossier).substring(0, 1600)}` : '');

        const chat = [
            { role: 'system', content: ASSISTANT_SYSTEM },
            { role: 'system', content: contextBlock },
            ...messages.slice(-12).map(m => ({
                role: m.role === 'assistant' ? 'assistant' : 'user',
                content: String(m.content || '').substring(0, 2000)
            }))
        ];

        let raw = null, modelUsed = null;
        for (const model of ASSISTANT_MODELS) {
            try {
                const r = await fetch('https://router.huggingface.co/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${HF_TOKEN}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model, messages: chat, max_tokens: 700, temperature: 0.4, stream: false }),
                    signal: AbortSignal.timeout(60000)
                });
                if (r.ok) {
                    const d = await r.json();
                    raw = (d.choices?.[0]?.message?.content || '').replace(/<think>[\s\S]*?<\/think>/g, '').trim();
                    if (raw) { modelUsed = model; logApiCall('HuggingFace', '/chat (ARIA assistant)', model, raw.length, 0, true); break; }
                } else {
                    logApiCall('HuggingFace', '/chat (ARIA assistant)', model, 0, 0, false, `HTTP ${r.status}`);
                    console.log(`[ARIA] ${model} returned ${r.status}, trying next...`);
                }
            } catch (e) {
                console.log(`[ARIA] ${model} failed: ${e.message}`);
            }
        }
        if (!raw) {
            const g = await callGeminiText(ASSISTANT_SYSTEM + '\n\n' + contextBlock,
                messages.slice(-6).map(m => `${m.role}: ${m.content}`).join('\n'), 700, 0.4);
            if (g) { raw = g.text; modelUsed = g.model; }
        }
        if (!raw) return res.status(502).json({ error: 'All assistant models are unavailable right now.' });

        // Enforce the schema: extract the JSON object; anything else becomes a plain reply
        let parsed = null;
        try {
            const m = raw.match(/\{[\s\S]*\}/);
            if (m) parsed = JSON.parse(m[0]);
        } catch (e) { /* fall through */ }
        if (!parsed || typeof parsed.reply !== 'string') parsed = { reply: raw.substring(0, 1200), actions: [] };
        const VALID_TYPES = ['set_building', 'navigate', 'research', 'find_on_globe', 'simulate'];
        const VALID_PAGES = ['index', 'globe', 'analysis', 'materials', 'material-efficiency', 'history',
                             'simulation', 'soil', 'weather', 'forensic', 'api-usage'];
        parsed.actions = (Array.isArray(parsed.actions) ? parsed.actions : [])
            .filter(a => a && VALID_TYPES.includes(a.type))
            .filter(a => a.type !== 'navigate' || VALID_PAGES.includes(a.page))
            .map(a => {
                if (a.highlight != null) a.highlight = String(a.highlight).substring(0, 160);
                return a;
            })
            .slice(0, 4);
        parsed.model = modelUsed;
        res.json(parsed);
    } catch (e) {
        console.error('[ARIA] endpoint error:', e.message);
        res.status(500).json({ error: e.message });
    }
});

// =========================================================
// MODEL STATUS ENDPOINT
// =========================================================
app.get('/api/model-status', async (req, res) => {
    const status = await getModelStatus();
    res.json(status);
});


// =========================================================
// SSE RESEARCH STREAM — THE AUTONOMOUS DEEP RESEARCH ENGINE
// =========================================================
app.get('/api/research-stream', async (req, res) => {
    const buildingName = req.query.q;
    if (!buildingName) {
        return res.status(400).json({ error: 'q parameter is required' });
    }

    totalAnalyses++;
    console.log('\n' + '='.repeat(70));
    console.log('  AUTONOMOUS DEEP RESEARCH: ' + buildingName);
    console.log('='.repeat(70));

    // Setup SSE
    res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    });

    const send = (event, data) => {
        res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };

    // Track gathered data across all phases
    const allSources = [];
    const allFindings = [];
    const allSnippets = [];
    const answers = {};
    let llmKnowledge = '';
    let llmReasoning = '';
    let tavilyCalls = 0;
    let ddgCalls = 0;
    let llmCalls = 0;
    let totalSourcesFound = 0;

    // Handle client disconnect
    let aborted = false;
    req.on('close', () => { aborted = true; });

    try {

        // ═══════════════════════════════════════════════════════
        // PHASE 1: Initial Reconnaissance
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 1, total: 7, title: 'Initial Reconnaissance', icon: 'search', status: 'running' });
        send('progress', { percent: 2 });
        send('thought', { text: `Beginning autonomous deep research on "${buildingName}"...`, type: 'reasoning' });

        if (aborted) return;

        // Tavily overview search
        send('thought', { text: `Searching Tavily for "${buildingName} overview history architect key facts"...`, type: 'search' });
        const overviewRes = await tavilySearch(buildingName + ' overview history who built it height dimensions key facts architect designer');
        tavilyCalls++;

        if (overviewRes) {
            answers.overview = overviewRes.answer || '';
            if (overviewRes.results) {
                for (const r of overviewRes.results) {
                    send('source', { title: r.title, url: r.url, snippet: (r.content || '').substring(0, 150), engine: 'tavily' });
                    allSources.push({ title: r.title, url: r.url });
                    allSnippets.push(r.content || '');
                    totalSourcesFound++;
                }
            }
            if (answers.overview) {
                send('thought', { text: `Tavily found: "${answers.overview.substring(0, 200)}..."`, type: 'extraction' });
            }
        }

        // DuckDuckGo cross-validation
        send('thought', { text: `Cross-validating with DuckDuckGo: "${buildingName} facts overview"...`, type: 'search' });
        const ddgOverview = await duckDuckGoTextSearch([buildingName + ' overview facts architect height']);
        ddgCalls++;
        const ddgOverviewResults = ddgOverview[Object.keys(ddgOverview)[0]] || [];
        for (const r of ddgOverviewResults) {
            if (r.title && r.url) {
                send('source', { title: r.title, url: r.url, snippet: (r.snippet || '').substring(0, 150), engine: 'duckduckgo' });
                allSources.push({ title: r.title, url: r.url });
                allSnippets.push(r.snippet || '');
                totalSourcesFound++;
            }
        }

        // Extract initial findings from overview
        const overviewText = answers.overview || '';
        const heightMatch = overviewText.match(/(\d[\d,.]+)\s*(meters?|m|feet|ft)\s*(tall|high|height)?/i);
        if (heightMatch) {
            send('finding', { key: 'Height', value: heightMatch[1] + ' ' + heightMatch[2], confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Height', value: heightMatch[1] + ' ' + heightMatch[2] });
        }
        const architectMatch = overviewText.match(/(?:designed\s+by|architect|built\s+by|chief\s+engineer)\s*(?:was|is|:)?\s*([A-Z][a-zA-Z\s.&'()-]+?)(?:\s*[,.]|\s+and\s|\s+in\s)/i);
        if (architectMatch) {
            send('finding', { key: 'Architect', value: architectMatch[1].trim(), confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Architect', value: architectMatch[1].trim() });
        }

        send('progress', { percent: 14 });
        send('phase', { phase: 1, total: 7, title: 'Initial Reconnaissance', icon: 'search', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 2: AI Knowledge Base — LLM's Own Intelligence
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 2, total: 7, title: 'AI Knowledge Base', icon: 'psychology', status: 'running' });
        send('progress', { percent: 16 });
        send('thought', { text: `Consulting the AI's built-in knowledge base about "${buildingName}"...`, type: 'reasoning' });
        send('thought', { text: 'The AI model will now share what it already knows from its training data — historical facts, engineering details, and architectural insights that go beyond what web searches provide.', type: 'insight' });

        const knowledgeData = await callLLMKnowledge(buildingName);
        llmCalls++;

        if (knowledgeData && knowledgeData.knowledge) {
            llmKnowledge = knowledgeData.knowledge;
            send('llm-insight', { text: llmKnowledge.substring(0, 1500) });
            send('thought', { text: `AI knowledge base contributed ${llmKnowledge.length} characters of expertise.`, type: 'reasoning' });

            // Extract findings from LLM knowledge
            const kText = llmKnowledge;
            const kYearMatch = kText.match(/(?:built|constructed|completed|opened|inaugurated)\s*(?:in|around|circa)?\s*(1[5-9]\d{2}|20\d{2})/i);
            if (kYearMatch) {
                send('finding', { key: 'Year', value: kYearMatch[1], confidence: 'medium', source: 'llm' });
                allFindings.push({ key: 'Year', value: kYearMatch[1] });
            }
            const kCostMatch = kText.match(/(?:cost|budget)\s*(?:of|was|approximately)?\s*\$?([\d,.]+\s*(?:million|billion))/i);
            if (kCostMatch) {
                send('finding', { key: 'Cost', value: '$' + kCostMatch[1], confidence: 'medium', source: 'llm' });
                allFindings.push({ key: 'Cost', value: '$' + kCostMatch[1] });
            }
        } else {
            send('thought', { text: 'AI model unavailable — continuing with internet research only.', type: 'reasoning' });
        }

        send('progress', { percent: 28 });
        send('phase', { phase: 2, total: 7, title: 'AI Knowledge Base', icon: 'psychology', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 3: Structural Deep-Dive
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 3, total: 7, title: 'Structural Deep-Dive', icon: 'engineering', status: 'running' });
        send('progress', { percent: 30 });
        send('thought', { text: `Deep-diving into structural engineering of "${buildingName}"...`, type: 'search' });

        const structuralRes = await tavilySearch(buildingName + ' engineering challenges problems during construction design innovations techniques structural system foundation');
        tavilyCalls++;

        if (structuralRes) {
            answers.challenges = structuralRes.answer || '';
            if (structuralRes.results) {
                for (const r of structuralRes.results) {
                    send('source', { title: r.title, url: r.url, snippet: (r.content || '').substring(0, 150), engine: 'tavily' });
                    allSources.push({ title: r.title, url: r.url });
                    allSnippets.push(r.content || '');
                    totalSourcesFound++;
                }
            }
            if (answers.challenges) {
                send('thought', { text: `Engineering data found: "${answers.challenges.substring(0, 200)}..."`, type: 'extraction' });
            }
        }

        send('progress', { percent: 40 });
        send('phase', { phase: 3, total: 7, title: 'Structural Deep-Dive', icon: 'engineering', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 4: Historical Timeline Research
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 4, total: 7, title: 'Historical Timeline', icon: 'timeline', status: 'running' });
        send('progress', { percent: 42 });
        send('thought', { text: `Researching construction timeline and historical milestones...`, type: 'search' });

        const [timelineRes, ddgTimeline] = await Promise.all([
            tavilySearch(buildingName + ' construction timeline phases milestones year started year completed step by step'),
            duckDuckGoTextSearch([buildingName + ' construction timeline history year built completed'])
        ]);
        tavilyCalls++;
        ddgCalls++;

        if (timelineRes) {
            answers.timeline = timelineRes.answer || '';
            if (timelineRes.results) {
                for (const r of timelineRes.results) {
                    send('source', { title: r.title, url: r.url, snippet: (r.content || '').substring(0, 150), engine: 'tavily' });
                    allSources.push({ title: r.title, url: r.url });
                    allSnippets.push(r.content || '');
                    totalSourcesFound++;
                }
            }
        }

        const ddgTimelineResults = ddgTimeline[Object.keys(ddgTimeline)[0]] || [];
        for (const r of ddgTimelineResults) {
            if (r.title && r.url) {
                send('source', { title: r.title, url: r.url, snippet: (r.snippet || '').substring(0, 150), engine: 'duckduckgo' });
                allSources.push({ title: r.title, url: r.url });
                allSnippets.push(r.snippet || '');
                totalSourcesFound++;
            }
        }

        // Extract timeline findings
        const timelineText = (answers.timeline || '') + ' ' + ddgTimelineResults.map(r => r.snippet || '').join(' ');
        const startMatch = timelineText.match(/(?:construction\s+(?:began|started)|began\s+construction|broke\s+ground)\s*(?:in|on)?\s*(1[5-9]\d{2}|20\d{2})/i);
        const endMatch = timelineText.match(/(?:completed|opened|inaugurated|finished)\s*(?:in|on)?\s*(1[5-9]\d{2}|20\d{2})/i);
        if (startMatch) {
            send('finding', { key: 'Construction Start', value: startMatch[1], confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Construction Start', value: startMatch[1] });
        }
        if (endMatch) {
            send('finding', { key: 'Completion Year', value: endMatch[1], confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Completion Year', value: endMatch[1] });
        }

        send('progress', { percent: 52 });
        send('phase', { phase: 4, total: 7, title: 'Historical Timeline', icon: 'timeline', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 5: Materials Analysis
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 5, total: 7, title: 'Materials Analysis', icon: 'hardware', status: 'running' });
        send('progress', { percent: 54 });
        send('thought', { text: `Analyzing construction materials, quantities, and costs...`, type: 'search' });

        const [materialsRes, ddgMaterials] = await Promise.all([
            tavilySearch(buildingName + ' construction materials used steel concrete glass iron labor workers cost funding financing detailed breakdown'),
            duckDuckGoTextSearch([buildingName + ' construction materials steel concrete quantity workers cost'])
        ]);
        tavilyCalls++;
        ddgCalls++;

        if (materialsRes) {
            answers.materials = materialsRes.answer || '';
            if (materialsRes.results) {
                for (const r of materialsRes.results) {
                    send('source', { title: r.title, url: r.url, snippet: (r.content || '').substring(0, 150), engine: 'tavily' });
                    allSources.push({ title: r.title, url: r.url });
                    allSnippets.push(r.content || '');
                    totalSourcesFound++;
                }
            }
            if (answers.materials) {
                send('thought', { text: `Materials data: "${answers.materials.substring(0, 200)}..."`, type: 'extraction' });
            }
        }

        const ddgMaterialsResults = ddgMaterials[Object.keys(ddgMaterials)[0]] || [];
        for (const r of ddgMaterialsResults) {
            if (r.title && r.url) {
                send('source', { title: r.title, url: r.url, snippet: (r.snippet || '').substring(0, 150), engine: 'duckduckgo' });
                allSources.push({ title: r.title, url: r.url });
                allSnippets.push(r.snippet || '');
                totalSourcesFound++;
            }
        }

        // Extract material findings
        const matText = (answers.materials || '') + ' ' + ddgMaterialsResults.map(r => r.snippet || '').join(' ');
        const steelMatch = matText.match(/([\d,.]+)\s*(?:metric\s+)?(?:tons?|tonnes?)\s*(?:of\s+)?(?:steel|iron)/i);
        const concreteMatch = matText.match(/([\d,.]+)\s*(?:cubic\s+(?:meters?|yards?)|m3|m³|tons?|tonnes?)\s*(?:of\s+)?concrete/i);
        const workerMatch = matText.match(/([\d,]+)\s*(?:workers?|laborers?|people|men)/i);
        if (steelMatch) {
            send('finding', { key: 'Steel/Iron', value: steelMatch[1] + ' tons', confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Steel/Iron', value: steelMatch[1] + ' tons' });
        }
        if (concreteMatch) {
            send('finding', { key: 'Concrete', value: concreteMatch[1] + ' m³', confidence: 'high', source: 'internet' });
            allFindings.push({ key: 'Concrete', value: concreteMatch[1] + ' m³' });
        }
        if (workerMatch) {
            send('finding', { key: 'Workers', value: workerMatch[1], confidence: 'medium', source: 'internet' });
            allFindings.push({ key: 'Workers', value: workerMatch[1] });
        }

        send('progress', { percent: 64 });
        send('phase', { phase: 5, total: 7, title: 'Materials Analysis', icon: 'hardware', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 6: Design & Architecture
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 6, total: 7, title: 'Design & Architecture', icon: 'palette', status: 'running' });
        send('progress', { percent: 66 });
        send('thought', { text: `Researching architectural design philosophy and aesthetics...`, type: 'search' });

        const blueprintRes = await tavilySearch(buildingName + ' architectural design blueprint structure floor plan features design philosophy style');
        tavilyCalls++;

        if (blueprintRes) {
            answers.blueprint = blueprintRes.answer || '';
            if (blueprintRes.results) {
                for (const r of blueprintRes.results) {
                    send('source', { title: r.title, url: r.url, snippet: (r.content || '').substring(0, 150), engine: 'tavily' });
                    allSources.push({ title: r.title, url: r.url });
                    allSnippets.push(r.content || '');
                    totalSourcesFound++;
                }
            }
        }

        // LLM reasoning over all internet findings gathered so far
        send('thought', { text: 'The AI is now reasoning over all gathered internet data, cross-referencing with its own knowledge...', type: 'reasoning' });
        const allInternetText = Object.values(answers).join('\n') + '\n' + allSnippets.join('\n');
        const reasonData = await callLLMReason(buildingName, allInternetText);
        llmCalls++;

        if (reasonData && reasonData.analysis) {
            llmReasoning = reasonData.analysis;
            send('llm-insight', { text: llmReasoning.substring(0, 1000) });
            send('thought', { text: 'AI reasoning complete — insights have been incorporated.', type: 'insight' });
        }

        send('progress', { percent: 76 });
        send('phase', { phase: 6, total: 7, title: 'Design & Architecture', icon: 'palette', status: 'complete' });

        if (aborted) return;


        // ═══════════════════════════════════════════════════════
        // PHASE 7: AI Synthesis & Report Generation
        // ═══════════════════════════════════════════════════════
        send('phase', { phase: 7, total: 7, title: 'AI Synthesis & Report', icon: 'smart_toy', status: 'running' });
        send('progress', { percent: 78 });
        send('thought', { text: 'Synthesizing ALL data — blending internet research with AI intelligence to generate the complete structured report...', type: 'reasoning' });
        send('thought', { text: `Data sources: ${tavilyCalls} Tavily searches, ${ddgCalls} DuckDuckGo searches, ${llmCalls} AI reasoning calls, ${totalSourcesFound} total sources found.`, type: 'insight' });

        // Combine everything for the extraction model
        const fullContext = [
            '=== INTERNET RESEARCH (Tavily + DuckDuckGo) ===',
            answers.overview || '',
            answers.timeline || '',
            answers.materials || '',
            answers.challenges || '',
            answers.blueprint || '',
            allSnippets.slice(0, 20).join('\n'),
            '',
            '=== AI KNOWLEDGE BASE ===',
            llmKnowledge || '',
            '',
            '=== AI REASONING & ANALYSIS ===',
            llmReasoning || ''
        ].join('\n');

        // Call the extraction LLM to produce structured JSON
        send('thought', { text: 'Running the AI extraction model to generate the final structured report from all gathered intelligence...', type: 'extraction' });
        const aiData = await callLangChainAgent(buildingName, fullContext);
        llmCalls++;

        let modelUsed = null;
        let useAI = false;

        if (aiData && aiData.status === 'success' && !aiData.error) {
            modelUsed = aiData.modelUsed || 'HuggingFace AI';
            useAI = true;
            send('thought', { text: `AI extraction successful via ${modelUsed}. Building final report...`, type: 'insight' });
        } else {
            send('thought', { text: 'AI extraction unavailable — building report with intelligent regex fallback...', type: 'reasoning' });
        }

        send('progress', { percent: 88 });

        // ── Build the final structured result ──
        let result;
        if (useAI) {
            result = buildResponseFromAI(buildingName, aiData, answers.overview || '', answers.timeline || '', answers.materials || '', answers.challenges || '', answers.blueprint || '', fullContext,
                { results: allSources.filter(s => s.title).slice(0, 5) },
                { results: allSources.filter(s => s.title).slice(5, 10) },
                { results: allSources.filter(s => s.title).slice(10, 15) },
                { results: allSources.filter(s => s.title).slice(15, 20) },
                { results: allSources.filter(s => s.title).slice(20, 25) }
            );
        } else {
            result = buildResponseFromRegex(buildingName, fullContext, answers.overview || '', answers.timeline || '', answers.materials || '', answers.challenges || '', answers.blueprint || '',
                { results: allSources.filter(s => s.title).slice(0, 5) },
                { results: allSources.filter(s => s.title).slice(5, 10) },
                { results: allSources.filter(s => s.title).slice(10, 15) },
                { results: allSources.filter(s => s.title).slice(15, 20) },
                { results: allSources.filter(s => s.title).slice(20, 25) }
            );
        }

        // Fetch theme-relevant images for materials, timeline phases, blueprint and hero
        send('thought', { text: 'Fetching theme-relevant images for every material and timeline phase from DuckDuckGo Image Search...', type: 'search' });
        await attachResearchImages(result, buildingName);
        result.modelUsed = modelUsed;

        // Add research metadata
        result.schemaVersion = 2;
        result.researchMeta = {
            tavilyCalls,
            ddgCalls,
            llmCalls,
            totalSources: totalSourcesFound,
            totalFindings: allFindings.length,
            llmKnowledgeUsed: !!llmKnowledge,
            llmReasoningUsed: !!llmReasoning
        };

        send('progress', { percent: 100 });
        send('phase', { phase: 7, total: 7, title: 'AI Synthesis & Report', icon: 'smart_toy', status: 'complete' });
        send('thought', { text: `Research complete! Generated ${result.timeline.length} timeline phases, ${result.materials.length} materials, from ${totalSourcesFound} sources.`, type: 'insight' });

        // Send the final result
        send('result', result);
        send('done', {});

        console.log('=== Deep Research Complete: ' + result.timeline.length + ' phases, ' + result.materials.length + ' materials ===');
        console.log('  Tavily: ' + tavilyCalls + ' | DDG: ' + ddgCalls + ' | LLM: ' + llmCalls + ' | Sources: ' + totalSourcesFound);
        console.log('='.repeat(70) + '\n');

    } catch (error) {
        console.error('Research Stream Error:', error);
        send('error', { message: error.message || 'Unknown error during research' });
    }

    res.end();
});


// =========================================================
// LEGACY ANALYSIS ENDPOINT (kept for backward compatibility)
// =========================================================
app.post('/api/analyze', async (req, res) => {
    try {
        const { buildingName } = req.body;
        if (!buildingName) {
            return res.status(400).json({ error: 'buildingName is required' });
        }

        totalAnalyses++;
        console.log('\n=== Starting deep analysis for: ' + buildingName + ' ===');

        // ── PHASE 1: Tavily Research ──
        const [overviewRes, timelineRes, materialsRes, challengesRes, blueprintRes] = await Promise.all([
            tavilySearch(buildingName + ' overview history who built it height dimensions key facts architect designer'),
            tavilySearch(buildingName + ' construction timeline phases milestones year started year completed step by step'),
            tavilySearch(buildingName + ' construction materials used steel concrete glass iron labor workers cost funding financing detailed breakdown'),
            tavilySearch(buildingName + ' engineering challenges problems during construction design innovations techniques structural'),
            tavilySearch(buildingName + ' architectural design blueprint structure floor plan cross section elevation view features')
        ]);

        const overview = overviewRes?.answer || '';
        const timeline = timelineRes?.answer || '';
        const materials = materialsRes?.answer || '';
        const challenges = challengesRes?.answer || '';
        const blueprintInfo = blueprintRes?.answer || '';

        const allSnippets = [
            ...(overviewRes?.results || []),
            ...(timelineRes?.results || []),
            ...(materialsRes?.results || []),
            ...(challengesRes?.results || []),
            ...(blueprintRes?.results || [])
        ].map(r => r.content).join('\n');

        const fullContext = overview + '\n' + timeline + '\n' + materials + '\n' + challenges + '\n' + blueprintInfo + '\n' + allSnippets;

        // ── PHASE 2: AI Extraction ──
        const aiData = await callLangChainAgent(buildingName, fullContext);
        let modelUsed = null;
        let useAI = false;

        if (aiData && aiData.status === 'success' && !aiData.error) {
            modelUsed = aiData.modelUsed || 'HuggingFace AI';
            useAI = true;
        }

        // ── PHASE 3: Build structured data ──
        let result;
        if (useAI) {
            result = buildResponseFromAI(buildingName, aiData, overview, timeline, materials, challenges, blueprintInfo, fullContext, overviewRes, timelineRes, materialsRes, challengesRes, blueprintRes);
        } else {
            result = buildResponseFromRegex(buildingName, fullContext, overview, timeline, materials, challenges, blueprintInfo, overviewRes, timelineRes, materialsRes, challengesRes, blueprintRes);
        }

        // ── PHASE 4: Fetch Images ──
        await attachResearchImages(result, buildingName);
        result.modelUsed = modelUsed;
        result.schemaVersion = 2;

        res.json(result);

    } catch (error) {
        console.error('API Error:', error);
        res.status(500).json({ error: error.message });
    }
});


// =========================================================
// BUILD RESPONSE FROM AI DATA
// =========================================================
function buildResponseFromAI(buildingName, aiData, overview, timeline, materials, challenges, blueprintInfo, fullContext, overviewRes, timelineRes, materialsRes, challengesRes, blueprintRes) {

    // ── Timeline nodes from AI ──
    const timelineNodes = [];
    const aiPhases = aiData.timelinePhases || [];

    if (aiPhases.length > 0) {
        for (let i = 0; i < aiPhases.length; i++) {
            const phase = aiPhases[i];
            const stats = phase.stats && phase.stats.length > 0 ? phase.stats : [
                { label: "Period", value: phase.year || "—" },
                { label: "Architect", value: aiData.architect || "—" },
                { label: "Workers", value: aiData.workers || "—" },
                { label: "Status", value: i === aiPhases.length - 1 ? "Completed" : "In Progress" }
            ];
            timelineNodes.push({
                year: phase.year || "Phase " + (i + 1),
                dateLabel: phase.year ? "CIRCA " + phase.year : "PHASE " + (i + 1),
                title: phase.title || "Construction Phase " + (i + 1),
                description: phase.description || "",
                stats: stats.slice(0, 4)
            });
        }
    } else {
        timelineNodes.push({
            year: aiData.constructionStart || "Start",
            dateLabel: aiData.constructionStart ? "CIRCA " + aiData.constructionStart : "INITIAL PHASE",
            title: "Foundation & Construction Begins",
            description: aiData.overview || overview || "The initial phase of construction.",
            stats: [
                { label: "Start", value: aiData.constructionStart || "Historic" },
                { label: "Workers", value: aiData.workers || "Thousands" },
                { label: "Architect", value: aiData.architect || "Notable" },
                { label: "Material", value: aiData.materials && aiData.materials.length > 0 ? aiData.materials[0].name : "Mixed" }
            ]
        });
        if (aiData.constructionEnd) {
            timelineNodes.push({
                year: aiData.constructionEnd,
                dateLabel: "CIRCA " + aiData.constructionEnd,
                title: "Completion & Inauguration",
                description: buildingName + " was completed in " + aiData.constructionEnd + ".",
                stats: [
                    { label: "Completed", value: aiData.constructionEnd },
                    { label: "Height", value: aiData.height ? aiData.height + " " + (aiData.heightUnit || "m") : "Landmark" },
                    { label: "Cost", value: aiData.cost || "Significant" },
                    { label: "Status", value: "Landmark" }
                ]
            });
        }
    }

    // ── Materials from AI ──
    const materialsData = [];
    const iconMap = {
        'Structural': 'hardware', 'Foundation': 'domain', 'Facade': 'window',
        'Interior': 'chair', 'Exterior': 'layers', 'Decorative': 'palette',
        'General': 'category'
    };

    const aiMaterials = aiData.materials || [];
    if (aiMaterials.length > 0) {
        for (const mat of aiMaterials) {
            materialsData.push({
                name: mat.name || "Building Material",
                quantity: mat.quantity || "Various",
                description: mat.description || "",
                icon: iconMap[mat.category] || guessIcon(mat.name),
                category: mat.category || "General",
                source: mat.source || "",
                role: mat.role || "",
                properties: mat.properties || {},
                modernEquivalent: mat.modernEquivalent || ""
            });
        }
    }
    if (materialsData.length === 0) {
        materialsData.push({
            name: "Primary Building Material",
            quantity: "Various",
            description: materials || "Structural materials used in the construction.",
            icon: "construction",
            category: "General"
        });
    }

    // ── Specifications ──
    const startYear = aiData.constructionStart;
    const endYear = aiData.constructionEnd;
    const specifications = [
        { metric: "Total Height", value: aiData.height || "Notable", unit: aiData.heightUnit || "Meters", significance: "Defining vertical dimension" },
        { metric: "Construction Period", value: (startYear && endYear) ? startYear + "-" + endYear : "Multiple Years", unit: "Years", significance: (startYear && endYear) ? (parseInt(endYear) - parseInt(startYear)) + " years of construction" : "Extended construction period" },
        { metric: "Structural Material", value: aiMaterials.length > 0 ? aiMaterials[0].name : "Mixed", unit: aiMaterials.length > 0 ? aiMaterials[0].quantity : "Various", significance: "Primary load-bearing material" },
        { metric: "Construction Cost", value: aiData.cost || "Significant", unit: "Currency", significance: "Total project investment" },
        { metric: "Labor Force", value: aiData.workers || "Thousands", unit: "Workers", significance: "Peak workforce during construction" },
        { metric: "Floors", value: aiData.floors || "Multiple", unit: "Stories", significance: "Total floor count" },
        { metric: "Architect / Engineer", value: aiData.architect || "Notable", unit: "—", significance: "Lead designer" }
    ];

    // ── Quick Stats ──
    const quickStats = {
        stat1: { label: "Total Height", value: aiData.height ? aiData.height + (aiData.heightUnit ? aiData.heightUnit.charAt(0) : "m") : "—" },
        stat2: { label: "Year Completed", value: endYear || "—" },
        stat3: { label: "Material", value: aiMaterials.length > 0 ? aiMaterials[0].name : "Mixed" }
    };

    // ── Blueprint ──
    const blueprintData = {
        overview: aiData.overview || overview || "Architectural overview.",
        features: aiData.features || extractFeatures(fullContext),
        structuralSystem: aiData.structuralSystem || "",
        designPhilosophy: aiData.designPhilosophy || ""
    };

    // ── Full Report ──
    const allSourcesList = [
        ...(overviewRes?.results || []),
        ...(timelineRes?.results || []),
        ...(materialsRes?.results || []),
        ...(challengesRes?.results || []),
        ...(blueprintRes?.results || [])
    ].map(r => ({ title: r.title, url: r.url })).filter((v, i, a) => a.findIndex(t => t.url === v.url) === i).slice(0, 12);

    // Prefer the AI's long research-paper prose; short Tavily answers are fallback only
    const aiTimelineProse = aiData.timelinePhases?.map(p =>
        (p.year ? p.year + ' — ' : '') + (p.title ? p.title + '\n' : '') + (p.description || '')
    ).join('\n\n') || '';
    const aiMaterialsProse = aiData.materials?.map(m =>
        m.name + (m.quantity ? ' (' + m.quantity + ')' : '') + ':\n' + (m.description || '')
    ).join('\n\n') || '';

    const fullReport = {
        overview: aiData.overview || overview,
        constructionTimeline: aiTimelineProse || timeline,
        materialsAndCost: aiMaterialsProse || materials,
        engineeringChallenges: aiData.engineeringChallenges || challenges,
        structuralSystem: aiData.structuralSystem || "",
        architecturalDesign: aiData.designPhilosophy || blueprintInfo,
        culturalImpact: aiData.culturalImpact || "",
        sources: allSourcesList
    };

    const summaryText = (aiData.overview || overview).split('.').slice(0, 3).join('.').trim() + '.';

    return {
        title: "Case Study: " + buildingName,
        subtitle: "The Structural Evolution of\n" + buildingName,
        summary: summaryText,
        timeline: timelineNodes,
        specifications: specifications,
        quickStats: quickStats,
        materials: materialsData,
        blueprint: blueprintData,
        fullReport: fullReport
    };
}


// =========================================================
// BUILD RESPONSE FROM REGEX (FALLBACK)
// =========================================================
function buildResponseFromRegex(buildingName, fullContext, overview, timeline, materials, challenges, blueprintInfo, overviewRes, timelineRes, materialsRes, challengesRes, blueprintRes) {
    // ---- EXTRACT FACTS WITH REGEX ----
    const heightMatch = fullContext.match(/(\d[\d,.]+)\s*(meters?|m|feet|ft)\s*(tall|high|height)/i);
    const yearStartMatch = fullContext.match(/(?:construction\s+(?:began|started|commenced)|began\s+construction|started\s+(?:building|construction)|broke\s+ground)\s*(?:in|on)?\s*(\d{4})/i)
        || fullContext.match(/(\d{4}).*(?:began|started|commenced|broke ground)/i);
    const yearEndMatch = fullContext.match(/(?:completed|opened|inaugurated|finished)\s*(?:in|on)?\s*(\d{4})/i)
        || fullContext.match(/(\d{4}).*(?:completed|opened|inaugurated)/i);
    const costMatch = fullContext.match(/(?:cost|budget|expense|financed?|funded)\s*(?:of|was|approximately|about|around)?\s*\$?([\d,.]+\s*(?:million|billion|trillion)?)/i);
    const workerMatch = fullContext.match(/([\d,]+)\s*(?:workers?|laborers?|labourers?|people|men)/i);
    const steelMatch = fullContext.match(/([\d,.]+)\s*(?:metric\s+)?(?:tons?|tonnes?)\s*(?:of\s+)?(?:steel|iron|metal|wrought\s*iron)/i)
        || fullContext.match(/([\d,.]+)\s*(?:metric\s+)?(?:tons?|tonnes?)\s*(?:of\s+)?(?:structural\s+)?(?:steel|iron)/i);
    const concreteMatch = fullContext.match(/([\d,.]+)\s*(?:cubic\s+(?:meters?|yards?|m3)|m3|m³|tons?|tonnes?)\s*(?:of\s+)?concrete/i);
    const glassMatch = fullContext.match(/([\d,.]+)\s*(?:glass\s+panels?|panels?\s+of\s+glass|square\s+(?:meters?|feet)\s+of\s+glass|pieces?\s+of\s+glass)/i);
    const architectMatch = fullContext.match(/(?:built\s+by|designed\s+by|architect|chief\s+engineer|engineer)\s*(?:was|is|:)?\s*([A-Z][a-zA-Z\s.&'()-]+?)(?:\s*[,.]|\s+for\s|\s+and\s+(?:the|it|his)|(?:\s+who\s)|\s+in\s|\s+as\s|\s+to\s)/i);
    const floorMatch = fullContext.match(/(\d+)\s*(?:floors?|stories?|storeys?|levels?)/i);

    const startYear = yearStartMatch ? yearStartMatch[1] : null;
    const endYear = yearEndMatch ? yearEndMatch[1] : null;

    // ---- BUILD TIMELINE ----
    const timelineNodes = [];

    const phase1Desc = timeline.split('.').filter(s => s.trim().length > 20).slice(0, 2);
    timelineNodes.push({
        year: startYear || "Phase 1",
        dateLabel: startYear ? "CIRCA " + startYear : "INITIAL PHASE",
        title: "Foundation & Construction Begins",
        description: phase1Desc.length > 0 ? phase1Desc.join('.').trim() + '.' : 'The planning and foundation phase established the groundwork for this landmark structure.',
        stats: [
            { label: "Construction Start", value: startYear || "Historic" },
            { label: "Workers", value: workerMatch ? workerMatch[1] : "Thousands" },
            { label: "Primary Material", value: steelMatch ? "Steel/Iron" : concreteMatch ? "Concrete" : "Mixed" },
            { label: "Architect/Engineer", value: architectMatch ? architectMatch[1].trim().substring(0, 28) : "Notable" }
        ]
    });

    const midYear = (startYear && endYear) ? Math.round((parseInt(startYear) + parseInt(endYear)) / 2).toString() : null;
    const matSentences = materials.split('.').filter(s => s.trim().length > 20).slice(0, 2);
    timelineNodes.push({
        year: midYear || "Phase 2",
        dateLabel: midYear ? "CIRCA " + midYear : "MID CONSTRUCTION",
        title: "Structural Assembly & Engineering",
        description: matSentences.length > 0 ? matSentences.join('.').trim() + '.' : 'The main structural assembly progressed with innovative engineering techniques.',
        stats: [
            { label: "Steel/Iron", value: steelMatch ? steelMatch[1] + " tons" : "Substantial" },
            { label: "Concrete", value: concreteMatch ? concreteMatch[1] + " m³" : "Substantial" },
            { label: "Est. Cost", value: costMatch ? "$" + costMatch[1] : "Significant" },
            { label: "Height", value: heightMatch ? heightMatch[1] + " " + heightMatch[2] : "Landmark" }
        ]
    });

    const challengeSentences = challenges.split('.').filter(s => s.trim().length > 20).slice(0, 3);
    timelineNodes.push({
        year: midYear ? String(parseInt(midYear) + 1) : "Phase 3",
        dateLabel: midYear ? "CIRCA " + String(parseInt(midYear) + 1) : "LATE CONSTRUCTION",
        title: "Engineering Challenges & Solutions",
        description: challengeSentences.length > 0 ? challengeSentences.join('.').trim() + '.' : 'The project faced significant engineering challenges that were overcome through innovation.',
        stats: [
            { label: "Key Challenge", value: "Structural" },
            { label: "Innovation", value: "Advanced" },
            { label: "Floors", value: floorMatch ? floorMatch[1] : "Multiple" },
            { label: "Glass", value: glassMatch ? glassMatch[1] + " panels" : "Extensive" }
        ]
    });

    if (endYear) {
        const completionSentences = overview.split('.').filter(s =>
            s.includes(endYear) || s.toLowerCase().includes('complet') ||
            s.toLowerCase().includes('open') || s.toLowerCase().includes('inaug')
        ).slice(0, 2);
        timelineNodes.push({
            year: endYear,
            dateLabel: "CIRCA " + endYear,
            title: "Completion & Inauguration",
            description: completionSentences.length > 0 ? completionSentences.join('.').trim() + '.' : buildingName + ' was completed in ' + endYear + ', becoming an iconic landmark.',
            stats: [
                { label: "Completed", value: endYear },
                { label: "Total Height", value: heightMatch ? heightMatch[1] + " " + heightMatch[2] : "Iconic" },
                { label: "Total Cost", value: costMatch ? "$" + costMatch[1] : "Major" },
                { label: "Status", value: "Landmark" }
            ]
        });
    }

    // ---- BUILD SPECIFICATIONS ----
    const specifications = [
        { metric: "Total Height", value: heightMatch ? heightMatch[1] : "Notable", unit: heightMatch ? heightMatch[2] : "Meters", significance: "Defining vertical dimension" },
        { metric: "Construction Period", value: (startYear && endYear) ? startYear + "-" + endYear : "Multiple Years", unit: "Years", significance: (startYear && endYear) ? (parseInt(endYear) - parseInt(startYear)) + " years of construction" : "Extended construction period" },
        { metric: "Structural Steel/Iron", value: steelMatch ? steelMatch[1] : "Substantial", unit: steelMatch ? "Metric Tons" : "Tons", significance: "Primary load-bearing material" },
        { metric: "Construction Cost", value: costMatch ? costMatch[1] : "Significant", unit: costMatch ? "USD" : "Currency", significance: "Total project investment" },
        { metric: "Labor Force", value: workerMatch ? workerMatch[1] : "Thousands", unit: "Workers", significance: "Peak workforce during construction" }
    ];

    // ---- BUILD MATERIALS DATA ----
    const materialsData = [];
    if (steelMatch || fullContext.toLowerCase().includes('steel') || fullContext.toLowerCase().includes('iron')) {
        materialsData.push({
            name: steelMatch && fullContext.toLowerCase().includes('iron') ? "Wrought Iron / Steel" : "Structural Steel",
            quantity: steelMatch ? steelMatch[1] + " tons" : "Substantial",
            description: extractSentencesAbout(fullContext, ['steel', 'iron', 'metal'], 2),
            icon: "hardware",
            category: "Structural"
        });
    }
    if (concreteMatch || fullContext.toLowerCase().includes('concrete')) {
        materialsData.push({
            name: "Reinforced Concrete",
            quantity: concreteMatch ? concreteMatch[1] + " m³" : "Substantial",
            description: extractSentencesAbout(fullContext, ['concrete', 'cement', 'reinforced'], 2),
            icon: "domain",
            category: "Foundation"
        });
    }
    if (glassMatch || fullContext.toLowerCase().includes('glass')) {
        materialsData.push({
            name: "Glass Panels",
            quantity: glassMatch ? glassMatch[1] + " panels" : "Extensive",
            description: extractSentencesAbout(fullContext, ['glass', 'facade', 'curtain wall', 'window'], 2),
            icon: "window",
            category: "Facade"
        });
    }
    if (fullContext.toLowerCase().includes('aluminum') || fullContext.toLowerCase().includes('aluminium')) {
        materialsData.push({
            name: "Aluminum Cladding",
            quantity: "Extensive",
            description: extractSentencesAbout(fullContext, ['aluminum', 'aluminium', 'cladding'], 2),
            icon: "layers",
            category: "Exterior"
        });
    }
    if (fullContext.toLowerCase().includes('stone') || fullContext.toLowerCase().includes('marble') || fullContext.toLowerCase().includes('granite') || fullContext.toLowerCase().includes('sandstone') || fullContext.toLowerCase().includes('brick')) {
        const stoneType = fullContext.toLowerCase().includes('marble') ? 'Marble' :
                         fullContext.toLowerCase().includes('granite') ? 'Granite' :
                         fullContext.toLowerCase().includes('sandstone') ? 'Red Sandstone' :
                         fullContext.toLowerCase().includes('brick') ? 'Brick' : 'Stone';
        materialsData.push({
            name: stoneType,
            quantity: "Extensive",
            description: extractSentencesAbout(fullContext, ['stone', 'marble', 'granite', 'sandstone', 'brick', 'masonry'], 2),
            icon: "wall",
            category: "Structure"
        });
    }
    if (fullContext.toLowerCase().includes('wood') || fullContext.toLowerCase().includes('timber')) {
        materialsData.push({
            name: "Timber / Wood",
            quantity: "Various",
            description: extractSentencesAbout(fullContext, ['wood', 'timber'], 2),
            icon: "park",
            category: "Interior"
        });
    }
    if (materialsData.length === 0) {
        materialsData.push({
            name: "Primary Building Material",
            quantity: "Various",
            description: materials || 'Structural materials used in the construction.',
            icon: "construction",
            category: "General"
        });
    }

    // ---- BUILD BLUEPRINT DATA ----
    const blueprintData = {
        overview: blueprintInfo || 'Architectural design and structural layout information.',
        features: extractFeatures(fullContext),
        structuralSystem: extractSentencesAbout(fullContext, ['structural system', 'foundation', 'core', 'frame', 'buttress', 'arch', 'dome', 'column', 'pillar', 'beam'], 3),
        designPhilosophy: extractSentencesAbout(fullContext, ['design', 'style', 'aesthetic', 'inspired', 'influence', 'architectural', 'neo', 'modern', 'gothic', 'classical', 'mughal', 'art deco'], 3)
    };

    // ---- BUILD FULL REPORT ----
    const fullReport = {
        overview: overview,
        constructionTimeline: timeline,
        materialsAndCost: materials,
        engineeringChallenges: challenges,
        architecturalDesign: blueprintInfo,
        sources: [
            ...(overviewRes?.results || []),
            ...(timelineRes?.results || []),
            ...(materialsRes?.results || []),
            ...(challengesRes?.results || []),
            ...(blueprintRes?.results || [])
        ].map(r => ({ title: r.title, url: r.url })).filter((v, i, a) => a.findIndex(t => t.url === v.url) === i).slice(0, 12)
    };

    // ---- QUICK STATS ----
    const quickStats = {
        stat1: { label: "Total Height", value: heightMatch ? heightMatch[1] + heightMatch[2].charAt(0) : "—" },
        stat2: { label: "Year Completed", value: endYear || "—" },
        stat3: { label: "Material", value: steelMatch ? steelMatch[1] + "t steel" : concreteMatch ? "Concrete" : "Mixed" }
    };

    const summaryText = overview.split('.').slice(0, 3).join('.').trim() + '.';

    return {
        title: "Case Study: " + buildingName,
        subtitle: "The Structural Evolution of\n" + buildingName,
        summary: summaryText,
        timeline: timelineNodes,
        specifications: specifications,
        quickStats: quickStats,
        materials: materialsData,
        blueprint: blueprintData,
        fullReport: fullReport
    };
}


// =========================================================
// HELPERS
// =========================================================
function extractSentencesAbout(text, keywords, maxSentences) {
    const sentences = text.split(/[.!]/).filter(s => s.trim().length > 15);
    const matches = sentences.filter(s => {
        const lower = s.toLowerCase();
        return keywords.some(k => lower.includes(k));
    });
    const result = matches.slice(0, maxSentences).join('. ').trim();
    return result ? result + '.' : '';
}

function extractFeatures(text) {
    const features = [];
    const featurePatterns = [
        { pattern: /observation\s*deck/i, name: "Observation Deck" },
        { pattern: /elevator|lift/i, name: "Elevator System" },
        { pattern: /spire|antenna|pinnacle/i, name: "Spire / Pinnacle" },
        { pattern: /swimming\s*pool/i, name: "Swimming Pool" },
        { pattern: /restaurant|dining/i, name: "Restaurant" },
        { pattern: /hotel/i, name: "Hotel" },
        { pattern: /office/i, name: "Office Space" },
        { pattern: /residential|apartment/i, name: "Residential" },
        { pattern: /mosque|temple|church|chapel/i, name: "Place of Worship" },
        { pattern: /museum/i, name: "Museum" },
        { pattern: /garden|park|landscape/i, name: "Gardens / Landscape" },
        { pattern: /fountain/i, name: "Fountain" },
        { pattern: /gate|entrance|gateway/i, name: "Main Gateway" },
        { pattern: /dome|cupola/i, name: "Dome" },
        { pattern: /tower|minaret/i, name: "Tower / Minaret" },
        { pattern: /courtyard/i, name: "Courtyard" },
        { pattern: /wall|fort|battlement/i, name: "Fortification Walls" }
    ];
    for (const fp of featurePatterns) {
        if (fp.pattern.test(text)) {
            features.push(fp.name);
        }
    }
    return features.length > 0 ? features : ["Architectural Landmark"];
}

function guessIcon(materialName) {
    if (!materialName) return 'category';
    const lower = materialName.toLowerCase();
    if (lower.includes('steel') || lower.includes('iron') || lower.includes('metal')) return 'hardware';
    if (lower.includes('concrete') || lower.includes('cement')) return 'domain';
    if (lower.includes('glass')) return 'window';
    if (lower.includes('wood') || lower.includes('timber')) return 'park';
    if (lower.includes('stone') || lower.includes('marble') || lower.includes('granite') || lower.includes('brick')) return 'wall';
    if (lower.includes('aluminum') || lower.includes('aluminium') || lower.includes('cladding')) return 'layers';
    if (lower.includes('copper')) return 'hexagon';
    return 'category';
}

// =========================================================
// FEA ENGINE (Python subprocess — deterministic math)
// =========================================================
function runFEAEngine(config) {
    return new Promise((resolve) => {
        const { execFile } = require('child_process');
        execFile('python', ['fea_engine.py', JSON.stringify(config)], { timeout: 60000 }, (error, stdout, stderr) => {
            if (error) {
                console.error("FEA Engine Error:", error.message);
                if (stderr) console.error("FEA stderr:", stderr);
                return resolve({ error: error.message });
            }
            try {
                const result = JSON.parse(stdout);
                resolve(result);
            } catch (e) {
                console.error("FEA Parse Error:", e, stdout);
                resolve({ error: "Failed to parse FEA output" });
            }
        });
    });
}

// =========================================================
// DEEPSEEK AI AGENT — THE INTERPRETER
// Reads deterministic FEA outputs, writes human-readable logs.
// Cordoned off from math — only reads AFTER calculation.
// =========================================================
const HF_TOKEN = process.env.HUGGINGFACEHUB_API_TOKEN || '';
const DEEPSEEK_MODELS = [
    'deepseek-ai/DeepSeek-V3.2',
    'meta-llama/Llama-3.3-70B-Instruct'
];

const FEA_INTERPRETER_SYSTEM = `You are a structural engineering AI interpreter for the ArchPi Digital Twin system. You read raw FEA (Finite Element Analysis) computation outputs and translate them into clear, concise, human-readable event log entries.

Rules:
- Write SHORT entries (1-2 sentences max)
- Use technical but accessible language
- Flag critical stress ratios (>90%) as "CRITICAL"
- Flag warning stress ratios (>70%) as "WARNING"
- Reference specific node/element IDs when relevant
- Include numerical values with units
- Never perform calculations yourself — only interpret the provided data`;

// Gemini text fallback — used when HF router credits are depleted (HTTP 402)
async function callGeminiText(systemContent, prompt, maxTokens = 1024, temperature = 0.2) {
    if (!GEMINI_API_KEY) return null;
    const models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite'];
    for (const model of models) {
        try {
            const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
                method: 'POST',
                headers: { 'x-goog-api-key': GEMINI_API_KEY, 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: prompt }] }],
                    systemInstruction: { parts: [{ text: systemContent }] },
                    generationConfig: { maxOutputTokens: Math.max(maxTokens, 4096), temperature }
                })
            });
            const data = await r.json();
            if (!r.ok) {
                console.log(`  [Gemini] ${model} returned ${r.status}, trying next...`);
                logApiCall('Gemini', `/${model}`, prompt.substring(0, 60), 0, 0, false, data.error?.message || `HTTP ${r.status}`);
                continue;
            }
            const text = (data.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join('').trim();
            if (text) {
                console.log(`  [Gemini] ${model} responded (${text.length} chars)`);
                logApiCall('Gemini', `/${model}`, prompt.substring(0, 60), text.length, 0, true);
                return { text, model };
            }
        } catch (e) {
            console.log(`  [Gemini] ${model} failed: ${e.message}, trying next...`);
            logApiCall('Gemini', `/${model}`, prompt.substring(0, 60), 0, 0, false, e.message);
        }
    }
    return null;
}

async function callDeepSeekAgent(prompt) {
    for (const model of DEEPSEEK_MODELS) {
        try {
            const res = await fetch(`https://router.huggingface.co/v1/chat/completions`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${HF_TOKEN}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    model: model,
                    messages: [
                        { role: 'system', content: FEA_INTERPRETER_SYSTEM },
                        { role: 'user', content: prompt }
                    ],
                    max_tokens: 500,
                    temperature: 0.3,
                    stream: false
                })
            });

            if (res.ok) {
                const data = await res.json();
                const text = data.choices?.[0]?.message?.content || '';
                // Strip <think> blocks from DeepSeek-R1
                const cleaned = text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
                console.log(`  [DeepSeek] ${model} responded: ${cleaned.substring(0, 80)}...`);
                logApiCall('HuggingFace', '/chat (FEA interpreter)', model, cleaned.length, 0, true);
                return { text: cleaned, model };
            }
            console.log(`  [DeepSeek] ${model} returned ${res.status}, trying next...`);
            logApiCall('HuggingFace', '/chat (FEA interpreter)', model, 0, 0, false, `HTTP ${res.status}`);
        } catch (e) {
            console.log(`  [DeepSeek] ${model} failed: ${e.message}, trying next...`);
        }
    }
    // HF exhausted — fall back to Gemini
    return await callGeminiText(FEA_INTERPRETER_SYSTEM, prompt, 500, 0.3);
}

// Generalized DeepSeek call with custom system prompt (V3 first — much faster
// than R1's thinking mode for structured JSON generation)
async function callDeepSeekJSON(systemContent, prompt, maxTokens = 900) {
    const models = ['deepseek-ai/DeepSeek-V3.2', 'meta-llama/Llama-3.3-70B-Instruct'];
    for (const model of models) {
        try {
            const res = await fetch(`https://router.huggingface.co/v1/chat/completions`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${HF_TOKEN}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    model,
                    messages: [
                        { role: 'system', content: systemContent },
                        { role: 'user', content: prompt }
                    ],
                    max_tokens: maxTokens,
                    temperature: 0.2,
                    stream: false
                })
            });
            if (res.ok) {
                const data = await res.json();
                const text = (data.choices?.[0]?.message?.content || '')
                    .replace(/<think>[\s\S]*?<\/think>/g, '').trim();
                if (text) {
                    logApiCall('HuggingFace', '/chat (structured JSON)', model, text.length, 0, true);
                    return { text, model };
                }
            } else {
                console.log(`  [DeepSeek/JSON] ${model} returned ${res.status}, trying next...`);
                logApiCall('HuggingFace', '/chat (structured JSON)', model, 0, 0, false, `HTTP ${res.status}`);
            }
        } catch (e) {
            console.log(`  [DeepSeek/JSON] ${model} failed: ${e.message}, trying next...`);
        }
    }
    // HF exhausted — fall back to Gemini
    return await callGeminiText(systemContent, prompt, maxTokens, 0.2);
}

// =========================================================
// AI BUILDING PROFILE — dynamic structural metadata for ANY structure,
// no dependence on a hardcoded building database
// =========================================================
const profileCache = new Map();
const PROFILE_CACHE_TTL = 24 * 60 * 60 * 1000;

app.get('/api/building-profile', async (req, res) => {
    const q = (req.query.q || '').trim();
    if (!q) return res.status(400).json({ error: 'q parameter required' });

    const key = q.toLowerCase();
    const hit = profileCache.get(key);
    if (hit && (Date.now() - hit.at) < PROFILE_CACHE_TTL) {
        console.log(`[Profile] Cache hit for "${q}"`);
        return res.json(hit.data);
    }

    console.log(`[Profile] Generating AI structural profile for "${q}"...`);
    const sys = 'You are a structural engineering knowledge base. You respond with ONLY strict valid JSON — no markdown fences, no commentary, no explanations.';
    const prompt = `Provide structural engineering metadata for this building/structure: "${q}".

Return JSON with EXACTLY these keys:
{
 "name": "canonical name",
 "category": "skyscraper|tower|monument|fort|palace|temple|statue|bridge|stadium|cathedral|other",
 "height_m": number,
 "base_width_m": number (width of the base in front elevation view, metres),
 "top_width_m": number,
 "floors": number (0 if not applicable),
 "structural_system": "short description",
 "primary_material": "short description",
 "foundation": "short description",
 "appearance": "one rich sentence for an artist. START with the overall massing in blunt unambiguous geometry words (a FLAT screen wall / a stepped pyramid outline / a rectangular slab / a cylindrical drum / a tapering square tower), then facade pattern, window rhythm, colour, and the signature features that make THIS building unmistakable. NEVER use ambiguous words like 'curved' or 'soaring' unless the entire building is truly cylindrical. Explicitly say 'flat front elevation' when the facade is planar.",
 "density_kgm3": number (average structural density),
 "lat": number, "lng": number,
 "seismic_zone": "string",
 "wind_design_kmh": number,
 "max_stress_mpa": number (estimated primary material stress capacity),
 "profile": [[t,w],...] 10 to 16 pairs — t is normalized height 0..1 from ground, w is silhouette width factor 0..1 relative to base width; describe the REAL front elevation shape including taper, domes, setbacks, spires,
 "setbacks": [{"h_m": number, "label": "short"}] up to 3,
 "annotations": [{"h_m": number, "label": "short engineering note", "side": "left" or "right"}] up to 4
}

Use real measured facts where known; otherwise give best engineering estimates. JSON only.`;

    const ai = await callDeepSeekJSON(sys, prompt, 1100);
    let data = null;
    if (ai && ai.text) {
        try {
            const m = ai.text.match(/\{[\s\S]*\}/);
            if (m) data = JSON.parse(m[0]);
        } catch (e) {
            console.log(`[Profile] JSON parse failed: ${e.message}`);
        }
    }

    if (!data || !data.height_m) {
        return res.status(404).json({ error: 'AI profile unavailable' });
    }

    data.model = ai.model;
    profileCache.set(key, { data, at: Date.now() });
    console.log(`[Profile] "${data.name}" — ${data.height_m}m, ${data.category}, via ${ai.model}`);
    res.json(data);
});

// =========================================================
// GEMINI PHOTO → ARCHITECTURAL DRAWING CONVERTER
// User uploads a photo of any structure (e.g. an ancient building not in any
// index); Gemini converts it into a technical 2D front-elevation line drawing
// that the stress-heatmap pipeline can analyze.
// =========================================================
const GEMINI_API_KEY = process.env.GEMINI_API_KEY || '';
const GEMINI_IMAGE_MODELS = ['gemini-3.1-flash-image', 'gemini-2.5-flash-image', 'gemini-3.1-flash-lite-image'];

// OpenRouter image generation — primary provider for all drawing generation.
// Uses the chat-completions endpoint with image modality; the model returns
// the image as a base64 data URL in message.images[].
const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY || '';
const OPENROUTER_IMAGE_MODEL = process.env.OPENROUTER_IMAGE_MODEL || 'google/gemini-3.1-flash-lite-image';

// inputImage: optional { mime, b64 } to condition generation on an uploaded photo
async function generateOpenRouterImage(promptText, inputImage) {
    if (!OPENROUTER_API_KEY) throw new Error('OPENROUTER_API_KEY not configured');
    const content = inputImage
        ? [
            { type: 'image_url', image_url: { url: `data:${inputImage.mime};base64,${inputImage.b64}` } },
            { type: 'text', text: promptText }
        ]
        : promptText;
    const r = await fetch('https://openrouter.ai/api/v1/chat/completions', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${OPENROUTER_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: OPENROUTER_IMAGE_MODEL,
            messages: [{ role: 'user', content }],
            modalities: ['image', 'text'],
            // must stay capped: OpenRouter pre-reserves max_tokens against the
            // account's remaining credits and 402s if the reservation is too big
            max_tokens: 8192
        }),
        signal: AbortSignal.timeout(120000)
    });
    if (!r.ok) {
        const errText = (await r.text()).substring(0, 150);
        logApiCall('OpenRouter', '/image generation', OPENROUTER_IMAGE_MODEL, 0, 0, false, `HTTP ${r.status}: ${errText}`);
        throw new Error(`HTTP ${r.status}: ${errText}`);
    }
    const d = await r.json();
    const url = d.choices?.[0]?.message?.images?.[0]?.image_url?.url || '';
    if (!url.startsWith('data:')) throw new Error('no image in response');
    logApiCall('OpenRouter', '/image generation', OPENROUTER_IMAGE_MODEL, url.length, 0, true);
    return url;
}

// The drawing-generation schema: every constraint exists so the output
// segments cleanly and maps stress-vs-height correctly in the analyzer
const DRAWING_PROMPT = `Convert this photograph into a professional 2D architectural front elevation drawing of the structure shown, following ALL of these requirements exactly:

STYLE
- Technical architectural elevation drawing: precise black ink linework on a PURE WHITE background
- Style of a real engineering/architectural elevation sheet (like CAD or hand-drafted elevation drawings)
- No shading gradients, no color, no gray washes — only clean black line work with hatching where appropriate

PROJECTION
- STRICT orthographic FRONT ELEVATION (straight-on view), zero perspective, zero foreshortening
- The full structure from ground line to the topmost point must be visible
- Preserve the TRUE real-life proportions of this specific structure (height-to-width ratio must match the actual building)

CONTENT
- Draw ONLY the single main structure from the photo
- NO background, NO sky, NO clouds, NO trees, NO people, NO vehicles, NO neighboring buildings
- NO text, NO labels, NO dimensions, NO title block, NO watermark, NO border frame
- Reproduce the structure's real architectural character faithfully: arches, domes, columns, minarets, spires, balconies, masonry courses, window patterns, ornamentation
- The outer silhouette must be a single closed continuous contour (no gaps in the outline)
- Interior architectural details drawn with finer line weight than the outer contour
- The structure must sit on a simple horizontal ground line at the bottom

COMPOSITION
- The structure centered horizontally, filling 85-95% of the image height
- Portrait orientation if the structure is taller than wide, landscape if wider than tall`;

// FLUX.1-dev text-to-image via the HF router's fal-ai provider route.
// Used when Gemini image generation is unavailable (free tier has zero
// image-gen quota): the photo is first identified by Gemini Vision, then
// FLUX draws the elevation from the name + description.
async function generateFluxDrawing(buildingName, description, size) {
    const subject = description
        ? `${buildingName} — ${description}`
        : buildingName;
    // Style locked to the reference FEA sheet's drafting: uniform thin CAD
    // linework, structure only. Colors, legend, title block and dimensions are
    // rendered live by the client so the heatmap stays dynamic.
    // FLUX pattern-matches monuments to its favourite lookalikes (every pink
    // Indian palace becomes a domed Mughal tomb). Explicitly forbid the
    // features the building's own description never mentions.
    const NEG_FEATURES = ['dome', 'minaret', 'spire', 'bell tower', 'colonnade'];
    const descLower = subject.toLowerCase();
    const negatives = NEG_FEATURES.filter(f => !descLower.includes(f.split(' ')[0]));
    const negLine = negatives.length
        ? `CRITICAL: this building has NO ${negatives.join(', NO ')} — draw none of those. `
        : '';
    const prompt = `Precise 2D CAD architectural front elevation drawing of ${subject}. ` +
        `Faithfully reproduce THIS specific structure's real distinctive architecture exactly as described — ` +
        `never substitute a different famous building. ` + negLine +
        `Drafted exactly like the building elevation on a structural engineering FEA analysis sheet: ` +
        `uniform thin black technical pen linework on a PURE WHITE background, ` +
        `strict orthographic front elevation with zero perspective and zero foreshortening, ` +
        `true real-life proportions of this specific structure. ` +
        `Fine drafted interior detail: floor lines, window mullion grids, panel divisions, masonry courses, ` +
        `and every facade element this exact building truly has — nothing invented, nothing borrowed from lookalike monuments, ` +
        `all in finer line weight than the crisp closed continuous outer silhouette contour. ` +
        `ONLY the single structure on one simple flat horizontal ground line — ` +
        `no background, no sky, no trees, no plants, no people, no vehicles, no neighboring buildings, ` +
        `no plaza, no pavement, no pathway, no garden, no fence, no perspective floor lines below the structure, ` +
        `no text, no labels, no dimensions, no title block, no border frame, no watermark, no color, no shading gradients. ` +
        `Structure centered, filling 90% of the image height.`;
    const W = size?.width || 1024, H = size?.height || 768;
    const aspectHint = H > W ? ' Portrait orientation image.' : (W > H ? ' Landscape orientation image.' : '');

    // Provider 0: OpenRouter image model (user-configured, fast and reliable)
    if (OPENROUTER_API_KEY) {
        try {
            console.log(`[Drawing] Generating "${buildingName}" via OpenRouter ${OPENROUTER_IMAGE_MODEL}...`);
            const dataUrl = await generateOpenRouterImage('Generate an image: ' + prompt + aspectHint);
            console.log(`[Drawing] OpenRouter drawing generated (${Math.round(dataUrl.length * 0.75 / 1024)} KB)`);
            return { dataUrl, model: OPENROUTER_IMAGE_MODEL };
        } catch (e) {
            console.log(`[Drawing] OpenRouter failed (${e.message.substring(0, 100)}) — trying HF FLUX`);
        }
    }

    // Provider 1: HF router fal-ai route (uses monthly HF inference credits)
    if (HF_TOKEN) {
        try {
            console.log(`[FLUX] Generating "${buildingName}" via HF router...`);
            const r = await fetch('https://router.huggingface.co/fal-ai/fal-ai/flux/dev', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${HF_TOKEN}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt, num_inference_steps: 28, image_size: { width: W, height: H } })
            });
            if (!r.ok) {
                const body = await r.text();
                logApiCall('HuggingFace', '/flux image', buildingName, 0, 0, false, `HTTP ${r.status}: ${body.substring(0, 120)}`);
                throw new Error(`HTTP ${r.status}: ${body.substring(0, 120)}`);
            }
            const d = await r.json();
            const url = d.images?.[0]?.url;
            if (!url) throw new Error('no image in response');
            const imgRes = await fetch(url);
            if (!imgRes.ok) throw new Error(`image download HTTP ${imgRes.status}`);
            const buf = Buffer.from(await imgRes.arrayBuffer());
            const mime = d.images[0].content_type || imgRes.headers.get('content-type') || 'image/jpeg';
            console.log(`[FLUX] HF router drawing generated (${Math.round(buf.length / 1024)} KB)`);
            logApiCall('HuggingFace', '/flux image', buildingName, buf.length, 0, true);
            return { dataUrl: `data:${mime};base64,${buf.toString('base64')}`, model: 'FLUX.1-dev' };
        } catch (e) {
            console.log(`[FLUX] HF router failed (${e.message.substring(0, 100)}) — trying Pollinations`);
        }
    }

    // Provider 2: Pollinations.ai — free keyless FLUX endpoint
    console.log(`[FLUX] Generating "${buildingName}" via Pollinations...`);
    // Seed from name + prompt so an enriched description explores a fresh
    // composition instead of resampling the old (wrong) one
    const seed = Math.abs([...(buildingName + '|' + prompt.length)].reduce((a, c) => a * 31 + c.charCodeAt(0) | 0, 7)) % 100000;
    const pUrl = `https://image.pollinations.ai/prompt/${encodeURIComponent(prompt)}` +
        `?width=${W}&height=${H}&model=flux&nologo=true&seed=${seed}`;
    const pr = await fetch(pUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!pr.ok) {
        logApiCall('Pollinations', '/image', buildingName, 0, 0, false, `HTTP ${pr.status}`);
        throw new Error(`Pollinations HTTP ${pr.status}`);
    }
    const pbuf = Buffer.from(await pr.arrayBuffer());
    if (pbuf.length < 5000) throw new Error('Pollinations returned an invalid image');
    const pmime = pr.headers.get('content-type') || 'image/jpeg';
    console.log(`[FLUX] Pollinations drawing generated (${Math.round(pbuf.length / 1024)} KB)`);
    logApiCall('Pollinations', '/image', buildingName, pbuf.length, 0, true);
    return { dataUrl: `data:${pmime};base64,${pbuf.toString('base64')}`, model: 'FLUX.1-dev (Pollinations)' };
}

// GET /api/generate-elevation?name=...&description=...
// Primary image source for EVERY building: FLUX.1-dev draws the elevation in
// the locked reference style so all structures render identically. Cached —
// generation takes ~10-30s, repeat visits are instant.
const drawingCache = new Map();          // nameKey -> { dataUrl, at }
const drawingInFlight = new Map();       // nameKey -> Promise (dedupe concurrent requests)
const DRAWING_TTL = 24 * 3600 * 1000;

app.get('/api/generate-elevation', async (req, res) => {
    const name = (req.query.name || '').trim();
    if (!name) return res.status(400).json({ error: 'name required' });
    const key = name.toLowerCase();

    const hit = drawingCache.get(key);
    if (hit && Date.now() - hit.at < DRAWING_TTL) {
        return res.json({ imageBase64: hit.dataUrl, model: hit.model || 'FLUX.1-dev', cached: true });
    }

    // Enrich the prompt from the request or the cached structural profile.
    // The profile's "appearance" sentence carries the distinctive features
    // (Hawa Mahal's honeycomb screen, not a generic domed monument) — without
    // it the image model draws the nearest famous lookalike.
    let description = (req.query.description || '').trim();
    const prof = profileCache.get(key);
    if (prof?.data) {
        const p = prof.data;
        const facts = [p.category, p.primary_material, p.height_m ? `${p.height_m}m tall` : '']
            .filter(Boolean).join(', ');
        const appearance = (p.appearance || '').trim();
        description = [description, appearance, facts].filter(Boolean).join('. ');
    }

    // Canvas orientation from the structure's real proportions (cached profile,
    // or the client's local estimate passed as ?h=&w=)
    let size = { width: 1024, height: 1024 };
    const h = prof?.data?.height_m || parseFloat(req.query.h) || 0;
    const w = prof?.data?.base_width_m || parseFloat(req.query.w) || 0;
    if (h && w) {
        if (h > w * 1.15) size = { width: 768, height: 1024 };
        else if (w > h * 1.15) size = { width: 1024, height: 768 };
    }

    try {
        let pending = drawingInFlight.get(key);
        if (!pending) {
            pending = generateFluxDrawing(name, description, size)
                .finally(() => drawingInFlight.delete(key));
            drawingInFlight.set(key, pending);
        }
        const { dataUrl, model } = await pending;
        drawingCache.set(key, { dataUrl, model, at: Date.now() });
        res.json({ imageBase64: dataUrl, model });
    } catch (e) {
        console.log(`[FLUX] Elevation generation failed for "${name}": ${e.message}`);
        res.status(502).json({ error: e.message });
    }
});

app.post('/api/convert-to-drawing', async (req, res) => {
    try {
        const { imageBase64, mimeType, buildingName, description } = req.body || {};
        if (!imageBase64) return res.status(400).json({ error: 'imageBase64 required' });
        if (!GEMINI_API_KEY) return res.status(503).json({ error: 'GEMINI_API_KEY not configured' });

        // Accept both raw base64 and data-URL form
        const b64 = imageBase64.replace(/^data:[^;]+;base64,/, '');
        const mime = mimeType || (imageBase64.match(/^data:([^;]+);/) || [])[1] || 'image/jpeg';

        console.log(`[Gemini] Converting uploaded photo to elevation drawing${buildingName ? ` (${buildingName})` : ''}...`);
        const promptText = buildingName
            ? `${DRAWING_PROMPT}\n\nCONTEXT\n- The structure in the photo is: ${buildingName}. Use your knowledge of its real architecture to make the elevation accurate.`
            : DRAWING_PROMPT;

        let lastError = 'unknown';

        // Provider 0: OpenRouter image model, conditioned on the uploaded photo
        if (OPENROUTER_API_KEY) {
            try {
                console.log(`[OpenRouter] Converting photo via ${OPENROUTER_IMAGE_MODEL}...`);
                const dataUrl = await generateOpenRouterImage(promptText, { mime, b64 });
                console.log(`[OpenRouter] Drawing generated (${Math.round(dataUrl.length * 0.75 / 1024)} KB)`);
                return res.json({ imageBase64: dataUrl, model: OPENROUTER_IMAGE_MODEL });
            } catch (e) {
                lastError = e.message;
                console.log(`[OpenRouter] Failed (${e.message.substring(0, 120)}) — trying Gemini direct`);
            }
        }
        for (const model of GEMINI_IMAGE_MODELS) {
            try {
                const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
                    method: 'POST',
                    headers: { 'x-goog-api-key': GEMINI_API_KEY, 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        contents: [{
                            parts: [
                                { inline_data: { mime_type: mime, data: b64 } },
                                { text: promptText }
                            ]
                        }],
                        generationConfig: { responseModalities: ['IMAGE', 'TEXT'] }
                    })
                });
                const data = await r.json();
                if (r.status === 429) {
                    // Image-generation quota exhausted (paid feature) — fail fast so the
                    // client falls back to the free vision-identification path
                    lastError = 'image generation quota exceeded (requires paid plan)';
                    console.log(`[Gemini] ${model}: quota exceeded — skipping remaining models`);
                    logApiCall('GeminiImage', `/${model}`, buildingName || 'photo', 0, 0, false, lastError);
                    break;
                }
                if (!r.ok) {
                    lastError = data.error?.message || `HTTP ${r.status}`;
                    console.log(`[Gemini] ${model}: ${lastError.substring(0, 120)}`);
                    logApiCall('GeminiImage', `/${model}`, buildingName || 'photo', 0, 0, false, lastError);
                    continue;
                }
                const parts = data.candidates?.[0]?.content?.parts || [];
                const imgPart = parts.find(p => p.inlineData?.data || p.inline_data?.data);
                if (imgPart) {
                    const out = imgPart.inlineData || imgPart.inline_data;
                    console.log(`[Gemini] ${model} produced drawing (${Math.round(out.data.length * 0.75 / 1024)} KB)`);
                    logApiCall('GeminiImage', `/${model}`, buildingName || 'photo', out.data.length, 0, true);
                    return res.json({
                        imageBase64: `data:${out.mimeType || out.mime_type || 'image/png'};base64,${out.data}`,
                        model
                    });
                }
                lastError = 'model returned no image';
            } catch (e) {
                lastError = e.message;
                console.log(`[Gemini] ${model} failed: ${e.message}`);
            }
        }
        // Gemini image generation unavailable — generate with FLUX.1-dev instead.
        // Needs a building name (from vision identification) since FLUX is
        // text-to-image and cannot see the uploaded photo.
        if (buildingName) {
            try {
                const { dataUrl, model } = await generateFluxDrawing(buildingName, description);
                return res.json({ imageBase64: dataUrl, model });
            } catch (e) {
                console.log(`[FLUX] Failed: ${e.message}`);
                lastError += `; FLUX: ${e.message}`;
            }
        }
        res.status(502).json({ error: `Drawing generation failed: ${lastError}` });
    } catch (e) {
        console.error('[Gemini] Endpoint error:', e.message);
        res.status(500).json({ error: e.message });
    }
});

// =========================================================
// GEMINI VISION PHOTO IDENTIFICATION (free tier)
// Identifies the structure in an uploaded photo and returns the same
// structural-profile JSON as /api/building-profile — powers uploads even
// without paid image generation
// =========================================================
app.post('/api/analyze-photo', async (req, res) => {
    try {
        const { imageBase64, mimeType } = req.body || {};
        if (!imageBase64) return res.status(400).json({ error: 'imageBase64 required' });
        if (!GEMINI_API_KEY) return res.status(503).json({ error: 'GEMINI_API_KEY not configured' });

        const b64 = imageBase64.replace(/^data:[^;]+;base64,/, '');
        const mime = mimeType || (imageBase64.match(/^data:([^;]+);/) || [])[1] || 'image/jpeg';

        console.log('[Gemini Vision] Identifying structure in uploaded photo...');
        const prompt = `Identify the building/structure in this photograph and return ONLY strict valid JSON (no markdown fences, no commentary) with EXACTLY these keys:
{
 "name": "canonical name of the structure (or a short descriptive name if unidentifiable)",
 "description": "one detailed sentence describing its architectural appearance for an artist: shape, key features, arches, domes, minarets, towers, materials",
 "category": "skyscraper|tower|monument|fort|palace|temple|statue|bridge|stadium|cathedral|other",
 "height_m": number, "base_width_m": number (front elevation), "top_width_m": number,
 "floors": number (0 if not applicable),
 "structural_system": "short", "primary_material": "short", "foundation": "short",
 "density_kgm3": number, "lat": number, "lng": number,
 "seismic_zone": "string", "wind_design_kmh": number,
 "max_stress_mpa": number (material capacity estimate),
 "profile": [[t,w],...] 12 to 16 pairs, t=normalized height from ground 0..1, w=front-elevation silhouette width factor 0..1 relative to base — describe the REAL shape seen in the photo,
 "setbacks": [{"h_m": number, "label": "short"}] up to 3,
 "annotations": [{"h_m": number, "label": "short engineering note", "side": "left" or "right"}] up to 4
}
Use real measured facts if you recognize the structure; otherwise estimate carefully from the photo.`;

        const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`, {
            method: 'POST',
            headers: { 'x-goog-api-key': GEMINI_API_KEY, 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ parts: [
                    { inline_data: { mime_type: mime, data: b64 } },
                    { text: prompt }
                ] }],
                // thinkingBudget 0: without it the model's hidden reasoning consumes
                // the output budget and truncates the JSON
                generationConfig: { temperature: 0.2, maxOutputTokens: 3000, thinkingConfig: { thinkingBudget: 0 } }
            })
        });
        const data = await r.json();
        if (!r.ok) {
            const msg = data.error?.message || `HTTP ${r.status}`;
            console.log(`[Gemini Vision] Failed: ${msg.substring(0, 120)}`);
            logApiCall('Gemini', '/vision (photo ID)', 'uploaded photo', 0, 0, false, msg);
            return res.status(502).json({ error: msg });
        }
        const text = data.candidates?.[0]?.content?.parts?.map(p => p.text || '').join('') || '';
        logApiCall('Gemini', '/vision (photo ID)', 'uploaded photo', text.length, 0, true);
        let parsed = null;
        try {
            const m = text.match(/\{[\s\S]*\}/);
            if (m) parsed = JSON.parse(m[0]);
        } catch (e) { /* fall through */ }
        if (!parsed || !parsed.height_m) return res.status(422).json({ error: 'could not parse structure profile' });

        console.log(`[Gemini Vision] Identified: ${parsed.name} (${parsed.height_m}m, ${parsed.category})`);
        // Seed the profile cache so subsequent sketch searches use the identity
        if (parsed.name) profileCache.set(parsed.name.toLowerCase(), { data: parsed, at: Date.now() });
        res.json(parsed);
    } catch (e) {
        console.error('[Gemini Vision] Endpoint error:', e.message);
        res.status(500).json({ error: e.message });
    }
});

// =========================================================
// SIMULATION STATE
// =========================================================
let activeSimulation = null;
const simulationHistory = [];

// =========================================================
// SIMULATION API ENDPOINTS
// =========================================================

// POST /api/simulation/start — Trigger a simulation
app.post('/api/simulation/start', async (req, res) => {
    const { type, buildingName, magnitude } = req.body;

    const validTypes = ['storm', 'seismic', 'analysis', 'gravity', 'wind', 'flood', 'lightning', 'fire', 'tornado', 'landslide', 'blizzard', 'volcanic'];
    if (!type || !validTypes.includes(type)) {
        return res.status(400).json({ error: 'Invalid simulation type. Use: ' + validTypes.join(', ') });
    }

    if (activeSimulation) {
        return res.status(409).json({ error: 'Simulation already running', simulationId: activeSimulation.id });
    }

    const simId = 'SIM-' + Date.now().toString(36).toUpperCase();
    const mag = typeof magnitude === 'number' ? Math.max(0.1, Math.min(5.0, magnitude)) : 1.0;

    activeSimulation = {
        id: simId,
        type,
        buildingName: buildingName || 'Structure AP-772-B',
        magnitude: mag,
        status: 'running',
        startTime: Date.now()
    };

    console.log(`\n${'='.repeat(60)}`);
    console.log(`  SIMULATION STARTED: ${simId} | Type: ${type} | Mag: ${mag}`);
    console.log(`${'='.repeat(60)}`);

    // Respond immediately — simulation runs async
    res.json({ simulationId: simId, status: 'running', type, magnitude: mag });

    // Broadcast simulation start to all WS clients
    broadcastWS({ type: 'simulation_start', data: { simulationId: simId, type, status: 'running' } });
    broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Simulation ${simId} initiated. Type: ${type.toUpperCase()}, Magnitude: ${mag}x`, level: 'info' } });

    // Run the simulation pipeline
    runSimulationPipeline(simId, type, mag, buildingName || 'Structure AP-772-B');
});

// GET /api/simulation/status
app.get('/api/simulation/status', (req, res) => {
    res.json(activeSimulation || { status: 'idle' });
});

// GET /api/simulation/verify — Run V&V suite
app.get('/api/simulation/verify', async (req, res) => {
    console.log('\n  Running FEA V&V Suite...');
    const result = await runFEAEngine({ command: 'verify' });
    res.json(result);
});

// GET /api/simulation/structure — Get default structure
app.get('/api/simulation/structure', async (req, res) => {
    const result = await runFEAEngine({ command: 'structure' });
    res.json(result);
});


// =========================================================
// SIMULATION PIPELINE — ORCHESTRATION
// =========================================================
async function runSimulationPipeline(simId, type, magnitude, buildingName) {
    try {
        // Phase 1: Initialize
        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `System initialized. Loading structural model ${buildingName}...`, level: 'info' } });
        await sleep(800);

        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Structural model loaded. Establishing sensor telemetry...`, level: 'info' } });
        await sleep(600);

        // Phase 2: Run FEA Engine (THE ABSOLUTE TRUTH)
        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `FEA Engine activated. Assembling global stiffness matrix K...`, level: 'info' } });
        await sleep(400);

        const feaResult = await runFEAEngine({
            command: 'analyze',
            type: type,
            magnitude: magnitude
        });

        if (feaResult.error) {
            broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `FEA ENGINE ERROR: ${feaResult.error}`, level: 'critical' } });
            activeSimulation = null;
            return;
        }

        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Matrix equation K·u=f solved. ${feaResult.element_stresses.length} elements computed.`, level: 'info' } });
        await sleep(300);

        // Phase 3: Stream telemetry data progressively
        const metrics = feaResult.metrics;
        const stresses = feaResult.element_stresses;
        const displacements = feaResult.node_displacements;

        // Progressive reveal: simulate data arriving in stages
        const stages = 8;
        for (let stage = 1; stage <= stages; stage++) {
            if (!activeSimulation) break;

            const progress = stage / stages;
            const scaleFactor = 0.2 + 0.8 * progress; // ramp from 20% to 100%

            // Scale metrics progressively
            const telemetry = {
                stage: stage,
                oscillation: +(metrics.oscillation_hz * (0.8 + 0.4 * Math.random() * scaleFactor)).toFixed(4),
                drift: +(metrics.lateral_drift_mm * scaleFactor * (0.95 + 0.1 * Math.random())).toFixed(3),
                compression: +(metrics.core_compression_gpa * scaleFactor * (0.9 + 0.2 * Math.random())).toFixed(4),
                beamStresses: stresses.map(s => ({
                    id: s.id,
                    label: s.label,
                    type: s.type,
                    stress: +(s.stress_mpa * scaleFactor).toFixed(3),
                    capacity: +(s.capacity_ratio * scaleFactor).toFixed(5),
                    status: s.capacity_ratio * scaleFactor > 0.9 ? 'CRITICAL' :
                            s.capacity_ratio * scaleFactor > 0.7 ? 'WARNING' : 'NOMINAL'
                })),
                nodeDisplacements: displacements.map(d => ({
                    id: d.id,
                    x: d.x,
                    y: d.y,
                    dx: +(d.dx * scaleFactor * (0.9 + 0.2 * Math.random())).toFixed(5),
                    dy: +(d.dy * scaleFactor * (0.9 + 0.2 * Math.random())).toFixed(5)
                }))
            };

            broadcastWS({ type: 'telemetry', data: telemetry });

            // Log structural events at key stages
            if (stage === 2) {
                broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Broadcasting live stress telemetry to diagnostic nodes.`, level: 'info' } });
            }
            if (stage === 4) {
                const critCount = stresses.filter(s => s.status === 'CRITICAL').length;
                if (critCount > 0) {
                    broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Warning: ${critCount} elements approaching yield threshold.`, level: 'warning' } });
                }
            }
            if (stage === 6) {
                const maxStress = stresses.reduce((max, s) => s.stress_mpa > max.stress_mpa ? s : max, stresses[0]);
                broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Peak stress detected at ${maxStress.label}: ${maxStress.stress_mpa.toFixed(1)} MPa (${(maxStress.capacity_ratio * 100).toFixed(1)}% capacity).`, level: maxStress.status === 'CRITICAL' ? 'critical' : 'warning' } });
            }

            await sleep(1500);
        }

        // Phase 4: AI Interpretation (DeepSeek reads the COMPLETED results)
        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `FEA computation complete. Invoking AI interpreter for analysis...`, level: 'info' } });

        const criticalElements = stresses.filter(s => s.status === 'CRITICAL');
        const warningElements = stresses.filter(s => s.status === 'WARNING');
        const maxDrift = metrics.lateral_drift_mm;

        // Build prompt for DeepSeek
        const aiPrompt = `Analyze these FEA results for a ${type.toUpperCase()} simulation (magnitude ${magnitude}x) on ${buildingName}:

GLOBAL METRICS:
- Max Stress Ratio: ${(metrics.max_stress_ratio * 100).toFixed(1)}%
- Lateral Drift: ${maxDrift.toFixed(1)}mm
- Core Compression: ${metrics.core_compression_gpa.toFixed(3)} GPa
- Seismic Freq: ${metrics.oscillation_hz.toFixed(2)} Hz
- Critical Elements: ${metrics.critical_elements}
- Warning Elements: ${metrics.warning_elements}

TOP 5 MOST STRESSED ELEMENTS:
${stresses.sort((a, b) => b.capacity_ratio - a.capacity_ratio).slice(0, 5).map(s =>
    `  ${s.label}: ${s.stress_mpa.toFixed(1)} MPa (${(s.capacity_ratio * 100).toFixed(1)}% of ${s.yield_strength_mpa} MPa yield) - ${s.status}`
).join('\n')}

Generate exactly 3 event log entries interpreting these results. Format each as:
LEVEL: message
Where LEVEL is INFO, WARNING, or CRITICAL.`;

        const aiResponse = await callDeepSeekAgent(aiPrompt);

        if (aiResponse && aiResponse.text) {
            // Parse AI response into log entries
            const lines = aiResponse.text.split('\n').filter(l => l.trim());
            for (const line of lines) {
                let level = 'info';
                let msg = line.trim();

                if (msg.startsWith('CRITICAL:')) { level = 'critical'; msg = msg.replace('CRITICAL:', '').trim(); }
                else if (msg.startsWith('WARNING:')) { level = 'warning'; msg = msg.replace('WARNING:', '').trim(); }
                else if (msg.startsWith('INFO:')) { level = 'info'; msg = msg.replace('INFO:', '').trim(); }

                if (msg.length > 10) {
                    broadcastWS({ type: 'ai_insight', data: { timestamp: timeNow(), message: msg, level, model: aiResponse.model } });
                    await sleep(800);
                }
            }
        } else {
            // Fallback if AI is unavailable — generate deterministic log entries
            if (metrics.critical_elements > 0) {
                broadcastWS({ type: 'ai_insight', data: { timestamp: timeNow(), message: `CRITICAL: ${metrics.critical_elements} structural elements exceeding 90% yield capacity under ${type} loading. Immediate dampening protocol recommended.`, level: 'critical', model: 'fallback' } });
            }
            if (maxDrift > 100) {
                broadcastWS({ type: 'ai_insight', data: { timestamp: timeNow(), message: `WARNING: Lateral drift of ${maxDrift.toFixed(1)}mm exceeds serviceability limit. Inter-story drift ratio: ${(metrics.drift_ratio * 100).toFixed(2)}%.`, level: 'warning', model: 'fallback' } });
            }
            broadcastWS({ type: 'ai_insight', data: { timestamp: timeNow(), message: `Analysis complete. Peak stress: ${(metrics.max_stress_ratio * 100).toFixed(1)}% capacity. Core compression: ${metrics.core_compression_gpa.toFixed(3)} GPa.`, level: 'info', model: 'fallback' } });
        }

        // Phase 5: Final telemetry with full results
        const finalTelemetry = {
            stage: 8,
            oscillation: metrics.oscillation_hz,
            drift: metrics.lateral_drift_mm,
            compression: metrics.core_compression_gpa,
            beamStresses: stresses.map(s => ({
                id: s.id, label: s.label, type: s.type,
                stress: s.stress_mpa, capacity: s.capacity_ratio, status: s.status
            })),
            nodeDisplacements: displacements.map(d => ({
                id: d.id, x: d.x, y: d.y, dx: d.dx, dy: d.dy
            }))
        };
        broadcastWS({ type: 'telemetry', data: finalTelemetry });

        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `Simulation ${simId} complete. All telemetry channels synchronized.`, level: 'info' } });

        // End simulation
        broadcastWS({ type: 'simulation_end', data: { simulationId: simId, type, status: 'complete', duration: Date.now() - activeSimulation.startTime } });

        simulationHistory.push({
            id: simId,
            type,
            magnitude,
            metrics,
            timestamp: new Date().toISOString()
        });

        activeSimulation = null;
        console.log(`  Simulation ${simId} COMPLETE\n${'='.repeat(60)}\n`);

    } catch (error) {
        console.error('Simulation Pipeline Error:', error);
        broadcastWS({ type: 'log', data: { timestamp: timeNow(), message: `SYSTEM ERROR: ${error.message}`, level: 'critical' } });
        activeSimulation = null;
    }
}


// =========================================================
// WEBSOCKET SERVER
// =========================================================
const { WebSocketServer } = require('ws');

const PORT = process.env.PORT || 3000;
const server = app.listen(PORT, () => {
    console.log('ArchPi Server running on http://localhost:' + PORT);
    console.log('WebSocket server running on ws://localhost:' + PORT + '/ws');
    console.log('LangChain Agent expected at: ' + LANGCHAIN_URL);
    console.log('DeepSeek models: ' + DEEPSEEK_MODELS.join(', '));
});

const wss = new WebSocketServer({ server, path: '/ws' });

const wsClients = new Set();

wss.on('connection', (ws) => {
    wsClients.add(ws);
    console.log(`  [WS] Client connected (${wsClients.size} total)`);

    // Send initial state
    ws.send(JSON.stringify({
        type: 'log',
        data: {
            timestamp: timeNow(),
            message: 'WebSocket connection established. Telemetry channel active.',
            level: 'info'
        }
    }));

    // Send current simulation status
    if (activeSimulation) {
        ws.send(JSON.stringify({
            type: 'simulation_start',
            data: { simulationId: activeSimulation.id, type: activeSimulation.type, status: 'running' }
        }));
    }

    ws.on('close', () => {
        wsClients.delete(ws);
        console.log(`  [WS] Client disconnected (${wsClients.size} total)`);
    });

    ws.on('error', (err) => {
        console.error('  [WS] Error:', err.message);
        wsClients.delete(ws);
    });
});

function broadcastWS(message) {
    const data = JSON.stringify(message);
    for (const client of wsClients) {
        if (client.readyState === 1) { // WebSocket.OPEN
            client.send(data);
        }
    }
}

// =========================================================
// UTILITY HELPERS
// =========================================================
function timeNow() {
    return new Date().toLocaleTimeString('en-GB', { hour12: false });
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
