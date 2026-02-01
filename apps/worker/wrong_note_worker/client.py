# PATH: apps/worker/wrong_note_worker/client.py
from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class APIClient:
    def __init__(self, *, api_base_url: str, worker_token: str, timeout_seconds: float) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout_seconds = float(timeout_seconds)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.worker_token}",
            "Content-Type": "application/json",
        }

    def get_next_job(self) -> Dict[str, Any]:
        url = f"{self.api_base_url}/api/v1/internal/wrong-note-worker/next/"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout_seconds)
        r.raise_for_status()
        return r.json()

    def get_job_data(self, *, job_id: int) -> Dict[str, Any]:
        url = f"{self.api_base_url}/api/v1/internal/wrong-note-worker/{int(job_id)}/data/"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout_seconds)
        r.raise_for_status()
        return r.json()

    def prepare_upload(self, *, job_id: int) -> Dict[str, Any]:
        url = f"{self.api_base_url}/api/v1/internal/wrong-note-worker/{int(job_id)}/prepare-upload/"
        r = requests.post(url, headers=self._headers(), json={}, timeout=self.timeout_seconds)
        r.raise_for_status()
        return r.json()

    def complete(self, *, job_id: int, file_path: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.api_base_url}/api/v1/internal/wrong-note-worker/{int(job_id)}/complete/"
        payload: Dict[str, Any] = {"file_path": str(file_path)}
        if meta is not None:
            payload["meta"] = meta
        r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_seconds)
        r.raise_for_status()
        return r.json()

    def fail(self, *, job_id: int, error_message: str) -> Dict[str, Any]:
        url = f"{self.api_base_url}/api/v1/internal/wrong-note-worker/{int(job_id)}/fail/"
        r = requests.post(
            url,
            headers=self._headers(),
            json={"error_message": str(error_message)[:5000]},
            timeout=self.timeout_seconds,
        )
        r.raise_for_status()
        return r.json()
