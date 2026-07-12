from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[2]; BASE="https://search.sh.gov.cn"; UA="Shanghai-Mobility-Evidence-Pipeline/1.2 (public research; respectful rate)"
DISTRICTS=["黄浦区","徐汇区","长宁区","静安区","普陀区","虹口区","杨浦区","浦东新区","闵行区","宝山区","嘉定区","金山区","松江区","青浦区","奉贤区","崇明区"]
TERMS=["一键叫车","95128","暖心车站","社区代叫车","助老出行","智慧屏 叫车","医院 一键叫车","出租车候客站 老人","电话叫车 老人","一键通 老人"]
def one(t):
    term,dist,year,page=t
    data={"text":f"{dist} {year} {term}","pageNo":str(page),"newsPageNo":str(page),"pageSize":"20","resourceType":"","channel":"","category1":"","category2":"","category3":"","category4":"","category6":"","category7":"","sortMode":"","searchMode":"","timeRange":"","accurateMode":"","district":dist,"street":"","stealthy":"0","showItemAgency":"false"}
    try:
        r=requests.post(BASE+"/searchResult",data=data,headers={"User-Agent":UA,"X-Requested-With":"XMLHttpRequest"},timeout=15); soup=BeautifulSoup(r.text,"html.parser"); out=[]
        for a in soup.find_all("a",href=True):
            if a["href"].startswith("/detail?") and a.get_text(" ",strip=True): out.append({"dataset_name":"elderly_hailing_access","district":dist,"year":year,"query":data["text"],"page":page,"title":a.get_text(" ",strip=True),"detail_url":BASE+a["href"]})
        return out
    except Exception:return []
def main():
    tasks=[(term,d,y,p) for term in TERMS for d in DISTRICTS for y in range(2018,2027) for p in (1,2)]
    rows=[]; seen=set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs=[ex.submit(one,t) for t in tasks]
        for i,f in enumerate(as_completed(fs),1):
            for x in f.result():
                if x["detail_url"] not in seen:seen.add(x["detail_url"]);rows.append(x)
            if i%200==0:print(f"searched {i}/{len(tasks)}",flush=True)
    p=ROOT/"data/interim/shgov_access_candidates.json";p.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"queries":len(tasks),"candidates":len(rows)},ensure_ascii=False))
if __name__=="__main__":main()
