import { useState } from "react";
import type { FormEvent } from "react";
import { sendChatMessage } from "../api";
import type { AgentTraceStep, CatalogMode, ChatResponse, OptimizeResponse } from "../types";

interface Props {
  catalogMode: CatalogMode;
  // The cart an answer recommends is shown in the page's cart column.
  onCart: (cart: OptimizeResponse) => void;
}

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
}

const EXAMPLE =
  "I need 2 gallons of milk, 2 lb of butter and a dozen eggs, I live in Princeton, NJ. What is my best order?";

const COORDINATOR_LABELS: Record<string, string> = {
  rule_based: "rule-based coordinator (no model)",
  llm: "Claude coordinator",
};

function AgentTrace({ steps }: { steps: AgentTraceStep[] }) {
  return (
    <details className="agent-trace">
      <summary>Breakdown</summary>
      <ol>
        {steps.map((step) => (
          <li key={step.step}>
            <strong>
              {step.sender} → {step.target}
            </strong>{" "}
            {step.action}
            <div className={step.result_status === "error" ? "warning-text" : "helper-text"}>
              {step.error_message ?? step.result_summary}
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

export default function ChatPanel({ catalogMode, onCart }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setMessages((current) => [...current, { role: "user", text }]);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const response = await sendChatMessage(text, conversationId, catalogMode);
      setConversationId(response.conversation_id);
      if (response.recommendation?.cart) onCart(response.recommendation.cart);
      setMessages((current) => [
        ...current,
        { role: "assistant", text: response.reply, response },
      ]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head-row">
        <h2>Chat assistant</h2>
        {messages.length === 0 && (
          <button type="button" className="inline-link-button" onClick={() => setInput(EXAMPLE)}>
            Use an example
          </button>
        )}
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <p className="helper-text">
            Say what you need and which town or zip code you are in (New Jersey, New York or
            Pennsylvania).
          </p>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`chat-message chat-${message.role}`}>
            <div className="chat-bubble">{message.text}</div>
            {message.response && (
              <>
                <span className="helper-text">
                  Answered by the{" "}
                  {COORDINATOR_LABELS[message.response.coordinator] ?? message.response.coordinator}
                </span>
                <AgentTrace steps={message.response.trace} />
              </>
            )}
          </div>
        ))}
        {busy && <p className="helper-text">Working on it…</p>}
      </div>

      {error && <div className="error">{error}</div>}

      <form className="chat-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="2 gallons of milk and a dozen eggs, I live in 08540"
          aria-label="Chat message"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
