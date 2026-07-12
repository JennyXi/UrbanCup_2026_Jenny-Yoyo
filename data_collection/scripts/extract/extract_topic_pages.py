from __future__ import annotations
import json,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"/"extract"))
from extract_shgov_pages import fetch,ROOT
def main():
    base=json.loads((ROOT/"data/interim/shgov_topic_candidates.json").read_text(encoding="utf-8")); old=json.loads((ROOT/"data/interim/shgov_evidence.json").read_text(encoding="utf-8")); seen=set(x["source_url"] for x in old); selected=[]; caps={"weather_transport_disruptions":4000,"subdistrict_elder_support_facts":4000}
    for c in base:
        d=c["dataset_name"]
        if d not in caps or caps[d]<=0 or c["detail_url"] in seen:continue
        seen.add(c["detail_url"]);selected.append(c);caps[d]-=1
    out=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs=[ex.submit(fetch,c) for c in selected]
        for i,f in enumerate(as_completed(fs),1):
            x=f.result()
            if x:out.append(x)
            if i%250==0:print(f"opened {i}/{len(selected)}; evidence pages {len(out)}",flush=True)
    p=ROOT/"data/interim/shgov_topic_evidence.json";p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"opened":len(selected),"evidence_pages":len(out)},ensure_ascii=False))
if __name__=="__main__":main()
