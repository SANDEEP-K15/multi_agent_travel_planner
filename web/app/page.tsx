"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BedDouble,
  Check,
  CloudSun,
  LogOut,
  Moon,
  Plane,
  Plus,
  Send,
  Sun,
  Wallet,
} from "lucide-react";

const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/+$/, "");

const QUICK_PROMPTS = [
  "Plan a 7-day Japan trip under ₹2L",
  "5-day Paris romantic getaway",
  "Dubai weekend luxury trip",
];

const AGENT_LABELS: Record<string, string> = {
  flight_agent: "Flights",
  hotel_agent: "Hotels",
  weather_agent: "Weather",
  budget_agent: "Budget",
  itinerary_agent: "Itinerary",
};

type PlanResult = {
  thread_id: string;
  status: "awaiting_approval" | "done" | "blocked";
  supervisor_reasoning: string;
  selected_agents: string[];
  flight_results: string;
  hotel_results: string;
  weather_results: string;
  budget_results: string;
  draft_itinerary?: string;
  final_response?: string;
  message?: string;
};

type Health = {
  status: string;
  features: {
    guardrails: boolean;
    mcp: boolean;
    human_approval: boolean;
    langsmith: boolean;
    checkpointer: string;
  };
};

type Message =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; kind: "text" | "error"; text: string }
  | { id: string; role: "assistant"; kind: "plan"; plan: PlanResult };

type Phase =
  | "idle"
  | "planning"
  | "awaiting_approval"
  | "finalising"
  | "done"
  | "answering";

const BUSY_TEXT: Partial<Record<Phase, string>> = {
  planning: "Agents are planning your trip. This can take up to a minute.",
  finalising: "Writing the final plan.",
  answering: "Reading your saved plan.",
};

