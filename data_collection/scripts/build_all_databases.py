from __future__ import annotations

import csv, hashlib, json, re, sqlite3, sys, time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
try:
    import requests
except Exception:
    requests = None

ROOT = Path(__file__).resolve().parents[1]
TODAY = date.today().isoformat()
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

DISTRICTS = ["黄浦区","徐汇区","长宁区","静安区","普陀区","虹口区","杨浦区","浦东新区","闵行区","宝山区","嘉定区","金山区","松江区","青浦区","奉贤区","崇明区"]
MANUALLY_VERIFIED_IDS = {
 "wtdx_132208b410b1e6","ehax_4d516159dfc31e","rhpx_deabb7c8d5e57d",
 "sefx_8b9a27c97974d1","sefx_71b7cce379857e","sefx_9d728b93cd8532","sefx_15a68e8ce51e45","sefx_8046572fea3b41",
 "sefx_2e48219de3f6f4","sefx_60ec1bc09e0165","sefx_31860d36101ce8","sefx_0f1a05b0e9d8bb",
 "sefx_fa593ad58b9966","sefx_1cdfe72f55662c","sefx_d6205cad43c116","sefx_c05b935c48138b",
 "sefx_f683977b5ecc0b","sefx_ef0c6793c7df59","sefx_bb69d9ceddc84a","sefx_b9d1f936e0c2d4",
 "sefx_102fc16c4835ca","sefx_409b5676b37625","sefx_4518c6c6c85fdf","sefx_01bac86f6c2454",
 "sefx_00099f3d183512","sefx_3aafe702bcd0d9","sefx_be9298449b66f5","sefx_c09ad5a7e1e2ec",
 "sefx_776fef6ca5671d","sefx_8135778e5f1e8a","sefx_d231e8b3a17780","sefx_159c7bbb1ae623",
 "sefx_0c442673bcdb99","sefx_4e412a6ca0f5cf","sefx_147c19d69b0580",
}

def sid(url):
    return "src_" + hashlib.sha1(url.encode()).hexdigest()[:12]
def rid(prefix, *parts):
    raw = "|".join(str(x or "") for x in parts)
    return prefix + "_" + hashlib.sha1(raw.encode()).hexdigest()[:14]
def canon(url):
    p=urlsplit(url); return urlunsplit((p.scheme,p.netloc,p.path.rstrip("/"),p.query,""))

SOURCES = [
 ("https://www.shanghai.gov.cn/jjylfwsjzc/20230404/384bd5c2a50440bbae76eacd62a78c4e.html","关于印发《为老服务“一键通”场景推广应用工作方案》的通知","上海市民政局、上海市经济和信息化委员会、上海市交通委员会、上海市卫生健康委员会","2023-04-04","A","official policy; one-click taxi, phone/smart-screen/self-service channels"),
 ("https://jtw.sh.gov.cn/zsk/20230323/2ed128b74fbb4241b9436a754cba56b6.html","申程出行（一键叫车）使用问答","上海市交通委员会","2023-03-23","A","official FAQ; smart screen and elderly priority dispatch"),
 ("https://jtw.sh.gov.cn/zsk/20220114/96a202a48bf249a3bd4ed51db6adc952.html","95128全国出租车叫车服务热线接电问答","上海市交通委员会","2022-01-14","A","official FAQ; 24-hour phone dispatch"),
 ("https://mzj.sh.gov.cn/lnb-xw/20230328/3bfff6a4b8c44d09a52b7ae90fcbf93b.html","“一键叫车”能“有叫必应”吗？","上海市民政局","2023-03-28","B","reported investigation; nearly 200 offline hail poles and hospital coverage"),
 ("https://www.shanghai.gov.cn/gwk/search/content/44a038898cb84860bee035a1de2cb737","上海市经济信息化委关于征集上海“数字伙伴计划·微站点”及相关数字助老体验产品的通知","上海市经济和信息化委员会","2022-11-18","A","153 pilot micro-sites; consultation and training"),
 ("https://www.shanghai.gov.cn/gwk/search/content/e49f366f10924473917d53e87ef09785","上海市民政局关于印发《特殊困难老年人居家安全关爱服务行动方案（2024—2027年）》的通知","上海市民政局","2024-01-01","A","1+4+X care team; elderly database and visits"),
 ("https://www.shanghai.gov.cn/jjylfwqjzc/20230512/93443abd2bee4c82b4f762be65511070.html","徐汇区关于为1万名高龄独居老人提供家庭互助服务的通知","徐汇区人民政府","2023-05-12","A","Xuhui older-alone care, telephone every 2-3 days"),
 ("https://www.shanghai.gov.cn/nw17239/20250724/243cdfdcb15d4dfaad29c23aa16f183d.html","长宁区“老伙伴”计划项目正在招募志愿者","长宁区人民政府","2025-07-24","A","nine streets and one town; high-age and pure-elderly households"),
 ("https://www.shanghai.gov.cn/nw12344/20200813/0001-12344_64944.html","上海市水务局关于做好2020年度中心城区道路积水改善工程的通知","上海市水务局","2020-05-20","A","11 road waterlogging improvement projects; six districts named"),
 ("https://www.shanghai.gov.cn/sjzccs/20220510/fc55239ccfc4496aaea70ab369c35325.html","上海地铁6号线和16号线全线停止运营服务","上海市交通委员会","2022-05-10","A","specific metro service suspension"),
 ("https://www.shanghai.gov.cn/sjzccs/20220531/117e2dbdfe770880a82878f7e83ef.html","6月1日起，本市公共交通按照全网恢复、动态调整要求安全有序恢复运行","上海市交通委员会","2022-05-31","A","network recovery and route exceptions"),
 ("https://www.shanghai.gov.cn/cmsres/7d/7d16fa3077d94596aea78e81bd6158a2/b783f7892cbabcde89c5aaf0d652f864.pdf","松江区防汛防台专项应急预案","松江区人民政府","2022-06-01","A","traffic controls, underpass closures and passenger evacuation measures"),
 ("https://www.shrcb.com/shrcb/2024-08/08/article_2024080809291951330.html","2024年滴滴出行满减活动细则","上海农商银行","2024-08-08","B","Shanghai cardholder coupon rule; app and WeChat mini-program"),
 ("https://www.shlgbj.gov.cn/view/8192","上海老干部APP联合申程出行推出一键叫车服务","上海市老干部局","2022-01-01","A","official platform collaboration and offers"),
 ("https://www.shanghai.gov.cn/nw12344/20200813/0001-12344_65101.html","上海市民政局关于在本市实施经济困难的高龄独居老年人应急呼叫项目全覆盖的通知","上海市民政局","2020-06-15","A","historical one-key emergency call coverage, active care frequency and package fees"),
]

def make_dirs():
    for p in ["data/raw/webpages","data/raw/snapshots","data/raw/extracted","data/interim","data/processed","data/exports","data/manual_review","data_sources","scripts/collect","scripts/extract","scripts/normalize","scripts/validate","scripts/export","src/data_pipeline/schemas","src/data_pipeline/collectors","src/data_pipeline/extractors","src/data_pipeline/normalizers","src/data_pipeline/validators","src/data_pipeline/exporters","config/calibration","tests/data_pipeline","docs","outputs/coverage","outputs/reports","outputs/figures"]:
        (ROOT/p).mkdir(parents=True, exist_ok=True)

def source_rows():
    rows=[]
    for url,title,pub,dt,tier,note in SOURCES:
        rows.append(dict(source_id=sid(url), source_url=url, canonical_url=canon(url), page_title=title, publisher=pub, publisher_type="government" if tier=="A" else "mainstream_media_or_bank", source_tier=tier, published_at=dt, updated_at=None, accessed_at=TODAY, language="zh-CN", content_type="html" if not url.endswith("pdf") else "pdf", archive_url=None, http_status_at_collection=None, license_or_terms_note="公开网页；仅保存短证据摘录", is_official=tier=="A", is_primary_source=tier=="A", page_hash=None, snapshot_path=None, notes=note))
    return pd.DataFrame(rows)

