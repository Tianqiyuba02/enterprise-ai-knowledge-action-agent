"use client";

import {
  ArrowRight,
  BookOpenText,
  Bot,
  CalendarRange,
  CornerDownLeft,
  Headphones,
  LoaderCircle,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, KeyboardEvent, useRef, useState } from "react";

import type { AssistantResponse } from "@/lib/contracts";
import { formatDate, formatHours, sentenceCase } from "@/lib/format";

type ConversationMessage = {
  id: number;
  role: "user" | "assistant";
  body: string;
  response?: AssistantResponse;
  error?: boolean;
};

const suggestions = [
  "How much annual leave do I have?",
  "What does the annual leave policy say?",
  "Prepare annual leave from 12 October 2026 to 16 October 2026 for a family trip.",
  "My laptop cannot connect to the office Wi-Fi. Prepare an IT support request.",
];

export function AssistantWorkspace({ firstName }: { firstName: string }) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const nextId = useRef(1);

  async function send(message: string) {
    const trimmed = message.trim();
    if (!trimmed || pending) return;
    const userMessage: ConversationMessage = {
      id: nextId.current++,
      role: "user",
      body: trimmed,
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setPending(true);
    try {
      const response = await fetch("/api/portal/assistant/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed, initiation_id: crypto.randomUUID() }),
      });
      const payload = (await response.json()) as AssistantResponse | { message?: string };
      if (!response.ok) {
        throw new Error("message" in payload && payload.message ? payload.message : "The assistant is unavailable.");
      }
      const result = payload as AssistantResponse;
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          body: result.answer ?? result.message ?? "The assistant could not complete that request.",
          response: result,
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          body: error instanceof Error ? error.message : "The assistant is unavailable.",
          error: true,
        },
      ]);
    } finally {
      setPending(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="assistant-layout">
      <section className="assistant-main">
        <header className="assistant-header">
          <div className="assistant-avatar"><Bot aria-hidden="true" size={21} /></div>
          <div>
            <p className="eyebrow">Governed employee assistant</p>
            <h1>How can I help, {firstName}?</h1>
          </div>
          <span className="online-indicator"><i /> Ready</span>
        </header>

        <div className="conversation" aria-live="polite">
          {messages.length === 0 ? (
            <div className="conversation-empty">
              <p>Ask about company policy or prepare an annual leave request.</p>
              <div className="suggestion-grid">
                {suggestions.map((suggestion, index) => (
                  <button type="button" onClick={() => void send(suggestion)} key={suggestion}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    {suggestion}
                    <ArrowRight aria-hidden="true" size={15} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <article className="message" data-role={message.role} key={message.id}>
                <span className="message-avatar">
                  {message.role === "assistant" ? <Bot aria-hidden="true" size={17} /> : <UserRound aria-hidden="true" size={17} />}
                </span>
                <div className="message-content" data-error={message.error || undefined}>
                  <p>{message.body}</p>
                  {message.response?.citations.length ? (
                    <div className="citation-list" aria-label="Sources">
                      {message.response.citations.map((citation) => (
                        <Link
                          href={`/policies/${encodeURIComponent(citation.doc_code)}/${encodeURIComponent(citation.version)}#${encodeURIComponent(citation.section_anchor)}`}
                          key={`${citation.doc_code}-${citation.version}-${citation.section_anchor}`}
                        >
                          <BookOpenText aria-hidden="true" size={14} />
                          {citation.title} · {citation.section_anchor}
                        </Link>
                      ))}
                    </div>
                  ) : null}
                  {message.response?.action ? (
                    <div className="prepared-card">
                      <div className="prepared-card-top">
                        <span>{message.response.action.action_type === "submit_annual_leave"
                          ? <CalendarRange aria-hidden="true" size={18} />
                          : <Headphones aria-hidden="true" size={18} />}</span>
                        <div><small>Authoritative persisted draft</small><strong>{message.response.action.action_type === "submit_annual_leave" ? "Annual leave request" : "IT support request"}</strong></div>
                        <span className="authority-badge"><ShieldCheck aria-hidden="true" size={13} /> Authoritative</span>
                      </div>
                      {message.response.action.draft.action_type === "submit_annual_leave" ? (
                        <dl>
                          <div><dt>Dates</dt><dd>{formatDate(message.response.action.draft.start_date)} – {formatDate(message.response.action.draft.end_date)}</dd></div>
                          <div><dt>Duration</dt><dd>{formatHours(message.response.action.draft.requested_hours)}</dd></div>
                          <div><dt>Balance after</dt><dd>{formatHours(message.response.action.draft.projected_balance_hours)}</dd></div>
                        </dl>
                      ) : (
                        <dl>
                          <div><dt>Summary</dt><dd>{message.response.action.draft.summary}</dd></div>
                          <div><dt>Category</dt><dd>{sentenceCase(message.response.action.draft.category)}</dd></div>
                          <div><dt>Urgency</dt><dd>{sentenceCase(message.response.action.draft.urgency)}</dd></div>
                        </dl>
                      )}
                      <p className="prepared-note">Chat cannot authorize or execute this request. Review it on the independent review surface.</p>
                      <Link className="button button-primary" href={message.response.action.action_type === "submit_annual_leave" ? `/leave/review/${message.response.action.action_id}` : `/it/review/${message.response.action.action_id}`}>
                        Review exact draft <ArrowRight aria-hidden="true" size={15} />
                      </Link>
                    </div>
                  ) : null}
                  {message.response?.action_not_created_reason ? (
                    <small className="inline-warning">Draft not created: {sentenceCase(message.response.action_not_created_reason)}</small>
                  ) : null}
                </div>
              </article>
            ))
          )}
          {pending ? (
            <div className="assistant-thinking">
              <LoaderCircle aria-hidden="true" className="spin" size={17} />
              Checking trusted sources…
            </div>
          ) : null}
        </div>

        <form className="composer" onSubmit={submit}>
          <label htmlFor="assistant-message" className="sr-only">Message the employee assistant</label>
          <textarea
            id="assistant-message"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onComposerKeyDown}
            maxLength={4000}
            rows={2}
            placeholder="Ask a policy question, prepare leave, or report an IT issue…"
            disabled={pending}
          />
          <button type="submit" disabled={pending || !input.trim()} aria-label="Send message">
            <CornerDownLeft aria-hidden="true" size={17} />
          </button>
          <small>Enter to send · Shift + Enter for a new line</small>
        </form>
      </section>

      <aside className="assistant-aside">
        <p className="eyebrow">Boundaries</p>
        <h2>Helpful, with a clear line.</h2>
        <div className="boundary-item"><span>01</span><div><strong>Reads trusted data</strong><p>Profile, balances and approved policies are scoped to you.</p></div></div>
        <div className="boundary-item"><span>02</span><div><strong>Prepares, never executes</strong><p>The assistant can create an authoritative draft for review.</p></div></div>
        <div className="boundary-item"><span>03</span><div><strong>You authorize elsewhere</strong><p>Typing “yes” in chat is never approval.</p></div></div>
        <div className="aside-seal"><ShieldCheck aria-hidden="true" size={18} /><span>V4 execution boundary preserved</span></div>
      </aside>
    </div>
  );
}
