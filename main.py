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
    parser.add_argument("--smoke-project",type=Path,help=argparse.SUPPRESS)
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
                packaged_cases=Path(sys.executable).parent/'可复现理论案例'
                if getattr(sys,'frozen',False):
                    app.load(Project.load(packaged_cases/'正常方案.cam.json'))
                    assert app.collect().to_dict()==demo_project().to_dict()
                    assert VERSION in root.title()
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
                problem_project=Project.load(packaged_cases/'问题方案.cam.json') if getattr(sys,'frozen',False) else demo_project(True)
                problem=calculate(problem_project); assert not problem.valid
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
                app.tree.selection_set("1","3"); app.waypoints.refresh_selection(); app.waypoints.delete()
                assert app.route_snapshot()["points"]==[p for i,p in enumerate(snapshot["points"]) if i not in (1,3)]
                assert app.result is None and str(app.export_button["state"])=="disabled"
                app.undo_route(); assert app.route_snapshot()==snapshot
                app.waypoints.select_all(); app.waypoints.delete(); assert not app.tree.get_children()
                app.undo_route(); assert app.route_snapshot()==snapshot
                editor=app.waypoints
                editor.begin_edit(1,0); editor.draft_text.set(str(snapshot["points"][1][0]+2))
                assert editor.commit() and app.route_snapshot()["points"][1][0]==snapshot["points"][1][0]+2
                app.undo_route(); assert app.route_snapshot()==snapshot
                revision=app.revision
                editor.begin_edit(1,1); editor.draft_text.set("NaN")
                assert not editor.commit() and app.route_snapshot()==snapshot and app.revision==revision
                editor.cancel_edit()
                editor.new_x.set("4700"); editor.new_y.set("800"); editor.add()
                assert app.route_snapshot()["points"]==snapshot["points"]+[[4700.,800.]]
                app.undo_route(); assert app.route_snapshot()==snapshot
                app.show_result(original)
                from camtool.editor_smoke import check_point_editors
                editor_checks=check_point_editors(app,directory/"双列表编辑验证")
                from camtool.view_smoke import check_views
                view_checks=check_views(app,directory/"视图与拖动验证")
                # Exercise the actual auxiliary widgets + background search in
                # the frozen EXE, including final exported-DXF independence.
                import time
                aux=app.auxiliary
                aux.settings["evaluations"].set("4")
                aux.settings["seconds"].set("10")
                aux.start()
                deadline=time.monotonic()+90
                while aux.busy:
                    root.update(); time.sleep(.01)
                    if time.monotonic()>deadline: raise AssertionError("辅助搜索超时")
                assert aux.outcome is not None,aux.summary.get()
                assert aux.outcome.verification is not None
                assert aux.outcome.verification.kinematic_pass,aux.outcome.status
                assert len(aux.outcome.verification.path)>10
                assert aux.outcome.candidate.project.route.waypoints==original.project.route.waypoints
                aux.outcome.export(directory/"辅助搜索与最终DXF检验")
                searched=aux.outcome
                assert app.collect().to_dict()==searched.original_project.to_dict()
                app.waypoints.begin_edit(1,0);app.waypoints.draft_text.set("尚未提交")
                page.adopt(True)
                assert app.waypoints.draft_text.get()=="尚未提交" and app.collect().to_dict()==searched.original_project.to_dict()
                app.waypoints.cancel_edit()
                page.adopt(True)
                assert app.collect().to_dict()==searched.candidate.project.to_dict()
                assert aux.editor.document.controls()==searched.candidate.project.route.auxiliary_points
                page.adopt(False)
                assert app.collect().to_dict()==searched.original_project.to_dict()
                user_summary=None;user_editor_compatibility=None;user_search_editor=None
                if args.smoke_project is not None:
                    from camtool.project_validation import validate_project_file
                    checked=validate_project_file(args.smoke_project,directory/"初试项目验证")
                    app.load(checked.result.project);app.show_result(checked.result)
                    app.views.select(4);app.report_tabs.select(1);root.update_idletasks()
                    assert len(app.checkpoint_tree.get_children())==len(checked.result.project.route.waypoints)
                    assert checked.summary["nominal_pass"],checked.summary
                    assert checked.summary["parameters_unchanged"]
                    aux.settings["evaluations"].set("12");aux.settings["seconds"].set("60")
                    aux.start();deadline=time.monotonic()+120
                    while aux.busy:
                        root.update();time.sleep(.01)
                        if time.monotonic()>deadline:raise AssertionError("初试候选编辑验证超时")
                    user_outcome=aux.outcome
                    assert user_outcome is not None,aux.summary.get()
                    assert user_outcome.candidate.project.route.auxiliary_points,"未产生含辅助点的真实候选"
                    assert app.collect().to_dict()==checked.result.project.to_dict()
                    user_outcome.export(directory/"初试项目验证"/"有限辅助搜索_仅验证采用与恢复")
                    page.adopt(True)
                    assert aux.editor.document.controls()==user_outcome.candidate.project.route.auxiliary_points
                    assert app.collect().route.waypoints==checked.result.project.route.waypoints
                    page.adopt(False)
                    assert app.collect().to_dict()==checked.result.project.to_dict()
                    user_search_editor={"explicit_adopt_restore":True,"automatic_current_input_replacement":False,
                        "controls":user_outcome.candidate.project.route.auxiliary_points,
                        "status":user_outcome.status,"candidate_main_pass":user_outcome.candidate.valid,
                        "candidate_independent_pass":None if user_outcome.verification is None else user_outcome.verification.kinematic_pass}
                    old_required=app.route_snapshot()
                    aux.editor.new_x.set("900");aux.editor.new_y.set("1000");aux.editor.new_segment.set("2")
                    aux.editor.add_button.invoke();root.update()
                    assert app.route_snapshot()==old_required
                    app.file=directory/"初试项目验证"/"初试_辅助编辑兼容副本.cam.json"
                    assert app.save()
                    reopened_user=Project.load(app.file)
                    assert reopened_user.route.auxiliary_points==[[1.,900.,1000.]]
                    assert reopened_user.route.waypoints==checked.result.project.route.waypoints
                    app.load(reopened_user)
                    assert aux.editor.document.controls()==reopened_user.route.auxiliary_points
                    app.load(checked.result.project);app.show_result(checked.result)
                    user_editor_compatibility=True
                    user_summary=checked.summary
                from camtool.improvement_smoke import check_improvement
                synthetic_ok=page.track.metadata['kind']=='synthetic'
                improvement_checks=check_improvement(app,directory/'设计改进助手验收')
                directory.joinpath("smoke.json").write_text(json.dumps({"ok": True, "valid": original.valid,
                    "project_roundtrip": True, "dxf_audit": True, "frozen": getattr(sys, "frozen", False),
                    "executable": sys.executable, "software_version": VERSION, "independent_verification": independent.kinematic_pass,
                    "independent_path_points": len(independent.path), "verification_report_export": True,
                    "synchronized_demo": True,"problem_location": True,"optimization_comparison": True,
                    "synthetic_csv_distinguished": synthetic_ok,
                    "fixed_dxf_prediction": True,"analysis_export": analysis.is_file(),
                    "bulk_waypoint_delete_undo": True,"single_navigation": True,
                    "inline_waypoint_edit": True,"isolated_waypoint_add": True,"waypoint_validation": True,
                    "auxiliary_background_search": True,"auxiliary_final_dxf_verification": True,
                    "auxiliary_candidate_explicit_adoption": True,
                    "dual_point_editor": editor_checks,
                    "figure_views": view_checks,
                    "design_improvement_assistant": improvement_checks,
                    "user_auxiliary_editor_compatibility": user_editor_compatibility,
                    "user_search_candidate_editor": user_search_editor,
                    "user_project_regression": user_summary, "stats": original.stats}, ensure_ascii=False), encoding="utf-8")
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
