# Multi-Agent Travel Planner

A travel planner run by a team of LLM agents. A supervisor reads the request and
routes it to flight, hotel, weather, budget and itinerary agents. The draft
itinerary is paused for human approval before the final plan is written.

## How it works

```text
User request
   -> input guardrail (NeMo Guardrails)
   -> supervisor (picks the agents the request needs)
   -> flight -> hotel -> weather -> budget -> itinerary
   -> human approval (LangGraph interrupt)
   -> final response
   -> output guardrail
```

| Piece | What it uses |
|---|---|
| Orchestration | LangGraph `StateGraph` with a checkpointer (Postgres, or in-memory when `DATABASE_URL` is not set) |
| Model | Groq (`GROQ_MODEL`, default `openai/gpt-oss-20b`) |
| Tools | MCP: Tavily search, AviationStack (bundled server), and a custom weather server |
| Safety | NeMo Guardrails on input and output |
| Tracing | LangSmith (optional) |
| Backend | FastAPI (`api.py`) |
| Web interface | Next.js (`web/`) |
| Alternative interface | Streamlit (`frontend.py`) |

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service status and enabled features |
| `POST /plan` | Run the agents until the draft itinerary is ready |
| `POST /approve` | Approve the draft, or send feedback for a revision |
| `POST /followup` | Answer a question about the saved plan with one model call |

## Run locally

Create a `.env` file in the project root:

```text
GROQ_API_KEY=...
TAVILY_API_KEY=...
AVIATIONSTACK_API_KEY=...
OPENWEATHER_API_KEY=...
```

Backend (Python 3.13):

```bash
pip install -r requirements.txt
uvicorn api:api --reload --port 8000
```

Web interface (Node 20+), in a second terminal:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000. The interface reads the backend address from
`NEXT_PUBLIC_API_URL` and defaults to `http://localhost:8000`.

## Deploy

- **Backend:** Render, using `render.yaml` (free plan). Set the four API keys as
  environment variables.
- **Web interface:** Vercel, with the project's Root Directory set to `web` and
  `NEXT_PUBLIC_API_URL` set to the Render URL.

The free backend sleeps when idle, so the first request after a break can take
about a minute.

## Credits

`aviationstack-mcp/` is the open-source
[AviationStack MCP server](https://github.com/pradumnasaraf/aviationstack-mcp)
by Pradumna Saraf (MIT licence), bundled here unchanged.
