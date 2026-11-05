CREATE TABLE public.export_jobs (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  data jsonb NOT NULL,
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id)
);
CREATE INDEX export_jobs_claim_idx ON public.export_jobs ((data->>'status'), created_at);
CREATE INDEX export_jobs_owner_idx ON public.export_jobs(user_id, course_id);
