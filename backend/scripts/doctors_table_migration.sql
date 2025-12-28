-- One-time, idempotent migration to store user profile names.
-- Run this in the Supabase SQL editor for an existing project.

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

