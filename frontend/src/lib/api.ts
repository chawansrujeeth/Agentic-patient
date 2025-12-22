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

type RequestOptions = {
  method?: string;
  body?: Record<string, unknown> | null;
};

const API_PREFIX = import.meta.env.VITE_API_BASE_URL ?? "/api";

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
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: options.method ?? "GET",
    headers: buildHeaders(auth, hasBody),
    body: hasBody ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Request failed.");
  }

  return (await response.json()) as T;
};

export const createSession = (auth: ApiAuth, caseId: string) =>
  apiFetch<{
    session_id: string;
    state?: SessionState;
    last_messages?: ChatMessage[];
  }>("/sessions", { method: "POST", body: { case_id: caseId } }, auth);

export const getSession = (auth: ApiAuth, sessionId: string) =>
  apiFetch<{ session_id: string; state?: SessionState; last_messages?: ChatMessage[] }>(
    `/sessions/${sessionId}`,
    { method: "GET" },
    auth
  );

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
