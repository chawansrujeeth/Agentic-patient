import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../App";
import TopBar from "../components/TopBar";
import { getUserCaseProgress, listCases } from "../lib/api";
import type { ApiAuth, ProblemSetCase, ProblemSetResponse, UserCaseProgress } from "../lib/api";
import useDebouncedValue from "../lib/useDebouncedValue";

const PAGE_SIZE = 10;

const difficultyOptions = ["All", "Easy", "Medium", "Hard"] as const;

const sortOptions = [
  { value: "title", label: "Title" },
  { value: "difficulty_asc", label: "Difficulty (Easy → Hard)" },
  { value: "difficulty_desc", label: "Difficulty (Hard → Easy)" },
] as const;

const difficultyBadgeClass = (difficulty: string) => {
  if (difficulty === "Easy") return "bg-emerald-100 text-emerald-700 border-emerald-200";
  if (difficulty === "Medium") return "bg-amber-100 text-amber-700 border-amber-200";
  return "bg-rose-100 text-rose-700 border-rose-200";
};

const skeletonRows = Array.from({ length: 6 }, (_, idx) => idx);

export default function ProblemSet() {
  const { auth, authReady, me, signOut } = useAuth();
  const navigate = useNavigate();

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput.trim(), 300);
  const [difficulty, setDifficulty] = useState<(typeof difficultyOptions)[number]>("All");
  const [tag, setTag] = useState<string>("All");
  const [sort, setSort] = useState<(typeof sortOptions)[number]["value"]>("title");
  const [page, setPage] = useState(1);

  const [data, setData] = useState<ProblemSetResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressByCaseId, setProgressByCaseId] = useState<Record<string, UserCaseProgress>>({});

  const tagOptions = useMemo(() => {
    const items = data?.items ?? [];
    const unique = new Set<string>();
    for (const item of items) {
      for (const t of item.tags ?? []) {
        if (t) unique.add(t);
      }
    }
    const sorted = Array.from(unique).sort((a, b) => a.localeCompare(b));
    if (tag !== "All" && tag && !unique.has(tag)) {
      sorted.unshift(tag);
    }
    return ["All", ...sorted];
  }, [data?.items, tag]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, difficulty, tag, sort]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;

    const controller = new AbortController();
    const run = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listCases(apiAuth, {
          search: debouncedSearch || null,
          difficulty: difficulty === "All" ? null : difficulty,
          tag: tag === "All" ? null : tag,
          sort: sort === "title" ? null : sort,
          page,
          limit: PAGE_SIZE,
        });
        if (!controller.signal.aborted) {
          setData(response);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : "Unable to load problem set.";
        setError(message);
      } finally {
        if (!controller.signal.aborted) setIsLoading(false);
      }
    };

    run();
    return () => controller.abort();
  }, [apiAuth, authReady, auth.mode, debouncedSearch, difficulty, tag, sort, page]);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    let cancelled = false;
    const run = async () => {
      try {
        const response = await getUserCaseProgress(apiAuth);
        if (cancelled) return;
        const map: Record<string, UserCaseProgress> = {};
        for (const item of response.items ?? []) {
          if (item?.case_id) map[item.case_id] = item;
        }
        setProgressByCaseId(map);
      } catch {
        if (!cancelled) setProgressByCaseId({});
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, authReady, auth.mode]);

  const items: ProblemSetCase[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleOpenCase = (caseId: string) => {
    const progress = progressByCaseId[caseId];
    if (progress?.status === "SOLVED") {
      navigate(`/problem/${encodeURIComponent(caseId)}?tab=problem`);
      return;
    }
    if (progress?.status === "IN_PROGRESS" && progress.last_session_id) {
      const qs = new URLSearchParams({ session_id: String(progress.last_session_id) });
      navigate(`/chat/${encodeURIComponent(caseId)}?${qs.toString()}`);
      return;
    }
    navigate(`/chat/${encodeURIComponent(caseId)}`);
  };

  const handleLogout = async () => {
    await signOut();
  };

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-10">
        <div className="mx-auto max-w-6xl">
          <div className="glass-panel p-8 text-center">Loading…</div>
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
        <TopBar
          mode={auth.mode}
          email={auth.email}
          guestId={auth.guestId}
          myUsername={me?.username ?? null}
          onLogout={handleLogout}
        />

        <section className="glass-panel p-6 sm:p-8">
          <div className="flex flex-col gap-6">
            <header className="flex flex-col gap-2">
              <h2 className="text-2xl font-semibold text-ink">Problem Set</h2>
              <p className="text-sm text-muted">Pick a case and jump straight into a new chat session.</p>
            </header>

            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <div className="input-shell md:max-w-md">
                <input
                  className="w-full bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
                  placeholder="Search cases…"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                />
              </div>

              <div className="flex flex-wrap gap-3">
                <label className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm shadow-sm">
                  <span className="text-muted">Difficulty</span>
                  <select
                    className="bg-transparent text-sm font-semibold text-ink focus:outline-none"
                    value={difficulty}
                    onChange={(event) => setDifficulty(event.target.value as (typeof difficultyOptions)[number])}
                  >
                    {difficultyOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm shadow-sm">
                  <span className="text-muted">Tag</span>
                  <select
                    className="bg-transparent text-sm font-semibold text-ink focus:outline-none"
                    value={tag}
                    onChange={(event) => setTag(event.target.value)}
                  >
                    {tagOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm shadow-sm">
                  <span className="text-muted">Sort</span>
                  <select
                    className="bg-transparent text-sm font-semibold text-ink focus:outline-none"
                    value={sort}
                    onChange={(event) => setSort(event.target.value as (typeof sortOptions)[number]["value"])}
                  >
                    {sortOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>

            {error ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
                {error}
              </div>
            ) : null}

            <div className="overflow-x-auto rounded-lg border border-border bg-card">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="border-b border-border bg-slate-50">
                  <tr>
                    <th className="px-4 py-3 font-semibold text-ink">Title</th>
                    <th className="px-4 py-3 font-semibold text-ink">Difficulty</th>
                    <th className="px-4 py-3 font-semibold text-ink">Tags</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoading
                    ? skeletonRows.map((idx) => (
                        <tr key={`sk-${idx}`} className="border-b border-border last:border-0">
                          <td className="px-4 py-4">
                            <div className="h-4 w-64 animate-pulse-soft rounded bg-ink/10" />
                          </td>
                          <td className="px-4 py-4">
                            <div className="h-6 w-20 animate-pulse-soft rounded-full bg-ink/10" />
                          </td>
                          <td className="px-4 py-4">
                            <div className="h-4 w-52 animate-pulse-soft rounded bg-ink/10" />
                          </td>
                        </tr>
                      ))
                    : items.map((item) => (
                        <tr
                          key={item.case_id}
                          className="border-b border-border transition-colors hover:bg-slate-50 last:border-0"
                        >
                          <td className="px-4 py-4">
                            <button
                              className="text-left font-semibold text-accent hover:underline"
                              onClick={() => handleOpenCase(item.case_id)}
                            >
                              <span className="inline-flex items-center gap-2">
                                {progressByCaseId[item.case_id]?.status === "SOLVED" ? (
                                  <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                                    ✓ Solved
                                  </span>
                                ) : null}
                                <span>{item.title}</span>
                              </span>
                            </button>
                          </td>
                          <td className="px-4 py-4 align-top">
                            <span
                              className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${difficultyBadgeClass(
                                item.difficulty
                              )}`}
                            >
                              {item.difficulty}
                            </span>
                          </td>
                          <td className="px-4 py-4 align-top text-muted">
                            {(item.tags ?? []).length ? (item.tags ?? []).join(", ") : "—"}
                          </td>
                        </tr>
                      ))}
                </tbody>
              </table>
            </div>

            {!isLoading && !error && items.length === 0 ? (
              <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted">
                No problems found.
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm text-muted">
                Page <span className="font-semibold text-ink">{page}</span> of{" "}
                <span className="font-semibold text-ink">{pageCount}</span>
                {total ? (
                  <>
                    {" "}
                    • <span className="font-semibold text-ink">{total}</span> total
                  </>
                ) : null}
              </div>
              <div className="flex items-center gap-3">
                <button
                  className="btn-secondary"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={isLoading || page <= 1}
                >
                  Prev
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                  disabled={isLoading || page >= pageCount}
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
