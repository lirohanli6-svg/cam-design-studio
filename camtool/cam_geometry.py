"""Main-design sampled contact evidence (not the independent DXF validator)."""
from __future__ import annotations
from typing import Callable, Any
import numpy as np
from numpy.typing import NDArray
from .project import ProjectError
Array = NDArray[np.float64]


def contact_evidence(pitch: Array, profile: Array, radius: float,
                     cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    start = profile[:-1] if np.linalg.norm(profile[-1]-profile[0])<1e-9 else profile
    centers = pitch[:-1] if len(pitch)>1 and np.linalg.norm(pitch[-1]-pitch[0])<1e-9 else pitch
    edges = np.roll(start,-1,axis=0)-start
    lengths = np.maximum(np.sum(edges**2,axis=1),1e-20)
    gaps = np.empty(len(centers)); segments = np.empty(len(centers),dtype=int)
    nearest = np.empty_like(centers)
    for i,center in enumerate(centers):
        if cancelled and cancelled(): raise ProjectError("计算已停止；未替换原方案。")
        t = np.clip(np.sum((center-start)*edges,axis=1)/lengths,0,1)
        candidates = start+t[:,None]*edges
        distances = np.linalg.norm(candidates-center,axis=1)
        j = int(np.argmin(distances)); gaps[i]=distances[j]-radius
        segments[i]=j; nearest[i]=candidates[j]
    return dict(gaps=gaps, segments=segments, nearest=nearest,
                minimum=float(gaps.min()), index=int(np.argmin(gaps)))
