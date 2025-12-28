-- One-time, idempotent migration to support /problemset listing fields on cases.
-- Run this in the Supabase SQL editor for an existing project.

alter table public.cases
  add column if not exists difficulty text not null default 'Easy';

alter table public.cases
  add column if not exists tags text[] not null default '{}'::text[];

alter table public.cases
  add column if not exists short_prompt text not null default '';

alter table public.cases
  add column if not exists estimated_time_min integer not null default 15;

alter table public.cases
  add column if not exists version integer not null default 1;

alter table public.cases
  add column if not exists is_published boolean not null default true;

create index if not exists cases_is_published_idx
  on public.cases (is_published);

create index if not exists cases_difficulty_idx
  on public.cases (difficulty);

create index if not exists cases_tags_gin_idx
  on public.cases using gin (tags);

create index if not exists cases_problemset_fts_idx
  on public.cases using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(short_prompt, '')));

