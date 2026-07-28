from __future__ import annotations
import argparse,gzip,hashlib,json,math,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from typing import Any,Iterable
BASE="https://api.prod.legislation.gov.au"; APPROVED_HOST="api.prod.legislation.gov.au"; BOUNDARY="F2021L01067"; PAGE_SIZE=100; TIMEOUT=60; MAX_ATTEMPTS=4; MAX_BYTES=8*1024*1024; DELAY=.35
VERSION_FIELDS=["titleId","start","retrospectiveStart","registerId","compilationNumber"]; TEXT_FIELDS=["type","titleId","titleName","provisions"]; RULESET="stage5-v0.6-closure-1"
def canonical(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def sha_bytes(d:bytes)->str:return hashlib.sha256(d).hexdigest()
def sha_file(p:Path)->str:
 h=hashlib.sha256()
 with p.open("rb") as f:
  for c in iter(lambda:f.read(1024*1024),b""):h.update(c)
 return h.hexdigest()
def write_json(p:Path,d:Any)->None:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,indent=2,sort_keys=True)+"\n",encoding="utf-8")
def gzip_lines(p:Path,lines:Iterable[str])->None:
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("wb") as raw:
  with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as z:
   for line in lines:z.write((line+"\n").encode())
def read_gzip_lines(p:Path)->list[str]:
 with gzip.open(p,"rt",encoding="utf-8") as f:return [x.strip() for x in f if x.strip()]
def request(out:Path,label:str,path:str,params:dict[str,Any]|None=None)->tuple[bytes,dict[str,str],str]:
 q=urllib.parse.urlencode(params or {});url=BASE+path+(("?"+q) if q else "");u=urllib.parse.urlparse(url)
 if u.scheme!="https" or u.hostname!=APPROVED_HOST:raise RuntimeError(f"unapproved request {url}")
 req=urllib.request.Request(url,method="GET",headers={"Accept":"application/json","User-Agent":"Politica-Stage5-v0.6-closure/1.0"});body=b"";headers={};status=None;error=None;started=time.monotonic();attempts=0
 for attempt in range(MAX_ATTEMPTS):
  attempts=attempt+1
  try:
   with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
    body=r.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES:raise RuntimeError("response size ceiling exceeded")
    headers=dict(r.headers.items());status=int(r.status)
   if status!=200:raise RuntimeError(f"HTTP {status}")
   error=None;break
  except urllib.error.HTTPError as e:
   status=int(e.code);headers=dict(e.headers.items()) if e.headers else {};body=e.read(MAX_BYTES+1);error=f"HTTP {e.code}: {e.reason}"
   if e.code not in {408,429,500,502,503,504}:break
  except Exception as e:error=f"{type(e).__name__}: {e}"
  if attempt+1<MAX_ATTEMPTS:time.sleep(2**attempt)
 digest=sha_bytes(body);raw=out/"raw";raw.mkdir(parents=True,exist_ok=True);bp=raw/f"{label}_{digest[:12]}.body.gz"
 with bp.open("wb") as f:
  with gzip.GzipFile(filename="",mode="wb",fileobj=f,mtime=0) as z:z.write(body)
 hp=raw/f"{label}_{digest[:12]}.headers.json";write_json(hp,headers);row={"label":label,"method":"GET","request_body":None,"requested_url":url,"status":status,"attempts":attempts,"duration_ms":int((time.monotonic()-started)*1000),"response_sha256":digest,"byte_count":len(body),"body_file":str(bp.relative_to(out)),"headers_file":str(hp.relative_to(out)),"error":error}
 with (out/"request_manifest.jsonl").open("a",encoding="utf-8") as f:f.write(json.dumps(row,sort_keys=True)+"\n")
 if error:raise RuntimeError(error)
 time.sleep(DELAY);return body,headers,url
def count_request(out:Path,label:str,collection:str,filter_expr:str|None=None)->tuple[int,str|None]:
 body,h,_=request(out,label,f"/v1/{collection}/$count",{"$filter":filter_expr} if filter_expr else None);return int(body.decode().strip()),h.get("X-Frl-Version")
