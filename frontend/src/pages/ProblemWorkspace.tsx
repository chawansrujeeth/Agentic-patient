import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../App";
import ChatBubble from "../components/ChatBubble";
import LoadingIndicator from "../components/LoadingIndicator";
import TopBar from "../components/TopBar";
import { getCaseDetails, getCaseProgress, getSession, listCaseSubmissions, patchSessionPublic } from "../lib/api";
import type { ApiAuth, CaseDetails, CaseSubmissionListItem, ChatMessage, SessionWithMessagesResponse } from "../lib/api";

type TabKey = "problem" | "editorial" | "solutions" | "submission" | "leaderboard";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "problem", label: "Problem" },
  { key: "editorial", label: "Editorial" },
  { key: "solutions", label: "Solutions" },
  { key: "submission", label: "Submission" },
  { key: "leaderboard", label: "Leaderboard" },
];

const normalizeTab = (raw: string | null): TabKey => {
  const key = (raw ?? "").trim().toLowerCase();
  if (key === "editorial") return "editorial";
  if (key === "solutions") return "solutions";
  if (key === "submission") return "submission";
  if (key === "leaderboard") return "leaderboard";
  return "problem";
};

const difficultyBadgeClass = (difficulty: string) => {
  if (difficulty === "Easy") return "bg-emerald-100 text-emerald-700 border-emerald-200";
  if (difficulty === "Medium") return "bg-amber-100 text-amber-700 border-amber-200";
  return "bg-rose-100 text-rose-700 border-rose-200";
};

const formatDate = (value?: string | null) => {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleString();
};

