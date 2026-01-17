import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../App";
import LoadingIndicator from "../components/LoadingIndicator";
import TopBar from "../components/TopBar";
import { getUserProfile, listUserSubmissions } from "../lib/api";
import type { ApiAuth, UserProfileResponse, UserSubmissionsResponse } from "../lib/api";

const formatDate = (value?: string | null) => {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleDateString();
};

const formatRelativeAge = (value?: string | null) => {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "—";

  const diffMs = Date.now() - dt.getTime();
  const diffDays = Math.max(0, Math.floor(diffMs / (1000 * 60 * 60 * 24)));

  if (diffDays <= 0) return "today";
  if (diffDays === 1) return "a day ago";
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 14) return "a week ago";
  if (diffDays < 30) {
    const weeks = Math.floor(diffDays / 7);
    return weeks <= 1 ? "a week ago" : `${weeks} weeks ago`;
  }
  if (diffDays < 60) return "a month ago";
  if (diffDays < 365) {
    const months = Math.floor(diffDays / 30);
    return months <= 1 ? "a month ago" : `${months} months ago`;
  }
  if (diffDays < 730) return "a year ago";
  const years = Math.floor(diffDays / 365);
  return `${years} years ago`;
};

const heatColor = (count: number) => {
  if (count <= 0) return "bg-slate-100";
  if (count === 1) return "bg-emerald-100";
  if (count === 2) return "bg-emerald-300";
  return "bg-emerald-500";
};

function Heatmap({ data }: { data: Array<{ date: string; count: number }> }) {
  const map = useMemo(() => new Map(data.map((d) => [d.date, d.count])), [data]);
  const dates = useMemo(() => data.map((d) => d.date), [data]);
  const start = dates[0] ? new Date(`${dates[0]}T00:00:00Z`) : null;
  const startDow = start ? start.getUTCDay() : 0; // 0=Sun..6=Sat

  const cells = useMemo(() => {
    const raw = data.map((d) => ({ ...d }));
    const padded: Array<{ date: string; count: number } | null> = [];
    for (let i = 0; i < startDow; i += 1) padded.push(null);
    for (const item of raw) padded.push(item);
    while (padded.length % 7 !== 0) padded.push(null);
    return padded;
  }, [data, startDow]);

  return (
    <div className="w-full">
      <div className="grid w-full aspect-[53/7] grid-flow-col auto-cols-fr grid-rows-7 gap-px rounded-md bg-border p-px">
        {cells.map((cell, idx) => {
          if (!cell) {
            return <div key={`pad-${idx}`} className="h-full w-full rounded-[2px] bg-wash" />;
          }
          const count = Number(cell.count ?? 0);
          return (
            <div
              key={cell.date}
              title={`${cell.date}: ${count} solved`}
              className={`h-full w-full rounded-[2px] ${heatColor(count)}`}
            />
          );
        })}
      </div>
    </div>
  );
}

