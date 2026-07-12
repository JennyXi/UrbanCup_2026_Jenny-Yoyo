import sqlite3
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]

def test_required_artifacts_exist():
    for p in [ROOT/"data/processed/shanghai_mobility_evidence.sqlite", ROOT/"data/exports/weather_transport_disruptions.csv", ROOT/"data/exports/elderly_hailing_access.parquet", ROOT/"data/exports/ride_hailing_promotion_rules.csv", ROOT/"data/exports/subdistrict_elder_support_facts.csv", ROOT/"data/exports/shanghai_nine_prototypes.csv"]:
        assert p.exists()

def test_primary_records_have_ids_and_sources():
    for name,idcol in [("weather_transport_disruptions","record_id"),("elderly_hailing_access","service_id"),("ride_hailing_promotion_rules","rule_version_id"),("subdistrict_elder_support_facts","fact_id")]:
        df=pd.read_csv(ROOT/f"data/exports/{name}.csv")
        assert len(df)>0
        assert df[idcol].notna().all() and df[idcol].is_unique
        assert df.primary_source_id.notna().all()

def test_links_and_sources_are_closed():
    src=pd.read_csv(ROOT/"data_sources/source_registry.csv")
    links=pd.read_csv(ROOT/"data/exports/record_source_links.csv")
    assert links.record_id.notna().all()
    assert set(links.source_id).issubset(set(src.source_id))
    assert links.evidence_quote.notna().all()

def test_boolean_tristate_and_no_assumed_facts():
    for name in ["elderly_hailing_access","ride_hailing_promotion_rules"]:
        df=pd.read_csv(ROOT/f"data/exports/{name}.csv")
        for c in [x for x in df.columns if x.startswith(("channel_","weekday_","weekend_","holiday_","auto_","app_","phone_","family_","community_","manual_","cash_")) or x in {"claim_required","real_time_hailing_supported","smartphone_required","mobile_number_required","membership_required","hospital_trip_required","specified_hospital_only","weather_triggered","first_come_first_served","stackable","new_user_only","existing_user_allowed"}]:
            vals=set(df[c].dropna().astype(str).str.lower())
            assert vals.issubset({"true","false","1","0","nan"}) or not vals
    facts=pd.read_csv(ROOT/"data/exports/subdistrict_elder_support_facts.csv")
    assert not (facts.fact_status=="assumed").any()

def test_sqlite_tables_nonempty():
    with sqlite3.connect(ROOT/"data/processed/shanghai_mobility_evidence.sqlite") as con:
        for table in ["weather_transport_disruptions","elderly_hailing_access","ride_hailing_promotion_rules","subdistrict_elder_support_facts"]:
            assert con.execute(f"select count(*) from {table}").fetchone()[0] > 0

def test_failed_http_responses_are_not_snapshots():
    src=pd.read_csv(ROOT/"data_sources/source_registry.csv")
    failed=src[pd.to_numeric(src.http_status_at_collection,errors="coerce").fillna(0).ge(400)]
    assert failed.snapshot_path.isna().all()
    assert failed.page_hash.isna().all()

def test_automatic_keyword_candidates_require_review():
    links=pd.read_csv(ROOT/"data/exports/record_source_links.csv")
    low=links[links.extraction_confidence.eq("low")]
    assert len(low)==0
    rejected=pd.read_csv(ROOT/"data/manual_review/rejected_candidates.csv")
    assert len(rejected)>0
    assert rejected.review_status.eq("rejected").all()

def test_search_log_contains_auditable_results_for_all_districts_and_datasets():
    log=pd.read_csv(ROOT/"data_sources/search_log.csv",low_memory=False)
    assert log.district.nunique()==16
    assert set(log.dataset_name)=={"weather_transport_disruptions","elderly_hailing_access","ride_hailing_promotion_rules","subdistrict_elder_support_facts"}
    assert log.result_url.notna().all() and log.result_title.notna().all()
    assert log.result_page.notna().all() and log.result_rank.notna().all()
    assert log.query_text.nunique()>100

def test_near_miss_dataset_audit_is_populated():
    near=pd.read_csv(ROOT/"data_sources/near_miss_public_datasets.csv")
    assert len(near)>=4
    for c in ["url","fields_available","fields_missing","why_not_equivalent","allowed_auxiliary_use"]:
        assert near[c].notna().all()

