import time
from pathlib import Path
from .storage import durable_json

def report_stem(payload):
    value = payload.get("system") or payload.get("type") or "report"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(value).lower())
    return safe.strip("-") or "report"

def save_report(app_dir, payload, now=None):
    app_dir=Path(app_dir); data=app_dir/"data"; data.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime("%Y%m%d-%H%M%S",now or time.localtime()); stem="report-%s-%s"%(report_stem(payload),stamp); path=data/(stem+".json"); n=2
    while path.exists(): path=data/("%s-%d.json"%(stem,n)); n+=1
    document=dict(payload); document["app_version"]=(app_dir/"VERSION").read_text().strip() if (app_dir/"VERSION").exists() else "unknown"; document["saved_at"]=time.strftime("%Y-%m-%dT%H:%M:%S%z",now or time.localtime())
    durable_json(path,document); return path

def issue_lines(payload):
    lines=[]
    for r in payload.get("results",[]):
        status=str(r.get("status","UNKNOWN"))
        if status in ("OK","VERIFIED"): continue
        subject=r.get("archive") or r.get("path") or "Unknown item"; detail=[]
        for key,label in (("missing","missing"),("bad","bad checksum/size"),("missing_dependencies","needs"),("chd_missing","missing CHD"),("extras","extras")):
            if r.get(key): detail.append(label+": "+", ".join(r[key][:3]))
        if r.get("layout"): detail.append("layout: "+str(r["layout"]))
        if r.get("expected"): detail.append("expected: "+str(r["expected"]))
        if r.get("member_issues"):
            detail.append("members: "+", ".join(str(item.get("path", "?"))+" ("+str(item.get("status", "?"))+")" for item in r["member_issues"][:3]))
        if r.get("error"): detail.append(str(r["error"]))
        lines.append(status+": "+subject+(" | "+" | ".join(detail) if detail else ""))
    scraping = payload.get("scraping", {}) if isinstance(payload.get("scraping"), dict) else {}
    not_found = scraping.get("not_found_details", [])
    if isinstance(not_found, list):
        for path in sorted(str(value) for value in not_found):
            lines.append("NO_MATCH: %s" % path)
    failures = scraping.get("lookup_failure_details", {})
    if isinstance(failures, dict):
        for path, error in sorted(failures.items()):
            lines.append("LOOKUP_FAILED: %s | %s" % (path, error))
    pending = scraping.get("media_pending_details", {})
    if isinstance(pending, dict):
        for path in sorted(pending):
            lines.append("ARTWORK_PENDING: %s" % path)
    return lines
