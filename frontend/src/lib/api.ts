export type ApiAuth = {
  mode: "none" | "guest" | "google";
  accessToken?: string | null;
  guestId?: string | null;
};

export type ChatMessage = {
  role: string;
  content: string;
  created_at?: string;
};

export type SessionState = {
  session_id?: string;
  status?: string;
  visit_number?: number;
  turn_in_visit?: number;
};

export type ProblemSetCase = {
  case_id: string;
  title: string;
  difficulty: "Easy" | "Medium" | "Hard";
  tags: string[];
  short_prompt: string;
  estimated_time_min: number;
  version: number;
};

export type ProblemSetResponse = {
  items: ProblemSetCase[];
  page: number;
  limit: number;
  total: number;
};

export type UserCaseProgress = {
  case_id: string;
  status: "NOT_STARTED" | "IN_PROGRESS" | "SOLVED";
  last_session_id?: string | null;
  solved_session_id?: string | null;
  solved_at?: string | null;
};

export type CommunitySubmission = {
  session_id: string;
  created_at?: string | null;
  ended_at?: string | null;
  author_display_name: string;
  author_username?: string;
};

export type SubmissionSessionMeta = {
  session_id: string;
  case_id: string;
  status: string;
  is_public: boolean;
  ended_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type CaseProgressResponse = {
  case_id: string;
  status: "NOT_STARTED" | "IN_PROGRESS" | "SOLVED";
  solved_session_id: string | null;
  last_session_id: string | null;
};

export type CaseSubmissionListItem = {
  session_id: string;
  author_name: string;
  author_username?: string;
  ended_at?: string | null;
  message_count: number;
};

export type SessionWithMessagesResponse = {
  session: SubmissionSessionMeta & { user_id?: string; status: "IN_PROGRESS" | "COMPLETED" };
  author_name?: string;
  author_username?: string | null;
  viewer_can_toggle_visibility?: boolean;
  messages: ChatMessage[];
};

export type CaseDetails = {
  case_id: string;
  title: string;
  difficulty: "Easy" | "Medium" | "Hard";
  tags: string[];
  short_prompt: string;
  estimated_time_min: number;
  version: number;
  patient_presentation?: string;
};

export type MeResponse = {
  user: {
    user_id: string;
    username: string;
    display_name: string;
    avatar_url?: string | null;
    bio?: string | null;
  };
};

export type UserProfileResponse = {
  user: {
    username: string;
    display_name: string;
    avatar_url?: string | null;
    bio?: string | null;
  };
  stats: {
    solved_count: number;
    current_streak: number;
    max_streak: number;
  };
  heatmap: Array<{ date: string; count: number }>;
  recent_solved: Array<{ case_id: string; title: string; difficulty: string; solved_at?: string | null }>;
  badges: Array<{ key: string; label: string; earned_at?: string | null }>;
};

export type UserSubmissionsResponse = {
  items: Array<{
    session_id: string;
    case_id: string;
    title: string;
    ended_at?: string | null;
    message_count: number;
  }>;
  page: number;
  limit: number;
  total: number;
};

type RequestOptions = {
  method?: string;
  body?: Record<string, unknown> | null;
};

const API_PREFIX = import.meta.env.VITE_API_BASE_URL ?? "/api";
export const BACKEND_IDLE_MESSAGE =
  "Backend is idle. Please wait a moment for it to wake up, then refresh.";

const isBackendIdleStatus = (status: number) => [502, 503, 504].includes(status);

const buildHeaders = (auth: ApiAuth, hasBody: boolean) => {
  const headers: Record<string, string> = {};

  if (hasBody) {
    headers["Content-Type"] = "application/json";
  }

  if (auth.mode === "google" && auth.accessToken) {
    headers.Authorization = `Bearer ${auth.accessToken}`;
  }

  if (auth.mode === "guest" && auth.guestId) {
    headers["X-Guest-Id"] = auth.guestId;
  }

  return headers;
};

const apiFetch = async <T>(path: string, options: RequestOptions, auth: ApiAuth): Promise<T> => {
  const hasBody = Boolean(options.body);
  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method: options.method ?? "GET",
      headers: buildHeaders(auth, hasBody),
      body: hasBody ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new Error(BACKEND_IDLE_MESSAGE);
  }

  if (!response.ok) {
    const text = await response.text();
    if (isBackendIdleStatus(response.status)) {
      throw new Error(BACKEND_IDLE_MESSAGE);
    }
    throw new Error(text || "Request failed.");
  }

  return (await response.json()) as T;
};

export const createSession = (auth: ApiAuth, caseId: string) =>
  apiFetch<{
    session_id: string;
    session?: Record<string, unknown>;
    state?: SessionState;
    last_messages?: ChatMessage[];
  }>("/sessions", { method: "POST", body: { case_id: caseId } }, auth);

export const getMe = (auth: ApiAuth) => apiFetch<MeResponse>(`/me`, { method: "GET" }, auth);

export const getSession = (auth: ApiAuth, sessionId: string) =>
  apiFetch<SessionWithMessagesResponse>(`/sessions/${sessionId}?all=true`, { method: "GET" }, auth);

export const sendMessage = (auth: ApiAuth, sessionId: string, message: string) =>
  apiFetch<{
    patient_message?: string;
    updated_state?: SessionState;
    state?: SessionState;
    last_messages?: ChatMessage[];
    messages?: ChatMessage[];
  }>(
    `/sessions/${sessionId}/send`,
    {
      method: "POST",
      body: { message },
    },
    auth
  );