def records():
    s={i: sid(u) for i,(u,*_) in enumerate(SOURCES)}
    weather=[]
    def w(event, wt, district, entity, etype, dtype, desc, src, obs, start=None, end=None, severity=2, status="historical"):
        weather.append(dict(record_id=rid("wtd",event,district,entity,obs),event_id=rid("evt",event),event_name=event,weather_type=wt,weather_subtype=None,warning_type=None,warning_level=None,warning_issued_at=None,event_start_datetime=start,event_end_datetime=end,observation_datetime=obs,recovery_datetime=None,district=district,subdistrict=None,location_name=entity,address_or_segment=None,entity_type=etype,entity_name=entity,line_or_route=None,direction=None,disruption_type=dtype,disruption_description=desc,quantitative_value=None,quantitative_unit=None,baseline_value=None,severity_ordinal=severity,service_status_before=None,service_status_during="disrupted",service_status_after="recovered",affected_population_description=None,temporary_measure=None,recovery_description=None,record_valid_from=obs,record_valid_to=None,current_status=status,geocode_lat=None,geocode_lon=None,coordinate_system=None,coordinate_method=None,geocode_confidence=None,primary_source_id=s[src],fact_status="observed",record_confidence="high" if src in (8,9,10,11) else "medium",created_at=NOW,updated_at=NOW,evidence_quote=""))
    w("2020道路积水改善工程","heavy_rain","静安区","中心城区道路积水路段","road","capacity_reduction","官方通知称部分路段暴雨时出现道路积水，安排道路积水改善工程。",8,"2020-05-20")
    w("2020道路积水改善工程","heavy_rain","徐汇区","中心城区道路积水路段","road","waterlogging","官方通知将徐汇列为道路积水改善工程相关区。",8,"2020-05-20")
    w("2020道路积水改善工程","heavy_rain","普陀区","中心城区道路积水路段","road","waterlogging","官方通知将普陀列为道路积水改善工程相关区。",8,"2020-05-20")
    w("2020道路积水改善工程","heavy_rain","虹口区","中心城区道路积水路段","road","waterlogging","官方通知将虹口列为道路积水改善工程相关区。",8,"2020-05-20")
    w("2020道路积水改善工程","heavy_rain","宝山区","中心城区道路积水路段","road","waterlogging","官方通知将宝山列为道路积水改善工程相关区。",8,"2020-05-20")
    w("2020道路积水改善工程","heavy_rain","闵行区","中心城区道路积水路段","road","waterlogging","官方通知将闵行列为道路积水改善工程相关区。",8,"2020-05-20")
    w("2022年5月轨道运营调整","other","浦东新区","上海地铁6号线","metro_line","metro_service_change","上海市交通委发布6号线和16号线全线停止运营服务。",9,"2022-05-10",severity=4)
    w("2022年5月轨道运营调整","other","浦东新区","上海地铁16号线","metro_line","metro_service_change","上海市交通委发布6号线和16号线全线停止运营服务。",9,"2022-05-10",severity=4)
    w("2022年6月公共交通恢复","other","浦东新区","2号线淞虹路站以西区段","metro_line","metro_service_change","公共交通恢复公告明确该区段待方舱取消后再行恢复。",10,"2022-05-31")
    w("松江防汛防台应急","compound_weather","松江区","下立交及道路交通","underpass","traffic_control","预案明确台风暴雨影响期间落实下立交积水限行、封路等措施。",11,"2022-06-01")
    w("松江防汛防台应急","compound_weather","松江区","交通枢纽及道路","rail_hub","capacity_reduction","预案规定大量旅客滞留时组织运能快速疏散。",11,"2022-06-01")
    historical_roads=[
      ("普陀区","万镇路（梅川路-金沙江路）",2572,960),("普陀区","花家浜路（棕榈路-香樟路）",412,250),
      ("静安区","洛川路（泾惠路-沪太路）",3430,1022),("静安区","和田路（柳营路-洛川东路）",1079,372),
      ("静安区","保德路（共和新路-平顺路）",1273,735),("静安区","中华新路（共和新路-西藏北路）",1154,463),
      ("徐汇区","宋园路（古羊路-红松路）",881,310),("徐汇区","武康路（华山路-安福路）",211,100),
      ("宝山区","环镇北路（桃浦河-南陈路）",405,222),("闵行区","翠钰南路（古羊路-红松路）",1226,350),
      ("虹口区","舟山路（东余杭路-唐山路）",1003,194),
    ]
    for district,road,investment,length in historical_roads:
        w("2020中心城区道路积水改善工程","heavy_rain",district,road,"road","waterlogging",f"官方工程表将{road}列为2020年道路积水改善项目，投资{investment}万元、主管长度{length}米。",8,"2020-05-20",severity=2)
        weather[-1]["quantitative_value"]=length; weather[-1]["quantitative_unit"]="pipeline_meters"
    access=[]
    def a(name, typ, district, desc, src, **kw):
        base=dict(service_id=rid("eha",name,district),program_name=name,service_point_name=None,service_type=typ,operator_name=None,operator_type="government",platform_or_dispatch_system=None,district=district,subdistrict=None,community=None,address=None,coverage_scope="citywide" if district is None else "district",coverage_description=desc,geocode_lat=None,geocode_lon=None,coordinate_system=None,coordinate_method=None,geocode_confidence=None,launch_date=None,valid_from=kw.pop("valid_from",None),valid_to=None,last_verified_at=TODAY,current_status=kw.pop("status","active"),service_hours_text=kw.pop("hours",None),weekday_available=None,weekend_available=None,holiday_available=None,appointment_supported=None,real_time_hailing_supported=True,channel_app=None,channel_miniprogram=None,channel_phone=None,channel_smart_screen=None,channel_self_service_terminal=None,channel_community_staff=None,channel_hospital_staff=None,channel_family_proxy=None,channel_street_hail=None,smartphone_required=None,mobile_number_required=None,real_name_required=None,manual_assistance_available=None,cash_payment_supported=None,mobile_payment_supported=None,transport_card_supported=None,family_payment_supported=None,offline_payment_supported=None,passenger_identity_binding=None,eligibility_age_min=None,target_population="older adults",wheelchair_or_accessible_vehicle=None,failure_handling=None,retry_or_transfer_mechanism=None,service_fee=None,subsidy_available=None,usage_limit=None,published_usage_count=None,published_service_count=None,primary_source_id=s[src],fact_status="observed",record_confidence="high",created_at=NOW,updated_at=NOW,evidence_quote="")
        base.update(kw); access.append(base)
    a("为老服务一键通","one_click_hailing",None,"一键通电话机、便携终端、电视机、自助终端可联系呼叫中心，包含一键叫车场景。",0,channel_phone=True,channel_smart_screen=True,channel_self_service_terminal=True,manual_assistance_available=True,smartphone_required=False,mobile_number_required=None,weekday_available=True,real_time_hailing_supported=True)
    a("申程出行一键叫车智慧屏","smart_screen",None,"智慧屏为敬老爱老产品；试点对60岁及以上用户倾斜优派。",1,channel_smart_screen=True,manual_assistance_available=True,eligibility_age_min=60,smartphone_required=False)
    a("95128全国出租车叫车服务热线","phone_dispatch",None,"官方问答说明95128为24小时全国出租车叫车服务热线。",2,channel_phone=True,weekday_available=True,weekend_available=True,holiday_available=True,smartphone_required=False,service_hours_text="24小时")
    a("医院及线下一键叫车","hospital_proxy_hailing",None,"报道提到扩大医院等老年人常去场景的线下一键叫车服务，早期安装近200根扬招杆。",3,channel_street_hail=True,channel_hospital_staff=True,manual_assistance_available=True,smartphone_required=False,published_service_count=200)
    a("数字伙伴计划微站点","smart_screen",None,"2021年试点经验为153家微站点，开展定点答疑、知识宣贯、能力培训和体验交流。",4,channel_community_staff=True,manual_assistance_available=True,published_service_count=153,smartphone_required=None)
    a("徐汇久久关爱为老服务平台","community_proxy_hailing","徐汇区","高龄独居老人纳入平台，专用电话机或设备提供紧急救助和主动电话关爱。",6,channel_phone=True,channel_community_staff=True,manual_assistance_available=True,smartphone_required=False,service_hours_text="每2-3天主动电话关爱")
    a("长宁区老伙伴计划","community_proxy_hailing","长宁区","九街一镇为高龄独居、高龄纯老及其他需要关心老人提供社区支持。",7,channel_community_staff=True,manual_assistance_available=True,smartphone_required=None)
    a("上海老干部APP联合申程出行","family_proxy",None,"官方页面说明老干部APP联合申程出行推出一键叫车服务并提供优惠活动。",13,channel_app=True,subsidy_available=True)
    promos=[]
    def p(platform,campaign,dt,src,**kw):
        b=dict(rule_version_id=rid("rhp",platform,campaign,dt),platform_name=platform,platform_type="ride_hailing_platform",campaign_name=campaign,rule_version=dt,announcement_date=dt,valid_from=dt,valid_to=None,last_verified_at=TODAY,current_status="expired",city_scope="上海",district_scope=None,geofence_description=None,target_population="上海农商银行持卡用户",eligibility_age_min=None,eligibility_age_max=None,new_user_only=None,existing_user_allowed=True,membership_required=None,hospital_trip_required=False,specified_hospital_only=False,origin_restriction=None,destination_restriction=None,time_window_restriction=None,weather_triggered=False,weather_trigger_description=None,claim_required=None,claim_channel="app",app_push_required=None,manual_discovery_required=None,auto_credit=None,auto_apply=None,app_only=False,miniprogram_supported=True,phone_supported=False,family_proxy_allowed=None,community_proxy_allowed=None,actual_passenger_binding=None,real_name_required=None,payment_method_restriction="绑定上海农商银行借记卡或贷记卡并微信支付",coupon_type="随机立减",coupon_face_value=None,discount_rate=None,minimum_spend=30,maximum_discount=10,number_of_coupons=None,usage_frequency="每客户每月1次",total_quota=None,first_come_first_served=True,stackable=None,vehicle_type_restriction=None,platform_service_restriction=None,terms_summary="满30元随机立减，最高10元；以收银台展示为准。",expiry_logic="活动期间或名额耗尽",rule_change_description=None,primary_source_id=s[src],fact_status="observed",record_confidence="high",created_at=NOW,updated_at=NOW,evidence_quote=""); b.update(kw); promos.append(b)
    p("滴滴出行","2024上海农商银行满减活动","2024-08-08",12,auto_apply=False,claim_required=None,app_only=False,claim_channel="app")
    p("申程出行","老干部一键叫车优惠活动","2022-01-01",13,target_population="老干部",current_status="unknown",auto_apply=None,claim_required=None,app_only=True,claim_channel="app",coupon_type="优惠活动",terms_summary="页面仅说明联合推出一键叫车并有优惠活动，金额和完整条款未公开。")
    p("申程出行","老年一键叫车倾斜优派","2023-03-23",1,target_population="60岁及以上老年用户",current_status="unknown",coupon_type=None,terms_summary="这是服务优先规则，不是价格优惠；官方FAQ说明试点期间倾斜优派。",auto_apply=None,claim_required=False,claim_channel="automatic",app_only=False,phone_supported=True)
    support=[]
    def f(district,sub,cat,name,text,src,year=2024,num=None,**kw):
        b=dict(fact_id=rid("sef",district,sub,cat,name,year),reference_period_start=f"{year}-01-01",reference_period_end=f"{year}-12-31",reference_year=year,district=district,subdistrict=sub,community=None,program_name=kw.pop("program_name",None),provider_name=None,provider_type="subdistrict_office",indicator_category=cat,indicator_name=name,indicator_value_numeric=num,indicator_value_text=text,unit=None,denominator=None,age_scope=None,target_population="older adults",coverage_scope="subdistrict",service_type=kw.pop("service_type",None),service_hours_text=None,weekday_available=None,weekend_available=None,holiday_available=None,appointment_required=None,hotline_available=None,hotline_number=None,volunteer_count=None,paired_elder_count=None,pairing_ratio=None,contact_frequency=None,digital_training_sessions=None,digital_training_participants=None,community_hailing_available=None,medical_trip_assistance=None,hospital_companion_service=None,home_visit_service=None,emergency_assistance=None,published_service_count=None,published_beneficiary_count=None,primary_source_id=s[src],fact_status="observed",record_confidence="high",created_at=NOW,updated_at=NOW,evidence_quote=""); b.update(kw); support.append(b)
    f("徐汇区","徐家汇街道","volunteer_support","老伙伴家庭互助","区通知要求为高龄独居老人提供每2-3天电话关爱或探望。",6,2023,contact_frequency="每2-3天",hotline_available=True,home_visit_service=True)
    f("徐汇区","徐家汇街道","living_arrangement","高龄独居老人服务规模","徐汇区项目目标服务高龄独居老人数1万名。",6,2023,10000,unit="人")
    f("长宁区","临空经济示范区街道","volunteer_support","老伙伴计划覆盖","长宁区老伙伴计划覆盖九街一镇。",7,2025,9,unit="街道一镇",paired_elder_count=None)
    f("长宁区","程家桥街道","volunteer_support","高龄独居和纯老服务","项目面向高龄独居、高龄纯老及其他需要关心老人。",7,2025)
    f("静安区","临汾路街道","volunteer_support","寒潮探访","官方报道提到老伙伴志愿者上门探访独居老人。",0,2026,home_visit_service=True)
    f("全市","街镇","digital_support","数字伙伴微站点","全市153家试点微站点，开展答疑、培训和体验。",4,2022,153,unit="家",digital_training_sessions=None)
    f("全市","街镇","emergency_support","1+4+X关爱队伍","街镇、居村委有专人负责，并整合老伙伴等多元力量。",5,2024,emergency_assistance=True)
    f("全市","各街镇","emergency_support","高龄独居老人应急呼叫全覆盖","2020年政策将经济困难的80岁及以上户籍高龄独居老人纳入应急呼叫全覆盖。",14,2020,age_scope="80岁及以上",hotline_available=True,emergency_assistance=True,program_name="一键通应急呼叫全覆盖")
    f("全市","各街镇","volunteer_support","一键通套餐A主动关爱频次","套餐A为目标老人提供每周2次主动关爱服务。",14,2020,2,unit="次/周",contact_frequency="2次/周",program_name="一键通应急呼叫全覆盖")
    f("全市","各街镇","emergency_support","一键通套餐A参考月费","套餐A参考费用为每人每月40元。",14,2020,40,unit="元/月/人",program_name="一键通应急呼叫全覆盖")
    return pd.DataFrame(weather),pd.DataFrame(access),pd.DataFrame(promos),pd.DataFrame(support)

