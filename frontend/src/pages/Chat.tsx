import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../App";
import CaseAutoStart from "../components/CaseAutoStart";
import ChatBubble from "../components/ChatBubble";
import LoadingIndicator from "../components/LoadingIndicator";
import TopBar from "../components/TopBar";
import { createSession, endVisit, getSession, sendMessage } from "../lib/api";
import type { ApiAuth, ChatMessage, SessionState } from "../lib/api";

const VIRAL_CASE_IDS = [
  "case_flu_like",
  "case_viral_headache_1",
  "case_viral_sore_throat_1",
  "case_viral_gi_1",
  "case_viral_cough_1",
  "case_viral_sinus_1",
  "case_viral_fatigue_1",
];

const pickRandomCaseId = (caseIds: string[]) =>
  caseIds[Math.floor(Math.random() * caseIds.length)];

const getCaseIdForMode = (mode: string, currentCaseId?: string | null) =>
  mode === "guest"
    ? currentCaseId && VIRAL_CASE_IDS.includes(currentCaseId)
      ? currentCaseId
      : pickRandomCaseId(VIRAL_CASE_IDS)
    : "case_diabetes_adherence_1";

const getSessionStorageKey = (auth: { mode: string; guestId?: string | null; email?: string | null }) => {
  if (auth.mode === "guest" && auth.guestId) {
    return `session_id_guest_${auth.guestId}`;
  }
  if (auth.mode === "google" && auth.email) {
    return `session_id_google_${auth.email}`;
  }
  if (auth.mode === "google") {
    return "session_id_google";
  }
  return null;
};

const getStoredSessionId = (auth: { mode: string; guestId?: string | null; email?: string | null }) => {
  const key = getSessionStorageKey(auth);
  if (!key) return null;
  return localStorage.getItem(key);
};

const storeSessionId = (
  auth: { mode: string; guestId?: string | null; email?: string | null },
  sessionId: string
) => {
  const key = getSessionStorageKey(auth);
  if (!key) return;
  localStorage.setItem(key, sessionId);
};

const clearStoredSessionId = (auth: { mode: string; guestId?: string | null; email?: string | null }) => {
  const key = getSessionStorageKey(auth);
  if (!key) return;
  localStorage.removeItem(key);
};

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

const mergeState = (previous: SessionState, next?: SessionState) => ({
  ...previous,
  ...(next ?? {}),
});

const pickState = (payload: { updated_state?: SessionState; state?: SessionState }) =>
  payload.updated_state ?? payload.state ?? undefined;

