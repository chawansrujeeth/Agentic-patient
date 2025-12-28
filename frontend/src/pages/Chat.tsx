import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../App";
import ChatBubble from "../components/ChatBubble";
import LoadingIndicator from "../components/LoadingIndicator";
import TopBar from "../components/TopBar";
import { BACKEND_IDLE_MESSAGE, createSession, endChat, getCaseProgress, getSession, patchSessionPublic, sendMessage } from "../lib/api";
import type { ApiAuth, ChatMessage } from "../lib/api";

const normalizeMessages = (messages: Array<ChatMessage | Record<string, unknown>> | undefined | null) => {
  if (!messages) return [];
  return messages
    .map((message) => {
      const role = (message as { role?: string }).role ?? "patient";
      const content =
        (message as { content?: string }).content ?? (message as { message?: string }).message ?? "";
      return { role, content };
    })
    .filter((message) => message.content);
};

export default function Chat() {
  const { auth, authReady, me, signOut } = useAuth();
  const navigate = useNavigate();
  const params = useParams();
  const [searchParams] = useSearchParams();
  const caseId = (params.caseId ?? "").trim();
  const requestedSessionId = (searchParams.get("session_id") ?? "").trim() || null;

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showErrorModal, setShowErrorModal] = useState(false);
  const [isCompleted, setIsCompleted] = useState(false);
  const [isPublic, setIsPublic] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const target = bottomRef.current;
    if (!target) return;
    target.scrollIntoView({ block: "end" });
  }, [messages.length, isBusy]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!caseId) return;
    let cancelled = false;

    const run = async () => {
      setIsBusy(true);
      setBusyLabel("Starting chat...");
      setError(null);
      setShowErrorModal(false);
      setSessionId(null);
      setMessages([]);
      setIsCompleted(false);
      setIsPublic(false);

      try {
        const progress = await getCaseProgress(apiAuth, caseId);
        if (progress.status === "SOLVED") {
          navigate(`/problem/${encodeURIComponent(caseId)}?tab=problem`, { replace: true });
          return;
        }
      } catch {
        // best-effort; proceed to start a new session
      }

      try {
        if (requestedSessionId) {
          const existing = await getSession(apiAuth, requestedSessionId);
          if (cancelled) return;
          if (existing.session.case_id !== caseId) {
            throw new Error("Session does not belong to this case.");
          }
          setSessionId(existing.session.session_id);
          setMessages(normalizeMessages(existing.messages));
          setIsCompleted(existing.session.status === "COMPLETED");
          setIsPublic(Boolean(existing.session.is_public));
          return;
        }
        const created = await createSession(apiAuth, caseId);
        if (cancelled) return;
        setSessionId(created.session_id);
        setMessages(normalizeMessages(created.last_messages));
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Unable to start session.";
        try {
          const parsed = JSON.parse(message);
          if (parsed && typeof parsed === "object" && "solved_session_id" in parsed && parsed.solved_session_id) {
            navigate(`/problem/${encodeURIComponent(caseId)}?tab=problem`, { replace: true });
            return;
          }
        } catch {
          // ignore
        }
        setError(message);
        setShowErrorModal(true);
      } finally {
        if (!cancelled) {
          setIsBusy(false);
          setBusyLabel(null);
        }
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseId, navigate, requestedSessionId]);

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || !sessionId || isBusy || isCompleted) return;

    const outgoing: ChatMessage = { role: "doctor", content: trimmed };
    setMessages((prev) => [...prev, outgoing]);
    setInput("");
    setIsBusy(true);
    setBusyLabel("Patient is responding...");
    setError(null);
    setShowErrorModal(false);

    try {
      const response = await sendMessage(apiAuth, sessionId, trimmed);
      const nextMessages = normalizeMessages(response.last_messages ?? response.messages);
      if (nextMessages.length) {
        setMessages(nextMessages);
      } else if (response.patient_message) {
        setMessages((prev) => [...prev, { role: "patient", content: response.patient_message }]);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to send message.";
      setError(message);
      setShowErrorModal(true);
    } finally {
      setIsBusy(false);
      setBusyLabel(null);
    }
  };

  const handleEndChat = async () => {
    if (!sessionId || isBusy || isCompleted) return;
    setIsBusy(true);
    setBusyLabel("Ending chat...");
    setError(null);
    setShowErrorModal(false);

    try {
      const response = await endChat(apiAuth, sessionId);
      setIsCompleted(true);
      const sessionMeta = response.session as { is_public?: boolean } | undefined;
      setIsPublic(Boolean(sessionMeta?.is_public));
      navigate(`/problem/${encodeURIComponent(caseId)}?tab=submission`, { replace: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to end chat.";
      setError(message);
      setShowErrorModal(true);
    } finally {
      setIsBusy(false);
      setBusyLabel(null);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  };

  const handleLogout = async () => {
    await signOut();
  };

  const inputDisabled = isBusy || !sessionId || isCompleted;

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-4xl">
          <div className="glass-panel p-8 text-center">
            <LoadingIndicator label="Preparing your session..." />
          </div>
        </div>
      </div>
    );
  }

  if (auth.mode === "none") return <Navigate to="/" replace />;
  if (!caseId) return <Navigate to="/problemset" replace />;

  return (
    <div className="min-h-screen px-6 py-10">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8">
        <TopBar
          mode={auth.mode}
          email={auth.email}
          guestId={auth.guestId}
          myUsername={me?.username ?? null}
          onLogout={handleLogout}
        />

        <section className="glass-panel flex h-[70vh] min-h-[520px] w-full flex-col overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border bg-slate-50 px-6 py-4">
            <span className="tab-pill bg-ink text-white">Chat</span>
            <div className="flex items-center gap-3">
              <button className="btn-secondary" onClick={() => navigate(`/problem/${encodeURIComponent(caseId)}?tab=solutions`)}>
                Solutions
              </button>
              <button className="btn-secondary" onClick={() => navigate("/problemset")}>
                Back to Problem Set
              </button>
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 py-6">
            {isCompleted ? (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                Completed — Read-only <span className="ml-2 text-amber-600">{`• ${isPublic ? "Public" : "Private"}`}</span>
              </div>
            ) : null}

            {error ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
                {error}
              </div>
            ) : null}

            {messages.length === 0 && !isBusy ? (
              <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted">
                Awaiting the first prompt. Start the visit when you are ready.
              </div>
            ) : null}
            {messages.map((message, index) => (
              <ChatBubble key={`${message.role}-${index}`} message={message} />
            ))}
            {isBusy ? <LoadingIndicator label={busyLabel ?? undefined} /> : null}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-border bg-slate-50 px-6 py-5">
            <div className="input-shell">
              <textarea
                className="h-16 w-full resize-none bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
                placeholder={inputDisabled ? "Completed — read-only" : "Ask the patient a question..."}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                disabled={inputDisabled}
              />
              <button className="btn-primary" onClick={handleSend} disabled={inputDisabled}>
                Send
              </button>
            </div>

            <div className="mt-4 flex flex-wrap gap-3">
              <button className="btn-secondary" onClick={handleEndChat} disabled={!sessionId || isBusy || isCompleted}>
                End Chat
              </button>
            </div>

            {isCompleted && sessionId ? (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
                <div className="text-sm">
                  <div className="font-semibold text-ink">Sharing</div>
                  <div className="text-xs text-muted">Make your completed chat visible to other users under this case.</div>
                </div>
                <label className="inline-flex items-center gap-2 text-sm">
                  <span className="text-muted">{isPublic ? "Public" : "Private"}</span>
                  <input
                    type="checkbox"
                    checked={isPublic}
                    onChange={async (event) => {
                      const next = event.target.checked;
                      setIsPublic(next);
                      try {
                        await patchSessionPublic(apiAuth, sessionId, next);
                      } catch (err) {
                        setIsPublic(!next);
                        const message = err instanceof Error ? err.message : BACKEND_IDLE_MESSAGE;
                        setError(message);
                        setShowErrorModal(true);
                      }
                    }}
                  />
                </label>
              </div>
            ) : null}
          </div>
        </section>
      </div>

      {showErrorModal && error ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-6 py-10">
          <div className="glass-panel w-full max-w-md p-6 text-center animate-rise">
            <h3 className="text-xl font-semibold text-ink">
              {error === BACKEND_IDLE_MESSAGE ? "Backend is waking up" : "Something went wrong"}
            </h3>
            <p className="mt-3 text-sm text-muted">
              {error === BACKEND_IDLE_MESSAGE ? BACKEND_IDLE_MESSAGE : "Please refresh the page and try again."}
            </p>
            {error !== BACKEND_IDLE_MESSAGE ? <p className="mt-3 text-xs text-muted">{error}</p> : null}
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <button className="btn-secondary" onClick={() => setShowErrorModal(false)}>
                Dismiss
              </button>
              <button className="btn-primary" onClick={() => window.location.reload()}>
                Refresh
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
