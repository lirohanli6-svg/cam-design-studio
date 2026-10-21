"""Reproducible theoretical cases; never represents measured vehicle data."""
from pathlib import Path
import copy
import json
import math
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from camtool.project import Project
from camtool.engine import calculate
from camtool.candidate_audit import audit_candidate
from camtool.auxiliary_search import search_auxiliaries,SearchSettings
from camtool.export import export_design
from camtool.verification import export_verification
from camtool.version import VERSION
directory=ROOT/'build'/f'v{VERSION}-theoretical-cases';directory.mkdir(parents=True,exist_ok=True)
examples=ROOT/'examples/辅助点理论案例';examples.mkdir(parents=True,exist_ok=True)
cases={}
p=Project.demo();p.route.waypoints=[[300.,400.],[1300.,400.]];cases['直线与圆形凸轮']=p
p=Project.demo();radius=1000.;theta=np.linspace(0,math.pi/3,9)
points=np.column_stack([300+radius*np.sin(theta)+p.vehicle.sensor_x*np.cos(theta),400+radius*(1-np.cos(theta))+p.vehicle.sensor_x*np.sin(theta)])
p.route.waypoints=points.tolist();p.route.start_x,p.route.start_y=points[0]
p.route.start_steer=math.degrees(math.atan(p.vehicle.wheelbase/radius));p.route.tension=1.;cases['已知圆弧']=p
cases['连续弯道_无辅助']=Project.demo()
p=Project.demo();p.route.auxiliary_points=[[1.,1600.,1100.]];cases['连续弯道_偏置辅助']=p
p=Project.demo();p.route.obstacles=[[300.,400.,30.]];cases['必过起点障碍_不可行']=p
p=Project.demo();p.mechanism.kind='ball';cases['顶球连续弯道']=p
rows={}
for name,p in cases.items():
    p.name='理论案例（非实测）·'+name;p.save(examples/(name+'.cam.json'))
    r=calculate(p);files=export_design(r,directory/name)
    audit,blob=audit_candidate(r);files['dxf'].write_bytes(blob);audit.filename=str(files['dxf'])
    export_verification(audit,directory/name/'独立检验')
    rows[name]=dict(metrics=r.stats,main_pass=r.valid,precision=r.precision,
                    independent_pass=audit.kinematic_pass,
                    independent_failures=[vars(c) for c in audit.checks if c.status=='不通过'])
    print(name,rows[name]['main_pass'],rows[name]['independent_pass'],flush=True)
o=search_auxiliaries(cases['连续弯道_偏置辅助'],SearchSettings(evaluations=12,seconds=45))
o.export(directory/'辅助点前后搜索')
rows['辅助点搜索']=dict(status=o.status,before=o.original.stats if o.original else None,after=o.candidate.stats,
                        auxiliary_points=o.candidate.project.route.auxiliary_points,attempts=len(o.trials),
                        improved=o.improved,independent_pass=bool(o.verification and o.verification.kinematic_pass))
record=dict(software_version=VERSION,kind='theoretical_not_measured',cases=rows,limitations='有限采样理论模型；未进行实车试验，案例不是用户实际项目。')
(directory/'案例结果.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
(examples/'实际理论验证结果.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(rows['辅助点搜索']['status'],flush=True)