export default function Chat() {
  const { auth, authReady, signOut } = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionState, setSessionState] = useState<SessionState>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [caseId, setCaseId] = useState(() => getCaseIdForMode(auth.mode));
  const [isBusy, setIsBusy] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const apiAuth: ApiAuth = useMemo(
    () => ({
      mode: auth.mode,
      accessToken: auth.accessToken,
      guestId: auth.guestId,
    }),
    [auth.mode, auth.accessToken, auth.guestId]
  );
  const storedSessionId = useMemo(() => {
    if (!authReady || auth.mode === "none") return null;
    return getStoredSessionId(auth);
  }, [authReady, auth.mode, auth.guestId, auth.email]);

  useEffect(() => {
    if (!authReady) return;
    if (auth.mode === "none") {
      setSessionId(null);
      setSessionState({});
      setMessages([]);
    }
  }, [authReady, auth.mode]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    setCaseId((prev) => getCaseIdForMode(auth.mode, prev));
  }, [authReady, auth.mode]);

  useEffect(() => {
    if (!authReady || auth.mode === "none" || sessionId || isRestoring) {
      return;
    }
    if (!storedSessionId) {
      return;
    }

    const restoreSession = async () => {
      setIsRestoring(true);
      setIsBusy(true);
      setBusyLabel("Restoring session...");
      setError(null);
      try {
        const data = await getSession(apiAuth, storedSessionId);
        setSessionId(data.session_id);
        setSessionState((prev) => mergeState(prev, data.state));
        setMessages(normalizeMessages(data.last_messages));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to restore session.");
        clearStoredSessionId(auth);
      } finally {
        setIsRestoring(false);
        setIsBusy(false);
        setBusyLabel(null);
      }
    };

    restoreSession();
  }, [authReady, auth.mode, auth.guestId, auth.email, sessionId, isRestoring, apiAuth, storedSessionId]);

  const startSession = async (preferredCaseId?: string) => {
    const targetCaseId = preferredCaseId ?? caseId;
    setError(null);
    setIsBusy(true);
    setBusyLabel("Starting session...");

    const attemptCreate = async (targetCaseId: string) => {
      const data = await createSession(apiAuth, targetCaseId);
      setSessionId(data.session_id);
      storeSessionId(auth, data.session_id);
      setSessionState((prev) =>
        mergeState(prev, {
          ...data.state,
          session_id: data.session_id,
        })
      );
      setMessages(normalizeMessages(data.last_messages));
      setCaseId(targetCaseId);
    };

    const normalizeErrorMessage = (err: unknown) => {
      const raw = err instanceof Error ? err.message : "Unable to start session.";
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object" && "error" in parsed) {
          return String(parsed.error);
        }
      } catch {
        return raw;
      }
      return raw;
    };

    try {
      await attemptCreate(targetCaseId);
    } catch (err) {
      setError(normalizeErrorMessage(err));
    } finally {
      setIsBusy(false);
      setBusyLabel(null);
    }
  };

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || !sessionId || isBusy) return;

    const outgoing: ChatMessage = { role: "doctor", content: trimmed };
    setMessages((prev) => [...prev, outgoing]);
    setInput("");
    setIsBusy(true);
    setBusyLabel("Patient is responding...");
    setError(null);

    try {
      const response = await sendMessage(apiAuth, sessionId, trimmed);
      const nextState = pickState(response);
      if (nextState) {
        setSessionState((prev) => mergeState(prev, nextState));
      }
      const nextMessages = normalizeMessages(response.last_messages ?? response.messages);
      if (nextMessages.length) {
        setMessages(nextMessages);
      } else if (response.patient_message) {
        setMessages((prev) => [...prev, { role: "patient", content: response.patient_message }]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to send message.");
    } finally {
      setIsBusy(false);
      setBusyLabel(null);
    }
  };

  const handleEndVisit = async () => {
    if (!sessionId || isBusy) return;
    setIsBusy(true);
    setBusyLabel("Ending visit...");
    setError(null);

    try {
      const response = await endVisit(apiAuth, sessionId);
      const nextState = pickState(response);
      if (nextState) {
        setSessionState((prev) => mergeState(prev, nextState));
      }
      const nextMessages = normalizeMessages(response.last_messages ?? response.messages);
      if (nextMessages.length) {
        setMessages(nextMessages);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to end visit.");
    } finally {
      setIsBusy(false);
      setBusyLabel(null);
    }
  };

  const handleEndChat = async () => {
    if (!sessionId || isBusy) return;
    setIsBusy(true);
    setBusyLabel("Ending chat...");
    setError(null);

    try {
      await endVisit(apiAuth, sessionId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to end visit.");
    } finally {
      clearStoredSessionId(auth);
      setSessionId(null);
      setSessionState({});
      setMessages([]);
      setInput("");
      const nextCaseId = getCaseIdForMode(auth.mode);
      setCaseId(nextCaseId);
      await startSession(nextCaseId);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const inputDisabled =
    isBusy || !sessionId || isRestoring || sessionState.status?.toLowerCase() === "ended";
  const displayMessages = messages;

  useEffect(() => {
    const target = bottomRef.current;
    if (!target) return;
    target.scrollIntoView({ block: "end" });
  }, [displayMessages.length, isBusy, sessionId]);

  const handleLogout = async () => {
    clearStoredSessionId(auth);
    setSessionId(null);
    setSessionState({});
    setMessages([]);
    await signOut();
  };

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

  if (auth.mode === "none") {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="min-h-screen px-6 py-10">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8">
        <TopBar mode={auth.mode} email={auth.email} guestId={auth.guestId} onLogout={handleLogout} />

        <div className="flex flex-col gap-6">
          <section className="glass-panel flex h-[70vh] min-h-[520px] w-full flex-col overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/60 px-6 py-4">
              <span className="tab-pill bg-ink text-white">Chat</span>
              {sessionId ? (
                <div className="flex items-center gap-3 text-xs uppercase tracking-[0.24em] text-muted">
                  <span>{`Session ${sessionId.slice(0, 8)}`}</span>
                  <span className="h-3 w-px bg-white/60" aria-hidden="true" />
                  <span>{`Visit ${sessionState.visit_number ?? "--"}`}</span>
                </div>
              ) : (
                <div className="text-xs uppercase tracking-[0.24em] text-muted">Preparing session</div>
              )}
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 py-6">
              {error ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
                  {error}
                </div>
              ) : null}

              {displayMessages.length === 0 && !isBusy ? (
                <div className="rounded-2xl border border-white/60 bg-white/70 px-4 py-6 text-center text-sm text-muted">
                  Awaiting the first prompt. Start the visit when you are ready.
                </div>
              ) : null}
              {displayMessages.map((message, index) => (
                <ChatBubble key={`${message.role}-${index}`} message={message} />
              ))}
              {isBusy ? <LoadingIndicator label={busyLabel ?? undefined} /> : null}
              <div ref={bottomRef} />
            </div>

            <div className="border-t border-white/60 px-6 py-5">
              <div className="input-shell">
                <textarea
                  className="h-16 w-full resize-none bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
                  placeholder={inputDisabled ? "Session locked" : "Ask the patient a question..."}
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
                <button className="btn-secondary" onClick={handleEndVisit} disabled={!sessionId || isBusy}>
                  End Visit
                </button>
                <button className="btn-secondary" onClick={handleEndChat} disabled={!sessionId || isBusy}>
                  End Chat
                </button>
              </div>
            </div>
          </section>

        </div>
      </div>

      <CaseAutoStart
        enabled={authReady && auth.mode !== "none" && !sessionId && !isRestoring && !storedSessionId}
        onStart={startSession}
      />
    </div>
  );
}