let counter = 0;
const newId = () => `m${Date.now()}_${counter++}`;

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(
      "Could not reach the planning service. It may still be waking up; wait a minute and try again.",
    );
  }
  if (!response.ok) {
    let detail = `The planning service returned an error (${response.status}).`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: (props) => (
            <div className="table-wrap">
              <table {...props} />
            </div>
          ),
          a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

function AgentSection({
  icon,
  title,
  text,
  raw,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
  raw?: boolean;
}) {
  if (!text?.trim()) return null;
  return (
    <details className="agent">
      <summary>
        {icon}
        {title}
      </summary>
      <div className="agent-body">
        {raw ? <pre className="raw">{text.trim()}</pre> : <Markdown>{text}</Markdown>}
      </div>
    </details>
  );
}

function PlanCard({
  plan,
  canApprove,
  onApprove,
}: {
  plan: PlanResult;
  canApprove: boolean;
  onApprove: () => void;
}) {
  if (plan.status === "blocked") {
    return (
      <div className="bubble">
        <span className="chip danger">Blocked by guardrail</span>
        <p style={{ margin: "8px 0 0" }}>
          {plan.message || "This request was blocked."}
        </p>
      </div>
    );
  }

  const hasAgentOutput =
    plan.flight_results ||
    plan.hotel_results ||
    plan.weather_results ||
    plan.budget_results;

  return (
    <div className="bubble plan">
      {plan.selected_agents.length > 0 && (
        <>
          <div className="section-label">Supervisor chose</div>
          <div className="chips">
            {plan.selected_agents.map((agent) => (
              <span key={agent} className="chip accent">
                {AGENT_LABELS[agent] ?? agent}
              </span>
            ))}
          </div>
          {plan.supervisor_reasoning && (
            <p className="muted small" style={{ margin: "8px 0 0" }}>
              {plan.supervisor_reasoning}
            </p>
          )}
        </>
      )}

      {hasAgentOutput && (
        <>
          <div className="section-label">Agent findings</div>
          <AgentSection
            icon={<Plane size={16} />}
            title="Flights"
            text={plan.flight_results}
          />
          <AgentSection
            icon={<BedDouble size={16} />}
            title="Hotels (search results)"
            text={plan.hotel_results}
            raw
          />
          <AgentSection
            icon={<CloudSun size={16} />}
            title="Weather"
            text={plan.weather_results}
            raw
          />
          <AgentSection
            icon={<Wallet size={16} />}
            title="Budget"
            text={plan.budget_results}
          />
        </>
      )}

      {plan.status === "awaiting_approval" ? (
        <>
          <div className="section-label">Draft itinerary</div>
          <Markdown>{plan.draft_itinerary || "_No draft was returned._"}</Markdown>
          {canApprove && (
            <div className="approval">
              <span>
                Approve this draft, or type the changes you want in the box below.
              </span>
              <button className="btn primary" onClick={onApprove}>
                <Check size={16} /> Approve draft
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="section-label">Final travel plan</div>
          <Markdown>{plan.final_response || "_No plan was returned._"}</Markdown>
        </>
      )}
    </div>
  );
}

export default function Home() {
  const [mounted, setMounted] = useState(false);
  const [user, setUser] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [health, setHealth] = useState<Health | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const busy = phase === "planning" || phase === "finalising" || phase === "answering";

  // Restore the saved name and theme once we are in the browser.
  useEffect(() => {
    try {
      setUser(localStorage.getItem("tp_user"));
      const saved = localStorage.getItem("tp_theme");
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      setTheme(saved === "dark" || saved === "light" ? saved : prefersDark ? "dark" : "light");
    } catch {
      /* storage can be blocked; the defaults still work */
    }
    setMounted(true);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Poll the backend until it answers: free hosting sleeps when idle.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      try {
        const response = await fetch(`${API_URL}/health`);
        if (!response.ok) throw new Error();
        const data = (await response.json()) as Health;
        if (!cancelled) setHealth(data);
      } catch {
        if (!cancelled) timer = setTimeout(check, 5000);
      }
    };
    check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, phase]);

  const add = useCallback((message: Message) => {
    setMessages((current) => [...current, message]);
  }, []);

  const fail = useCallback(
    (error: unknown, fallback: Phase) => {
      add({
        id: newId(),
        role: "assistant",
        kind: "error",
        text: error instanceof Error ? error.message : "Something went wrong.",
      });
      setPhase(fallback);
    },
    [add],
  );

  const receivePlan = useCallback(
    (plan: PlanResult) => {
      add({ id: newId(), role: "assistant", kind: "plan", plan });
      if (plan.status === "awaiting_approval") {
        setThreadId(plan.thread_id);
        setPhase("awaiting_approval");
      } else if (plan.status === "done") {
        setThreadId(plan.thread_id);
        setPhase("done");
      } else {
        setPhase(threadId ? "done" : "idle");
      }
    },
    [add, threadId],
  );

  const startPlan = async (query: string) => {
    add({ id: newId(), role: "user", text: query });
    setPhase("planning");
    try {
      receivePlan(await post<PlanResult>("/plan", { query, user_id: user ?? "guest" }));
    } catch (error) {
      fail(error, "idle");
    }
  };

  const sendApproval = async (approved: boolean, feedback: string) => {
    if (!threadId) return;
    add({
      id: newId(),
      role: "user",
      text: approved ? "Approved. Finalise the plan." : feedback,
    });
    setPhase("finalising");
    try {
      receivePlan(
        await post<PlanResult>("/approve", { thread_id: threadId, approved, feedback }),
      );
    } catch (error) {
      fail(error, "awaiting_approval");
    }
  };

  const askFollowUp = async (question: string) => {
    add({ id: newId(), role: "user", text: question });
    setPhase("answering");
    try {
      const data = await post<{ status: string; answer: string }>("/followup", {
        thread_id: threadId,
        question,
      });
      add({ id: newId(), role: "assistant", kind: "text", text: data.answer });
      setPhase("done");
    } catch (error) {
      fail(error, "done");
    }
  };

  const submit = (text: string) => {
    const value = text.trim();
    if (!value || busy) return;
    setInput("");
    if (phase === "awaiting_approval") void sendApproval(false, value);
    else if (phase === "done" && threadId) void askFollowUp(value);
    else void startPlan(value);
  };

  const newTrip = () => {
    setMessages([]);
    setThreadId(null);
    setPhase("idle");
    setInput("");
  };

  const signIn = (event: React.FormEvent) => {
    event.preventDefault();
    const name = nameDraft.trim().slice(0, 40);
    if (!name) return;
    try {
      localStorage.setItem("tp_user", name);
    } catch {
      /* ignore */
    }
    setUser(name);
  };

  const switchUser = () => {
    try {
      localStorage.removeItem("tp_user");
    } catch {
      /* ignore */
    }
    newTrip();
    setNameDraft("");
    setUser(null);
  };

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("tp_theme", next);
    } catch {
      /* ignore */
    }
  };

  if (!mounted) return null;

  if (!user) {
    return (
      <main className="gate">
        <form className="card" onSubmit={signIn}>
          <div className="brand-mark">
            <Plane size={20} />
          </div>
          <h1>Routewise</h1>
          <p className="muted" style={{ margin: 0 }}>
            A team of AI agents plans your trip, and you approve the draft before
            it is finalised.
          </p>
          <input
            autoFocus
            value={nameDraft}
            onChange={(event) => setNameDraft(event.target.value)}
            placeholder="Your name"
            aria-label="Your name"
            maxLength={40}
          />
          <button className="btn primary block" disabled={!nameDraft.trim()}>
            Start planning
          </button>
        </form>
      </main>
    );
  }

  const placeholder =
    phase === "awaiting_approval"
      ? "Type the changes you want, or approve the draft above…"
      : phase === "done"
        ? "Ask a follow-up about your plan…"
        : "Where do you want to go?";

  const lastPlanId = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.kind === "plan")?.id;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Plane size={20} />
          </span>
          Routewise
        </div>
        <button
          className="icon-btn"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <section className="card">
            <h2>Session</h2>
            <div className="muted">Hi, {user}</div>
            <div className="thread">
              {threadId ? `Thread: ${threadId}` : "No trip in progress"}
            </div>
            <div className="stack">
              <button className="btn block" onClick={newTrip} disabled={busy}>
                <Plus size={16} /> New trip
              </button>
              <button className="btn block" onClick={switchUser} disabled={busy}>
                <LogOut size={16} /> Switch user
              </button>
            </div>
          </section>

          <section className="card">
            <h2>Quick prompts</h2>
            <div className="stack">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  className="prompt-btn"
                  disabled={busy || phase === "awaiting_approval"}
                  onClick={() => {
                    if (phase === "done") newTrip();
                    void startPlan(prompt);
                  }}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </section>

          <section className="card">
            <h2>System</h2>
            <div className="chips">
              {health ? (
                <>
                  <span className="chip ok">
                    <span className="dot" /> Ready
                  </span>
                  {health.features.guardrails && <span className="chip">Guardrails</span>}
                  {health.features.mcp && <span className="chip">MCP tools</span>}
                  {health.features.human_approval && (
                    <span className="chip">Human approval</span>
                  )}
                  {health.features.langsmith && <span className="chip">LangSmith</span>}
                  <span className="chip">LangGraph</span>
                </>
              ) : (
                <span className="chip warn">
                  <span className="dot" /> Waking up the planning service…
                </span>
              )}
            </div>
            {!health && (
              <p className="muted small" style={{ margin: "10px 0 0" }}>
                Free hosting sleeps when idle. The first load can take about a minute.
              </p>
            )}
          </section>
        </aside>

        <main className="card chat">
          <div className="chat-head">
            <h1>Travel assistant</h1>
            <p className="muted small">
              Plan a trip, review the draft, then ask follow-ups without re-running the
              agents.
            </p>
          </div>

          <div className="messages" aria-live="polite">
            <div className="msg">
              <span className="avatar">
                <Plane size={16} />
              </span>
              <div className="bubble">
                Welcome, <strong>{user}</strong>. Tell me where you want to go, how long
                for, and your budget.
              </div>
            </div>

            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="msg user">
                  <div className="bubble">{message.text}</div>
                </div>
              ) : (
                <div key={message.id} className="msg">
                  <span className="avatar">
                    <Plane size={16} />
                  </span>
                  {message.kind === "plan" ? (
                    <PlanCard
                      plan={message.plan}
                      canApprove={
                        phase === "awaiting_approval" && message.id === lastPlanId
                      }
                      onApprove={() => void sendApproval(true, "")}
                    />
                  ) : message.kind === "error" ? (
                    <div className="bubble error">{message.text}</div>
                  ) : (
                    <div className="bubble">
                      <Markdown>{message.text}</Markdown>
                    </div>
                  )}
                </div>
              ),
            )}

            {busy && (
              <div className="msg">
                <span className="avatar">
                  <Plane size={16} />
                </span>
                <div className="bubble">
                  <span className="typing">
                    <i />
                    <i />
                    <i />
                    <span className="muted">{BUSY_TEXT[phase]}</span>
                  </span>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              submit(input);
            }}
          >
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={placeholder}
              aria-label="Message"
              disabled={busy}
            />
            <button className="btn primary" disabled={busy || !input.trim()}>
              <Send size={16} /> Send
            </button>
          </form>
        </main>
      </div>
    </div>
  );
}
