"""Resolve Slack user IDs to display names, disk-cached.

Degrades to raw IDs on any Slack error (e.g. missing users:read scope) so it
never takes down the Slack section of the pull.
"""
import json
import os
import time
from pathlib import Path

from slack_sdk.errors import SlackApiError

DEFAULT_CACHE = Path(__file__).parent / ".cache" / "slack_users.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600


def _pick_name(u: dict) -> str:
    profile = u.get("profile") or {}
    return profile.get("real_name") or profile.get("display_name") or u.get("name") or u.get("id", "")


class SlackUserDirectory:
    def __init__(self, client, own_id, cache_path=DEFAULT_CACHE):
        self.client = client
        self.own_id = own_id
        self.cache_path = Path(cache_path)
        self._names = None  # loaded lazily

    def resolve_many(self, ids):
        self._ensure_loaded()
        out = {}
        for uid in ids:
            if uid == self.own_id:
                out[uid] = "You"
            elif uid in self._names:
                out[uid] = self._names[uid]
            else:
                out[uid] = self._lazy_lookup(uid)
        return out

    # --- internals ---

    def _ensure_loaded(self):
        if self._names is not None:
            return
        cached = self._load_cache()
        if cached is not None:
            self._names = cached
        else:
            self._names = self._bulk_load()
            if self._names:
                self._save_cache(self._names)

    def _load_cache(self):
        try:
            if self.cache_path.exists() and (time.time() - self.cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
                return json.loads(self.cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _save_cache(self, names):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(names))
            os.replace(tmp, self.cache_path)
        except OSError:
            pass  # cache is best-effort

    def _bulk_load(self):
        names = {}
        cursor = None
        try:
            while True:
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                resp = self.client.users_list(**params)
                for u in resp.get("members", []):
                    names[u["id"]] = _pick_name(u)
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        except SlackApiError as e:
            if e.response.get("error") == "missing_scope":
                needed = e.response.get("needed", "users:read")
                print(f"⚠ Slack user names unavailable: missing scope '{needed}'. Showing raw IDs.")
            else:
                print(f"⚠ Slack users.list failed: {e}. Showing raw IDs.")
        return names

    def _lazy_lookup(self, uid):
        try:
            resp = self.client.users_info(user=uid)
            name = _pick_name(resp["user"])
            if name:  # only cache if we got a name
                self._names[uid] = name
                self._save_cache(self._names)
                return name
            else:
                return uid
        except SlackApiError:
            return uid
