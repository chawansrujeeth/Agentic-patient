-- Ranked full-text search for /problemset.
-- Exposed via PostgREST as RPC: POST /rest/v1/rpc/problemset_search_cases

create or replace function public.problemset_search_cases(
  p_search text,
  p_difficulty text default null,
  p_tag text default null,
  p_page integer default 1,
  p_limit integer default 20
)
returns table (
  case_id text,
  title text,
  difficulty text,
  tags text[],
  short_prompt text,
  estimated_time_min integer,
  version integer,
  total bigint
)
language sql
stable
as $$
  with q as (
    select websearch_to_tsquery('english', p_search) as tsq
  ),
  filtered as (
    select
      c.case_id,
      c.title,
      c.difficulty,
      c.tags,
      c.short_prompt,
      c.estimated_time_min,
      c.version,
      ts_rank(
        to_tsvector('english', coalesce(c.title, '') || ' ' || coalesce(c.short_prompt, '')),
        (select tsq from q)
      ) as rank
    from public.cases c
    where
      c.is_published = true
      and (p_difficulty is null or c.difficulty = p_difficulty)
      and (p_tag is null or c.tags @> array[p_tag]::text[])
      and to_tsvector('english', coalesce(c.title, '') || ' ' || coalesce(c.short_prompt, '')) @@ (select tsq from q)
  ),
  numbered as (
    select
      f.*,
      count(*) over () as total
    from filtered f
    order by f.rank desc, f.title asc
    limit greatest(1, least(100, p_limit))
    offset greatest(0, (greatest(1, p_page) - 1) * greatest(1, least(100, p_limit)))
  )
  select
    case_id,
    title,
    difficulty,
    tags,
    short_prompt,
    estimated_time_min,
    version,
    total
  from numbered;
$$;

