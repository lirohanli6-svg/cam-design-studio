"""Display-only equal-scale camera, independent of engineering coordinates."""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class Viewport:
    home: tuple[float,float,float,float] | None=None
    cx: float=0.
    cy: float=0.
    zoom: float=1.
    width: float=200.
    height: float=100.

    def prepare(self,bounds: tuple[float,float,float,float],width: float,height: float) -> None:
        self.width=max(120.,width);self.height=max(100.,height)
        if self.home is None:self.fit(bounds)

    @property
    def scale(self) -> float:
        assert self.home is not None
        a,b,c,d=self.home
        return min(max(20.,self.width-100)/max(b-a,1e-6),max(20.,self.height-60)/max(d-c,1e-6))*self.zoom

    def fit(self,bounds: tuple[float,float,float,float]) -> None:
        a,b,c,d=bounds
        self.home=(a,b if b>a else a+1,c,d if d>c else c+1)
        self.restore()

    def restore(self) -> None:
        if self.home is not None:
            a,b,c,d=self.home;self.cx=(a+b)/2;self.cy=(c+d)/2;self.zoom=1.

    def xy(self,x: float,y: float) -> tuple[float,float]:
        return self.width/2+(x-self.cx)*self.scale,self.height/2+(self.cy-y)*self.scale

    def unxy(self,x: float,y: float) -> tuple[float,float]:
        return self.cx+(x-self.width/2)/self.scale,self.cy-(y-self.height/2)/self.scale

    def zoom_at(self,factor: float,x: float,y: float) -> None:
        if not math.isfinite(factor) or factor<=0:return
        wx,wy=self.unxy(x,y);self.zoom=min(512.,max(.05,self.zoom*factor))
        self.cx=wx-(x-self.width/2)/self.scale;self.cy=wy+(y-self.height/2)/self.scale

    def pan(self,dx: float,dy: float) -> None:
        self.cx-=dx/self.scale;self.cy+=dy/self.scale

    def focus(self,x: float,y: float,radius: float) -> None:
        self.cx=x;self.cy=y
        self.zoom=1.
        self.zoom=min(512.,max(.05,min(self.width,self.height)/max(radius*2,1e-6)/self.scale))

    def visible(self) -> tuple[float,float,float,float]:
        a,d=self.unxy(0,0);b,c=self.unxy(self.width,self.height)
        return a,b,c,d


def grid_step(scale: float) -> float:
    raw=90/max(scale,1e-12);base=10**math.floor(math.log10(raw))
    return next((m*base for m in (1.,2.,5.,10.) if m*base>=raw),10*base)
