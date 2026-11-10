"""Application context: settings, database, engine configuration and models."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from flexdrop.config import EngineConfig, Settings
from flexdrop.db.session import Database
from flexdrop.ml.registry import ModelBundle, load_bundle


@dataclass
class AppContext:
    settings: Settings
    engine_config: EngineConfig
    db: Database
    models: ModelBundle
    # Serialises optimisation runs inside one process. Cross-process safety comes from the
    # conditional (compare-and-set) status transitions performed when plans are persisted.
    optimize_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def build(cls, settings: Settings, create_schema: bool = False) -> AppContext:
        db = Database(settings.database_url)
        if create_schema:
            db.create_all()
        cfg = settings.engine_config()
        return cls(settings=settings, engine_config=cfg, db=db, models=load_bundle(settings.model_dir, cfg.travel))
