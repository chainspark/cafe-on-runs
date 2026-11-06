"""Thin wrapper around Garmin's official Personal API client.

Handles token acquisition/caching, one forced refresh on 401, and polite pacing.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

from garmin_personal_api import (
    GarminPersonalApiClient,
    HttpError,
    PersonalApiAuthenticator,
    resolve_api_key,
)

PAGE_SIZE = 100
_PAUSE_S = 0.5


class Garmin:
    def __init__(self) -> None:
        self._api_key = resolve_api_key()
        self._auth = PersonalApiAuthenticator()
        token = self._auth.obtain_token(self._api_key)
        self._client = GarminPersonalApiClient(token.access_token)

    def __enter__(self) -> "Garmin":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()
        self._auth.close()

    def _refresh(self) -> None:
        token = self._auth.obtain_token(self._api_key, force_refresh=True)
        self._client.update_bearer_token(token.access_token)

    def _call(self, fn, *args, **kwargs):
        for attempt in range(4):
            try:
                result = fn(*args, **kwargs)
                time.sleep(_PAUSE_S)
                return result
            except HttpError as e:
                if e.status_code == 401 and attempt == 0:
                    self._refresh()
                elif e.status_code == 429 or e.status_code >= 500:
                    time.sleep(5 * 2**attempt)
                else:
                    raise
        return fn(*args, **kwargs)

    def search_activities(self, **kwargs: Any) -> list[dict]:
        return self._call(self._client.search_activities, **kwargs) or []

    def iter_activities(self, page_size: int = PAGE_SIZE) -> Iterator[dict]:
        """Yield every activity summary, newest first."""
        start = 0
        while True:
            page = self.search_activities(start=start, limit=page_size)
            if not page:
                return
            yield from page
            start += len(page)

    def get_activity(self, activity_id: int) -> dict:
        return self._call(self._client.get_activity, activity_id)

    def download_activity(self, activity_id: int) -> bytes:
        return self._call(self._client.download_activity, activity_id)


def is_run(activity: dict) -> bool:
    return "running" in (activity.get("activityType") or {}).get("typeKey", "")
