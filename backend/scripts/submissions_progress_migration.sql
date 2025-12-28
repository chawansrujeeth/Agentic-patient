-- One-time, idempotent migration to support LeetCode-like solved submissions:
-- - per-user case progress
-- - session completion + read-only locking
-- - public community submissions
-- - evaluation artifacts (temp payload for later scoring)
--
-- Run this in the Supabase SQL editor for an existing project.

create extension if not exists pgcrypto;

alter table public.sessions
  add column if not exists ended_at timestamptz;

alter table public.sessions
  add column if not exists is_public boolean not null default false;

create index if not exists sessions_case_public_idx
  on public.sessions (case_id, is_public, ended_at desc);

create index if not exists sessions_case_public_status_ended_idx
  on public.sessions (case_id, is_public, status, ended_at desc);

create table if not exists public.user_case_progress (
  user_id uuid not null,
  case_id text not null references public.cases(case_id) on delete cascade,
  status text not null default 'NOT_STARTED',
  last_session_id uuid references public.sessions(session_id) on delete set null,
  solved_session_id uuid references public.sessions(session_id) on delete set null,
  solved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, case_id)
);

create index if not exists user_case_progress_user_idx
  on public.user_case_progress (user_id, status, updated_at desc);

create table if not exists public.evaluation_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.sessions(session_id) on delete cascade,
  user_id uuid not null,
  case_id text not null,
  created_at timestamptz not null default now(),
  status text not null default 'PENDING',
  payload jsonb not null default '{}'::jsonb,
  unique (session_id)
);

create index if not exists evaluation_artifacts_session_idx
  on public.evaluation_artifacts (session_id);
