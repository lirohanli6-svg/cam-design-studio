"""Required visits and soft auxiliary guides share one C2 arclength curve.

This module owns only route shape. It knows nothing of cam geometry or contact.
"""
from __future__ import annotations

import math
from typing import Callable
import numpy as np
from numpy.typing import NDArray
from .project import Project, ProjectError

Array = NDArray[np.float64]


def required_nodes(p: Project) -> Array:
    points = np.asarray(p.route.waypoints, dtype=float)
    start = np.array([p.route.start_x, p.route.start_y])
    return np.vstack([start, points]) if np.linalg.norm(points[0]-start) > .01 else points


def derivatives(points: Array, heading: float, tension: float, offsets: list[float]) -> tuple[Array, Array]:
    """Clamped-start/end-estimated spline in shared chord-length parameter.

    The tridiagonal solve avoids a dense O(n³) solve for long checkpoint lists.
    Local tangent adjustments use quintic reconstruction with shared first/second
    derivatives, so adjoining segments retain C2 continuity in this parameter.
    """
    h = np.linalg.norm(np.diff(points, axis=0), axis=1)
    d = np.diff(points, axis=0)/h[:, None]
    n = len(points)
    a = np.zeros(n); b = np.ones(n); c = np.zeros(n); rhs = np.zeros((n, 2))
    initial = np.array([math.cos(heading), math.sin(heading)])*tension
    b[0], c[0], rhs[0] = 2*h[0], h[0], 6*(d[0]-initial)
    for i in range(1, n-1):
        a[i], b[i], c[i] = h[i-1], 2*(h[i-1]+h[i]), h[i]
        rhs[i] = 6*(d[i]-d[i-1])
    if n>2:
        # One-sided nonuniform quadratic derivative: unlike a natural end this
        # does not force a continuing final bend to release all curvature.
        end=d[-1]+h[-1]/(h[-2]+h[-1])*(d[-1]-d[-2])
        a[-1],b[-1],rhs[-1]=h[-1],2*h[-1],6*(end-d[-1])
    for i in range(1, n):
        f = a[i]/b[i-1]; b[i] -= f*c[i-1]; rhs[i] -= f*rhs[i-1]
    second = np.zeros_like(points); second[-1] = rhs[-1]/b[-1]
    for i in range(n-2, -1, -1): second[i] = (rhs[i]-c[i]*second[i+1])/b[i]
    first = np.vstack([d-h[:, None]*(2*second[:-1]+second[1:])/6,
                       d[-1]+h[-1]*(second[-2]+2*second[-1])/6])
    for i, angle in enumerate(offsets, 1):
        if i >= n-1: break
        rad = math.radians(angle); x,y = first[i]
        first[i] = [x*math.cos(rad)-y*math.sin(rad), x*math.sin(rad)+y*math.cos(rad)]
    return first, second


def sample_route(p: Project, heading: float, cancelled: Callable[[], bool] | None = None) -> Array:
    points = required_nodes(p); r = p.route
    first, second = derivatives(points, heading, r.tension, r.tangent_offsets)
    pieces = []
    for i, h in enumerate(np.linalg.norm(np.diff(points,axis=0),axis=1)):
        if cancelled and cancelled(): raise ProjectError("计算已停止；未替换原方案。")
        m0,m1 = first[i]*h,first[i+1]*h
        a0,a1,a2 = points[i],m0,second[i]*h*h/2
        d = points[i+1]-a0-a1-a2
        e = m1-a1-2*a2
        f = second[i+1]*h*h-2*a2
        a3,a4,a5 = 10*d-4*e+f/2,-15*d+7*e-f,6*d-3*e+f/2
        t = np.linspace(0,1,max(20,int(h/r.step)*3+1))[:,None]
        guides = [np.asarray(row[1:]) for row in r.auxiliary_points if int(row[0])==i]
        mid = a0+a1*.5+a2*.25+a3*.125+a4*.0625+a5*.03125
        offset = .35*(np.mean(guides,axis=0)-mid) if guides else np.zeros(2)
        def evaluate(t: Array) -> Array:
            return a0+a1*t+a2*t*t+a3*t**3+a4*t**4+a5*t**5+offset*64*t**3*(1-t)**3
        segment = evaluate(t)
        s = np.r_[0.,np.cumsum(np.linalg.norm(np.diff(segment,axis=0),axis=1))]
        if np.any(np.diff(s)<1e-9): raise ProjectError("路线出现停滞或折返，请调整必过点或辅助点。")
        uniform = np.linspace(0,s[-1],int(math.ceil(s[-1]/r.step))+1)
        piece = evaluate(np.interp(uniform,s,t[:,0])[:,None])
        pieces.append(piece if i==0 else piece[1:])
    return np.vstack(pieces)
