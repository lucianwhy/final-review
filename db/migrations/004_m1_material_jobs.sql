CREATE TABLE public.material_jobs (
  job_id text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  document_id text NOT NULL,
  idempotency_key text,
  fingerprint text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
  stage text,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  error_code text,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id),
  FOREIGN KEY (user_id, document_id) REFERENCES public.documents(user_id, document_id)
    ON DELETE CASCADE
);
CREATE UNIQUE INDEX material_jobs_idempotency_idx
  ON public.material_jobs(user_id, course_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX material_jobs_claim_idx ON public.material_jobs(available_at, created_at)
  WHERE status IN ('queued','running');
CREATE INDEX material_jobs_course_idx ON public.material_jobs(user_id, course_id, created_at DESC);
