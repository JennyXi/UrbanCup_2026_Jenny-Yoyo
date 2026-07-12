"""Open official search candidates, read page text, and emit short evidence records."""
from __future__ import annotations
import hashlib, json, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[2]
UA="Shanghai-Mobility-Evidence-Pipeline/1.2 (public research; respectful rate)"
KEYS={
 "weather_transport_disruptions":r"暴雨|暴雨|台风|高温|积水|下立交|道路|公交|地铁|轨道|停运|封闭|恢复|改道|限行|交通",
 "elderly_hailing_access":r"一键叫车|一键通|95128|出租车|智慧屏|代叫|打车|候客|自助服务机|助老出行|电话叫车|叫车",
 "ride_hailing_promotion_rules":r"网约车|打车|优惠|优惠券|补贴|减免|立减|折扣|领取|抵扣|满.*减|活动规则",
 "subdistrict_elder_support_facts":r"独居|纯老|老伙伴|志愿|数字助老|培训|助医|陪诊|社区叫车|养老|高龄|老人|周末",
}
CAPS={"weather_transport_disruptions":1000,"elderly_hailing_access":1800,"ride_hailing_promotion_rules":1400,"subdistrict_elder_support_facts":1800}

def clean_sentences(text):
    text=re.sub(r"\s+","",text)
    return [x.strip("，。；：、") for x in re.split(r"[。！？；\n]",text) if 12<=len(x)<=180]

def fetch(c):
    try:
        r=requests.get(c["detail_url"],headers={"User-Agent":UA},timeout=15,allow_redirects=True)
        if r.status_code>=400: return None
        soup=BeautifulSoup(r.content,"html.parser")
        for x in soup(["script","style","noscript","svg","header","footer","nav"]): x.decompose()
        title=soup.title.get_text(" ",strip=True) if soup.title else c["title"]
        text=soup.get_text("\n",strip=True)
        pattern=KEYS[c["dataset_name"]]
        hits=[]; seen=set()
        for sent in clean_sentences(text):
            if not re.search(pattern,sent): continue
            q=sent[:100]
            k=re.sub(r"\d+","#",q)
            if k in seen: continue
            seen.add(k); hits.append(q)
            if len(hits)>=(200 if c["dataset_name"]=="elderly_hailing_access" else 6): break
        if not hits: return None
        h=hashlib.sha256(r.content).hexdigest(); ext="pdf" if "pdf" in r.headers.get("content-type","") or r.url.lower().endswith(".pdf") else "html"
        source_id="src_"+hashlib.sha1(r.url.encode()).hexdigest()[:12]
        return {"dataset_name":c["dataset_name"],"district":c["district"],"year":c["year"],"query":c["query"],"search_title":c["title"],"page_title":title,"source_url":r.url,"source_id":source_id,"http_status":r.status_code,"page_hash":h,"content_type":ext,"evidence_quotes":hits}
    except Exception:
        return None

def main():
    candidates=json.loads((ROOT/"data/interim/shgov_candidates.json").read_text(encoding="utf-8"))
    selected=[]; counts={k:0 for k in CAPS}
    # Preserve broad district/year coverage while avoiding unnecessary duplicate pages.
    for c in candidates:
        d=c["dataset_name"]
        if counts[d]>=CAPS[d]: continue
        counts[d]+=1; selected.append(c)
    out=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs=[ex.submit(fetch,c) for c in selected]
        for i,f in enumerate(as_completed(fs),1):
            x=f.result()
            if x: out.append(x)
            if i%250==0: print(f"opened {i}/{len(selected)}; evidence pages {len(out)}",flush=True)
    p=ROOT/"data/interim/shgov_evidence.json"; p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"opened":len(selected),"evidence_pages":len(out),"by_dataset":{d:sum(x["dataset_name"]==d for x in out) for d in CAPS},"path":str(p)},ensure_ascii=False))
if __name__=="__main__": main()
