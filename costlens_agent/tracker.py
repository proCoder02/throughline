"""
Vendored copy of CostLens' costlens_sdk.py (CostLensTracker only) — speech2text
is a separate repo/venv from CostLens, so this can't be imported directly.
Batches usage records and flushes them to CostLens' /usage/ingest endpoint.
"""

import time
import threading
import logging
from typing import List
from dataclasses import dataclass, asdict

import httpx

logger = logging.getLogger("costlens-agent")


@dataclass
class UsageRecord:
    provider: str
    endpoint: str
    method: str = "POST"
    feature_tag: str = "untagged"
    request_count: int = 1
    tokens_used: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    status_code: int = 200


class CostLensTracker:
    def __init__(
        self,
        api_key: str,
        costlens_url: str,
        batch_size: int = 5,
        flush_interval_seconds: int = 5,
    ):
        self.api_key = api_key
        self.costlens_url = costlens_url.rstrip("/")
        self.batch_size = batch_size
        self.flush_interval = flush_interval_seconds

        self._buffer: List[dict] = []
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=10)

        self._flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self._flush_thread.start()

    def log(
        self,
        provider: str,
        endpoint: str,
        method: str = "POST",
        feature_tag: str = "untagged",
        request_count: int = 1,
        tokens_used: int = 0,
        cost: float = 0.0,
        latency_ms: int = 0,
        status_code: int = 200,
    ):
        record = UsageRecord(
            provider=provider,
            endpoint=endpoint,
            method=method,
            feature_tag=feature_tag,
            request_count=request_count,
            tokens_used=tokens_used,
            cost=cost,
            latency_ms=latency_ms,
            status_code=status_code,
        )
        with self._lock:
            self._buffer.append(asdict(record))
            if len(self._buffer) >= self.batch_size:
                self._flush()

    def _flush(self):
        if not self._buffer:
            return
        records = self._buffer.copy()
        self._buffer.clear()

        try:
            response = self._client.post(
                f"{self.costlens_url}/usage/ingest",
                json={"records": records},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if response.status_code != 201:
                logger.warning("CostLens ingest failed: %s", response.status_code)
        except Exception as e:
            logger.warning("CostLens flush error: %s", e)
            with self._lock:
                self._buffer = records + self._buffer

    def _periodic_flush(self):
        while True:
            time.sleep(self.flush_interval)
            with self._lock:
                self._flush()
