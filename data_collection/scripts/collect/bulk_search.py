"""Search and snapshot public pages for evidence expansion.

This collector stores only search candidates here; extraction happens after the page is
opened and text is read. It is intentionally conservative about domains and rate.
"""
from __future__ import annotations
import hashlib, json, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[2]
UA="Shanghai-Mobility-Evidence-Pipeline/1.1 (public research; respectful rate)"
DOMAINS=("shanghai.gov.cn","sh.gov.cn","shmetro.com","jtw.sh.gov.cn","mzj.sh.gov.cn")
DISTRICTS=["黄浦区","徐汇区","长宁区","静安区","普陀区","虹口区","杨浦区","浦东新区","闵行区","宝山区","嘉定区","金山区","松江区","青浦区","奉贤区","崇明区"]
TERMS={
 "weather_transport_disruptions":["暴雨 道路积水 交通","台风 公交 调整","高温 公交 运营","地铁 出入口 关闭","下立交 封闭 恢复"],
 "elderly_hailing_access":["老人 一键叫车","95128","暖心车站","社区 代叫车","助老出行","智慧屏 叫车","医院 一键叫车"],
 "ride_hailing_promotion_rules":["打车 优惠 活动规则","网约车 优惠券 领取","老人 打车优惠","医院 打车补贴","自动抵扣 打车","申程出行 优惠"],
 "subdistrict_elder_support_facts":["街道 独居老人 老伙伴","街道 数字助老 培训","街道 志愿者 结对 老人","街道 助医 陪诊","街道 社区叫车","街道 周末 养老服务"]}

def google(q, start=0):
    u="https://www.google.com/search?q="+quote(q)+f"&num=10&start={start}&hl=zh-CN"
    r=requests.get(u,headers={"User-Agent":UA},timeout=8)
    soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for block in soup.select("div.MjjYud, div.g"):
        a=block.select_one("a[href]")
        if not a: continue
        href=a.get("href","")
        if href.startswith("/url?q="): href=href.split("/url?q=",1)[1].split("&",1)[0]
        if not href.startswith("http") or not any(d in urlparse(href).netloc for d in DOMAINS): continue
        title=(block.select_one("h3") or a).get_text(" ",strip=True)
        out.append({"url":href,"title":title,"query":q,"rank":len(out)+1})
    return out

def main():
    rows=[]; seen=set(); tasks=[]
    for dataset,terms in TERMS.items():
        for district in DISTRICTS:
            for year in range(2018,2027):
                q=f"site:shanghai.gov.cn {district} {year} ("+" OR ".join(terms)+")"
                tasks.append((dataset,district,year,q))
    def run(task):
        try: return task,google(task[3])
        except Exception: return task,[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures=[ex.submit(run,t) for t in tasks]
        for i,fut in enumerate(as_completed(futures),1):
            (dataset,district,year,q),results=fut.result()
            for item in results:
                key=(item["url"],dataset)
                if key in seen: continue
                seen.add(key); item.update(dataset_name=dataset,district=district,year=year,search_engine="Google",searched_at="2026-07-11")
                rows.append(item)
            if i%25==0: print(f"searched {i}/{len(tasks)}",flush=True)
    p=ROOT/"data/interim/bulk_search_candidates.json"; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"candidates":len(rows),"path":str(p)},ensure_ascii=False))
if __name__=="__main__": main()
