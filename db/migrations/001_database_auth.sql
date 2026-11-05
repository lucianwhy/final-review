-- Self-managed local PostgreSQL schema. Apply with psql before starting FastAPI.
create extension if not exists vector;

create table if not exists public.app_users (
  id uuid primary key,
  email text not null unique,
  password_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.auth_sessions (
  session_hash text primary key,
  user_id uuid not null references public.app_users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
create index if not exists auth_sessions_active_user_idx
  on public.auth_sessions(user_id, expires_at) where revoked_at is null;

-- This small, durable limiter intentionally removes Redis from the initial setup.
create table if not exists public.auth_login_attempts (
  id bigint generated always as identity primary key,
  email text not null,
  ip_address inet,
  successful boolean not null,
  created_at timestamptz not null default now()
);
create index if not exists auth_login_attempts_limiter_idx
  on public.auth_login_attempts(email, ip_address, created_at desc) where successful = false;

-- Flexible JSON payloads preserve the existing Store contract, while indexed
-- scope columns enforce ownership in every PostgresStore query.
create table if not exists public.courses (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.documents (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, document_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.document_chunks (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, document_id text not null, data jsonb not null,
  content text not null, embedding vector not null
);
create table if not exists public.conversations (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, conversation_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.messages (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, conversation_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.fast_quiz_sessions (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.attempts (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.learning_events (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.review_sessions (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.knowledge_points (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.checkpoints (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  thread_id text not null, checkpoint_ns text not null default '', checkpoint_id text not null,
  created_at timestamptz, data jsonb not null
);
create table if not exists public.pending_writes (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  thread_id text not null, checkpoint_ns text not null default '', checkpoint_id text not null,
  created_at timestamptz, data jsonb not null
);

create index if not exists courses_owner_idx on public.courses(user_id, course_id);
create index if not exists documents_scope_idx on public.documents(user_id, course_id);
create index if not exists chunks_scope_idx on public.document_chunks(user_id, course_id, document_id);
create index if not exists messages_scope_idx on public.messages(user_id, course_id, conversation_id, created_at);
create index if not exists attempts_scope_idx on public.attempts(user_id, course_id, created_at);
create index if not exists checkpoints_scope_idx on public.checkpoints(user_id, thread_id, checkpoint_ns, checkpoint_id);
create index if not exists writes_scope_idx on public.pending_writes(user_id, thread_id, checkpoint_ns, checkpoint_id);
