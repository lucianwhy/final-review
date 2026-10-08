CREATE TABLE public.quiz_jobs (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  conversation_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  data jsonb NOT NULL,
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id),
  CHECK (data->>'status' IN ('queued','running','failed','succeeded'))
);
CREATE INDEX quiz_jobs_claim_idx ON public.quiz_jobs ((data->>'status'), created_at);
CREATE INDEX quiz_jobs_scope_idx ON public.quiz_jobs(user_id, course_id, conversation_id);

-- Old placeholder records remain readable. Generated papers bind to a real asset revision.
ALTER TABLE public.quiz_revision_payloads ADD COLUMN asset_revision_id text;
ALTER TABLE public.quiz_revision_payloads ADD CONSTRAINT quiz_payload_asset_fk
  FOREIGN KEY (user_id, asset_revision_id) REFERENCES public.asset_revisions(user_id, revision_id);

ALTER TABLE public.question_revisions ADD COLUMN question_revision_id text;
ALTER TABLE public.question_revisions ADD COLUMN quiz_revision_id text;
ALTER TABLE public.question_revisions ADD CONSTRAINT question_revision_owner_key
  UNIQUE (user_id, question_revision_id);
ALTER TABLE public.question_revisions ADD CONSTRAINT question_revision_quiz_fk
  FOREIGN KEY (user_id, quiz_revision_id) REFERENCES public.quiz_revision_payloads(user_id, quiz_revision_id);
ALTER TABLE public.question_revisions ADD CONSTRAINT question_revision_paper_course_key
  UNIQUE (user_id, question_revision_id, quiz_revision_id, course_id);
ALTER TABLE public.source_references ADD COLUMN question_revision_id text;
ALTER TABLE public.source_references ADD CONSTRAINT source_reference_question_fk
  FOREIGN KEY (user_id, question_revision_id, asset_revision_id, course_id)
  REFERENCES public.question_revisions(user_id, question_revision_id, quiz_revision_id, course_id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION public.enforce_generated_quiz_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.asset_revision_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.asset_revisions r JOIN public.learning_assets a
    ON a.user_id=r.user_id AND a.asset_id=r.asset_id
    WHERE r.user_id=NEW.user_id AND r.revision_id=NEW.asset_revision_id
      AND r.course_id=NEW.course_id AND a.data->>'asset_type'='quiz'
      AND r.state=NEW.state AND NEW.quiz_revision_id=r.revision_id
  ) THEN RAISE EXCEPTION 'quiz payload must match its quiz asset revision'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER quiz_payload_scope BEFORE INSERT OR UPDATE ON public.quiz_revision_payloads
FOR EACH ROW EXECUTE FUNCTION public.enforce_generated_quiz_scope();

CREATE FUNCTION public.enforce_question_revision_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' AND OLD.question_revision_id IS NOT NULL AND OLD.data IS DISTINCT FROM NEW.data THEN
    RAISE EXCEPTION 'question revision contents are immutable';
  END IF;
  IF TG_OP='DELETE' THEN
    IF EXISTS (SELECT 1 FROM public.quiz_revision_payloads q WHERE q.user_id=OLD.user_id
      AND q.quiz_revision_id=OLD.quiz_revision_id AND q.state IN ('confirmed','superseded')) THEN
      RAISE EXCEPTION 'confirmed question history is immutable';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.question_revision_id IS NOT NULL AND (
    NEW.question_revision_id IS DISTINCT FROM NEW.record_key OR NOT EXISTS (
      SELECT 1 FROM public.quiz_revision_payloads q WHERE q.user_id=NEW.user_id
        AND q.quiz_revision_id=NEW.quiz_revision_id AND q.course_id=NEW.course_id
    )
  ) THEN RAISE EXCEPTION 'question must belong to its owner paper course'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER question_revision_scope BEFORE INSERT OR UPDATE OR DELETE ON public.question_revisions
FOR EACH ROW EXECUTE FUNCTION public.enforce_question_revision_scope();

CREATE FUNCTION public.enforce_quiz_payload_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND OLD.state IN ('confirmed','superseded') THEN
    RAISE EXCEPTION 'confirmed quiz history is immutable';
  END IF;
  IF TG_OP='UPDATE' AND OLD.state IN ('confirmed','superseded') AND (
    (OLD.data - 'state' - 'updated_at') IS DISTINCT FROM (NEW.data - 'state' - 'updated_at')
    OR (OLD.state='superseded' AND NEW.state<>'superseded')
    OR (OLD.state='confirmed' AND NEW.state NOT IN ('confirmed','superseded'))
  ) THEN RAISE EXCEPTION 'confirmed quiz history is immutable'; END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER quiz_payload_immutable BEFORE UPDATE OR DELETE ON public.quiz_revision_payloads
FOR EACH ROW EXECUTE FUNCTION public.enforce_quiz_payload_immutability();
