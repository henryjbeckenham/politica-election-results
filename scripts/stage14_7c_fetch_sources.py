#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, time, urllib.parse, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

EVENT='13745'
BASE=f'https://results.aec.gov.au/{EVENT}/Website/Downloads'
OUT=Path(os.environ.get('STAGE147C_OUTPUT','stage14_7c_raw')).resolve()
FILES=[
'HouseCandidatesDownload-13745.csv','HouseMembersElectedDownload-13745.csv','HouseFirstPrefsByCandidateByVoteTypeDownload-13745.csv',
'HouseStateFirstPrefsByPollingPlaceDownload-13745-NSW.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-VIC.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-QLD.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-WA.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-SA.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-TAS.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-ACT.csv','HouseStateFirstPrefsByPollingPlaceDownload-13745-NT.csv',
'HouseTcpByCandidateByVoteTypeDownload-13745.csv','HouseTcpByCandidateByPollingPlaceDownload-13745.csv','HouseTppByStateDownload-13745.csv','HouseTppByDivisionDownload-13745.csv','HouseTppByPollingPlaceDownload-13745.csv','HouseDopByDivisionDownload-13745.csv','HouseInformalByDivisionDownload-13745.csv','HouseTurnoutByDivisionDownload-13745.csv','HouseVotesCountedByDivisionDownload-13745.csv',
'SenateCandidatesDownload-13745.csv','SenateGroupVotingTicketsDownload-13745.csv','SenateSenatorsElectedDownload-13745.csv','SenateFirstPrefsByStateByVoteTypeDownload-13745.csv','SenateFirstPrefsByDivisionByVoteTypeDownload-13745.csv','SenateFirstPrefsByGroupByVoteTypeDownload-13745.csv','SenateFirstPrefsByStateByGroupByVoteTypeDownload-13745.csv','SenateUseOfGvtByStateDownload-13745.csv','SenateUseOfGvtByGroupDownload-13745.csv','SenateDopDownload-13745.zip',
'SenateStateBtlDownload-13745-NSW.zip','SenateStateBtlDownload-13745-VIC.zip','SenateStateBtlDownload-13745-QLD.zip','SenateStateBtlDownload-13745-WA.zip','SenateStateBtlDownload-13745-SA.zip','SenateStateBtlDownload-13745-TAS.zip','SenateStateBtlDownload-13745-ACT.zip','SenateStateBtlDownload-13745-NT.zip',
'SenateInformalByStateDownload-13745.csv','SenateInformalByDivisionDownload-13745.csv','SenateTurnoutByStateDownload-13745.csv','SenateTurnoutByDivisionDownload-13745.csv','SenateVotesCountedByStateDownload-13745.csv','SenateVotesCountedByDivisionDownload-13745.csv','GeneralPollingPlacesDownload-13745.csv','GeneralEnrolmentByStateDownload-13745.csv','GeneralEnrolmentByDivisionDownload-13745.csv']

def now(): return datetime.now(timezone.utc).isoformat()
def sha(p):
 d=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): d.update(b)
 return d.hexdigest()
def html(p):
 s=p.read_bytes()[:4096].lstrip().lower()
 return s.startswith(b'<html') or s.startswith(b'<!doctype html') or b'<html' in s[:1024]
def safe(name):
 q=PurePosixPath(name)
 return bool(name) and not q.is_absolute() and '..' not in q.parts and '\\' not in name

