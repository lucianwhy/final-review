"""Restore links to note drafts created before note chats were persisted.

Run without --apply to inspect the count. This cannot reconstruct the original
chat transcript, which was never stored; the restored message says so plainly.
"""

import argparse

from final_review.config import Settings
from final_review.postgres import PostgresStore
from final_review.storage import stable_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    url = Settings().database_url.get_secret_value()
    if not url:
        raise RuntimeError("DATABASE_URL 未配置")
    store = PostgresStore(url)
    try:
        with store.connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id,course_id,data FROM review_sessions "
                "WHERE data->'response'->'draft'->>'asset_id' IS NOT NULL"
            )
            sessions = cursor.fetchall()
        restored = 0
        for session in sessions:
            user_id = str(session["user_id"])
            course_id, data = session["course_id"], session["data"]
            response = data.get("response") or {}
            draft = response.get("draft")
            session_id = data.get("session_id")
            if not isinstance(draft, dict) or not session_id:
                continue
            token = store.bind_user(user_id)
            try:
                asset = store.get("learning_asset", draft["asset_id"])
                if not asset or asset.get("course_id") != course_id:
                    continue
                messages = store.scan("message", {"course_id": course_id})
                if any((item.get("draft") or {}).get("asset_id") == draft["asset_id"]
                       for item in messages):
                    continue
                conversation_id = f"chat-recovered-{session_id}"
                key = stable_key(course_id, conversation_id)
                if store.get("conversation", key):
                    continue
                restored += 1
                if not arguments.apply:
                    continue
                timestamp = asset.get("created_at") or data.get("created_at")
                with store.transaction():
                    store.put("conversation", key, {
                        "conversation_id": conversation_id,
                        "course_id": course_id,
                        "user_id": user_id,
                        "title": asset.get("title") or "历史笔记草稿",
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    })
                    store.put("message", stable_key(key, "restored-draft"), {
                        "conversation_id": conversation_id,
                        "course_id": course_id,
                        "user_id": user_id,
                        "role": "assistant",
                        "content": "已恢复这份历史笔记草稿的入口。生成时的原对话消息未曾保存。",
                        "draft": draft,
                        "created_at": timestamp,
                    })
            finally:
                store.reset_user(token)
        print(f"{'已恢复' if arguments.apply else '可恢复'} {restored} 条历史笔记草稿入口")
    finally:
        store.close()


if __name__ == "__main__":
    main()
