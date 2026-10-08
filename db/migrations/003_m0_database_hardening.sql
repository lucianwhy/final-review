-- M0-R1: relational contracts for the JSON-backed Store tables.
-- This file is intentionally additive; 002 is historical and must not change.

CREATE TABLE IF NOT EXISTS public.schema_migrations (
  version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(),
  applied_by text NOT NULL
);

ALTER TABLE public.courses ADD CONSTRAINT courses_owner_course_key UNIQUE (user_id, course_id);
ALTER TABLE public.documents ADD CONSTRAINT documents_owner_document_key UNIQUE (user_id, document_id);
ALTER TABLE public.documents ADD CONSTRAINT documents_owner_course_fk FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);

-- Project stable identifiers out of JSON.  A missing identifier is a migration
-- error: silently inventing one would break source and attempt provenance.
ALTER TABLE public.learning_assets ADD COLUMN IF NOT EXISTS asset_id text;
ALTER TABLE public.learning_assets ADD COLUMN IF NOT EXISTS current_revision_id text;
UPDATE public.learning_assets SET asset_id = data->>'asset_id' WHERE asset_id IS NULL;
UPDATE public.learning_assets SET current_revision_id = data->>'current_revision_id' WHERE current_revision_id IS NULL;
ALTER TABLE public.learning_assets ALTER COLUMN asset_id SET NOT NULL;
ALTER TABLE public.learning_assets ADD CONSTRAINT learning_assets_owner_asset_key UNIQUE (user_id, asset_id);
ALTER TABLE public.learning_assets ADD CONSTRAINT learning_assets_owner_course_fk
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);

ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS revision_id text;
ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS asset_id text;
ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS revision_no integer;
ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;
ALTER TABLE public.asset_revisions ADD COLUMN IF NOT EXISTS content_hash text;
UPDATE public.asset_revisions SET revision_id = COALESCE(revision_id, data->>'revision_id', record_key);
UPDATE public.asset_revisions SET asset_id = data->>'asset_id' WHERE asset_id IS NULL;
UPDATE public.asset_revisions SET revision_no = (data->>'revision_no')::integer WHERE revision_no IS NULL AND data ? 'revision_no';
UPDATE public.asset_revisions SET state = COALESCE(data->>'state', 'draft') WHERE state IS NULL;
UPDATE public.asset_revisions SET confirmed_at = (data->>'confirmed_at')::timestamptz WHERE confirmed_at IS NULL AND data ? 'confirmed_at';
UPDATE public.asset_revisions SET content_hash = md5(COALESCE(data->>'title','') || E'\n' || COALESCE(data->>'markdown','') || E'\n' || COALESCE(data->>'source_document_ids','')) WHERE content_hash IS NULL;
ALTER TABLE public.asset_revisions ALTER COLUMN revision_id SET NOT NULL;
ALTER TABLE public.asset_revisions ALTER COLUMN asset_id SET NOT NULL;
ALTER TABLE public.asset_revisions ALTER COLUMN revision_no SET NOT NULL;
ALTER TABLE public.asset_revisions ALTER COLUMN state SET NOT NULL;
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_owner_revision_key UNIQUE (user_id, revision_id);
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_owner_asset_number_key UNIQUE (user_id, asset_id, revision_no);
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_number_check CHECK (revision_no > 0);
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_state_check CHECK (state IN ('draft','confirmed','superseded','archived','deleted'));
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_owner_course_fk FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);
ALTER TABLE public.asset_revisions ADD CONSTRAINT asset_revisions_owner_asset_fk FOREIGN KEY (user_id, asset_id) REFERENCES public.learning_assets(user_id, asset_id);
ALTER TABLE public.learning_assets ADD CONSTRAINT learning_assets_current_revision_fk FOREIGN KEY (user_id, current_revision_id) REFERENCES public.asset_revisions(user_id, revision_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE public.material_versions ADD COLUMN IF NOT EXISTS material_version_id text;
UPDATE public.material_versions SET material_version_id = COALESCE(material_version_id, data->>'material_version_id', record_key);
ALTER TABLE public.material_versions ALTER COLUMN material_version_id SET NOT NULL;
ALTER TABLE public.material_versions ADD CONSTRAINT material_versions_owner_version_key UNIQUE (user_id, material_version_id);
ALTER TABLE public.material_versions ADD CONSTRAINT material_versions_owner_course_fk FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);
ALTER TABLE public.material_versions ADD CONSTRAINT material_versions_owner_document_fk FOREIGN KEY (user_id, document_id) REFERENCES public.documents(user_id, document_id);

