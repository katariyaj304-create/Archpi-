# LinkedIn Post — ArchPi (copy-paste ready)

> **How to use this file:** Copy the post below straight into LinkedIn. Attach 3–5 screenshots
> (use `docs/screenshots/` — home, simulation, soil, forensic, API dashboard) or better, a
> 60-second screen recording of: search a building → watch the AI research stream → run the
> earthquake simulation. Posts with a video get ~5× the reach. Post between 9–11 AM on a
> weekday for best visibility. Reply to every comment in the first 2 hours.

---

## ⭐ THE MAIN POST (paste this)

I typed "Taj Mahal" into a search box. Four minutes later I had a full engineering dossier — researched, illustrated, physics-tested, and soil-verified. Built by software I wrote myself. Monthly running cost: ₹0.

Meet **ArchPi** — an AI-powered structural engineering platform I built solo. 🏛️

The idea: every building is slowly dying — concrete cracks, iron rusts, soil shifts — but a real engineering check-up costs lakhs and takes weeks. So most buildings never get one. What if it could start from just typing a name?

What ArchPi does for any building on Earth:

🔍 An AI research engine runs 8 parallel web searches, cross-checks ~40 sources against the model's own knowledge, and streams its reasoning live — you literally watch it think

📐 A real Finite-Element physics engine (direct stiffness method, NumPy — no AI guessing) stress-tests the structure under earthquakes and storms. Verified against beam theory to 17 decimal places

📸 Upload a photo of any building — Gemini Vision identifies it, an image model redraws it as a CAD elevation, and the physics runs on that

🌍 Click anywhere on the map — live satellite soil data (ISRIC) + Terzaghi's bearing-capacity equation tell you what that exact ground can hold

🧪 A forensic lab in software: XRD mineral fingerprinting, radiocarbon dating, tree-ring dating, isotope tracing — guarded by 119 automated tests

🤖 ARIA, a built-in JARVIS-style assistant (Qwen 72B) that understands Hindi & English and operates the whole app: "Hawa Mahal ko globe par dhundo" — and it does

The engineering lessons that mattered most:

1️⃣ AI is a storyteller, never a calculator — every safety-critical number comes from deterministic math; the LLM only explains results afterwards

2️⃣ Everything needs a fallback — 5 provider chains (DeepSeek → Llama → Gemini; FLUX → Pollinations...). The day every paid quota ran out, the app kept working

3️⃣ You can't fix what you can't see — every API call is metered against its real free-tier limit on a live dashboard, down to the dollar balance

Stack: Node/Express · Python (FastAPI ×2, Flask) · LangChain · NumPy/SciPy · PostGIS on Supabase · Leaflet · Puppeteer-tested · 4 cooperating services · 11 pages

I'm looking for AI engineering internship opportunities — if this is the kind of builder energy your team needs, my DMs are open. And I'd genuinely love your feedback: what would you add to ArchPi?

#AIEngineering #MachineLearning #StructuralEngineering #BuildInPublic #LLM #Python #NodeJS #SideProject #Internship #GenAI #EngineeringStudent #India

---

## 🔁 SHORTER ALTERNATIVE (if you prefer punchy)

Type a building's name. Get an engineering firm.

That's ArchPi — my solo-built AI platform that researches any building on Earth (40 web sources, live-streamed AI reasoning), draws its blueprint, stress-tests it with a real physics engine (not AI guesses — verified to 17 decimal places against beam theory), checks the soil beneath it via satellite data, and even has a JARVIS-style assistant that takes commands in Hindi and English.

4 servers. 11 pages. 119 automated tests. 8 AI providers arranged in fallback chains so clever the monthly bill is ₹0.

Biggest lesson: in AI engineering, the model is 20% of the work. The other 80% is what happens when the model fails — and designing for that is what separates a demo from a product.

Open to AI engineering internships. DMs open. 🚀

#AIEngineering #BuildInPublic #LLM #Python #GenAI #Internship

---

## 📎 Attachment checklist

- [ ] 3–5 screenshots from `docs/screenshots/` (lead with the simulation heatmap — it's the most striking)
- [ ] OR a 60-second screen recording (best reach)
- [ ] Add the GitHub link in the FIRST COMMENT, not the post body (LinkedIn suppresses posts with external links): `github.com/katariyaj304-create/Archpi`
- [ ] Tag 2–3 relevant people/communities if appropriate
