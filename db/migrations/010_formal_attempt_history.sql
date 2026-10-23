-- A running Attempt may outlive the current quiz revision. New Attempts still
-- require a confirmed paper; existing ones keep their immutable parent after
-- that paper becomes superseded by a later confirmation.
CREATE OR REPLACE FUNCTION public.enforce_attempt_parent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' AND (
    NEW.quiz_revision_id IS DISTINCT FROM OLD.quiz_revision_id OR
    NEW.legacy_session_id IS DISTINCT FROM OLD.legacy_session_id
  ) THEN RAISE EXCEPTION 'attempt parent is immutable'; END IF;
  IF NEW.quiz_revision_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.quiz_revision_payloads q
    WHERE q.user_id=NEW.user_id AND q.quiz_revision_id=NEW.quiz_revision_id
      AND q.course_id=NEW.course_id
      AND (
        q.state='confirmed' OR
        (q.state='superseded' AND (
          TG_OP='UPDATE' OR EXISTS (
            -- Store.put uses INSERT ... ON CONFLICT DO UPDATE. PostgreSQL runs
            -- BEFORE INSERT before the conflict UPDATE, so recognize an existing
            -- Attempt with the same immutable parent without allowing a new one.
            SELECT 1 FROM public.attempts existing
            WHERE existing.record_key=NEW.record_key AND existing.user_id=NEW.user_id
              AND existing.course_id=NEW.course_id
              AND existing.quiz_revision_id=NEW.quiz_revision_id
          )
        ))
      )
  ) THEN RAISE EXCEPTION 'attempt must reference a confirmed quiz revision'; END IF;
  RETURN NEW;
END $$;

-- Keep answer and grade records queryable without rewriting legacy Attempts.
ALTER TABLE public.attempts ADD CONSTRAINT attempts_owner_course_record_key
  UNIQUE (user_id, course_id, record_key);

CREATE TABLE public.attempt_answers (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  attempt_id text NOT NULL,
  question_revision_id text NOT NULL,
  created_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  UNIQUE (user_id, attempt_id, question_revision_id),
  FOREIGN KEY (user_id, course_id, attempt_id)
    REFERENCES public.attempts(user_id, course_id, record_key),
  FOREIGN KEY (user_id, question_revision_id)
    REFERENCES public.question_revisions(user_id, question_revision_id)
);
CREATE INDEX attempt_answers_course_idx ON public.attempt_answers(user_id, course_id, attempt_id);

CREATE TABLE public.question_grades (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  attempt_id text NOT NULL,
  question_revision_id text NOT NULL,
  created_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  UNIQUE (user_id, attempt_id, question_revision_id),
  FOREIGN KEY (user_id, course_id, attempt_id)
    REFERENCES public.attempts(user_id, course_id, record_key),
  FOREIGN KEY (user_id, question_revision_id)
    REFERENCES public.question_revisions(user_id, question_revision_id)
);
CREATE INDEX question_grades_course_idx ON public.question_grades(user_id, course_id, attempt_id);

CREATE FUNCTION public.enforce_formal_question_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.attempts a JOIN public.question_revisions q
      ON q.user_id=a.user_id AND q.question_revision_id=NEW.question_revision_id
    WHERE a.user_id=NEW.user_id AND a.course_id=NEW.course_id
      AND a.record_key=NEW.attempt_id AND q.course_id=NEW.course_id
      AND q.quiz_revision_id=a.quiz_revision_id
      AND a.quiz_revision_id IS NOT NULL
      AND (
        (TG_TABLE_NAME='attempt_answers' AND a.data->>'status'='in_progress') OR
        (TG_TABLE_NAME='question_grades' AND a.data->>'status'='submitted')
      )
  ) THEN RAISE EXCEPTION 'formal answer or grade must belong to its attempt revision'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER attempt_answers_scope BEFORE INSERT OR UPDATE ON public.attempt_answers
  FOR EACH ROW EXECUTE FUNCTION public.enforce_formal_question_scope();
CREATE TRIGGER question_grades_scope BEFORE INSERT OR UPDATE ON public.question_grades
  FOR EACH ROW EXECUTE FUNCTION public.enforce_formal_question_scope();

CREATE FUNCTION public.prevent_question_grade_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'question grade snapshot is immutable';
END $$;
CREATE TRIGGER question_grades_immutable BEFORE UPDATE OR DELETE ON public.question_grades
  FOR EACH ROW EXECUTE FUNCTION public.prevent_question_grade_change();