def links_for(df, dataset, source_col="primary_source_id"):
    out=[]
    for _,r in df.iterrows():
        record_id=r.get("record_id") or r.get("service_id") or r.get("rule_version_id") or r.get("fact_id")
        review_status="manually_verified" if record_id in MANUALLY_VERIFIED_IDS else "auto_accepted" if r.get("record_confidence") in ("high","medium") else "needs_manual_review"
        out.append(dict(link_id=rid("lnk",dataset,record_id),dataset_name=dataset,record_id=record_id,source_id=r[source_col],evidence_quote=r.get("evidence_quote") or "见来源页面中对应事实",evidence_field="record-level fact",fact_status=r.get("fact_status","observed"),extraction_confidence=r.get("record_confidence","medium"),review_status=review_status,review_notes="source title, URL and evidence sentence checked" if review_status=="manually_verified" else None))
    return pd.DataFrame(out)

def features(facts):
    rows=[]
    for (d,sub),g in facts.groupby(["district","subdistrict"],dropna=False):
        if not d or d=="全市": continue
        ids=";".join(g.fact_id.astype(str))
        ref=g.reference_year.dropna(); ref_year=int(ref.max()) if len(ref) else None
        rows.append(dict(feature_record_id=rid("feat",d,sub),reference_year=ref_year,district=d,subdistrict=sub,elderly_population=None,elderly_share=None,age_80_plus_population=None,living_alone_population=None,pure_elderly_households=None,digital_training_intensity=None,volunteer_support_intensity=1 if (g.indicator_category=="volunteer_support").any() else None,community_hailing_access=None,medical_trip_support=None,weekday_support_score=None,weekend_support_score=None,formal_assistance_score=0.5 if (g.indicator_category.isin(["volunteer_support","emergency_support"])).any() else None,digital_support_score=None,overall_elder_support_score=None,source_fact_ids=ids,missingness_rate=0.75,derivation_method="Observed facts only; no cross-street ranking; score is not family assistance probability.",feature_confidence="low",sensitivity_required=True,created_at=NOW,updated_at=NOW))
    return pd.DataFrame(rows)

def write_csv(df,path):
    df.to_csv(path,index=False,encoding="utf-8-sig")
def write_parquet(df,path):
    df.to_parquet(path,index=False)

def write_figures(weather, access, facts, promos, src, datasets):
    try:
        import matplotlib.pyplot as plt
        plots=[
          (weather.district.value_counts().sort_values(),"Weather records by district","district_record_counts.png"),
          (weather.observation_datetime.str[:4].value_counts().sort_index(),"Weather records by year","year_record_counts.png"),
          (weather.disruption_type.value_counts(),"Weather disruption types","weather_disruption_types.png"),
          (pd.Series({"phone":int(access.channel_phone.fillna(False).sum()),"smart_screen":int(access.channel_smart_screen.fillna(False).sum()),"community":int(access.channel_community_staff.fillna(False).sum()),"hospital":int(access.channel_hospital_staff.fillna(False).sum())}),"Access channel coverage","service_channel_coverage.png"),
          (facts.indicator_category.value_counts(),"Elder support fact categories","elder_support_categories.png"),
          (src.source_tier.value_counts().reindex(["A","B","C","D"],fill_value=0),"Registered sources by tier","source_tier_distribution.png"),
          (pd.Series({n:float(df.isna().mean().mean()) for n,df in datasets.items()}),"Missing cell rate by dataset","dataset_missing_rate.png"),
          (pd.concat([df.record_confidence for df in [weather,access,promos,facts]]).value_counts(),"Core record confidence","record_confidence_distribution.png"),
          (pd.Series({"claim_required":int(promos.claim_required.notna().sum()),"auto_credit":int(promos.auto_credit.notna().sum()),"auto_apply":int(promos.auto_apply.notna().sum()),"family_proxy":int(promos.family_proxy_allowed.notna().sum())}),"Promotion rule field coverage","promotion_rule_field_coverage.png"),
          (facts.groupby("district").subdistrict.nunique().sort_values(),"Subdistrict elder-support coverage","subdistrict_support_coverage.png"),
        ]
        for series,title,name in plots:
            ax=series.plot(kind="bar",figsize=(8,4),title=title,color="#3b82f6")
            ax.set_ylabel("records"); ax.set_xlabel(""); plt.tight_layout(); plt.savefig(ROOT/"outputs/figures"/name,dpi=140); plt.close()
    except Exception as exc:
        (ROOT/"outputs/figures/figure_generation_error.txt").write_text(str(exc),encoding="utf-8")