def curl(url,dest,referer):
 tmp=dest.with_suffix(dest.suffix+'.part'); tmp.unlink(missing_ok=True)
 cmd=['curl','--fail','--location','--silent','--show-error','--http1.1','--connect-timeout','30','--max-time','900','--retry','2','--retry-all-errors','--user-agent','Mozilla/5.0 (compatible; PoliticaElectionResults/1.0)','--referer',referer,'--header','Accept-Encoding: identity','-o',str(tmp),'-w','%{http_code}\n%{url_effective}\n%{content_type}\n%{size_download}\n',url]
 r=subprocess.run(cmd,text=True,capture_output=True)
 meta={'url':url,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 if r.returncode or not tmp.exists() or not tmp.stat().st_size or html(tmp): tmp.unlink(missing_ok=True); return False,meta
 tmp.replace(dest); return True,meta

def snapshots(url):
 q='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode({'url':url,'output':'json','fl':'timestamp,original,statuscode,mimetype,digest,length','filter':['statuscode:200','collapse:digest'],'from':'2007','to':'2026','limit':'20'},doseq=True)
 try:
  req=urllib.request.Request(q,headers={'User-Agent':'PoliticaElectionResults/1.0'})
  with urllib.request.urlopen(req,timeout=60) as r: data=json.load(r)
  if not isinstance(data,list) or len(data)<2:return []
  h=data[0]; rows=[dict(zip(h,x)) for x in data[1:] if len(x)==len(h)]
  return sorted(rows,key=lambda x:x.get('timestamp',''),reverse=True)
 except Exception:return []

def acquire(name):
 dest=OUT/'official_sources'/name; dest.parent.mkdir(parents=True,exist_ok=True)
 official=f'{BASE}/{name}'; ref=f'https://results.aec.gov.au/{EVENT}/Website/Default.htm'; attempts=[]
 for method,url in [('aec_https',official),('aec_http',official.replace('https://','http://',1))]:
  ok,meta=curl(url,dest,ref); attempts.append({'method':method,**meta})
  if ok:return dest,method,url,attempts
 for snap in snapshots(official)[:2]:
  u=f"https://web.archive.org/web/{snap['timestamp']}id_/{snap.get('original',official)}"
  ok,meta=curl(u,dest,'https://web.archive.org/'); attempts.append({'method':'wayback','snapshot':snap,**meta})
  if ok:return dest,'wayback',u,attempts
 return None,None,None,attempts

def verify(name,p):
 rec={'filename':name,'official_url':f'{BASE}/{name}','retrieved_at':now(),'size_bytes':p.stat().st_size,'sha256':sha(p),'event_id_in_filename':f'-{EVENT}' in name,'html_rejected':not html(p)}
 if name.endswith('.csv'):
  s=p.read_bytes()[:1048576]
  enc=None
  for e in ('utf-8-sig','utf-8','cp1252','latin-1'):
   try:s.decode(e);enc=e;break
   except UnicodeDecodeError:pass
  rec.update({'format':'csv','encoding_detected':enc,'line_count':sum(1 for _ in p.open('rb')),'verification_status':'PASS' if enc and p.stat().st_size>0 else 'FAIL'})
 else:
  try:
   with zipfile.ZipFile(p) as z:
    infos=z.infolist(); names=[i.filename for i in infos]; bad=z.testzip(); unsafe=[n for n in names if not safe(n)]; dup=sorted({n for n in names if names.count(n)>1}); syms=[i.filename for i in infos if ((i.external_attr>>16)&0o170000)==0o120000]
    members=[{'name':i.filename,'size_bytes':i.file_size,'compressed_size_bytes':i.compress_size,'crc32':f'{i.CRC:08x}'} for i in infos if not i.is_dir()]
   ok=bool(members) and not bad and not unsafe and not dup and not syms
   rec.update({'format':'zip','member_count':len(members),'members':members,'crc_failure':bad,'unsafe_members':unsafe,'duplicate_members':dup,'symlinks':syms,'verification_status':'PASS' if ok else 'FAIL'})
  except Exception as e:rec.update({'format':'zip','verification_status':'FAIL','error':str(e)})
 if not rec['event_id_in_filename'] or not rec['html_rejected']:rec['verification_status']='FAIL'
 return rec

def main():
 if OUT.exists():shutil.rmtree(OUT)
 (OUT/'official_sources').mkdir(parents=True); records=[]
 for i,name in enumerate(FILES,1):
  print(f'[{i:02d}/47] {name}',flush=True); p,method,url,attempts=acquire(name)
  if p:
   r=verify(name,p);r.update({'retrieval_status':'retrieved','retrieval_method':method,'retrieved_url':url,'attempts':attempts})
  else:r={'filename':name,'official_url':f'{BASE}/{name}','retrieval_status':'blocked','verification_status':'BLOCKED','attempts':attempts}
  records.append(r);print(f"  {r['retrieval_status']} / {r['verification_status']}",flush=True)
 passed=sum(r['verification_status']=='PASS' for r in records)
 manifest={'schema_version':1,'stage':'14.7C','event_id':EVENT,'generated_at':now(),'required':47,'retrieved':sum(r['retrieval_status']=='retrieved' for r in records),'verified':passed,'status':'PASS' if passed==47 else 'COMPLETE_WITH_RECORDED_BLOCKERS','records':records}
 (OUT/'STAGE_14_7C_RAW_ACQUISITION_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
 (OUT/'STAGE_14_7C_RAW_SOURCE_CHECKSUMS.sha256').write_text(''.join(f"{r['sha256']}  official_sources/{r['filename']}\n" for r in sorted(records,key=lambda x:x['filename']) if r.get('sha256')))
 print(json.dumps({k:manifest[k] for k in ('status','required','retrieved','verified')},indent=2))
 return 0
if __name__=='__main__':raise SystemExit(main())
