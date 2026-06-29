import { useState, useRef, useCallback, useEffect } from "react";
import { streamChat, getCurrentUser } from "../api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [userRole, setUserRole] = useState<string>("member");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setUserRole(u.role))
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const canSend = userRole !== "viewer";

  const sendMessage = useCallback(async () => {
    if (!input.trim() || streaming || !canSend) return;
    const userMsg: ChatMessage = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);

    let assistantContent = "";
    try {
      const history = [...messages, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));
      for await (const event of streamChat(history)) {
        if (event.type === "text") {
          assistantContent += event.data.content as string;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant") {
              // Update existing assistant message with the latest content
              return [...prev.slice(0, -1), { role: "assistant", content: assistantContent }];
            } else {
              // First chunk: append a new assistant message
              return [...prev, { role: "assistant", content: assistantContent }];
            }
          });
        } else if (event.type === "error") {
          const errMsg = (event.data as { message?: string })?.message || "Unknown error";
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: `⚠️ Error: ${errMsg}` },
          ]);
          break;
        }
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Network error";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `⚠️ Error: ${errMsg}` },
      ]);
    } finally {
      setStreaming(false);
    }
  }, [input, messages, streaming, canSend]);

  return (
    <div className="chat-container">
      <div className="page-header">
        <h1 className="page-title">Chat</h1>
        <p className="page-subtitle">Interact with your AI agents</p>
      </div>

      {userRole === "viewer" && (
        <div className="chat-viewer-notice">
          You are in viewer mode. You can see chat results but cannot send messages.
        </div>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--text-muted)" }}>
            <div style={{ fontSize: "2.5rem", marginBottom: 12 }}>💬</div>
            <p style={{ fontSize: "0.95rem" }}>Start a conversation with your AI agent.</p>
            <p style={{ fontSize: "0.82rem", marginTop: 4 }}>Type a message below to begin.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`chat-message chat-message-${m.role}`}
          >
            <div className={`chat-bubble chat-bubble-${m.role}`}>
              <div className="chat-bubble-label">{m.role}</div>
              {m.content}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <input
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
          placeholder={canSend ? "Type a message..." : "Viewing only"}
          disabled={streaming || !canSend}
        />
        <button
          className="btn btn-primary"
          onClick={sendMessage}
          disabled={streaming || !canSend}
        >
          {streaming ? "Sending..." : "Send"}
        </button>
      </div>
    </div>
  );
}