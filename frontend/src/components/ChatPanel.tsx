import { useState, useRef, useEffect } from "react";
import { chat, type ChatResponse } from "../api";

interface Message {
  role: "user" | "assistant";
  content: string;
  meta?: ChatResponse;
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setLoading(true);
    try {
      const res = await chat({ question: q });
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.error || res.answer,
          meta: res,
        },
      ]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `Error: ${msg}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <h2>Data Agent</h2>
            <p>上传数据文件，然后用自然语言提问</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <div className="chat-msg-bubble">
              <div className="chat-msg-text">{m.content}</div>
              {m.meta?.sql_query && (
                <details className="chat-sql">
                  <summary>SQL 查询</summary>
                  <pre>{m.meta.sql_query}</pre>
                </details>
              )}
              {m.meta && m.meta.confidence > 0 && (
                <div className="chat-meta">
                  置信度: {(m.meta.confidence * 100).toFixed(0)}% &middot;
                  技能: {m.meta.skill_used}
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="chat-msg chat-msg-assistant">
            <div className="chat-msg-bubble chat-loading">思考中…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <form
        className="chat-input-bar"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          className="chat-input"
          placeholder="输入你的问题…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button className="chat-send" disabled={loading || !input.trim()}>
          发送
        </button>
      </form>
    </div>
  );
}