def test_every_low_confidence_record_is_in_manual_review_queue():
    for name in ["weather_transport_disruptions","elderly_hailing_access","ride_hailing_promotion_rules","subdistrict_elder_support_facts"]:
        exported=pd.read_csv(ROOT/f"data/exports/{name}.csv")
        queued=pd.read_csv(ROOT/f"data/manual_review/{name}_review_queue.csv")
        assert len(queued)==int(exported.record_confidence.eq("low").sum())
        assert queued.review_status.eq("needs_manual_review").all()

def test_manual_review_decisions_are_reflected_in_source_links():
    decisions=pd.read_csv(ROOT/"data/manual_review/review_decisions.csv")
    links=pd.read_csv(ROOT/"data/exports/record_source_links.csv")
    assert len(decisions)>=10
    checked=links[links.record_id.isin(decisions.record_id)]
    assert len(checked)==len(decisions)
    assert checked.review_status.eq("manually_verified").all()

def test_acceptance_audit_matches_core_exports():
    import json
    audit=json.loads((ROOT/"outputs/reports/acceptance_audit.json").read_text(encoding="utf-8"))
    for name,metrics in audit["core_datasets"].items():
        df=pd.read_csv(ROOT/f"data/exports/{name}.csv")
        assert metrics["records"]==len(df)
        assert sum(metrics["source_tier_distribution"].values())==len(df)
        assert not df.record_confidence.eq("low").any()
    assert audit["rejected_candidates"]==len(pd.read_csv(ROOT/"data/manual_review/rejected_candidates.csv"))

def test_module_entrypoint_is_importable():
    from src.data_pipeline.build_all import main
    assert callable(main)

def test_required_common_tables_and_coverage_figures_exist():
    paths=[
      ROOT/"data/exports/source_registry.csv",ROOT/"data/exports/source_registry.parquet",
      ROOT/"data_sources/query_templates.csv",ROOT/"data_sources/blocked_sources.csv",ROOT/"data_sources/record_source_links.csv",
      ROOT/"outputs/figures/source_tier_distribution.png",ROOT/"outputs/figures/dataset_missing_rate.png",
      ROOT/"outputs/figures/record_confidence_distribution.png",ROOT/"outputs/figures/promotion_rule_field_coverage.png",
      ROOT/"outputs/figures/subdistrict_support_coverage.png",
    ]
    assert all(p.exists() and p.stat().st_size>0 for p in paths)
    templates=pd.read_csv(ROOT/"data_sources/query_templates.csv")
    assert templates.groupby("dataset_name").search_round.nunique().eq(3).all()

def test_three_search_waves_show_nonincreasing_new_core_sources():
    sat=pd.read_csv(ROOT/"outputs/coverage/search_saturation.csv")
    assert sat.search_round.tolist()==[1,2,3]
    assert sat.opened_evidence_pages.gt(0).all()
    assert sat.new_core_sources.diff().iloc[1:].le(0).all()

def test_parameter_mapping_has_complete_nonplaceholder_evidence_fields():
    import json
    cfg=json.loads((ROOT/"config/calibration/shanghai_evidence_based.yaml").read_text(encoding="utf-8"))
    required={"parameter_name","scenario","value_low","value_base","value_high","unit","evidence_basis","supporting_record_ids","supporting_source_ids","derivation_method","fact_status","confidence","sensitivity_required","limitations"}
    assert len(cfg["parameters"])>=10
    assert all(required.issubset(p) for p in cfg["parameters"])
    assert not any("src_" in p["supporting_source_ids"] for p in cfg["parameters"])
    assert {"W1","W2","P1","P2","P3","elder_agent"}.issubset({p["scenario"] for p in cfg["parameters"]})

def test_data_dictionary_lists_every_exported_core_column():
    text=(ROOT/"docs/DATA_DICTIONARY.md").read_text(encoding="utf-8")
    for name in ["weather_transport_disruptions","elderly_hailing_access","ride_hailing_promotion_rules","subdistrict_elder_support_facts"]:
        df=pd.read_csv(ROOT/f"data/exports/{name}.csv")
        assert f"## {name}" in text
        assert all(f"`{c}`" in text for c in df.columns)

def test_historical_2020_records_are_preserved_with_specific_evidence():
    weather=pd.read_csv(ROOT/"data/exports/weather_transport_disruptions.csv")
    roads=weather[weather.event_name.eq("2020中心城区道路积水改善工程")]
    assert len(roads)==11
    assert roads.entity_name.nunique()==11
    assert roads.quantitative_value.notna().all()
    support=pd.read_csv(ROOT/"data/exports/subdistrict_elder_support_facts.csv")
    old=support[support.program_name.eq("一键通应急呼叫全覆盖")]
    assert len(old)==3 and old.reference_year.eq(2020).all()
    assert old.primary_source_id.nunique()==1
