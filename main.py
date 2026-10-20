from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from pathlib import Path

from camtool import Project, calculate, export_design
from camtool.ui import App, prepare_display
from camtool.verification import VerificationInput, verify_dxf, export_verification
from camtool.version import VERSION
from camtool.workbench import (TRACK_TEMPLATE, demo_project, index_at_time, perturbation_analysis,
                              read_measured_track, time_axis)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    prepare_display()
    root = tk.Tk()
    app = App(root)
    smoke_failed: list[str] = []
    if args.smoke_dir:
        def smoke() -> None:
            directory = args.smoke_dir
            directory.mkdir(parents=True, exist_ok=True)
            try:
                app.show_result(calculate(app.collect()))
                root.update_idletasks()
                app.render()
                assert app.result is not None
                files = export_design(app.result, directory, overwrite=True)
                reopened = Project.load(files["project"])
                assert reopened.to_dict() == app.result.project.to_dict()
                import ezdxf
                drawing = ezdxf.readfile(files["dxf"])
                assert drawing.units == 4 and not drawing.audit().has_errors
                import numpy as np
                q = VerificationInput.from_project(app.result.project, app.result.stats["cam_usage"]*360)
                nominal = np.column_stack([np.degrees(app.result.cam_angle), np.degrees(app.result.cam_delta)])
                independent = verify_dxf(files["dxf"], q, reference=app.result.target_path, nominal=nominal)
                assert independent.kinematic_pass, [(c.name, c.status, c.detail) for c in independent.checks if c.status == "不通过"]
                app.views.select(app.verification_frame)
                app.verifier.file.set(str(files["dxf"]))
                app.verifier.load(q)
                app.verifier.show_result(independent)
                root.update_idletasks()
                app.verifier.render()
                verification_files = export_verification(independent, directory/"二次检验", overwrite=True)
                assert verification_files["report"].is_file()
                # Exercise the newly packaged widgets and real computations as well.
                from unittest.mock import patch
                original=app.result
                page=app.workbench
                app.views.select(page.frames["demo"])
                page.seek(str(time_axis(original)[-1]/2))
                root.update_idletasks(); app.render()
                assert int(app.frame.get())==index_at_time(original,float(page.time.get()))
                assert all(page.canvases[key].find_all() for key in ("demo_route","demo_cam","demo_mechanism"))
                problem=calculate(demo_project(True)); assert not problem.valid
                app.show_result(problem)
                app.views.select(page.frames["issues"])
                issue=next(i for i,d in enumerate(page.diags) if d.name=="前轮转角")
                page.issue_tree.selection_set(str(issue)); page.choose_issue()
                root.update_idletasks(); app.render()
                assert int(app.frame.get())==page.diags[issue].route_index
                app.show_result(original)
                page.set_comparison(original,original)
                assert "未改善" in page.compare_status.get()
                assert page.comparison is not None
                page.comparison.export(directory/"优化对比")
                page.adopt(True); page.adopt(False)
                synthetic=directory/"软件自检构造轨迹_非实测.csv"
                synthetic.write_text(TRACK_TEMPLATE.replace("kind=measured","kind=synthetic").replace(
                    "请填写真实采集设备、日期和记录编号","发布自检构造（非实测）")+''.join(
                        f"{i},{point[0]},{point[1]}\n" for i,point in enumerate(original.path)),encoding="utf-8-sig")
                page.track,page.measured_rows=page.measure_data(read_measured_track(synthetic),original)
                page.perturbations=perturbation_analysis(original,files["dxf"],0,0,0,0,2,4)
                assert page.perturbations[0]["path"]
                page.analysis_revision=app.revision
                page.fill_analysis_table(); app.views.select(page.frames["analysis"])
                root.update_idletasks(); app.render()
                analysis=directory/"轨迹扰动自检.json"
                with patch("tkinter.filedialog.asksaveasfilename",return_value=str(analysis)): page.export_analysis()
                assert json.loads(analysis.read_text(encoding="utf-8"))["software_version"]==VERSION
                # Exercise bulk editing and the single navigation strip in the packaged EXE.
                assert all(sum(app.navigation.tab(tab,"text")==label for tab in app.navigation.tabs())==1
                           for label in ("同步演示","问题定位","优化对比","实测与扰动"))
                app.navigation.select(0); root.update()
                assert app.views.index(app.views.select())==0
                snapshot=app.route_snapshot()
                app.tree.selection_set("1","3"); app.select_row(None); app.delete_point()
                assert app.route_snapshot()["points"]==[p for i,p in enumerate(snapshot["points"]) if i not in (1,3)]
                assert app.result is None and str(app.export_button["state"])=="disabled"
                app.undo_route(); assert app.route_snapshot()==snapshot
                app.select_all_points(); app.delete_point(); assert not app.tree.get_children()
                app.undo_route(); assert app.route_snapshot()==snapshot
                app.show_result(original)
                directory.joinpath("smoke.json").write_text(json.dumps({"ok": True, "valid": app.result.valid,
                    "project_roundtrip": True, "dxf_audit": True, "frozen": getattr(sys, "frozen", False),
                    "executable": sys.executable, "software_version": VERSION, "independent_verification": independent.kinematic_pass,
                    "independent_path_points": len(independent.path), "verification_report_export": True,
                    "synchronized_demo": True,"problem_location": True,"optimization_comparison": True,
                    "synthetic_csv_distinguished": page.track.metadata['kind']=='synthetic',
                    "fixed_dxf_prediction": True,"analysis_export": analysis.is_file(),
                    "bulk_waypoint_delete_undo": True,"single_navigation": True,
                    "stats": app.result.stats}, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                import traceback
                smoke_failed.append(str(exc))
                directory.joinpath("smoke-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            finally:
                root.after(300, root.destroy)
        root.after(500, smoke)
    root.mainloop()
    if smoke_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