def page_request(out:Path,label:str,collection:str,fields:list[str],order:list[str],skip:int,filter_expr:str|None=None)->list[dict[str,Any]]:
 p={"$select":",".join(fields),"$orderby":",".join(order),"$top":PAGE_SIZE,"$skip":skip}
 if filter_expr:p["$filter"]=filter_expr
 body,_,_=request(out,label,f"/v1/{collection}",p);v=json.loads(body.decode()).get("value")
 if not isinstance(v,list):raise RuntimeError(f"invalid {collection} page")
 return v
def selected(r:dict[str,Any],fields:list[str])->dict[str,Any]:return {f:r.get(f) for f in fields}
def obs_id(r:dict[str,Any],fields:list[str])->str:return sha_bytes(canonical(selected(r,fields)))
def load_seed_versions(seed:Path)->tuple[list[dict[str,Any]],dict[str,Any]]:
 c=seed/"collections/Versions";cp=json.loads((c/"checkpoint.json").read_text())
 if cp.get("completed_page")!=3297 or cp.get("rows_seen")!=329800:raise RuntimeError("retained Versions checkpoint is not the governed seed")
 pages=sorted((c/"raw").glob("page_*.body.gz"))
 if len(pages)!=3298:raise RuntimeError("retained Versions page count mismatch")
 rows=[]
 for p in pages:
  with gzip.open(p,"rb") as f:v=json.load(f).get("value")
  if not isinstance(v,list):raise RuntimeError(f"invalid retained page {p.name}")
  rows.extend(selected(r,VERSION_FIELDS) for r in v)
 if len(rows)!=329800:raise RuntimeError("retained Versions row count mismatch")
 return rows,cp
def load_titles(seed:Path)->list[str]:
 c=seed/"collections/Titles";r=json.loads((c/"source_enumeration_result.json").read_text());ids=read_gzip_lines(c/"source_external_identifier_set.txt.gz")
 if r.get("status")!="passed" or len(ids)!=r.get("expected_count") or len(ids)!=len(set(ids)):raise RuntimeError("retained Titles set is not complete")
 return ids
def load_plan(p:Path)->dict[str,Any]:
 plan=json.loads(p.read_text());checks=[plan.get("status")=="passed",plan.get("null_title_id_count")==0,plan.get("document_count")==plan.get("leaf_document_count_sum"),plan.get("document_count")==plan.get("root_document_count_sum"),len(plan.get("leaves",[]))==plan.get("leaf_partition_count")]
 if not all(checks):raise RuntimeError("Document partition plan is not accepted")
 return plan
def make_differences(out:Path,families:dict[str,set[str]],canonical_by_family:dict[str,set[str]])->dict[str,int]:
 counts={"source_raw_canonical_match":0,"source_raw_only_candidate":0,"canonical_only_apparent_absence":0}
 def rows():
  for family in sorted(families):
   for identifier in sorted(families[family]):
    canonical_ids=canonical_by_family.get(family,set());classification="source_raw_canonical_match" if identifier in canonical_ids else "source_raw_only_candidate";counts[classification]+=1
    yield json.dumps({"source_system":"Federal Register of Legislation","source_namespace":family,"record_family":family,"external_identifier":identifier,"classification":classification,"raw_evidence_present":True,"canonical_present":identifier in canonical_ids,"automatic_remediation_permitted":False,"recommended_action":"retain as ingestion candidate" if classification.endswith("candidate") else "none","ruleset_version":RULESET},sort_keys=True)
  for family in sorted(canonical_by_family):
   for identifier in sorted(canonical_by_family[family]-families.get(family,set())):
    counts["canonical_only_apparent_absence"]+=1;yield json.dumps({"source_system":"Federal Register of Legislation","source_namespace":family,"record_family":family,"external_identifier":identifier,"classification":"canonical_only_apparent_absence","automatic_remediation_permitted":False,"recommended_action":"retain history; repeated completed evidence required","ruleset_version":RULESET},sort_keys=True)
 gzip_lines(out/"difference_report.jsonl.gz",rows());return counts
