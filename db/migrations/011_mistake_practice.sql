-- M4 first round: immutable grading provenance with separate mastery and practice records.
CREATE TABLE public.mistakes (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  attempt_id text NOT NULL,
  question_revision_id text NOT NULL,
  created_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  UNIQUE (user_id, attempt_id, question_revision_id),
  UNIQUE (user_id, course_id, record_key),
  FOREIGN KEY (user_id, course_id, attempt_id)
    REFERENCES public.attempts(user_id, course_id, record_key),
  FOREIGN KEY (user_id, question_revision_id)
    REFERENCES public.question_revisions(user_id, question_revision_id)
);
CREATE INDEX mistakes_course_idx ON public.mistakes(user_id, course_id, created_at DESC);

CREATE TABLE public.mastery_observations (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  mistake_id text NOT NULL,
  created_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  FOREIGN KEY (user_id, course_id, mistake_id)
    REFERENCES public.mistakes(user_id, course_id, record_key)
);
CREATE INDEX mastery_observations_mistake_idx
  ON public.mastery_observations(user_id, course_id, mistake_id, created_at);

CREATE TABLE public.mistake_practices (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  mistake_id text NOT NULL,
  created_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  FOREIGN KEY (user_id, course_id, mistake_id)
    REFERENCES public.mistakes(user_id, course_id, record_key)
);
CREATE INDEX mistake_practices_mistake_idx
  ON public.mistake_practices(user_id, course_id, mistake_id, created_at DESC);

CREATE FUNCTION public.enforce_mistake_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.attempts a
    JOIN public.question_revisions q ON q.user_id=a.user_id
      AND q.question_revision_id=NEW.question_revision_id
    JOIN public.question_grades g ON g.user_id=a.user_id
      AND g.course_id=a.course_id AND g.attempt_id=a.record_key
      AND g.question_revision_id=q.question_revision_id
    WHERE a.user_id=NEW.user_id AND a.course_id=NEW.course_id
      AND a.record_key=NEW.attempt_id AND a.quiz_revision_id=q.quiz_revision_id
      AND a.data->>'status'='submitted'
  ) THEN RAISE EXCEPTION 'mistake must reference a graded question in its attempt revision'; END IF;
  IF TG_OP='UPDATE' AND (
    NEW.attempt_id IS DISTINCT FROM OLD.attempt_id OR
    NEW.question_revision_id IS DISTINCT FROM OLD.question_revision_id OR
    (NEW.data - 'mastery_status' - 'updated_at' - 'mastery_version')
      IS DISTINCT FROM (OLD.data - 'mastery_status' - 'updated_at' - 'mastery_version')
  ) THEN RAISE EXCEPTION 'mistake provenance is immutable'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER mistakes_scope BEFORE INSERT OR UPDATE ON public.mistakes
  FOR EACH ROW EXECUTE FUNCTION public.enforce_mistake_scope();