export default function ProblemWorkspace() {
  const { auth, authReady, me, signOut } = useAuth();
  const navigate = useNavigate();
  const params = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const caseId = (params.caseId ?? "").trim();

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const tab = useMemo(() => normalizeTab(searchParams.get("tab")), [searchParams]);

  const [progress, setProgress] = useState<{
    status: "NOT_STARTED" | "IN_PROGRESS" | "SOLVED";
    solved_session_id: string | null;
    last_session_id: string | null;
  } | null>(null);
  const [caseDetails, setCaseDetails] = useState<CaseDetails | null>(null);
  const [submissions, setSubmissions] = useState<CaseSubmissionListItem[]>([]);
  const [submissionData, setSubmissionData] = useState<SessionWithMessagesResponse | null>(null);
  const [isPublic, setIsPublic] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!caseId) return;

    let cancelled = false;
    const run = async () => {
      setIsLoading(true);
      setError(null);
      setProgress(null);
      try {
        const p = await getCaseProgress(apiAuth, caseId);
        if (cancelled) return;
        setProgress(p);
        if (p.status !== "SOLVED") {
          const qs = new URLSearchParams();
          if (p.status === "IN_PROGRESS" && p.last_session_id) {
            qs.set("session_id", p.last_session_id);
          }
          const suffix = qs.toString() ? `?${qs.toString()}` : "";
          navigate(`/chat/${encodeURIComponent(caseId)}${suffix}`, { replace: true });
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to load progress.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseId, navigate]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!caseId) return;
    let cancelled = false;
    const run = async () => {
      try {
        const response = await getCaseDetails(apiAuth, caseId);
        if (!cancelled) setCaseDetails(response.case);
      } catch {
        if (!cancelled) setCaseDetails(null);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseId]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!caseId) return;
    if (tab !== "submission") return;
    if (!progress) return;
    if (progress.status !== "SOLVED") return;
    if (!progress.solved_session_id) {
      setSubmissionData(null);
      setError("Submission not found. Contact support.");
      return;
    }

    let cancelled = false;
    const run = async () => {
      setError(null);
      try {
        const response = await getSession(apiAuth, progress.solved_session_id as string);
        if (cancelled) return;
        setSubmissionData(response);
        setIsPublic(Boolean(response.session.is_public));
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to load submission.");
        setSubmissionData(null);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseId, progress, tab]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!caseId) return;
    if (tab !== "solutions") return;
    let cancelled = false;
    const run = async () => {
      try {
        const response = await listCaseSubmissions(apiAuth, caseId, 20);
        if (!cancelled) setSubmissions(response.items ?? []);
      } catch {
        if (!cancelled) setSubmissions([]);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, caseId, tab]);

  const handleLogout = async () => {
    await signOut();
  };

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-4xl">
          <div className="glass-panel p-8 text-center">
            <LoadingIndicator label="Loading workspace..." />
          </div>
        </div>
      </div>
    );
  }

  if (auth.mode === "none") return <Navigate to="/" replace />;
  if (!caseId) return <Navigate to="/problemset" replace />;

  const title = caseDetails?.title ?? caseId;
  const difficulty = caseDetails?.difficulty ?? "Easy";
  const tags = caseDetails?.tags ?? [];

  const submissionMessages: ChatMessage[] = submissionData?.messages ?? [];
  const submissionMeta = submissionData?.session;
  const submissionEndedAt = submissionMeta?.ended_at ?? null;
  const canTogglePublic = Boolean(submissionData?.viewer_can_toggle_visibility);

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

        <section className="glass-panel p-6 sm:p-8">
          <header className="flex flex-col gap-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-semibold text-ink">{title}</h2>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${difficultyBadgeClass(
                      difficulty
                    )}`}
                  >
                    {difficulty}
                  </span>
                  {tags.length ? (
                    <span className="text-xs text-muted">{tags.join(" · ")}</span>
                  ) : (
                    <span className="text-xs text-muted">—</span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn-secondary" onClick={() => navigate("/problemset")}>
                  Back to Problem Set
                </button>
              </div>
            </div>

            <nav className="flex flex-wrap gap-4 border-b border-border">
              {TABS.map((t) => {
                const isActive = t.key === tab;
                return (
                  <button
                    key={t.key}
                    className={`-mb-px border-b-2 px-2 py-2 text-sm font-semibold transition-colors ${
                      isActive ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"
                    }`}
                    onClick={() => {
                      const next = new URLSearchParams(searchParams);
                      next.set("tab", t.key);
                      setSearchParams(next, { replace: true });
                    }}
                  >
                    {t.label}
                  </button>
                );
              })}
            </nav>
          </header>

          {error ? (
            <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
              {error}
            </div>
          ) : null}

          {tab === "problem" ? (
            <div className="mt-6 space-y-6">
              <div className="rounded-lg border border-border bg-card px-5 py-4">
                <h3 className="text-sm font-semibold text-ink">Prompt</h3>
                <p className="mt-2 text-sm text-muted">{caseDetails?.short_prompt ?? "—"}</p>
              </div>

              <div className="rounded-lg border border-border bg-card px-5 py-4">
                <h3 className="text-sm font-semibold text-ink">Patient Presentation</h3>
                <p className="mt-2 whitespace-pre-wrap text-sm text-muted">
                  {caseDetails?.patient_presentation ?? caseDetails?.short_prompt ?? "—"}
                </p>
              </div>
            </div>
          ) : null}

          {tab === "editorial" ? (
            <div className="mt-6 rounded-lg border border-border bg-card px-5 py-8 text-center text-sm text-muted">
              Editorial will be added soon.
            </div>
          ) : null}

          {tab === "solutions" ? (
            <div className="mt-6 space-y-4">
              <div className="rounded-lg border border-border bg-card px-5 py-4 text-sm text-muted">
                Solutions will be added soon.
              </div>
              <div className="rounded-lg border border-border bg-card px-5 py-4">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-ink">Community Submissions</h3>
                  <span className="text-xs text-muted">{`${submissions.length} found`}</span>
                </div>
                {!submissions.length ? (
                  <div className="mt-3 text-sm text-muted">No public submissions yet.</div>
                ) : (
                  <div className="mt-3 space-y-2">
                    {submissions.map((item) => (
                      <button
                        key={item.session_id}
                        className="w-full rounded-lg border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-slate-50"
                        onClick={() => navigate(`/submission/${encodeURIComponent(item.session_id)}`)}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-semibold text-ink">
                              {item.author_username ? (
                                <Link
                                  to={`/u/${encodeURIComponent(item.author_username)}`}
                                  className="hover:underline"
                                  onClick={(event) => event.stopPropagation()}
                                >
                                  {item.author_name}
                                </Link>
                              ) : (
                                item.author_name
                              )}
                            </div>
                            <div className="mt-1 text-xs text-muted">{`Ended: ${formatDate(item.ended_at ?? null)}`}</div>
                          </div>
                          <div className="text-xs text-muted">{`${item.message_count} message${
                            item.message_count === 1 ? "" : "s"
                          }`}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {tab === "submission" ? (
            <div className="mt-6">
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                Completed — Read-only
                <span className="ml-2 text-amber-600">{`• Ended ${formatDate(submissionEndedAt)}`}</span>
                <span className="ml-2 text-amber-600">{`• ${isPublic ? "Public" : "Private"}`}</span>
              </div>

              {canTogglePublic && submissionMeta?.session_id ? (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
                  <div className="text-sm">
                    <div className="font-semibold text-ink">Sharing</div>
                    <div className="text-xs text-muted">Toggle visibility for your completed submission.</div>
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
                          await patchSessionPublic(apiAuth, submissionMeta.session_id, next);
                        } catch (err) {
                          setIsPublic(!next);
                          setError(err instanceof Error ? err.message : "Unable to update visibility.");
                        }
                      }}
                    />
                  </label>
                </div>
              ) : null}

              <div className="mt-6 rounded-lg border border-border bg-card">
                <div className="max-h-[60vh] overflow-y-auto px-6 py-6">
                  {isLoading ? <LoadingIndicator label="Loading..." /> : null}
                  {submissionMessages.map((message, index) => (
                    <ChatBubble key={`${message.role}-${index}`} message={message} />
                  ))}
                  {!isLoading && !submissionMessages.length ? (
                    <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted">
                      No messages found.
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          ) : null}

          {tab === "leaderboard" ? (
            <div className="mt-6 rounded-lg border border-border bg-card px-5 py-8 text-center text-sm text-muted">
              Coming soon.
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
