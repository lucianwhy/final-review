-- M0 durable domain contracts. Existing JSON-backed records remain readable.
-- Every table keeps the Store's record_key/user_id/course_id/data pattern so
-- authorization remains server-side in PostgresStore.

alter table public.courses add column if not exists status text not null default 'active'
  check (status in ('active', 'archived', 'deleted', 'purged'));
alter table public.courses add column if not exists archived_at timestamptz;
alter table public.courses add column if not exists deleted_at timestamptz;
alter table public.courses add column if not exists purge_after timestamptz;
alter table public.courses add column if not exists deletion_confirmed_at timestamptz;
create index if not exists courses_lifecycle_idx on public.courses(user_id, status, created_at);

-- Existing fast-quiz attempts remain legacy records until M3 creates confirmed
-- quiz revisions. Nullable columns preserve all historical JSON payloads.
alter table public.attempts add column if not exists quiz_revision_id text;
alter table public.attempts add column if not exists legacy_session_id text;
alter table public.attempts add column if not exists grading_snapshot jsonb;
alter table public.attempts add column if not exists model_version text;
alter table public.attempts add column if not exists grading_disclaimer_version text;
update public.attempts
  set legacy_session_id = data->>'session_id'
  where legacy_session_id is null and data ? 'session_id';
alter table public.attempts add constraint attempts_revision_xor_legacy
  check (quiz_revision_id is null or legacy_session_id is null) not valid;

create table if not exists public.exams (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.learning_assets (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.asset_revisions (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.quiz_revision_payloads (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.question_revisions (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.material_versions (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, document_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.source_references (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.source_reference_snapshots (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, document_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.confirmation_requests (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create table if not exists public.audit_events (
  record_key text primary key, user_id uuid not null references app_users(id) on delete cascade,
  course_id text not null, created_at timestamptz, data jsonb not null
);
create index if not exists exams_scope_idx on public.exams(user_id, course_id, created_at);
create index if not exists assets_scope_idx on public.learning_assets(user_id, course_id, created_at);
create index if not exists revisions_scope_idx on public.asset_revisions(user_id, course_id, created_at);
create index if not exists quiz_revisions_scope_idx on public.quiz_revision_payloads(user_id, course_id, created_at);
create index if not exists question_revisions_scope_idx on public.question_revisions(user_id, course_id, created_at);
create index if not exists material_versions_scope_idx on public.material_versions(user_id, course_id, document_id);
create index if not exists references_scope_idx on public.source_references(user_id, course_id);
create index if not exists confirmations_scope_idx on public.confirmation_requests(user_id, course_id, created_at);
create index if not exists attempts_revision_scope_idx on public.attempts(user_id, course_id, quiz_revision_id);