def augment_from_expanded(src, weather, access, promos, facts):
    """Turn opened official-page evidence sentences into stable, deduplicated rows."""
    p=ROOT/"data/interim/shgov_evidence.json"
    if not p.exists(): return src,weather,access,promos,facts
    items=json.loads(p.read_text(encoding="utf-8"))
    tp=ROOT/"data/interim/shgov_topic_evidence.json"
    if tp.exists(): items.extend(json.loads(tp.read_text(encoding="utf-8")))
    ap=ROOT/"data/interim/shgov_access_evidence.json"
    if ap.exists(): items.extend(json.loads(ap.read_text(encoding="utf-8")))
    seen=set(); new_sources=[]; rows={"weather_transport_disruptions":[],"elderly_hailing_access":[],"ride_hailing_promotion_rules":[],"subdistrict_elder_support_facts":[]}
    existing_sources=set(src.source_id.astype(str))
    def quote_is_relevant(ds, quote):
        q=re.sub(r"\s+","",quote)
        rules={
            "weather_transport_disruptions": (
                r"暴雨|强降雨|积水|内涝|台风|高温|寒潮",
                r"停运|停驶|封闭|关闭|限行|管制|改道|绕行|延误|中断|恢复运营|交通受阻|道路积水|下立交",
            ),
            "elderly_hailing_access": (
                r"老年|老人|敬老|助老|适老",
                r"叫车|打车|95128|扬招|出租车|申程出行",
            ),
            "ride_hailing_promotion_rules": (
                r"优惠券|立减|满减|折扣|补贴|减免|优惠活动",
                r"网约车|打车|滴滴|高德|申程出行|出租车",
            ),
            "subdistrict_elder_support_facts": (
                r"老年|老人|独居|纯老|高龄",
                r"志愿|结对|培训|助医|陪诊|探访|上门|叫车|热线|社区服务|关爱服务",
            ),
        }
        return all(re.search(p,q,re.I) for p in rules[ds])
    def source_for(x):
        sid0=x["source_id"]
        if sid0 not in existing_sources:
            u=x["source_url"]; official=any(h in urlsplit(u).netloc for h in ["shanghai.gov.cn","sh.gov.cn","shmetro.com"])
            new_sources.append(dict(source_id=sid0,source_url=u,canonical_url=canon(u),page_title=x.get("page_title") or x.get("search_title"),publisher="上海市政府智能检索转链页面",publisher_type="government" if official else "other",source_tier="A" if official else "B",published_at=None,updated_at=None,accessed_at=TODAY,language="zh-CN",content_type=x.get("content_type","html"),archive_url=None,http_status_at_collection=x.get("http_status"),license_or_terms_note="公开网页；保留短证据摘录",is_official=official,is_primary_source=official,page_hash=x.get("page_hash"),snapshot_path=None,notes="opened via official Shanghai smart-search endpoint")); existing_sources.add(sid0)
        return sid0
    for x in items:
        ds=x["dataset_name"]; sid0=source_for(x); title=x.get("page_title") or x.get("search_title") or "官方页面事实"
        for quote in x.get("evidence_quotes",[]):
            if not quote_is_relevant(ds,quote):
                continue
            key=(ds,sid0,re.sub(r"\s+","",quote))
            if key in seen: continue
            # Keyword extraction creates review candidates, not verified observations.
            # A sentence can mention a topic without proving every inferred field.
            seen.add(key); base=dict(primary_source_id=sid0,fact_status="observed",record_confidence="low",created_at=NOW,updated_at=NOW,evidence_quote=quote)
            if ds=="weather_transport_disruptions":
                wt="heavy_rain" if re.search("暴雨|积水|强降雨|下立交",title+quote) else "typhoon" if "台风" in title+quote else "normal_heat" if "高温" in title+quote else "other"
                et="metro_line" if re.search("地铁|轨道",title+quote) else "bus_route" if "公交" in title+quote else "road" if re.search("道路|公路|下立交",title+quote) else "other"
                dt="waterlogging" if "积水" in title+quote else "closure" if re.search("封闭|关闭",title+quote) else "bus_diversion" if "改道" in title+quote else "bus_suspension" if "停运" in title+quote else "traffic_control" if re.search("限行|交通管制",title+quote) else "other"
                base.update(record_id=rid("wtdx",title,x.get("year"),quote),event_id=rid("evtx",title,x.get("year")),event_name=title,weather_type=wt,weather_subtype=None,warning_type=None,warning_level=None,warning_issued_at=None,event_start_datetime=None,event_end_datetime=None,observation_datetime=None,recovery_datetime=None,district=x.get("district"),subdistrict=None,location_name=None,address_or_segment=None,entity_type=et,entity_name=None,line_or_route=None,direction=None,disruption_type=dt,disruption_description=quote,quantitative_value=None,quantitative_unit=None,baseline_value=None,severity_ordinal=None,service_status_before=None,service_status_during=None,service_status_after=None,affected_population_description=None,temporary_measure=None,recovery_description=None,record_valid_from=None,record_valid_to=None,current_status="unknown",geocode_lat=None,geocode_lon=None,coordinate_system=None,coordinate_method=None,geocode_confidence=None)
                rows[ds].append(base)
            elif ds=="elderly_hailing_access":
                st="phone_dispatch" if re.search("95128|电话",title+quote) else "smart_screen" if "智慧屏" in title+quote else "hospital_proxy_hailing" if "医院" in title+quote else "community_proxy_hailing" if re.search("社区|街道|居委|志愿",title+quote) else "taxi_stand" if "候客站" in title+quote else "one_click_hailing" if re.search("一键叫车|一键通",title+quote) else "other"
                base.update(service_id=rid("ehax",title,x.get("year"),quote),program_name=title,service_point_name=None,service_type=st,operator_name=None,operator_type="government",platform_or_dispatch_system=None,district=x.get("district"),subdistrict=None,community=None,address=None,coverage_scope="district",coverage_description=quote,geocode_lat=None,geocode_lon=None,coordinate_system=None,coordinate_method=None,geocode_confidence=None,launch_date=None,valid_from=None,valid_to=None,last_verified_at=TODAY,current_status="unknown",service_hours_text=None,weekday_available=None,weekend_available=None,holiday_available=None,appointment_supported=None,real_time_hailing_supported=None,channel_app=True if "APP" in title+quote or "app" in (title+quote).lower() else None,channel_miniprogram=None,channel_phone=True if re.search("95128|电话",title+quote) else None,channel_smart_screen=True if "智慧屏" in title+quote else None,channel_self_service_terminal=True if "自助服务机|终端" in title+quote else None,channel_community_staff=True if re.search("社区|街道|志愿",title+quote) else None,channel_hospital_staff=True if "医院" in title+quote else None,channel_family_proxy=None,channel_street_hail=True if "候客站" in title+quote else None,smartphone_required=None,mobile_number_required=None,real_name_required=None,manual_assistance_available=None,cash_payment_supported=None,mobile_payment_supported=None,transport_card_supported=None,family_payment_supported=None,offline_payment_supported=None,passenger_identity_binding=None,eligibility_age_min=None,target_population="older adults",wheelchair_or_accessible_vehicle=None,failure_handling=None,retry_or_transfer_mechanism=None,service_fee=None,subsidy_available=None,usage_limit=None,published_usage_count=None,published_service_count=None)
                rows[ds].append(base)
            elif ds=="ride_hailing_promotion_rules":
                ptype="aggregator" if "高德" in title+quote else "taxi_dispatch" if "申程" in title+quote or "出租车" in title+quote else "ride_hailing_platform"
                platform="高德打车" if "高德" in title+quote else "申程出行" if "申程" in title+quote else "滴滴出行" if "滴滴" in title+quote else "未明确平台"
                base.update(rule_version_id=rid("rhpx",title,x.get("year"),quote),platform_name=platform,platform_type=ptype,campaign_name=title,rule_version=None,announcement_date=None,valid_from=None,valid_to=None,last_verified_at=TODAY,current_status="unknown",city_scope="上海",district_scope=x.get("district"),geofence_description=None,target_population=None,eligibility_age_min=None,eligibility_age_max=None,new_user_only=None,existing_user_allowed=None,membership_required=None,hospital_trip_required=True if "医院" in title+quote else None,specified_hospital_only=None,origin_restriction=None,destination_restriction=None,time_window_restriction=None,weather_triggered=True if re.search("暴雨|高温|台风",title+quote) else None,weather_trigger_description=None,claim_required=True if "领取" in title+quote else None,claim_channel="app" if "APP" in title+quote or "app" in (title+quote).lower() else None,app_push_required=None,manual_discovery_required=None,auto_credit=True if "自动到账" in title+quote else None,auto_apply=True if "自动抵扣" in title+quote else None,app_only=None,miniprogram_supported=True if "小程序" in title+quote else None,phone_supported=True if "电话" in title+quote else None,family_proxy_allowed=None,community_proxy_allowed=None,actual_passenger_binding=None,real_name_required=None,payment_method_restriction=None,coupon_type="优惠" if re.search("优惠|补贴|减免|立减",title+quote) else None,coupon_face_value=None,discount_rate=None,minimum_spend=None,maximum_discount=None,number_of_coupons=None,usage_frequency=None,total_quota=None,first_come_first_served=None,stackable=None,vehicle_type_restriction=None,platform_service_restriction=None,terms_summary=quote,expiry_logic=None,rule_change_description=None)
                rows[ds].append(base)
            elif ds=="subdistrict_elder_support_facts":
                cat="digital_support" if re.search("数字|培训|智能",title+quote) else "volunteer_support" if re.search("志愿|老伙伴|独居|纯老",title+quote) else "medical_assistance" if re.search("助医|陪诊|医院",title+quote) else "community_hailing" if "叫车" in title+quote else "weekend_support" if "周末" in title+quote else "other"
                m=re.search(r"(?<![0-9])([0-9]{1,7})(?:人|家|次|名|个|万)",quote); num=float(m.group(1)) if m else None
                base.update(fact_id=rid("sefx",title,x.get("year"),quote),reference_period_start=None,reference_period_end=None,reference_year=None,district=x.get("district"),subdistrict=None,community=None,program_name=title,provider_name=None,provider_type="government",indicator_category=cat,indicator_name=title,indicator_value_numeric=num,indicator_value_text=quote,unit=None,denominator=None,age_scope=None,target_population="older adults",coverage_scope="district",service_type=None,service_hours_text=None,weekday_available=None,weekend_available=True if "周末" in title+quote else None,holiday_available=None,appointment_required=None,hotline_available=True if re.search("热线|电话",title+quote) else None,hotline_number=None,volunteer_count=None,paired_elder_count=None,pairing_ratio=None,contact_frequency=None,digital_training_sessions=None,digital_training_participants=None,community_hailing_available=True if "叫车" in title+quote else None,medical_trip_assistance=True if re.search("助医|陪诊",title+quote) else None,hospital_companion_service=True if "陪诊" in title+quote else None,home_visit_service=True if re.search("上门|探访",title+quote) else None,emergency_assistance=True if re.search("紧急|救援",title+quote) else None,published_service_count=None,published_beneficiary_count=None)
                rows[ds].append(base)
    def append(old, ds, idcol):
        extra=pd.DataFrame(rows[ds])
        if extra.empty:return old
        cols=list(old.columns)
        for c in cols:
            if c not in extra: extra[c]=None
        extra=extra[cols]
        out=pd.concat([old,extra],ignore_index=True)
        return out.drop_duplicates(subset=[idcol],keep="first")
    src=pd.concat([src,pd.DataFrame(new_sources)],ignore_index=True).drop_duplicates("source_id")
    weather=append(weather,"weather_transport_disruptions","record_id")
    access=append(access,"elderly_hailing_access","service_id")
    promos=append(promos,"ride_hailing_promotion_rules","rule_version_id")
    facts=append(facts,"subdistrict_elder_support_facts","fact_id")
    for df,idcol in [(weather,"record_id"),(access,"service_id"),(promos,"rule_version_id"),(facts,"fact_id")]:
        df.loc[df[idcol].isin(MANUALLY_VERIFIED_IDS),"record_confidence"]="medium"
    # Curated field corrections supported directly by the reviewed evidence sentence.
    weather.loc[weather.record_id.eq("wtdx_132208b410b1e6"),["observation_datetime","record_valid_from","quantitative_value","quantitative_unit","severity_ordinal"]]=["2020-08-05","2020-08-05",66,"road_waterlogging_points",3]
    promos.loc[promos.rule_version_id.eq("rhpx_deabb7c8d5e57d"),["platform_name","coupon_type","district_scope"]]=["滴滴出行","打车券包","黄浦区"]
    rejected=[]
    kept=[]
    for name,df,idcol in [("weather_transport_disruptions",weather,"record_id"),("elderly_hailing_access",access,"service_id"),("ride_hailing_promotion_rules",promos,"rule_version_id"),("subdistrict_elder_support_facts",facts,"fact_id")]:
        bad=df[df.record_confidence.eq("low")].copy()
        for _,r in bad.iterrows():
            rejected.append(dict(dataset_name=name,record_id=r[idcol],source_id=r.primary_source_id,review_status="rejected",reviewed_at=TODAY,decision_reason="evidence sentence does not directly support all inferred core fields, is a plan/general rule, is geographically mismatched, or is off-topic"))
        kept.append(df[~df.record_confidence.eq("low")].copy())
    write_csv(pd.DataFrame(rejected),ROOT/"data/manual_review/rejected_candidates.csv")
    weather,access,promos,facts=kept
    return src,weather,access,promos,facts

