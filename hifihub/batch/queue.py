"""Procesamiento por lotes: N enlaces (archivo .txt o lista) en cola asyncio.

Cada descarga corre en un hilo del pool (`asyncio.to_thread`) limitado por un semáforo. 

Un enlace roto no aborta el lote: se reintenta con backoff y, si agota intentos, se registra el error y se continúa. El download_archive de yt-dlp evita re-descargar lo que ya existe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hifihub.batch.registry import Registry
from hifihub.engine.downloader import DEFAULT_PLAYER_CLIENTS, Downloader

BatchEventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class BatchItem:
    url: str
    status: str = "pending"      # Estados de - pending | ok | error
    error: str | None = None
    attempts: int = 0


@dataclass
class BatchReport:
    items: list[BatchItem] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return sum(1 for i in self.items if i.status == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status == "error")

    @property
    def total(self) -> int:
        return len(self.items)


def parse_links_file(path: Path | str) -> list[str]:
    """Un enlace por línea; líneas vacías y comentarios (#) se ignoran."""
    urls: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


class BatchRunner:
    def __init__(
        self,
        concurrency: int = 2,
        retries: int = 1,
        on_event: BatchEventCallback | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.concurrency = max(1, concurrency)
        self.retries = max(0, retries)
        self._on_event = on_event
        self._registry = registry
        # Backoff entre reintentos (segundos); corto en tests vía atributo. Recomiendo aumentarlo para evitar bloqueantes.
        self.retry_delay = 5.0

    def _emit(self, channel: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(channel, payload)

    async def run(
        self,
        urls: list[str],
        dest: Path,
        *,
        profile: str = "purista",
        template: str = "flat",
        enrich: bool = False,
        acoustid_key: str = "",
        replaygain: bool = False,
        cookies_browser: str = "",
        cookies_file: str = "",
        player_clients: str = "",
        pacing: float = 0.0,
    ) -> BatchReport:
        report = BatchReport(items=[BatchItem(url=u) for u in urls])
        sem = asyncio.Semaphore(self.concurrency)
        job = dict(
            profile=profile, template=template, enrich=enrich,
            acoustid_key=acoustid_key, replaygain=replaygain,
            cookies_browser=cookies_browser, cookies_file=cookies_file,
            player_clients=player_clients, pacing=pacing,
        )

        async def worker(index: int, item: BatchItem) -> None:
            async with sem:
                for attempt in range(self.retries + 1):
                    item.attempts = attempt + 1
                    try:
                        await asyncio.to_thread(self._download_one, item.url, dest, job)
                        item.status = "ok"
                        break
                    except Exception as e: 
                        item.error = str(e)
                        if attempt < self.retries:
                            await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    item.status = "error"
                self._record(item, dest, job)
                from hifihub.engine.downloader import humanize_error
                self._emit("batch:item", {
                    "index": index, "total": report.total, "url": item.url,
                    "status": item.status,
                    "error": humanize_error(item.error) if item.error else None,
                    "done": sum(1 for i in report.items if i.status != "pending"),
                })

        await asyncio.gather(*(worker(i, it) for i, it in enumerate(report.items)))
        self._emit("batch:complete", {
            "total": report.total, "ok": report.ok, "failed": report.failed,
        })
        return report

    def _download_one(self, url: str, dest: Path, job: dict[str, Any]) -> None:
        Downloader().download_audio(
            url, dest,
            profile=job["profile"], template=job["template"],
            enrich=job["enrich"], acoustid_key=job["acoustid_key"],
            replaygain=job["replaygain"],
            cookies_browser=job.get("cookies_browser", ""),
            cookies_file=job.get("cookies_file", ""),
            player_clients=job.get("player_clients", "") or DEFAULT_PLAYER_CLIENTS,
            pacing=job.get("pacing", 0.0),
            use_archive=True,
        )

    def _record(self, item: BatchItem, dest: Path, job: dict[str, Any]) -> None:
        if self._registry is None:
            return
        try:
            self._registry.record(
                item.url, item.status, error=item.error,
                profile=job["profile"], template=job["template"],
                dest=str(dest), attempts=item.attempts,
            )
        except Exception:  # Ex para que el registro nunca tumbe el lote
            pass


def run_batch(urls: list[str], dest: Path, **kwargs: Any) -> BatchReport:
    """Envoltorio síncrono (para CLI y para el hilo del puente de la UI)."""
    runner_kwargs = {
        k: kwargs.pop(k)
        for k in ("concurrency", "retries", "on_event", "registry")
        if k in kwargs
    }
    runner = BatchRunner(**runner_kwargs)
    return asyncio.run(runner.run(urls, dest, **kwargs))
