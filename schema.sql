create extension if not exists pgcrypto;

create table if not exists public.youth (id uuid primary key default gen_random_uuid(), name text not null, gender text not null check (gender in ('boy','girl')), grade text, active boolean not null default true, created_at timestamptz not null default now());
create table if not exists public.profiles (user_id uuid primary key references auth.users(id) on delete cascade, username text not null unique, email text not null, role text not null check (role in ('youth','servant')), youth_id uuid references public.youth(id) on delete set null, created_at timestamptz not null default now());
create table if not exists public.awards (id uuid primary key default gen_random_uuid(), youth_id uuid not null references public.youth(id) on delete cascade, category text not null check (category in ('early','bible_read','verse_recite','involvement','misc')), points integer not null check (points > 0 and points <= 100), note text, awarded_by text not null, created_at timestamptz not null default now());
create table if not exists public.notifications (id uuid primary key default gen_random_uuid(), youth_id uuid not null references public.youth(id) on delete cascade, category text not null, points integer not null, note text, awarded_by text not null, created_at timestamptz not null default now(), read_at timestamptz);
create index if not exists awards_youth_id_idx on public.awards(youth_id); create index if not exists notifications_youth_id_idx on public.notifications(youth_id);
alter table public.youth enable row level security; alter table public.awards enable row level security; alter table public.notifications enable row level security;
alter table public.profiles enable row level security;
-- Run these two migration statements if the profiles table already existed.
alter table public.profiles add column if not exists username text;
create unique index if not exists profiles_username_unique_idx on public.profiles(username) where username is not null;
-- The backend uses the service-role key. Keep it only in Render environment variables.
