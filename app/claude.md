# Project Setup

ArchPi runs as four services, all started from `app/`:

- Main server (UI + research + FEA): `npm run dev` (Node, port 3000)
- LangChain agent (LLM extraction): `python langchain_agent.py` (Flask, port 5001)
- Soil backend: `python -m uvicorn soil_backend.main:app --port 8000`
- Forensic backend: `python -m uvicorn forensic_backend.main:app --port 8010`

- Run tests: `pytest tests/ -q` (or `npm test`)
- Autonomy Rule: Always run the tests using your Bash tool after you edit a file to verify your own work.
