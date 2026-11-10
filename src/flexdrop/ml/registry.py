"""Model registry: persists and loads trained artefacts with metadata.

Security note: artefacts are loaded with joblib (pickle). Only load from a directory the
service operator controls (``FLEXDROP_MODEL_DIR``); never from user-supplied paths.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn

from flexdrop.config import TravelParams
from flexdrop.ml.models import (
    AcceptanceModel,
    AnalyticAcceptance,
    AnalyticCoordination,
    AnalyticPrepTime,
    AnalyticTravelTime,
    CoordinationModel,
    LearnedAcceptance,
    LearnedCoordination,
    LearnedPrepTime,
    LearnedTravelTime,
    PrepTimeModel,
    TravelTimeModel,
)

log = logging.getLogger(__name__)

ARTEFACTS = {
    "prep": "prep_model.joblib",
    "travel": "travel_model.joblib",
    "acceptance": "acceptance_model.joblib",
    "coordination": "coordination_model.joblib",
}
METADATA_FILE = "metadata.json"


@dataclass
class ModelBundle:
    prep: PrepTimeModel
    travel: TravelTimeModel
    acceptance: AcceptanceModel
    coordination: CoordinationModel
    metadata: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, str]:
        return {
            "prep": self.prep.name,
            "travel": self.travel.name,
            "acceptance": self.acceptance.name,
            "coordination": self.coordination.name,
        }


def analytic_bundle(travel_params: TravelParams) -> ModelBundle:
    return ModelBundle(
        prep=AnalyticPrepTime(),
        travel=AnalyticTravelTime(travel_params),
        acceptance=AnalyticAcceptance(),
        coordination=AnalyticCoordination(),
        metadata={"source": "analytic-priors"},
    )


def save_model(model_dir: Path, key: str, model: Any) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_dir / ARTEFACTS[key])


def write_metadata(model_dir: Path, metadata: dict[str, Any]) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    meta = dict(metadata)
    meta["sklearn_version"] = sklearn.__version__
    (model_dir / METADATA_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True))


def _load(path: Path, expected: type) -> Any | None:
    if not path.exists():
        return None
    try:
        obj = joblib.load(path)
    except Exception:  # corrupted or incompatible artefact -> fall back, never crash serving
        log.exception("failed to load model artefact %s; using analytic prior", path)
        return None
    if not isinstance(obj, expected):
        log.error("artefact %s has unexpected type %s; using analytic prior", path, type(obj).__name__)
        return None
    return obj


def _smoke_test(key: str, model: Any) -> bool:
    """Run one tiny prediction so incompatible artefacts are rejected at load time, not mid-request."""
    from flexdrop.ml.features import AcceptanceFeatures, CoordinationFeatures, PrepFeatures

    try:
        if key == "prep":
            model.predict([PrepFeatures(True, 12.0, 2, 20.0, False, 1)])
        elif key == "travel":
            model.predict_minutes(np.array([1.0]), 0.0)
        elif key == "acceptance":
            model.predict_proba([AcceptanceFeatures(10.0, 10.0, 0.5, True, 300.0, 20.0)])
        else:
            model.predict_proba([CoordinationFeatures(10.0, 2, 2, 1, 20.0, True)])
    except Exception:
        log.exception("model artefact '%s' failed its smoke test; using analytic prior", key)
        return False
    return True


def load_bundle(model_dir: str | Path, travel_params: TravelParams) -> ModelBundle:
    """Load trained models; any missing, incompatible or failing artefact falls back to its analytic prior.

    Artefacts pickled with a different scikit-learn version are never loaded (pickles are not
    portable across versions); retrain with ``flexdrop train`` or ``flexdrop pipeline``.
    """
    d = Path(model_dir)
    bundle = analytic_bundle(travel_params)
    meta_path = d / METADATA_FILE
    if meta_path.exists():
        try:
            bundle.metadata = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            log.error("invalid model metadata at %s", meta_path)
        trained_with = bundle.metadata.get("sklearn_version")
        if trained_with and trained_with != sklearn.__version__:
            log.warning(
                "models in %s were trained with scikit-learn %s but %s is installed; using analytic priors. "
                "Run `flexdrop pipeline` (or `flexdrop train`) to retrain for this environment.",
                d,
                trained_with,
                sklearn.__version__,
            )
            bundle.metadata["skipped_reason"] = "sklearn_version_mismatch"
            return bundle
    expected = {
        "prep": LearnedPrepTime,
        "travel": LearnedTravelTime,
        "acceptance": LearnedAcceptance,
        "coordination": LearnedCoordination,
    }
    for key, cls in expected.items():
        model = _load(d / ARTEFACTS[key], cls)
        if model is not None and _smoke_test(key, model):
            setattr(bundle, key, model)
    log.info("model bundle loaded: %s", bundle.describe())
    return bundle
