from threading import local

from final_review.postgres import PostgresStore


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *_args):
        pass

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)


def test_search_excludes_embedding_from_retrieval_payload():
    store = PostgresStore.__new__(PostgresStore)
    store._local = local()
    store._local.connection = _Connection(
        [
            {
                "data": {
                    "chunk_id": "chunk-1",
                    "course_id": "course-1",
                    "embedding": [0.1, 0.2, 0.3],
                },
                "similarity": 0.9,
            }
        ]
    )
    token = store.bind_user("user-1")
    try:
        rows = store.search([0.1, 0.2, 0.3], "course-1", "", 1)
    finally:
        store.reset_user(token)

    assert rows == [{"chunk_id": "chunk-1", "course_id": "course-1", "similarity": 0.9}]