def live(a:argparse.Namespace)->None:
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);scope=json.loads(a.scope.read_text());plan=load_plan(a.document_plan);seed=a.seed.resolve();titles=load_titles(seed);seed_versions,checkpoint=load_seed_versions(seed);cv={x.strip() for x in a.canonical_ids.read_text().splitlines() if x.strip()};expected={"F2016L01916","F2026C00596","O-000882"}
 if cv!=expected:raise RuntimeError("accepted canonical identifier set mismatch")
 canonical_by_family={"Title":{"F2016L01916"},"Version":{"F2026C00596"},"Department":{"O-000882"}};counts={};releases=set()
 for collection in ["Titles","Versions","Documents","Departments","TextApplies"]:
  count,release=count_request(out,f"count_{collection.lower()}",collection);counts[collection]=count
  if release:releases.add(release)
 lt,rel=count_request(out,"versions_lt_boundary","Versions",f"titleId lt '{BOUNDARY}'");releases.add(rel) if rel else None
 eq,rel=count_request(out,"versions_eq_boundary","Versions",f"titleId eq '{BOUNDARY}'");releases.add(rel) if rel else None
 gt,rel=count_request(out,"versions_gt_boundary","Versions",f"titleId gt '{BOUNDARY}'");releases.add(rel) if rel else None
 if len(releases)!=1:raise RuntimeError("source release changed during closure count capture")
 seed_lt=[r for r in seed_versions if r.get("titleId")<BOUNDARY];seed_eq=[r for r in seed_versions if r.get("titleId")==BOUNDARY]
 if len(seed_lt)!=lt or len(seed_eq)!=1 or counts["Versions"]!=lt+eq+gt:raise RuntimeError("retained Versions prefix no longer reconciles to current key ranges")
 boundary=[selected(r,VERSION_FIELDS) for r in page_request(out,"versions_boundary_rows","Versions",VERSION_FIELDS,VERSION_FIELDS,0,f"titleId eq '{BOUNDARY}'")];seed_obs={obs_id(r,VERSION_FIELDS) for r in seed_versions};missing=[r for r in boundary if obs_id(r,VERSION_FIELDS) not in seed_obs]
 if len(boundary)!=eq or len(missing)!=eq-len(seed_eq):raise RuntimeError("Versions boundary overlap is not deterministic")
 remainder=[]
 for page in range(math.ceil(gt/PAGE_SIZE)):
  vals=page_request(out,f"versions_after_{page:04d}","Versions",VERSION_FIELDS,VERSION_FIELDS,page*PAGE_SIZE,f"titleId gt '{BOUNDARY}'");expected_count=min(PAGE_SIZE,gt-page*PAGE_SIZE)
  if len(vals)!=expected_count:raise RuntimeError(f"Versions remainder page {page} count mismatch")
  remainder.extend(selected(r,VERSION_FIELDS) for r in vals)
 all_versions=seed_versions+missing+remainder
 if len(all_versions)!=counts["Versions"]:raise RuntimeError("complete Versions observation count mismatch")
 version_obs=[obs_id(r,VERSION_FIELDS) for r in all_versions]
 if len(version_obs)!=len(set(version_obs)):raise RuntimeError("duplicate Version source observation")
 version_ids=[r["registerId"] for r in all_versions if r.get("registerId")]
 if len(version_ids)!=len(set(version_ids)):raise RuntimeError("duplicate authoritative Version registerId")
 null_version_count=len(all_versions)-len(version_ids);departments=page_request(out,"departments_all","Departments",["id"],["id"],0);department_ids=[r.get("id") for r in departments if r.get("id")]
 if len(department_ids)!=counts["Departments"] or len(department_ids)!=len(set(department_ids)):raise RuntimeError("Department identifier set mismatch")
 text_rows=[]
 for page in range(math.ceil(counts["TextApplies"]/PAGE_SIZE)):
  vals=page_request(out,f"textapplies_{page:04d}","TextApplies",TEXT_FIELDS,["titleId","type","titleName"],page*PAGE_SIZE);expected_count=min(PAGE_SIZE,counts["TextApplies"]-page*PAGE_SIZE)
  if len(vals)!=expected_count:raise RuntimeError("TextApplies page count mismatch")
  text_rows.extend(selected(r,TEXT_FIELDS) for r in vals)
 text_obs=[obs_id(r,TEXT_FIELDS) for r in text_rows];text_duplicate_count=len(text_obs)-len(set(text_obs))
 if counts["Titles"]!=len(titles) or plan["title_identifier_count"]!=len(titles):raise RuntimeError("Titles count drift blocks closure")
 if counts["Documents"]!=plan["document_count"]:raise RuntimeError("Documents count drift blocks closure")
 families={"Title":set(titles),"Version":set(version_ids),"Department":set(department_ids)};differences=make_differences(out,families,canonical_by_family)
 if differences["canonical_only_apparent_absence"]!=0 or differences["source_raw_canonical_match"]!=3:raise RuntimeError("canonical representative identifiers did not reconcile")
 gzip_lines(out/"sets/titles.txt.gz",sorted(families["Title"]));gzip_lines(out/"sets/versions.txt.gz",sorted(families["Version"]));gzip_lines(out/"sets/departments.txt.gz",sorted(families["Department"]));gzip_lines(out/"sets/version_observations.txt.gz",version_obs);gzip_lines(out/"sets/textapplies_observations.txt.gz",text_obs)
 result={"status":"passed","ruleset_version":RULESET,"scope_decision":scope["decision_id"],"source_release":next(iter(releases)),"counts":counts,"identifier_sets":{k:len(v) for k,v in families.items()},"dependent_observations":{"Versions_without_registerId":null_version_count,"Documents":{"count":counts["Documents"],"partition_count":plan["leaf_partition_count"],"planned_pages":plan["planned_page_count"],"coverage":"complete parent-prefix count plan"},"TextApplies":{"count":len(text_obs),"unique_compound_observations":len(set(text_obs)),"duplicate_compound_observations":text_duplicate_count}},"differences":differences,"documents_partition_plan_sha256":sha_file(a.document_plan),"titles_seed_sha256":sha_file(seed/"collections/Titles/source_external_identifier_set.txt.gz"),"versions_seed_checkpoint":checkpoint,"final_watermark_committed":True,"watermark_meaning":"Stage 5 validation coverage completed; not a production source-change watermark","disappearance_threshold_advanced":False,"production_systems_addressed":[],"stage6_implementation_started":False}
 result["semantic_result_sha256"]=sha_bytes(canonical({k:result[k] for k in ["ruleset_version","scope_decision","counts","identifier_sets","dependent_observations","differences","documents_partition_plan_sha256"]}));write_json(out/"closure_reconciliation_result.json",result);write_json(out/"closure_scope.json",scope);print(json.dumps(result,indent=2,sort_keys=True))
