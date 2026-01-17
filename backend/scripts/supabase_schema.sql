-- Minimal Supabase schema for docs-ai.
-- Run this in the Supabase SQL editor for your project.

create extension if not exists pgcrypto;

create table if not exists public.doctors (
  doctor_id uuid primary key,
  display_name text not null default '',
  level integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists doctors_display_name_idx
  on public.doctors (display_name);

create index if not exists doctors_updated_at_idx
  on public.doctors (updated_at desc);

-- Public-facing user profiles (username -> user_id mapping).
create table if not exists public.users (
  user_id uuid primary key,
  username text not null,
  display_name text not null default '',
  avatar_url text not null default '',
  bio text not null default '',
  created_at timestamptz not null default now()
);

create unique index if not exists users_username_idx
  on public.users (username);

create table if not exists public.cases (
  case_id text primary key,
  title text not null default '',
  difficulty text not null default 'Easy',
  tags text[] not null default '{}'::text[],
  short_prompt text not null default '',
  estimated_time_min integer not null default 15,
  version integer not null default 1,
  is_published boolean not null default true,
  seed jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists cases_is_published_idx
  on public.cases (is_published);

create index if not exists cases_difficulty_idx
  on public.cases (difficulty);

create index if not exists cases_tags_gin_idx
  on public.cases using gin (tags);

-- Text search index for /problemset listing/search (Postgres full-text search).
create index if not exists cases_problemset_fts_idx
  on public.cases using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(short_prompt, '')));

create table if not exists public.sessions (
  session_id uuid primary key,
  user_id uuid not null,
  case_id text not null references public.cases(case_id) on delete cascade,
  status text not null default 'active',
  is_public boolean not null default false,
  ended_at timestamptz,
  visit_number integer not null default 1,
  turn_in_visit integer not null default 0,
  graph_state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists sessions_user_status_idx
  on public.sessions (user_id, status, updated_at desc);

create index if not exists sessions_user_status_ended_idx
  on public.sessions (user_id, status, ended_at desc);

create index if not exists sessions_user_public_status_ended_idx
  on public.sessions (user_id, is_public, status, ended_at desc);

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
