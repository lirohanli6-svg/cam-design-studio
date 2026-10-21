"""Ordered distance to route segments, shared by design checks and displays.

Independent DXF verification keeps its own comparison implementation.
"""
from __future__ import annotations
import math
from typing import Any
import numpy as np
from numpy.typing import NDArray
from .project import ProjectError
Array = NDArray[np.float64]


def ordered_deviations(path: Array, targets: list[list[float]], tolerance: float) -> list[dict[str, Any]]:
    if len(path) < 2:
        raise ProjectError("轨迹至少需要两个采样点。")
    edges = np.diff(path, axis=0)
    lengths2 = np.sum(edges*edges, axis=1)
    cursor = 0.
    rows = []
    for number, point in enumerate(targets, 1):
        t = np.clip(np.sum((np.asarray(point)-path[:-1])*edges, axis=1)/np.maximum(lengths2,1e-20), 0, 1)
        segment = min(int(cursor), len(t)-1)
        t[segment] = max(t[segment], cursor-segment)
        positions = np.arange(len(t))+t
        distances = np.linalg.norm(path[:-1]+t[:,None]*edges-point, axis=1)
        distances[positions < cursor-1e-9] = np.inf
        index = int(np.argmin(distances))
        error = float(distances[index])
        if math.isfinite(error):
            cursor = float(positions[index])
        rows.append(dict(number=number, target=point, error_mm=error if math.isfinite(error) else None,
                         position=cursor, point=(path[index]+t[index]*edges[index]).tolist(),
                         passed=error <= tolerance))
    return rows