CREATE TABLE IF NOT EXISTS public.material_locators (
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, material_version_id text NOT NULL, locator_id text NOT NULL,
  locator_kind text NOT NULL CHECK (locator_kind IN ('document','page','chunk','range')),
  ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal >= 0), data jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (user_id, material_version_id, locator_id),
  UNIQUE (user_id, material_version_id, locator_kind, ordinal),
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id),
  FOREIGN KEY (user_id, material_version_id) REFERENCES public.material_versions(user_id, material_version_id)
);
INSERT INTO public.material_locators(user_id, course_id, material_version_id, locator_id, locator_kind, ordinal)
SELECT user_id, course_id, material_version_id, 'document', 'document', 0 FROM public.material_versions
ON CONFLICT DO NOTHING;

ALTER TABLE public.source_references ADD COLUMN IF NOT EXISTS asset_revision_id text;
ALTER TABLE public.source_references ADD COLUMN IF NOT EXISTS material_version_id text;
ALTER TABLE public.source_references ADD COLUMN IF NOT EXISTS locator_id text;
UPDATE public.source_references SET asset_revision_id = COALESCE(asset_revision_id, data->>'revision_id') WHERE asset_revision_id IS NULL;
UPDATE public.source_references SET material_version_id = COALESCE(material_version_id, data->>'material_version_id') WHERE material_version_id IS NULL;
UPDATE public.source_references SET locator_id = COALESCE(locator_id, data->>'locator_id', 'document') WHERE locator_id IS NULL;
ALTER TABLE public.source_references ALTER COLUMN asset_revision_id SET NOT NULL;
ALTER TABLE public.source_references ALTER COLUMN material_version_id SET NOT NULL;
ALTER TABLE public.source_references ALTER COLUMN locator_id SET NOT NULL;
ALTER TABLE public.source_references ADD CONSTRAINT source_references_owner_course_fk FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);
ALTER TABLE public.source_references ADD CONSTRAINT source_references_revision_fk FOREIGN KEY (user_id, asset_revision_id) REFERENCES public.asset_revisions(user_id, revision_id);
ALTER TABLE public.source_references ADD CONSTRAINT source_references_material_fk FOREIGN KEY (user_id, material_version_id) REFERENCES public.material_versions(user_id, material_version_id);
ALTER TABLE public.source_references ADD CONSTRAINT source_references_locator_fk FOREIGN KEY (user_id, material_version_id, locator_id) REFERENCES public.material_locators(user_id, material_version_id, locator_id);

-- Course ownership is enforced for every M0 resource.  The DO block keeps a
-- direct psql rerun safe; the migration runner normally skips applied versions.
DO $$ DECLARE t text; n text; BEGIN
  FOREACH t IN ARRAY ARRAY['exams','quiz_revision_payloads','question_revisions','source_reference_snapshots','confirmation_requests','audit_events','attempts'] LOOP
    n := t || '_owner_course_fk';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname=n) THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id)', t, n);
    END IF;
  END LOOP;
END $$;

ALTER TABLE public.fast_quiz_sessions ADD COLUMN IF NOT EXISTS session_id text;
UPDATE public.fast_quiz_sessions SET session_id = data->>'session_id' WHERE session_id IS NULL;
ALTER TABLE public.fast_quiz_sessions ALTER COLUMN session_id SET NOT NULL;
ALTER TABLE public.fast_quiz_sessions ADD CONSTRAINT fast_quiz_sessions_owner_course_session_key UNIQUE (user_id, course_id, session_id);
ALTER TABLE public.fast_quiz_sessions ADD CONSTRAINT fast_quiz_sessions_owner_course_fk FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id);

ALTER TABLE public.quiz_revision_payloads ADD COLUMN IF NOT EXISTS quiz_revision_id text;
ALTER TABLE public.quiz_revision_payloads ADD COLUMN IF NOT EXISTS state text;
UPDATE public.quiz_revision_payloads SET quiz_revision_id = COALESCE(quiz_revision_id, data->>'quiz_revision_id', record_key);
UPDATE public.quiz_revision_payloads SET state = COALESCE(data->>'state', 'draft') WHERE state IS NULL;
ALTER TABLE public.quiz_revision_payloads ALTER COLUMN quiz_revision_id SET NOT NULL;
ALTER TABLE public.quiz_revision_payloads ALTER COLUMN state SET NOT NULL;
ALTER TABLE public.quiz_revision_payloads ADD CONSTRAINT quiz_revisions_owner_key UNIQUE (user_id, quiz_revision_id);
ALTER TABLE public.quiz_revision_payloads ADD CONSTRAINT quiz_revisions_state_check CHECK (state IN ('draft','confirmed','superseded','archived','deleted'));

ALTER TABLE public.attempts DROP CONSTRAINT IF EXISTS attempts_revision_xor_legacy;
-- Covers records inserted by an older application after 002 had already run.
UPDATE public.attempts SET legacy_session_id = data->>'session_id'
  WHERE legacy_session_id IS NULL AND quiz_revision_id IS NULL AND data ? 'session_id';
