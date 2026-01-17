import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../App";
import ChatBubble from "../components/ChatBubble";
import LoadingIndicator from "../components/LoadingIndicator";
import TopBar from "../components/TopBar";
import { getCaseProgress, getSession, patchSessionPublic } from "../lib/api";
import type { ApiAuth, ChatMessage, SessionWithMessagesResponse } from "../lib/api";

const formatDate = (value?: string | null) => {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleString();
};

export default function SubmissionViewer() {
  const { auth, authReady, me, signOut } = useAuth();
  const navigate = useNavigate();
  const params = useParams();

  const caseIdParam = (params.caseId ?? "").trim() || null;
  const sessionIdParam = (params.sessionId ?? "").trim() || null;

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const [data, setData] = useState<SessionWithMessagesResponse | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    let cancelled = false;

    const run = async () => {
      setIsLoading(true);
      setError(null);
      try {
        let sessionId = sessionIdParam;
        let caseId = caseIdParam;

        if (!sessionId) {
          if (!caseId) {
            throw new Error("Missing case id.");
          }
          const progress = await getCaseProgress(apiAuth, caseId);
          if (progress.status !== "SOLVED" || !progress.solved_session_id) {
            navigate(`/chat/${encodeURIComponent(caseId)}`, { replace: true });
            return;
          }
          sessionId = progress.solved_session_id;
        }

        const response = await getSession(apiAuth, sessionId);
        if (cancelled) return;
        setData(response);
        setMessages(response.messages ?? []);
        if (!caseId) caseId = response.session.case_id;
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to load submission.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseIdParam, navigate, sessionIdParam]);

  const handleLogout = async () => {
    await signOut();
  };

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-4xl">
          <div className="glass-panel p-8 text-center">
            <LoadingIndicator label="Loading submission..." />
          </div>
        </div>
      </div>
    );
  }

  if (auth.mode === "none") return <Navigate to="/" replace />;

  const session = data?.session;
  const caseId = session?.case_id ?? caseIdParam ?? "";
  const viewerCanToggle = Boolean(data?.viewer_can_toggle_visibility);
  const isPublic = Boolean(session?.is_public);

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
            <span className="tab-pill bg-ink text-white">Submission</span>
            <div className="flex flex-wrap items-center gap-3 text-xs uppercase tracking-[0.24em] text-muted">
              <span>{`Completed`}</span>
              <span className="h-3 w-px bg-border" aria-hidden="true" />
              <span>{`Ended ${formatDate(session?.ended_at ?? null)}`}</span>
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 py-6">
            {error ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
                {error}
              </div>
            ) : null}

            <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
              Completed — Read-only
              <span className="ml-2 text-amber-600">{`• ${isPublic ? "Public" : "Private"}`}</span>
              {data?.author_name ? (
                <span className="ml-2 text-amber-600">
                  {"• "}
                  {data.author_username ? (
                    <Link className="font-semibold hover:underline" to={`/u/${encodeURIComponent(data.author_username)}`}>
                      {data.author_name}
                    </Link>
                  ) : (
                    data.author_name
                  )}
                </span>
              ) : null}
            </div>

            {isLoading ? <LoadingIndicator label="Loading transcript..." /> : null}
            {messages.map((message, index) => (
              <ChatBubble key={`${message.role}-${index}`} message={message} />
            ))}
          </div>

          <div className="border-t border-border bg-slate-50 px-6 py-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-3">
                <button className="btn-secondary" onClick={() => navigate("/problemset")} disabled={isLoading}>
                  Back to Problem Set
                </button>
                {caseId ? (
                  <button
                    className="btn-secondary"
                    onClick={() => navigate(`/problem/${encodeURIComponent(caseId)}?tab=solutions`)}
                    disabled={isLoading}
                  >
                    View Community Submissions
                  </button>
                ) : null}
              </div>

              {viewerCanToggle && session?.session_id ? (
                <label className="inline-flex items-center gap-2 text-sm">
                  <span className="text-muted">{isPublic ? "Public" : "Private"}</span>
                  <input
                    type="checkbox"
                    checked={isPublic}
                    disabled={isLoading || isToggling}
                    onChange={async (event) => {
                      const next = event.target.checked;
                      if (!session?.session_id) return;
                      setIsToggling(true);
                      try {
                        await patchSessionPublic(apiAuth, session.session_id, next);
                        setData((prev) =>
                          prev
                            ? {
                                ...prev,
                                session: { ...prev.session, is_public: next },
                              }
                            : prev
                        );
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Unable to update visibility.");
                      } finally {
                        setIsToggling(false);
                      }
                    }}
                  />
                </label>
              ) : null}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
