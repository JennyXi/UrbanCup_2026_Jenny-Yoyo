from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"/"extract"))
from extract_shgov_pages import fetch, ROOT

def main():
    base=json.loads((ROOT/"data/interim/shgov_candidates.json").read_text(encoding="utf-8"))
    extra=json.loads((ROOT/"data/interim/shgov_access_candidates.json").read_text(encoding="utf-8"))
    seen=set(); selected=[]
    pool=base+extra
    # Prefer titles that name an access channel; generic taxi pages are retained only as fallback.
    import re
    pool=sorted(pool,key=lambda c: 0 if re.search(r"一键叫车|95128|暖心车站|智慧屏|助老|代叫|候客站|电话叫车",c.get("title", "")) else 1)
    for c in pool:
        if c["dataset_name"]!="elderly_hailing_access" or c["detail_url"] in seen: continue
        seen.add(c["detail_url"]); selected.append(c)
        if len(selected)>=300: break
    out=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs=[ex.submit(fetch,c) for c in selected]
        for i,f in enumerate(as_completed(fs),1):
            x=f.result()
            if x: out.append(x)
            if i%250==0: print(f"opened {i}/{len(selected)}; evidence pages {len(out)}",flush=True)
    p=ROOT/"data/interim/shgov_access_evidence.json"; p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"opened":len(selected),"evidence_pages":len(out)},ensure_ascii=False))
if __name__=="__main__":main()
