"""Run the Markdown stop-condition audit and persist machine-readable evidence."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CORE = ["weather_transport_disruptions", "elderly_hailing_access", "ride_hailing_promotion_rules", "subdistrict_elder_support_facts"]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")


def main() -> int:
    build = run([sys.executable, "scripts/build_all_databases.py"])
    tests = run([sys.executable, "-m", "pytest", "-q"])
    log = pd.read_csv(ROOT / "data_sources/search_log.csv", low_memory=False)
    links = pd.read_csv(ROOT / "data/exports/record_source_links.csv")
    near = pd.read_csv(ROOT / "data_sources/near_miss_public_datasets.csv")
    sat = pd.read_csv(ROOT / "outputs/coverage/search_saturation.csv")
    protos = pd.read_csv(ROOT / "data/exports/shanghai_nine_prototypes.csv")
    with sqlite3.connect(ROOT / "data/processed/shanghai_mobility_evidence.sqlite") as con:
        counts = {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in CORE}
    export_ok = all((ROOT/f"data/exports/{n}.csv").exists() and (ROOT/f"data/exports/{n}.parquet").exists() for n in CORE)
    docs = ["DATA_DICTIONARY.md","SEARCH_METHODOLOGY.md","DATABASE_ABSENCE_SEARCH.md","DATA_QUALITY_REPORT.md","MODEL_PARAMETER_MAPPING.md","SOURCE_LIMITATIONS.md","REPRODUCTION.md"]
    core_ids = set()
    idcols = {"weather_transport_disruptions":"record_id","elderly_hailing_access":"service_id","ride_hailing_promotion_rules":"rule_version_id","subdistrict_elder_support_facts":"fact_id"}
    for n,c in idcols.items(): core_ids.update(pd.read_csv(ROOT/f"data/exports/{n}.csv")[c].astype(str))
    checks = [
      ("1_four_nonempty_databases", all(v>0 for v in counts.values()), counts),
      ("2_all_16_districts_searched", log.district.nunique()==16 and log.result_url.notna().all(), {"districts":int(log.district.nunique()),"results":len(log)}),
      ("3_three_search_rounds", sat.search_round.tolist()==[1,2,3] and sat.opened_evidence_pages.gt(0).all(), sat.to_dict("records")),
      ("4_absence_search_completed", len(near)>=4 and (ROOT/"docs/DATABASE_ABSENCE_SEARCH.md").exists(), {"near_misses":len(near)}),
      ("5_marginal_additions_declined", sat.new_core_sources.diff().iloc[1:].le(0).all(), sat.new_core_sources.tolist()),
      ("6_all_records_have_source_links", core_ids.issubset(set(links.record_id.astype(str))) and links.source_id.notna().all(), {"core_records":len(core_ids),"linked_records":int(links.record_id.nunique())}),
      ("7_automated_tests_pass", tests.returncode==0, tests.stdout.strip()),
      ("8_sqlite_csv_parquet_generated", build.returncode==0 and export_ok, {"build_returncode":build.returncode,"exports":export_ok}),
      ("9_required_documents_generated", all((ROOT/"docs"/d).exists() for d in docs), docs),
      ("10_nine_real_subdistrict_prototypes", len(protos)==9 and protos.subdistrict.notna().all(), protos[["district","subdistrict"]].to_dict("records")),
      ("11_existing_project_tests_still_pass", tests.returncode==0, tests.stdout.strip()),
      ("12_one_command_rebuild", build.returncode==0, "python scripts/build_all_databases.py"),
    ]
    result={"generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),"all_achieved":all(x[1] for x in checks),"checks":[{"requirement":n,"achieved":bool(ok),"evidence":ev} for n,ok,ev in checks]}
    out=ROOT/"outputs/reports"; out.mkdir(parents=True,exist_ok=True)
    (out/"requirements_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# Markdown停止条件审计","",f"全部满足：{result['all_achieved']}","","| 条件 | 状态 |","|---|---|"]+[f"| {x['requirement']} | {'通过' if x['achieved'] else '未通过'} |" for x in result["checks"]]
    (out/"requirements_audit.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result["all_achieved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