export default function Profile() {
  const { auth, authReady, me, signOut } = useAuth();
  const params = useParams();
  const navigate = useNavigate();

  const usernameParam = (params.username ?? "").trim();

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const [profile, setProfile] = useState<UserProfileResponse | null>(null);
  const [submissions, setSubmissions] = useState<UserSubmissionsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!usernameParam) {
      setError("User not found.");
      return;
    }
    let cancelled = false;
    const run = async () => {
      setIsLoading(true);
      setError(null);
      setProfile(null);
      setSubmissions(null);
      try {
        const response = await getUserProfile(apiAuth, usernameParam);
        if (cancelled) return;
        setProfile(response);
        if (response.user.username && response.user.username !== usernameParam) {
          navigate(`/u/${encodeURIComponent(response.user.username)}`, { replace: true });
          return;
        }
        const isOwner = Boolean(me?.username && response.user.username === me.username);
        if (!isOwner) {
          const subs = await listUserSubmissions(apiAuth, response.user.username, { page: 1, limit: 10 });
          if (!cancelled) setSubmissions(subs);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "User not found.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode, me?.username, navigate, usernameParam]);

  const handleLogout = async () => {
    await signOut();
  };

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-6xl">
          <div className="glass-panel p-8 text-center">
            <LoadingIndicator label="Loading profile..." />
          </div>
        </div>
      </div>
    );
  }

  if (auth.mode === "none") return <Navigate to="/" replace />;

  if (error) {
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
          <div className="glass-panel p-8">
            <div className="text-lg font-semibold text-ink">404 — User not found</div>
            <div className="mt-2 text-sm text-muted">{error}</div>
            <div className="mt-6">
              <Link className="btn-secondary" to="/problemset">
                Back to Problem Set
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const user = profile?.user;
  const stats = profile?.stats;
  const isOwner = Boolean(user?.username && me?.username && user.username === me.username);

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
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
            <aside className="lg:col-span-4">
              <div className="rounded-xl border border-border bg-card p-5">
                <div className="flex items-center gap-4">
                  <div className="h-14 w-14 overflow-hidden rounded-full border border-border bg-slate-100">
                    {user?.avatar_url ? (
                      <img className="h-full w-full object-cover" alt="" src={user.avatar_url} />
                    ) : null}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-lg font-semibold text-ink">{user?.display_name ?? "—"}</div>
                    <div className="truncate text-sm text-muted">{user?.username ? `@${user.username}` : "—"}</div>
                  </div>
                </div>

                {user?.bio ? <div className="mt-4 text-sm text-muted">{user.bio}</div> : null}

                <div className="mt-5 grid grid-cols-2 gap-3">
                  <div className="rounded-lg border border-border bg-slate-50 px-3 py-2">
                    <div className="text-xs text-muted">Solved</div>
                    <div className="text-lg font-semibold text-ink">{stats?.solved_count ?? 0}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-slate-50 px-3 py-2">
                    <div className="text-xs text-muted">Streak</div>
                    <div className="text-lg font-semibold text-ink">{stats?.current_streak ?? 0}</div>
                  </div>
                </div>

                <div className="mt-5">
                  <div className="text-xs font-semibold text-muted">Badges</div>
                  {!profile?.badges?.length ? (
                    <div className="mt-2 text-sm text-muted">No badges yet.</div>
                  ) : (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {profile.badges.map((b) => (
                        <div
                          key={b.key}
                          title={b.earned_at ? `Earned ${formatDate(b.earned_at)}` : undefined}
                          className="rounded-md border border-border bg-card px-2 py-1 text-xs font-semibold text-ink"
                        >
                          {b.label}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </aside>

            <main className="lg:col-span-8">
              <div className="space-y-6">
                <div className="rounded-xl border border-border bg-card p-5">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-ink">Activity</div>
                      <div className="text-xs text-muted">Last 365 days (UTC)</div>
                    </div>
                    <div className="text-xs text-muted">{`Max streak: ${stats?.max_streak ?? 0} days`}</div>
                  </div>
                  <div className="mt-4">
                    {profile?.heatmap ? <Heatmap data={profile.heatmap} /> : null}
                  </div>
                </div>

                <div className="rounded-xl border border-border bg-card p-5">
                  <div className="text-sm font-semibold text-ink">Recent Solved</div>
                  {!profile?.recent_solved?.length ? (
                    <div className="mt-3 text-sm text-muted">No solves yet.</div>
                  ) : (
                    <div className="mt-3 divide-y divide-border">
                      {profile.recent_solved.slice(0, 10).map((item) => (
                        <button
                          key={`${item.case_id}-${item.solved_at ?? ""}`}
                          className="flex w-full items-center justify-between gap-3 py-3 text-left hover:bg-slate-50"
                          onClick={() => {
                            if (isOwner) {
                              navigate(`/problem/${encodeURIComponent(item.case_id)}?tab=problem`);
                            } else {
                              navigate(`/problemset`);
                            }
                          }}
                        >
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-ink">{item.title || item.case_id}</div>
                            <div className="mt-1 text-xs text-muted">{item.difficulty}</div>
                          </div>
                          <div className="shrink-0 text-xs text-muted">{formatRelativeAge(item.solved_at ?? null)}</div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {!isOwner ? (
                  <div className="rounded-xl border border-border bg-card p-5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">Public Submissions</div>
                      <div className="text-xs text-muted">{submissions ? `${submissions.total} total` : ""}</div>
                    </div>
                    {isLoading && !submissions ? (
                      <div className="mt-4">
                        <LoadingIndicator label="Loading submissions..." />
                      </div>
                    ) : null}
                    {!submissions?.items?.length ? (
                      <div className="mt-3 text-sm text-muted">No public submissions yet.</div>
                    ) : (
                      <div className="mt-3 divide-y divide-border">
                        {submissions.items.map((sub) => (
                          <Link
                            key={sub.session_id}
                            to={`/submission/${encodeURIComponent(sub.session_id)}`}
                            className="flex items-center justify-between gap-3 py-3 hover:bg-slate-50"
                          >
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold text-ink">{sub.title || sub.case_id}</div>
                              <div className="mt-1 text-xs text-muted">{`Messages: ${sub.message_count}`}</div>
                            </div>
                            <div className="shrink-0 text-xs text-muted">{formatDate(sub.ended_at ?? null)}</div>
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            </main>
          </div>
        </section>

        {isLoading && !profile ? (
          <div className="glass-panel p-6 text-center">
            <LoadingIndicator label="Loading..." />
          </div>
        ) : null}
      </div>
    </div>
  );
}