ALTER TABLE public.attempts ADD CONSTRAINT attempts_revision_xor_legacy CHECK ((quiz_revision_id IS NULL) <> (legacy_session_id IS NULL));
ALTER TABLE public.attempts ADD CONSTRAINT attempts_quiz_revision_fk FOREIGN KEY (user_id, quiz_revision_id) REFERENCES public.quiz_revision_payloads(user_id, quiz_revision_id);
ALTER TABLE public.attempts ADD CONSTRAINT attempts_legacy_session_fk FOREIGN KEY (user_id, course_id, legacy_session_id) REFERENCES public.fast_quiz_sessions(user_id, course_id, session_id);

CREATE OR REPLACE FUNCTION public.enforce_revision_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND OLD.state IN ('confirmed','superseded') THEN RAISE EXCEPTION 'confirmed revision history is immutable'; END IF;
  IF TG_OP = 'UPDATE' AND OLD.state IN ('confirmed','superseded') THEN
    IF OLD.asset_id IS DISTINCT FROM NEW.asset_id OR OLD.revision_no IS DISTINCT FROM NEW.revision_no
      OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
      OR (OLD.data - 'state' - 'updated_at' - 'confirmed_at') IS DISTINCT FROM (NEW.data - 'state' - 'updated_at' - 'confirmed_at')
      OR (OLD.state = 'superseded' AND NEW.state IS DISTINCT FROM 'superseded')
      OR (OLD.state = 'confirmed' AND NEW.state NOT IN ('confirmed','superseded')) THEN
      RAISE EXCEPTION 'confirmed revision history is immutable';
    END IF;
    IF OLD.state = 'confirmed' AND NEW.state = 'superseded' AND EXISTS (
      SELECT 1 FROM public.learning_assets a WHERE a.user_id=OLD.user_id AND a.current_revision_id=OLD.revision_id
    ) THEN RAISE EXCEPTION 'asset must point to a replacement before superseding its revision'; END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
DROP TRIGGER IF EXISTS asset_revisions_immutable ON public.asset_revisions;
CREATE TRIGGER asset_revisions_immutable BEFORE UPDATE OR DELETE ON public.asset_revisions FOR EACH ROW EXECUTE FUNCTION public.enforce_revision_immutability();

CREATE OR REPLACE FUNCTION public.enforce_source_reference_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.asset_revisions r WHERE r.user_id=NEW.user_id AND r.revision_id=NEW.asset_revision_id AND r.course_id=NEW.course_id)
     OR NOT EXISTS (SELECT 1 FROM public.material_versions m WHERE m.user_id=NEW.user_id AND m.material_version_id=NEW.material_version_id AND m.course_id=NEW.course_id) THEN
    RAISE EXCEPTION 'source reference must remain in its owner course';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS source_references_scope ON public.source_references;
CREATE TRIGGER source_references_scope BEFORE INSERT OR UPDATE ON public.source_references FOR EACH ROW EXECUTE FUNCTION public.enforce_source_reference_scope();

CREATE OR REPLACE FUNCTION public.enforce_asset_current_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.asset_revisions r WHERE r.user_id=NEW.user_id AND r.revision_id=NEW.current_revision_id
      AND r.asset_id=NEW.asset_id AND r.state='confirmed'
  ) THEN RAISE EXCEPTION 'asset current revision must be its confirmed revision'; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS learning_assets_current_revision ON public.learning_assets;
CREATE TRIGGER learning_assets_current_revision BEFORE INSERT OR UPDATE ON public.learning_assets FOR EACH ROW EXECUTE FUNCTION public.enforce_asset_current_revision();

CREATE OR REPLACE FUNCTION public.enforce_attempt_parent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.quiz_revision_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.quiz_revision_payloads q WHERE q.user_id=NEW.user_id AND q.quiz_revision_id=NEW.quiz_revision_id
      AND q.course_id=NEW.course_id AND q.state='confirmed'
  ) THEN RAISE EXCEPTION 'attempt must reference a confirmed quiz revision'; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS attempts_parent_is_usable ON public.attempts;
CREATE TRIGGER attempts_parent_is_usable BEFORE INSERT OR UPDATE ON public.attempts FOR EACH ROW EXECUTE FUNCTION public.enforce_attempt_parent();

CREATE INDEX IF NOT EXISTS asset_revisions_asset_revision_no_idx ON public.asset_revisions(user_id, asset_id, revision_no);
CREATE INDEX IF NOT EXISTS source_references_revision_idx ON public.source_references(user_id, asset_revision_id);
CREATE INDEX IF NOT EXISTS attempts_legacy_session_idx ON public.attempts(user_id, course_id, legacy_session_id);
