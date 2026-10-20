"""Assembly references in the same face view as the exported cam outline."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .engine import Array, Result
from .project import ProjectError


@dataclass(frozen=True)
class CamMarkings:
    start: Array
    arrow: Array
    arrowhead: Array
    direction: str


def cam_markings(result: Result) -> CamMarkings:
    if not len(result.profile):
        raise ProjectError("尚无凸轮轮廓，无法标记出发点。")
    # Index zero is the actual roller/ball contact at drive distance zero,
    # including the configured phase. It is not the polar zero-angle point.
    start = result.profile[0].copy()
    radius = float(np.linalg.norm(result.profile, axis=1).max())*1.14
    direction = result.project.cam.direction
    angles = np.radians(270+direction*np.linspace(-50, 50, 41))
    arrow = radius*np.column_stack([np.cos(angles), np.sin(angles)])
    tip = arrow[-1]
    tangent = direction*np.array([-math.sin(angles[-1]), math.cos(angles[-1])])
    normal = np.array([-tangent[1], tangent[0]])
    length = radius*0.07
    arrowhead = np.array([tip-length*tangent+length*0.45*normal,
                          tip, tip-length*tangent-length*0.45*normal])
    return CamMarkings(start, arrow, arrowhead, "逆时针" if direction == 1 else "顺时针")
