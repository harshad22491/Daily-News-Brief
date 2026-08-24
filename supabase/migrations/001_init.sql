create extension if not exists pgcrypto;

create table if not exists public.recipients (
  id text primary key,
  token text not null unique,
  active boolean not null default true
);

create table if not exists public.articles (
  id text primary key,
  url text not null,
  source text not null,
  source_label text not null,
  feed text not null,
  title text not null,
  snippet text not null default '',
  topic text,
  keywords jsonb not null default '[]'::jsonb,
  published_at timestamptz not null,
  premium boolean not null default false,
  cluster_id text,
  first_seen timestamptz not null default now()
);

create index if not exists articles_published_at_idx
  on public.articles (published_at desc);
create index if not exists articles_cluster_id_idx
  on public.articles (cluster_id);
create index if not exists articles_topic_idx
  on public.articles (topic);

create table if not exists public.issues (
  id text primary key,
  date date not null,
  recipient text not null references public.recipients (id) on delete cascade,
  sent_at timestamptz,
  partial boolean not null default false,
  subject text not null
);

create index if not exists issues_recipient_date_idx
  on public.issues (recipient, date desc);

create table if not exists public.issue_items (
  issue_id text not null references public.issues (id) on delete cascade,
  article_id text not null references public.articles (id) on delete cascade,
  topic text not null default '',
  slot text not null check (slot in ('lead', 'brief', 'noted', 'number', 'finally')),
  primary key (issue_id, article_id, slot)
);

create index if not exists issue_items_article_id_idx
  on public.issue_items (article_id);

create table if not exists public.ratings (
  recipient text not null references public.recipients (id) on delete cascade,
  kind text not null check (kind in ('topic', 'article')),
  item text not null,
  score smallint not null check (score between 1 and 5),
  rated_at timestamptz not null default now(),
  date date not null,
  primary key (recipient, kind, item, date)
);

create index if not exists ratings_recipient_date_idx
  on public.ratings (recipient, date);

create table if not exists public.keyword_weights (
  recipient text not null references public.recipients (id) on delete cascade,
  topic text not null,
  keyword text not null,
  weight double precision not null default 1.0 check (weight between 0.2 and 3.0),
  observations integer not null default 0 check (observations >= 0),
  status text not null default 'active' check (status in ('active', 'retired')),
  changed_by text not null default 'seed' check (changed_by in ('seed', 'bandit', 'critique')),
  updated_at timestamptz not null default now(),
  primary key (recipient, topic, keyword)
);

create index if not exists keyword_weights_recipient_topic_idx
  on public.keyword_weights (recipient, topic);

create table if not exists public.prompt_versions (
  version text primary key,
  file text not null,
  diff text not null,
  rationale text not null default '',
  created_at timestamptz not null default now(),
  verdict text not null default 'NOT_APPLIED'
);

create table if not exists public.followup_requests (
  id uuid primary key default gen_random_uuid(),
  recipient text not null references public.recipients (id) on delete cascade,
  article_id text not null references public.articles (id) on delete cascade,
  status text not null default 'offered' check (status in ('offered', 'consented', 'sent')),
  created_at timestamptz not null default now()
);

create index if not exists followup_requests_recipient_created_idx
  on public.followup_requests (recipient, created_at desc);

create table if not exists public.send_log (
  date text not null,
  recipient text not null,
  message_id text not null default '',
  status text not null,
  detail text not null default '',
  primary key (date, recipient)
);

alter table public.recipients enable row level security;
alter table public.articles enable row level security;
alter table public.issues enable row level security;
alter table public.issue_items enable row level security;
alter table public.ratings enable row level security;
alter table public.keyword_weights enable row level security;
alter table public.prompt_versions enable row level security;
alter table public.followup_requests enable row level security;
alter table public.send_log enable row level security;
