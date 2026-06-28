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
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setUserRole(u.role))
      .catch(() => {});
  }, []);

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
          setMessages((prev) => [
            ...prev.slice(0, -1),
            { role: "assistant", content: assistantContent },
          ]);
        }
      }
      if (assistantContent) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: assistantContent },
        ]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setStreaming(false);
    }
  }, [input, messages, streaming, canSend]);

  return (
    <div style={{ maxWidth: 720, margin: "16px auto", padding: 16 }}>
      {userRole === "viewer" && (
        <p style={{ color: "#666", fontStyle: "italic" }}>
          You are in viewer mode. You can see chat results but cannot send messages.
        </p>
      )}
      <div
        style={{
          border: "1px solid #ccc",
          borderRadius: 8,
          padding: 16,
          marginBottom: 16,
          minHeight: 400,
          overflowY: "auto",
        }}
      >
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: 8,
              textAlign: m.role === "user" ? "right" : "left",
            }}
          >
            <strong>{m.role}:</strong> {m.content}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder={canSend ? "Type a message..." : "Viewing only"}
          disabled={streaming || !canSend}
          style={{ flex: 1, padding: 8 }}
        />
        <button onClick={sendMessage} disabled={streaming || !canSend}>
          Send
        </button>
      </div>
    </div>
  );
}
