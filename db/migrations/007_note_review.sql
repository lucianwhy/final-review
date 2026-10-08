-- Backfill note navigation pointers without changing immutable revision contents.
UPDATE public.learning_assets a
SET data = a.data || jsonb_build_object('latest_revision_id', latest.revision_id,
                                      'generation_method', CASE WHEN latest.data ? 'points' THEN 'ai' ELSE 'legacy' END)
FROM (
  SELECT DISTINCT ON (user_id, asset_id) user_id, asset_id, revision_id, data
  FROM public.asset_revisions ORDER BY user_id, asset_id, revision_no DESC
) latest
WHERE a.user_id = latest.user_id AND a.asset_id = latest.asset_id
  AND a.data->>'asset_type' = 'note' AND NOT (a.data ? 'latest_revision_id');

CREATE UNIQUE INDEX IF NOT EXISTS asset_revisions_unique_number
ON public.asset_revisions(user_id, asset_id, revision_no);
