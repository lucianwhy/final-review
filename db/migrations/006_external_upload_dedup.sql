-- Legacy documents have no content_sha256 and are deliberately left untouched.
-- A deleted file may be uploaded again as a new document.
CREATE UNIQUE INDEX documents_active_name_content_idx
ON public.documents (user_id, course_id, (data->>'file_name'), (data->>'content_sha256'))
WHERE data ? 'content_sha256' AND data->>'parse_status' <> 'deleted';