def replay(a:argparse.Namespace)->None:
 root=a.output.resolve();result=json.loads((root/"closure_reconciliation_result.json").read_text());families={"Title":set(read_gzip_lines(root/"sets/titles.txt.gz")),"Version":set(read_gzip_lines(root/"sets/versions.txt.gz")),"Department":set(read_gzip_lines(root/"sets/departments.txt.gz"))};cv={x.strip() for x in a.canonical_ids.read_text().splitlines() if x.strip()}
 if cv!={"F2016L01916","F2026C00596","O-000882"}:raise RuntimeError("accepted canonical identifier set mismatch")
 cbf={"Title":{"F2016L01916"},"Version":{"F2026C00596"},"Department":{"O-000882"}};rd=root/"replay";rd.mkdir(exist_ok=True);differences=make_differences(rd,families,cbf);check={"status":"passed" if differences==result["differences"] else "failed","differences":differences,"source_identifier_counts":{k:len(v) for k,v in families.items()},"source_contacted":False,"original_semantic_result_sha256":result["semantic_result_sha256"]};write_json(rd/"replay_result.json",check)
 if check["status"]!="passed":raise SystemExit(1)
def main()->None:
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest="cmd",required=True);q=sub.add_parser("live")
 for n in ["seed","document_plan","scope","canonical_ids","output"]:q.add_argument("--"+n.replace("_","-"),type=Path,required=True)
 q=sub.add_parser("replay");q.add_argument("--output",type=Path,required=True);q.add_argument("--canonical-ids",type=Path,required=True);a=p.parse_args();live(a) if a.cmd=="live" else replay(a)
if __name__=="__main__":main()
