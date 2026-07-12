"""Collect candidates from Shanghai's official smart-search endpoint."""
from __future__ import annotations
import json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[2]
BASE="https://search.sh.gov.cn"
UA="Shanghai-Mobility-Evidence-Pipeline/1.2 (public research; respectful rate)"
DISTRICTS=["黄浦区","徐汇区","长宁区","静安区","普陀区","虹口区","杨浦区","浦东新区","闵行区","宝山区","嘉定区","金山区","松江区","青浦区","奉贤区","崇明区"]
QUERIES={
 "weather_transport_disruptions":"暴雨 交通",
 "elderly_hailing_access":"老人 叫车",
 "ride_hailing_promotion_rules":"网约车 优惠",
 "subdistrict_elder_support_facts":"街道 老人 服务",
}

def search(task):
    dataset,district,year,page=task
    q=QUERIES[dataset]
    data={"text":f"{district} {year} {q}","pageNo":str(page),"newsPageNo":str(page),"pageSize":"20","resourceType":"","channel":"","category1":"","category2":"","category3":"","category4":"","category6":"","category7":"","sortMode":"","searchMode":"","timeRange":"","accurateMode":"","district":district,"street":"","stealthy":"0","showItemAgency":"false"}
    try:
        r=requests.post(BASE+"/searchResult",data=data,headers={"User-Agent":UA,"X-Requested-With":"XMLHttpRequest"},timeout=15)
        soup=BeautifulSoup(r.text,"html.parser")
        out=[]
        for a in soup.find_all("a",href=True):
            href=a["href"]
            if not href.startswith("/detail?"): continue
            title=a.get_text(" ",strip=True)
            if not title: continue
            out.append({"dataset_name":dataset,"district":district,"year":year,"query":data["text"],"page":page,"title":title,"detail_url":BASE+href})
        return out
    except Exception:
        return []

def main():
    tasks=[(d,dist,y,p) for d in QUERIES for dist in DISTRICTS for y in range(2018,2027) for p in range(1,4)]
    rows=[]; seen=set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs=[ex.submit(search,t) for t in tasks]
        for i,f in enumerate(as_completed(fs),1):
            for x in f.result():
                if x["detail_url"] in seen: continue
                seen.add(x["detail_url"]); rows.append(x)
            if i%100==0: print(f"searched {i}/{len(tasks)}",flush=True)
    p=ROOT/"data/interim/shgov_candidates.json"; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"queries":len(tasks),"candidates":len(rows),"path":str(p)},ensure_ascii=False))

if __name__=="__main__": main()