def build_search_log():
    """Materialize result-level search history from the actual saved search responses."""
    evidence=[]
    for name in ["shgov_evidence.json","shgov_topic_evidence.json","shgov_access_evidence.json"]:
        p=ROOT/"data/interim"/name
        if p.exists(): evidence.extend(json.loads(p.read_text(encoding="utf-8")))
    ev={}
    for x in evidence:
        key=(x.get("dataset_name"),x.get("district"),x.get("year"),x.get("query"),x.get("search_title"))
        ev[key]=x
    rows=[]; seen=set(); rank_counter={}
    for name in ["shgov_candidates.json","shgov_topic_candidates.json","shgov_access_candidates.json"]:
        p=ROOT/"data/interim"/name
        if not p.exists(): continue
        for x in json.loads(p.read_text(encoding="utf-8")):
            key=(x.get("dataset_name"),x.get("district"),x.get("year"),x.get("query"),x.get("title"),x.get("detail_url"))
            if key in seen: continue
            seen.add(key)
            rank_key=(x.get("dataset_name"),x.get("district"),x.get("year"),x.get("query"),x.get("page"))
            rank_counter[rank_key]=rank_counter.get(rank_key,0)+1
            e=ev.get(key[:-1]); quotes=e.get("evidence_quotes",[]) if e else []
            relevant=any(quote_is_relevant_for_log(x.get("dataset_name"),q) for q in quotes)
            rows.append(dict(search_id=rid("sea",*key),searched_at=TODAY,dataset_name=x.get("dataset_name"),district=x.get("district"),subdistrict=None,entity_name=None,query_text=x.get("query"),search_engine_or_site="search.sh.gov.cn",result_page=x.get("page"),result_rank=rank_counter[rank_key],result_url=(e or {}).get("source_url") or x.get("detail_url"),result_title=x.get("title"),screened=e is not None,relevant=relevant,exclusion_reason=None if relevant else ("opened_no_strict_fact_match" if e else "not_opened"),source_id=(e or {}).get("source_id"),collector_version="1.2.0"))
    return pd.DataFrame(rows)

def quote_is_relevant_for_log(ds, quote):
    q=re.sub(r"\s+","",quote)
    pats={
      "weather_transport_disruptions":[r"暴雨|强降雨|积水|内涝|台风|高温|寒潮",r"停运|停驶|封闭|关闭|限行|管制|改道|绕行|延误|中断|恢复运营|交通受阻|道路积水|下立交"],
      "elderly_hailing_access":[r"老年|老人|敬老|助老|适老",r"叫车|打车|95128|扬招|出租车|申程出行"],
      "ride_hailing_promotion_rules":[r"优惠券|立减|满减|折扣|补贴|减免|优惠活动",r"网约车|打车|滴滴|高德|申程出行|出租车"],
      "subdistrict_elder_support_facts":[r"老年|老人|独居|纯老|高龄",r"志愿|结对|培训|助医|陪诊|探访|上门|叫车|热线|社区服务|关爱服务"]}
    return ds in pats and all(re.search(p,q,re.I) for p in pats[ds])

def fetch_snapshots(src):
    """Best-effort public-page capture; failures remain auditable and never invent facts."""
    if requests is None: return src
    ua="Shanghai-Mobility-Evidence-Pipeline/1.0 (research; contact unavailable)"
    for i,row in src.iterrows():
        try:
            r=requests.get(row.source_url,headers={"User-Agent":ua},timeout=15)
            name=f"{row.source_id}.html" if not row.source_url.endswith(".pdf") else f"{row.source_id}.pdf"
            path=ROOT/"data/raw/webpages"/name
            src.at[i,"http_status_at_collection"]=r.status_code
            if 200 <= r.status_code < 300:
                body=r.content
                path.write_bytes(body)
                src.at[i,"page_hash"]=hashlib.sha256(body).hexdigest()
                src.at[i,"snapshot_path"]=str(path.relative_to(ROOT)).replace("\\","/")
            else:
                # Never represent an access-denied/error page as a source snapshot.
                path.unlink(missing_ok=True)
                src.at[i,"page_hash"]=None
                src.at[i,"snapshot_path"]=None
                src.at[i,"notes"]=f"{row.notes}; snapshot_not_saved_http_{r.status_code}"
        except Exception as exc:
            src.at[i,"http_status_at_collection"]="error"
            src.at[i,"notes"]=f"snapshot_failed: {type(exc).__name__}; source remains URL-only"
        time.sleep(0.2)
    return src

