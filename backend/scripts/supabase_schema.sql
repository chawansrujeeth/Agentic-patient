-- Minimal Supabase schema for docs-ai.
-- Run this in the Supabase SQL editor for your project.

create table if not exists public.cases (
  case_id text primary key,
  title text not null default '',
  seed jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.sessions (
  session_id uuid primary key,
  user_id uuid not null,
  case_id text not null references public.cases(case_id) on delete cascade,
  status text not null default 'active',
  visit_number integer not null default 1,
  turn_in_visit integer not null default 0,
  graph_state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists sessions_user_status_idx
  on public.sessions (user_id, status, updated_at desc);

create table if not exists public.messages (
  id bigserial primary key,
  session_id uuid not null references public.sessions(session_id) on delete cascade,
  visit_number integer not null default 1,
  turn_index integer not null default 0,
  role text not null,
  content text not null,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists messages_session_idx
  on public.messages (session_id);

create index if not exists messages_session_turn_idx
  on public.messages (session_id, visit_number, turn_index);

create table if not exists public.visit_summaries (
  id bigserial primary key,
  session_id uuid not null references public.sessions(session_id) on delete cascade,
  visit_number integer not null default 1,
  summary text not null default '',
  embedding jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (session_id, visit_number)
);