export const summarizeSession = (auth: ApiAuth, sessionId: string) =>
  apiFetch<{
    updated_state?: SessionState;
    state?: SessionState;
    last_messages?: ChatMessage[];
    messages?: ChatMessage[];
    summary?: string;
  }>(`/sessions/${sessionId}/summarize`, { method: "POST" }, auth);

export const endVisit = (auth: ApiAuth, sessionId: string) =>
  apiFetch<{
    updated_state?: SessionState;
    state?: SessionState;
    last_messages?: ChatMessage[];
    messages?: ChatMessage[];
  }>(`/sessions/${sessionId}/endvisit`, { method: "POST" }, auth);

export const getHistory = (auth: ApiAuth, sessionId: string, count = 50) =>
  apiFetch<{ messages?: ChatMessage[]; last_messages?: ChatMessage[] }>(
    `/sessions/${sessionId}/history?n=${count}`,
    { method: "GET" },
    auth
  );

export const endChat = (auth: ApiAuth, sessionId: string) =>
  apiFetch<{
    session: Record<string, unknown>;
    progress?: UserCaseProgress | null;
    artifact?: Record<string, unknown> | null;
  }>(`/sessions/${sessionId}/end`, { method: "POST", body: {} }, auth);

export const patchSessionPublic = (auth: ApiAuth, sessionId: string, isPublic: boolean) =>
  apiFetch<{ session: Record<string, unknown> }>(
    `/sessions/${sessionId}/public`,
    { method: "PATCH", body: { is_public: isPublic } },
    auth
  );

export const getCaseProgress = (auth: ApiAuth, caseId: string) =>
  apiFetch<CaseProgressResponse>(`/cases/${encodeURIComponent(caseId)}/progress`, { method: "GET" }, auth);

export const listCaseSubmissions = (auth: ApiAuth, caseId: string, limit = 50) =>
  apiFetch<{ case_id: string; items: CaseSubmissionListItem[] }>(
    `/cases/${encodeURIComponent(caseId)}/submissions?${new URLSearchParams({ limit: String(limit) }).toString()}`,
    { method: "GET" },
    auth
  );

export const getCaseDetails = (auth: ApiAuth, caseId: string) =>
  apiFetch<{ case: CaseDetails }>(`/cases/${encodeURIComponent(caseId)}`, { method: "GET" }, auth);

export const getUserCaseProgress = (auth: ApiAuth) =>
  apiFetch<{ items: UserCaseProgress[] }>(`/user_case_progress`, { method: "GET" }, auth);

export const getUserCaseProgressForCase = (auth: ApiAuth, caseId: string) =>
  apiFetch<{ item: UserCaseProgress | null }>(
    `/user_case_progress?${new URLSearchParams({ case_id: caseId }).toString()}`,
    { method: "GET" },
    auth
  );

export const completeSession = (auth: ApiAuth, sessionId: string, makePublic?: boolean) =>
  apiFetch<{
    session: Record<string, unknown>;
    progress?: UserCaseProgress | null;
    artifact?: Record<string, unknown> | null;
  }>(
    `/sessions/${sessionId}/complete`,
    {
      method: "POST",
      body: makePublic === undefined ? {} : { make_public: makePublic },
    },
    auth
  );

export const setSessionVisibility = (auth: ApiAuth, sessionId: string, isPublic: boolean) =>
  apiFetch<{ session: Record<string, unknown> }>(
    `/sessions/${sessionId}/visibility`,
    { method: "POST", body: { is_public: isPublic } },
    auth
  );

export const listCommunitySubmissions = (auth: ApiAuth, caseId: string, limit = 50) =>
  apiFetch<{ case_id: string; items: CommunitySubmission[] }>(
    `/cases/${encodeURIComponent(caseId)}/community_submissions?${new URLSearchParams({
      limit: String(limit),
    }).toString()}`,
    { method: "GET" },
    auth
  );

export const getSubmission = (auth: ApiAuth, sessionId: string) =>
  apiFetch<{
    session: SubmissionSessionMeta;
    author_display_name?: string;
    author_username?: string | null;
    messages: ChatMessage[];
    viewer_can_toggle_visibility?: boolean;
    case_id?: string;
  }>(`/submissions/${sessionId}`, { method: "GET" }, auth);

export const getUserProfile = (auth: ApiAuth, username: string) =>
  apiFetch<UserProfileResponse>(`/users/${encodeURIComponent(username)}/profile`, { method: "GET" }, auth);

export const resolveUsernameByUserId = (auth: ApiAuth, userId: string) =>
  apiFetch<{ user_id: string; username: string }>(`/users/id/${encodeURIComponent(userId)}`, { method: "GET" }, auth);

export const listUserSubmissions = (
  auth: ApiAuth,
  username: string,
  params: { caseId?: string | null; page?: number; limit?: number }
) => {
  const query = new URLSearchParams();
  if (params.caseId) query.set("case_id", params.caseId);
  if (params.page) query.set("page", String(params.page));
  if (params.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  return apiFetch<UserSubmissionsResponse>(
    `/users/${encodeURIComponent(username)}/submissions${qs ? `?${qs}` : ""}`,
    { method: "GET" },
    auth
  );
};

export const listCases = (
  auth: ApiAuth,
  params: {
    search?: string | null;
    difficulty?: string | null;
    tag?: string | null;
    sort?: string | null;
    page?: number;
    limit?: number;
  }
) => {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  if (params.difficulty) query.set("difficulty", params.difficulty);
  if (params.tag) query.set("tag", params.tag);
  if (params.sort) query.set("sort", params.sort);
  if (params.page) query.set("page", String(params.page));
  if (params.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  return apiFetch<ProblemSetResponse>(`/cases${qs ? `?${qs}` : ""}`, { method: "GET" }, auth);
};