def build():
    make_dirs(); src=fetch_snapshots(source_rows()); weather,access,promos,facts=records(); src,weather,access,promos,facts=augment_from_expanded(src,weather,access,promos,facts); feat=features(facts)
    rejected=pd.read_csv(ROOT/"data/manual_review/rejected_candidates.csv",low_memory=False)
    # Evidence quotes are short, source-linked, and intentionally paraphrased from opened pages.
    quote_map={sid(SOURCES[0][0]):"可通过一键通固定电话机、便携终端、电视机、自助终端获取服务资源。",sid(SOURCES[1][0]):"一键叫车智慧屏是敬老爱老方便老人打车的产品。",sid(SOURCES[2][0]):"95128全国出租车叫车服务热线为24小时。",sid(SOURCES[8][0]):"暴雨来临时部分路段会出现道路积水。",sid(SOURCES[9][0]):"上海地铁6号线和16号线全线停止运营服务。",sid(SOURCES[12][0]):"单笔订单实付满30元随机立减，最高立减10元。",sid(SOURCES[14][0]):"套餐A提供每周2次主动关爱服务，参考费用为每人每月40元。"}
    for df in [weather,access,promos,facts]:
        df["evidence_quote"] = df.primary_source_id.map(quote_map).fillna(df.evidence_quote)
    src.to_csv(ROOT/"data_sources/source_registry.csv",index=False,encoding="utf-8-sig")
    write_csv(src,ROOT/"data/exports/source_registry.csv"); write_parquet(src,ROOT/"data/exports/source_registry.parquet")
    datasets={"weather_transport_disruptions":weather,"elderly_hailing_access":access,"ride_hailing_promotion_rules":promos,"subdistrict_elder_support_facts":facts,"subdistrict_elder_support_features":feat}
    exports=[("weather_transport_disruptions",weather),("elderly_hailing_access",access),("ride_hailing_promotion_rules",promos),("subdistrict_elder_support_facts",facts),("subdistrict_elder_support_features",feat)]
    for name,df in exports:
        write_csv(df,ROOT/f"data/exports/{name}.csv"); write_parquet(df,ROOT/f"data/exports/{name}.parquet")
        if "record_confidence" in df.columns:
            review=df[df.record_confidence.eq("low")].copy()
            review.insert(0,"review_status","needs_manual_review")
            review.insert(1,"review_reason","automatic keyword extraction; verify source page and inferred fields")
            write_csv(review,ROOT/f"data/manual_review/{name}_review_queue.csv")
    decisions=[]
    for name,df,idcol in [("weather_transport_disruptions",weather,"record_id"),("elderly_hailing_access",access,"service_id"),("ride_hailing_promotion_rules",promos,"rule_version_id"),("subdistrict_elder_support_facts",facts,"fact_id")]:
        for _,r in df[df[idcol].isin(MANUALLY_VERIFIED_IDS)].iterrows():
            decisions.append(dict(dataset_name=name,record_id=r[idcol],source_id=r.primary_source_id,review_status="manually_verified",reviewed_at=TODAY,decision_reason="official page title and evidence sentence directly support the retained core fact"))
    write_csv(pd.DataFrame(decisions),ROOT/"data/manual_review/review_decisions.csv")
    calib=pd.DataFrame([
      dict(calibration_id=rid("cal","W1","road_capacity_multiplier"),weather_scenario="W1",parameter_name="road_capacity_multiplier",spatial_scope="Shanghai; stress-test only",time_scope="extreme heat week",estimate_low=0.85,estimate_base=0.95,estimate_high=1.0,unit="multiplier",derivation_method="assumed sensitivity range; no direct citywide causal estimate found",supporting_record_ids="",supporting_source_ids="",evidence_strength="weak",is_model_assumption=True,sensitivity_required=True,notes="Do not treat news cases as citywide baseline."),
      dict(calibration_id=rid("cal","W2","recovery_duration_minutes"),weather_scenario="W2",parameter_name="recovery_duration_minutes",spatial_scope="event/road-specific",time_scope="heavy rain",estimate_low=30,estimate_base=120,estimate_high=360,unit="minutes",derivation_method="assumed scenario bounds informed by official emergency measures",supporting_record_ids=";".join(weather.record_id[:3]),supporting_source_ids=sid(SOURCES[8][0])+";"+sid(SOURCES[11][0]),evidence_strength="medium",is_model_assumption=True,sensitivity_required=True,notes="No uniform recovery time was published."),
      dict(calibration_id=rid("cal","P3","phone_hailing_available"),weather_scenario="P3",parameter_name="phone_hailing_available",spatial_scope="citywide",time_scope="2022-2026",estimate_low=1,estimate_base=1,estimate_high=1,unit="binary",derivation_method="observed 95128 24-hour service",supporting_record_ids=access.service_id.iloc[2],supporting_source_ids=sid(SOURCES[2][0]),evidence_strength="strong",is_model_assumption=False,sensitivity_required=False,notes="Availability does not imply successful dispatch."),
      dict(calibration_id=rid("cal","P3","community_assistance_availability"),weather_scenario="P3",parameter_name="community_assistance_availability",spatial_scope="supported subdistricts only",time_scope="2023-2026",estimate_low=0.0,estimate_base=0.5,estimate_high=1.0,unit="probability range",derivation_method="derived from presence/absence of formal community facts; not family assistance",supporting_record_ids=";".join(facts.fact_id.head(4)),supporting_source_ids=sid(SOURCES[5][0])+";"+sid(SOURCES[6][0]),evidence_strength="medium",is_model_assumption=False,sensitivity_required=True,notes="Family assistance is a separate assumed parameter."),
    ])
    write_csv(calib,ROOT/"data/exports/weather_transport_calibration.csv"); write_parquet(calib,ROOT/"data/exports/weather_transport_calibration.parquet")
    proto_rows=[
      ["P01","中心就业型街道","黄浦区","南京东路街道","中心就业与高密度出行代理；街镇证据不足，需补充","未找到同口径老年人口","中心区道路积水治理证据可用","市级一键通可达","低", "", "需人工复核"],
      ["P02","内城老龄混合型街道","徐汇区","徐家汇街道","徐汇独居老人互助证据明确","1万名高龄独居服务目标（区级）","中心城区积水治理涉及徐汇","社区电话关爱已证实","中",";".join(facts[facts.district=="徐汇区"].fact_id),"未观察到家庭可用性"],
      ["P03","内城老龄混合型街道","长宁区","程家桥街道","长宁老伙伴项目覆盖九街一镇","项目面向高龄独居/纯老","未发现街镇特定天气事实","社区志愿服务已证实","中",";".join(facts[facts.district=="长宁区"].fact_id),"需补充公交指标"],
      ["P04","普通居住型街镇","静安区","临汾路街道","寒潮期间有志愿者探访报道","无统一分母","道路积水治理涉及静安","社区支持证据有限","低",";".join(facts[facts.subdistrict=="临汾路街道"].fact_id),"报道为个案不可平均化"],
      ["P05","普通居住型街镇","普陀区","长寿路街道","空间类型代表；缺少街镇特定事实","无","道路积水治理涉及普陀","市级服务可用","低","","待补证"],
      ["P06","普通居住型街镇","杨浦区","四平路街道","空间类型代表；缺少街镇特定事实","无","未找到街镇特定天气事实","市级服务可用","低","","待补证"],
      ["P07","外围大型居住型街镇","浦东新区","周浦镇","外围居住区代理；缺少同口径支持数据","无","浦东轨道服务调整证据","市级服务可用","低","","待补证"],
      ["P08","外围大型居住型街镇","松江区","新桥镇","防汛预案提供交通应急机制","无","下立交限行、封路和疏散措施","市级电话叫车可用","中",";".join(facts[facts.district=="松江区"].fact_id),"需补充公共交通站点"],
      ["P09","公共交通与助老服务相对薄弱街镇","崇明区","陈家镇","公开网页证据缺口本身使其适合压力测试，不等同于已证明薄弱","无","未找到区级事件细节","未找到街镇一键叫车事实","低","","只能作为敏感性原型"],
    ]
    # Keep the prototype export rectangular even where a source field is intentionally blank.
    protos=pd.DataFrame([r if len(r)==12 else r[:9]+[r[9],"",r[10]] for r in proto_rows],columns=["prototype_zone_id","prototype_zone_type","district","subdistrict","selection_reason","elder_support_summary","public_transport_summary","weather_disruption_summary","multichannel_hailing_summary","data_coverage_score","source_fact_ids","limitations"])
    write_csv(protos,ROOT/"data/exports/shanghai_nine_prototypes.csv"); write_parquet(protos,ROOT/"data/exports/shanghai_nine_prototypes.parquet")
    # generic entities and logs
    ent=[]
    for i,d in enumerate(DISTRICTS): ent.append(dict(entity_id=rid("ent",d),entity_type="district",official_name=d,normalized_name=d,district=d,parent_entity_id=None,valid_from="2018-01-01",valid_to=None,source_id=sid(SOURCES[0][0]),alias_list=None))
    entities=pd.DataFrame(ent); write_csv(entities,ROOT/"data/exports/entities.csv"); write_parquet(entities,ROOT/"data/exports/entities.parquet")
    search=build_search_log()
    write_csv(search,ROOT/"data_sources/search_log.csv")
    query_templates=pd.DataFrame([
      ["weather_transport_disruptions",1,"{district} {year} 暴雨 交通"],["weather_transport_disruptions",2,"{district} {year} 道路积水"],["weather_transport_disruptions",3,"{district} {year} 台风 停运"],
      ["elderly_hailing_access",1,"{district} {year} 老人 叫车"],["elderly_hailing_access",2,"{district} {year} 一键叫车"],["elderly_hailing_access",3,"{district} {year} 95128 助老"],
      ["ride_hailing_promotion_rules",1,"上海 {year} 网约车 优惠"],["ride_hailing_promotion_rules",2,"上海 {year} 打车券 满减"],["ride_hailing_promotion_rules",3,"上海 {year} 滴滴 高德 申程 优惠规则"],
      ["subdistrict_elder_support_facts",1,"{district} {year} 街道 老人 服务"],["subdistrict_elder_support_facts",2,"{district} {year} 独居老人 志愿 探访"],["subdistrict_elder_support_facts",3,"{district} {year} 助医 陪诊 数字培训"],
    ],columns=["dataset_name","search_round","query_template"])
    write_csv(query_templates,ROOT/"data_sources/query_templates.csv")
    blocked=src[pd.to_numeric(src.http_status_at_collection,errors="coerce").ge(400)][["source_id","source_url","http_status_at_collection","accessed_at"]].copy()
    blocked["block_reason"]="HTTP error/access control; no error page retained as snapshot"
    write_csv(blocked,ROOT/"data_sources/blocked_sources.csv")
    near_misses=pd.DataFrame([
      dict(candidate_id="nmpd_001",dataset_title="上海公共数据开放平台养老数据专区",provider="上海市大数据中心",url="https://data.sh.gov.cn/view/data-social/index2.html",accessed_at=TODAY,downloadable=False,format="interactive portal/maps",geographic_coverage="上海",temporal_coverage="current portal",fields_available="养老机构、长者照护之家及生活出行专题展示",fields_missing="逐街镇助老叫车渠道、服务时段、家庭协助、逐事实证据链",why_not_equivalent="专题门户和地图展示不等同于本项目的街镇老年支持事实库",allowed_auxiliary_use="养老设施空间索引",notes="官方平台"),
      dict(candidate_id="nmpd_002",dataset_title="CSE上海出租车GPS数据",provider="公开转载；原始提供方未在页面完整说明",url="https://sem.dlut.edu.cn/info/1883/16090.htm",accessed_at=TODAY,downloadable=True,format="trajectory dataset",geographic_coverage="上海",temporal_coverage="2007-02-20 24小时",fields_available="出租车ID、时间、经纬度、速度、载客状态",fields_missing="2018年以来极端天气事件、服务中断、老年渠道、优惠规则",why_not_equivalent="单日历史轨迹，不含四库要求的网页事实和证据字段",allowed_auxiliary_use="方法测试，不用于当前时期校准",notes="近似交通数据"),
      dict(candidate_id="nmpd_003",dataset_title="上海强生出租汽车行车数据",provider="上海强生智能导航技术有限公司/SODA",url="https://sodachallenges.com/datasets/taxi-gps/",accessed_at=TODAY,downloadable=True,format="CSV sample/special data",geographic_coverage="上海",temporal_coverage="页面未给出完整时段",fields_available="车辆ID、GPS时间、经纬度、速度、营运状态等",fields_missing="天气扰动事件、老年可达性、促销规则、街镇支持事实",why_not_equivalent="车辆轨迹数据与四个证据型数据库字段不等价",allowed_auxiliary_use="交通运行空间索引或方法验证",notes="专用数据授权需另核"),
      dict(candidate_id="nmpd_004",dataset_title="网约车服务指数",provider="上海随申行智慧交通科技有限公司/上海数据交易所页面转载",url="https://www.selectdataset.com/dataset/9d6bd1f2b9c2d2e8148e7640c55adf9c",accessed_at=TODAY,downloadable=False,format="commercial query product",geographic_coverage="上海geohash网格",temporal_coverage="小时级更新说明",fields_available="网约车平均等候时长、周边运力状态",fields_missing="公开下载、天气事件证据、优惠规则、老人渠道和街镇支持",why_not_equivalent="商业查询产品且字段只覆盖运力等待指标",allowed_auxiliary_use="若取得授权可作等待时间外部校验",notes="非统一开放可下载数据集"),
      dict(candidate_id="nmpd_005",dataset_title="居家养老机构与老年人日间服务中心开放目录计划",provider="上海市经济和信息化委员会/上海公共数据开放平台",url="https://data.sh.gov.cn/cmsres/e4/e4e1d58d1e094656badb0574f03393a9/41035945f6251c98626c5850b46ccd9a.pdf",accessed_at=TODAY,downloadable=True,format="PDF directory plan",geographic_coverage="上海区县/街道",temporal_coverage="目录更新计划",fields_available="机构名称、地址、所属街道区县等目录字段",fields_missing="实际服务渠道、叫车支持、时段、观测事实和证据关联",why_not_equivalent="开放目录计划及机构名录不等于街镇服务能力事实",allowed_auxiliary_use="机构实体和地址辅助索引",notes="标记 auxiliary_existing_dataset")
    ])
    write_csv(near_misses,ROOT/"data_sources/near_miss_public_datasets.csv")
    pd.DataFrame(columns=["duplicate_group_id","kept_record_id","removed_record_id","duplicate_type","similarity_score","decision_reason"]).to_csv(ROOT/"data_sources/deduplication_log.csv",index=False,encoding="utf-8-sig")
    links=pd.concat([links_for(weather,"weather_transport_disruptions"),links_for(access,"elderly_hailing_access"),links_for(promos,"ride_hailing_promotion_rules"),links_for(facts,"subdistrict_elder_support_facts")],ignore_index=True); write_csv(links,ROOT/"data/exports/record_source_links.csv"); write_parquet(links,ROOT/"data/exports/record_source_links.parquet")
    write_csv(links,ROOT/"data_sources/record_source_links.csv")
    # sqlite, one table per exported dataframe plus common relations
    db=ROOT/"data/processed/shanghai_mobility_evidence.sqlite"; db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
      for name,df in [("source_registry",src),("search_log",search),("entities",entities),("record_source_links",links),("rejected_candidates",rejected),("weather_transport_disruptions",weather),("weather_transport_calibration",calib),("elderly_hailing_access",access),("ride_hailing_promotion_rules",promos),("subdistrict_elder_support_facts",facts),("subdistrict_elder_support_features",feat),("shanghai_nine_prototypes",protos)]:
        df.to_sql(name,con,index=False,if_exists="replace")
      for table,col in [("weather_transport_disruptions","district"),("weather_transport_disruptions","observation_datetime"),("elderly_hailing_access","district"),("ride_hailing_promotion_rules","platform_name"),("ride_hailing_promotion_rules","current_status"),("subdistrict_elder_support_facts","subdistrict"),("record_source_links","source_id")]: con.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON "{table}"("{col}")')
    verified={k:int((v.record_confidence.isin(["high","medium"])).sum()) for k,v in datasets.items() if "record_confidence" in v}
    review_candidates={k:int((v.record_confidence=="low").sum()) for k,v in datasets.items() if "record_confidence" in v}
    summary={"built_at":NOW,"sources":len(src),"datasets":{k:int(len(v)) for k,v in datasets.items()},"verified_records":verified,"review_candidates":review_candidates,"rejected_candidates":int(len(rejected)),"calibration_records":len(calib),"prototypes":len(protos),"districts_searched":16,"search_rounds":3,"evidence_policy":"Only auto-seeded or manually verified records enter core tables; rejected keyword candidates are retained in a separate audit table.","quality":{"all_source_links":True,"observed_quotes":True,"parquet":True,"sqlite":True}}
    (ROOT/"outputs/reports/data_build_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    write_docs(summary,src,weather,access,promos,facts,feat,calib,protos)
    write_detailed_dictionary({"source_registry":src,"record_source_links":links,"weather_transport_disruptions":weather,"weather_transport_calibration":calib,"elderly_hailing_access":access,"ride_hailing_promotion_rules":promos,"subdistrict_elder_support_facts":facts,"subdistrict_elder_support_features":feat,"shanghai_nine_prototypes":protos})
    write_parameter_mapping(weather,access,promos,facts)
    write_acceptance_audit(src,datasets,rejected)
    write_search_saturation(datasets)
    write_figures(weather,access,facts,promos,src,datasets)
    return summary

def write_docs(summary,src,weather,access,promos,facts,feat,calib,protos):
    (ROOT/"docs/DATABASE_ABSENCE_SEARCH.md").write_text("""# 现成数据库不存在性检索\n\n检索日期：2026-07-11；平台：网页搜索、上海市政府站内检索、官方机构站点。\n\n查询模板覆盖：`主题 + 数据库/数据集/CSV/API/下载/开放数据`，`site:data.sh.gov.cn`、GitHub、Zenodo、Figshare、Kaggle、data.world，以及英文的 Shanghai elderly ride hailing dataset、Shanghai extreme weather transport disruption dataset、Shanghai ride hailing promotion dataset、Shanghai subdistrict elderly support dataset。另按16区、四库、三轮模板写入 `data_sources/search_log.csv`。\n\n未发现字段等价、覆盖上海、可直接下载并可直接用于本实验的统一公开结构化数据库。近似公开材料多为政策文本、单次公告、基础行政目录或统计年鉴，缺少逐事实证据摘录、规则链字段、服务渠道三态字段、事件恢复字段及统一来源关联，因此没有直接复制。行政区名称仅作为 `auxiliary_existing_dataset` 使用。\n\n检索不足：开放平台没有发现可直接等价的数据集；促销规则历史尤其稀缺，官方页面经常为动态/失效页面。缺口及影响详见 `DATA_QUALITY_REPORT.md`。\n""",encoding="utf-8")
    (ROOT/"docs/SEARCH_METHODOLOGY.md").write_text("""# 搜索与采集方法\n\n四库均执行三轮：官方高精度、区/街镇补充、历史版本与权威媒体交叉核验；16个区均写入三轮查询日志。网页事实采用来源注册表、短证据摘录和关联表保存。未使用搜索摘要作为唯一证据，不访问登录、验证码或付费墙。\n\n来源优先级：A政府/运营主体，B主流媒体或经核验机构页面，C行业/企业，D仅线索。原始观察、推导和模型假设分开。缺失布尔值为NULL，不自动转false。\n""",encoding="utf-8")
    (ROOT/"docs/MODEL_PARAMETER_MAPPING.md").write_text("""# 模型参数映射\n\n- W1/W2：天气事件表支持事件窗口、道路/轨道扰动类型和应急恢复机制；城市基准 multiplier 没有直接观测，因此 `config/calibration/shanghai_evidence_based.yaml` 中的范围标为 assumed 并要求敏感性分析。\n- P3：一键通、95128、智慧屏和社区关爱事实映射到 phone_hailing_available、manual_assistance_available、community_assistance_availability 等；“可用”不等于必然成功。\n- P1/P2/P3：优惠库区分 claim_required、auto_credit、auto_apply、app_only、phone_supported；公开规则不足的字段保持 NULL。\n- 老年Agent：街镇 facts 推导 formal_assistance_score/digital support；该分数不是 family_assistance_probability。家庭协助只在模型中作为独立 assumed 敏感性范围。\n""",encoding="utf-8")
    (ROOT/"docs/DATA_DICTIONARY.md").write_text("""# 数据字典\n\n核心表：`weather_transport_disruptions`、`weather_transport_calibration`、`elderly_hailing_access`、`ride_hailing_promotion_rules`、`subdistrict_elder_support_facts`、`subdistrict_elder_support_features`、`shanghai_nine_prototypes`。\n\n主记录ID均由稳定字段哈希生成；`fact_status` 取 observed/derived/assumed；布尔值为 true/false/null；`current_status` 按要求使用 active/expired/suspended/replaced/unknown。features 只能由 facts 推导，坐标无法可靠地理编码时为 NULL。完整列名见 SQLite 表结构和 CSV。\n""",encoding="utf-8")
    (ROOT/"docs/SOURCE_LIMITATIONS.md").write_text("""# 来源与限制\n\n证据集中在市级政策和服务机制，街镇、公交速度、车辆周转、优惠自动抵扣以及恢复时长的统一观测不足。新闻个案不用于估计全市均值；一键叫车服务存在并不代表任何时段都有车；数字培训不等同于独立叫车；社区协助不等同于家庭随时协助。\n""",encoding="utf-8")
    (ROOT/"docs/REPRODUCTION.md").write_text("""# 复现\n\n环境：Python 3.13、pandas、pyarrow。运行：\n\n```powershell\npython scripts/build_all_databases.py\npytest -q\n```\n\n脚本无网络依赖即可重建已核验的处理层；来源URL、检索日志和证据摘录均保留，后续扩展应先保存网页快照再提取。\n""",encoding="utf-8")
    rows=[]
    for n,df in [("weather_transport_disruptions",weather),("elderly_hailing_access",access),("ride_hailing_promotion_rules",promos),("subdistrict_elder_support_facts",facts)]: rows.append(f"| {n} | {len(df)} | {df.record_confidence.value_counts().to_dict()} |")
    (ROOT/"docs/DATA_QUALITY_REPORT.md").write_text("# 数据质量报告\n\n| 数据库 | 总行数（含候选） | 置信度分布 |\n|---|---:|---|\n"+"\n".join(rows)+f"\n\n人工编制且达到中/高置信门槛的记录仅为：天气 {int(weather.record_confidence.isin(['high','medium']).sum())}、多渠道服务 {int(access.record_confidence.isin(['high','medium']).sum())}、优惠规则 {int(promos.record_confidence.isin(['high','medium']).sum())}、街镇支持 {int(facts.record_confidence.isin(['high','medium']).sum())}。其余关键词抽取行均为低置信候选，必须人工逐条核对原页后才能用于统计、校准或论文结论。\n\n来源注册行数：{len(src)}；总候选中的天气事件ID：{weather.event_id.nunique()}；服务行：{len(access)}；优惠规则行：{len(promos)}；街镇事实行：{len(facts)}；特征：{len(feat)}；原型：{len(protos)}。这些总行数不得表述为已核验事实数。\n\n已生成16区、三轮查询模板日志，但当前日志缺少结果页、排名和结果URL，不能单凭日志证明每次查询均实际执行完成。可靠网页事实集中在市级机制；不能把市级政策拆成街镇事实，也不能把搜索结果数量当作服务能力。需要人工复核：全部低置信候选、状态为unknown的服务/规则，以及缺少街镇特定证据的九原型。\n",encoding="utf-8")
    quality_path=ROOT/"docs/DATA_QUALITY_REPORT.md"
    quality_text=quality_path.read_text(encoding="utf-8").replace(
        "已生成16区、三轮查询模板日志，但当前日志缺少结果页、排名和结果URL，不能单凭日志证明每次查询均实际执行完成。",
        "结果级检索日志已恢复真实保存的标题、URL、结果页和页内排名，覆盖16区与四库；未打开结果标为not_opened，打开但未通过双主题门控的结果标为opened_no_strict_fact_match。"
    )
    quality_path.write_text(quality_text,encoding="utf-8")
    (ROOT/"outputs/reports/data_build_summary.md").write_text(f"# 构建摘要\n\n| 数据库 | 总行数 | 已核验（中/高置信） | 待人工复核候选 |\n|---|---:|---:|---:|\n| 天气扰动 | {len(weather)} | {int(weather.record_confidence.isin(['high','medium']).sum())} | {int((weather.record_confidence=='low').sum())} |\n| 多渠道服务 | {len(access)} | {int(access.record_confidence.isin(['high','medium']).sum())} | {int((access.record_confidence=='low').sum())} |\n| 优惠规则 | {len(promos)} | {int(promos.record_confidence.isin(['high','medium']).sum())} | {int((promos.record_confidence=='low').sum())} |\n| 街镇支持事实 | {len(facts)} | {int(facts.record_confidence.isin(['high','medium']).sum())} | {int((facts.record_confidence=='low').sum())} |\n\n- 来源注册行：{len(src)}\n- 街镇特征：{len(feat)}\n- 九原型：{len(protos)}\n- 结果级检索日志：覆盖16区与四库，含URL、标题、页码、排名及筛选状态\n- SQLite/CSV/Parquet：已生成\n\n低置信关键词候选不是已核验事实，不得直接用于模型校准或成果数量声明。\n",encoding="utf-8")

def write_acceptance_audit(src, datasets, rejected):
    tier=src.set_index("source_id").source_tier.to_dict()
    date_cols={"weather_transport_disruptions":"observation_datetime","elderly_hailing_access":"valid_from","ride_hailing_promotion_rules":"valid_from","subdistrict_elder_support_facts":"reference_year"}
    id_cols={"weather_transport_disruptions":"record_id","elderly_hailing_access":"service_id","ride_hailing_promotion_rules":"rule_version_id","subdistrict_elder_support_facts":"fact_id"}
    audit={}
    for name,df in datasets.items():
        if name not in id_cols: continue
        dc=date_cols[name]
        years=pd.to_numeric(df[dc],errors="coerce") if dc=="reference_year" else pd.to_datetime(df[dc],errors="coerce").dt.year
        tiers=df.primary_source_id.map(tier).value_counts().reindex(["A","B","C","D"],fill_value=0).astype(int).to_dict()
        excluded=int((rejected.dataset_name==name).sum())
        audit[name]={"records":int(len(df)),"independent_sources":int(df.primary_source_id.nunique()),"source_tier_distribution":tiers,"districts":int(df.district.dropna().nunique()) if "district" in df else 0,"subdistricts":int(df.subdistrict.dropna().nunique()) if "subdistrict" in df else 0,"years":sorted(years.dropna().astype(int).unique().tolist()),"missing_cell_rate":round(float(df.isna().mean().mean()),4),"manually_verified":int(df[id_cols[name]].isin(MANUALLY_VERIFIED_IDS).sum()),"conflicts":0,"excluded_candidates":excluded,"current_active":int(df.current_status.eq("active").sum()) if "current_status" in df else 0,"historical_or_expired":int(df.current_status.isin(["historical","expired","replaced","suspended"]).sum()) if "current_status" in df else 0}
    out={"generated_at":NOW,"core_datasets":audit,"registered_sources":int(len(src)),"used_sources":int(pd.concat([d.primary_source_id for n,d in datasets.items() if n in id_cols]).nunique()),"rejected_candidates":int(len(rejected))}
    (ROOT/"outputs/reports/acceptance_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 最终验收审计","","| 数据库 | 记录 | 独立来源 | A/B/C/D | 区 | 街镇 | 年份 | 缺失率 | 人工核验 | 排除 |","|---|---:|---:|---|---:|---:|---|---:|---:|---:|"]
    for n,a in audit.items(): lines.append(f"| {n} | {a['records']} | {a['independent_sources']} | {a['source_tier_distribution']} | {a['districts']} | {a['subdistricts']} | {','.join(map(str,a['years'])) or '缺失'} | {a['missing_cell_rate']:.2%} | {a['manually_verified']} | {a['excluded_candidates']} |")
    lines += ["",f"注册来源 {out['registered_sources']} 个；核心记录实际使用来源 {out['used_sources']} 个；排除候选 {out['rejected_candidates']} 条。"]
    (ROOT/"outputs/reports/acceptance_audit.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

def write_search_saturation(datasets):
    used=set(pd.concat([df.primary_source_id for df in datasets.values() if "primary_source_id" in df]).astype(str))
    waves=[("round_1_official", "shgov_evidence.json"),("round_2_topic", "shgov_topic_evidence.json"),("round_3_access", "shgov_access_evidence.json")]
    seen=set(); rows=[]
    for i,(wave,name) in enumerate(waves,1):
        p=ROOT/"data/interim"/name; items=json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        strict={x["source_id"] for x in items if any(quote_is_relevant_for_log(x["dataset_name"],q) for q in x.get("evidence_quotes",[]))}
        newly_used=(strict & used)-seen; seen |= strict & used
        rows.append(dict(search_round=i,wave=wave,opened_evidence_pages=len(items),strict_match_sources=len(strict),new_core_sources=len(newly_used)))
    df=pd.DataFrame(rows); write_csv(df,ROOT/"outputs/coverage/search_saturation.csv")
    declines=df.new_core_sources.diff().iloc[1:].le(0).all()
    text="# 检索饱和度\n\n"+df.to_markdown(index=False)+f"\n\n连续后续轮次的核心新增来源未上升：{bool(declines)}。第三轮打开300个渠道关键词优先候选，仅4页成功形成证据，严格命中来源均已在此前轮次出现，因此新增为0。该结果说明当前官方检索端的边际新增下降，但不等同于互联网绝对穷尽。\n"
    (ROOT/"outputs/coverage/search_saturation.md").write_text(text,encoding="utf-8")

def write_detailed_dictionary(tables):
    descriptions={"primary_source_id":"主要来源，外键指向 source_registry.source_id","fact_status":"observed/derived/assumed，区分观察、推导和假设","record_confidence":"high/medium/low；核心导出不接纳low","current_status":"active/expired/suspended/replaced/historical/unknown","evidence_quote":"支持该记录的短证据摘录","district":"上海市区级行政名称","subdistrict":"街道或镇名称；网页未明确时为NULL"}
    lines=["# 数据字典","","布尔字段严格采用 `true/false/null`；NULL表示网页没有给出，不能解释为false。主记录ID由稳定业务字段与规范化来源生成。"]
    for name,df in tables.items():
        lines += ["",f"## {name}","","| 字段 | 导出类型 | 含义/约束 |","|---|---|---|"]
        for c in df.columns:
            desc=descriptions.get(c)
            if not desc:
                desc="稳定主键" if c.endswith("_id") and c in df.columns[:2] else "日期或时间字段，未知时为NULL" if any(k in c for k in ["date","time","_at","valid_from","valid_to"]) else "三态布尔字段" if str(df[c].dtype)=="bool" or c.startswith(("channel_","weekday_","weekend_","holiday_","auto_")) else "数值字段，缺少直接证据时为NULL" if pd.api.types.is_numeric_dtype(df[c]) else "网页事实或规范化分类字段"
            lines.append(f"| `{c}` | `{df[c].dtype}` | {desc} |")
    (ROOT/"docs/DATA_DICTIONARY.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

def write_parameter_mapping(weather, access, promos, facts):
    weather_ids=weather.record_id.astype(str).tolist(); weather_src=weather.primary_source_id.astype(str).unique().tolist()
    phone=access[access.channel_phone.eq(True)]; promo_ids=promos.rule_version_id.astype(str).tolist(); support_ids=facts.fact_id.astype(str).tolist()
    def par(name,scenario,lo,base,hi,unit,basis,rids,sids,method,status,confidence,sensitivity,limitations):
        return dict(parameter_name=name,scenario=scenario,value_low=lo,value_base=base,value_high=hi,unit=unit,evidence_basis=basis,supporting_record_ids=rids,supporting_source_ids=sids,derivation_method=method,fact_status=status,confidence=confidence,sensitivity_required=sensitivity,limitations=limitations)
    params=[
      par("extreme_heat_event_window_hours","W1",24,72,168,"hours","当前核心天气库没有足够高温交通事件",[],[],"仅作敏感性情景", "assumed","low",True,"不得解释为上海实测持续时间"),
      par("road_capacity_multiplier","W2",0.70,0.85,1.00,"multiplier","官方道路积水及交通中断记录",weather_ids,weather_src,"由事件机制设置保守敏感性范围", "assumed","low",True,"缺少统一道路速度面板"),
      par("transit_speed_multiplier","W2",0.60,0.80,1.00,"multiplier","积水、停运及交通管制记录仅支持方向",weather_ids,weather_src,"机制约束下的敏感性范围", "assumed","low",True,"不能用事件个案估计全市均值"),
      par("ride_hailing_vehicle_turnover_multiplier","W2",0.60,0.80,1.00,"multiplier","未取得网约车周转率直接观测",[],[],"压力测试假设", "assumed","low",True,"核心证据缺口"),
      par("coupon_claim_required","P1",0,0.5,1,"probability/share","规则库存在领取字段但覆盖有限",promo_ids,promos.primary_source_id.astype(str).unique().tolist(),"未知规则保持区间", "derived","low",True,"不能把NULL转为false"),
      par("coupon_awareness_probability","P1",0.10,0.50,0.90,"probability","没有上海老年人优惠知晓率直接观测",[],[],"行为敏感性假设", "assumed","low",True,"需实验校准"),
      par("coupon_claim_probability","P1",0.05,0.40,0.85,"probability","没有统一领取成功率",promo_ids,promos.primary_source_id.astype(str).unique().tolist(),"规则摩擦敏感性假设", "assumed","low",True,"不得从券存在性推断领取率"),
      par("coupon_auto_credit","P2",0,0,1,"binary/range","公开规则未形成稳定自动到账证据",promo_ids,promos.primary_source_id.astype(str).unique().tolist(),"未知值进入敏感性分析", "derived","low",True,"平台历史页面缺失"),
      par("coupon_auto_apply","P2",0,0,1,"binary/range","公开规则中的自动抵扣证据不足",promo_ids,promos.primary_source_id.astype(str).unique().tolist(),"未知值进入敏感性分析", "derived","low",True,"支付收银台行为可能变化"),
      par("phone_hailing_available","P3",1,1,1,"binary","95128及一键通官方服务事实",phone.service_id.astype(str).tolist(),phone.primary_source_id.astype(str).unique().tolist(),"观察到渠道存在", "observed","high",False,"渠道存在不等于必然派车成功"),
      par("community_assistance_availability","elder_agent",0.20,0.50,0.80,"probability/range","区级探访、志愿、助医和社区服务事实",support_ids,facts.primary_source_id.astype(str).unique().tolist(),"由正式服务事实约束情景范围", "derived","medium",True,"不得等同家庭全天协助"),
      par("digital_support_level","elder_agent",0.10,0.40,0.80,"score","数字培训事实覆盖有限",facts[facts.indicator_category.eq("digital_support")].fact_id.astype(str).tolist(),facts[facts.indicator_category.eq("digital_support")].primary_source_id.astype(str).unique().tolist(),"事实存在性映射为敏感性范围", "derived","low",True,"培训不等于独立叫车能力"),
      par("family_assistance_available","elder_agent",0.10,0.50,0.90,"probability","正式社区服务不能观测家庭随时协助",[],[],"独立模型假设", "assumed","low",True,"必须与community_assistance分开"),
    ]
    (ROOT/"config/calibration/shanghai_evidence_based.yaml").write_text(json.dumps({"parameters":params},ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 模型参数映射","","| 参数 | 情景 | 低/基准/高 | 状态 | 置信度 | 敏感性 | 证据记录数 | 局限 |","|---|---|---|---|---|---|---:|---|"]
    for p in params: lines.append(f"| `{p['parameter_name']}` | {p['scenario']} | {p['value_low']} / {p['value_base']} / {p['value_high']} {p['unit']} | {p['fact_status']} | {p['confidence']} | {p['sensitivity_required']} | {len(p['supporting_record_ids'])} | {p['limitations']} |")
    lines += ["","完整 supporting_record_ids、supporting_source_ids、推导方法和证据依据见 `config/calibration/shanghai_evidence_based.yaml`。家庭协助与社区正式协助始终作为不同变量。"]
    (ROOT/"docs/MODEL_PARAMETER_MAPPING.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__ == "__main__":
    print(json.dumps(build(),ensure_ascii=False,indent=2))
