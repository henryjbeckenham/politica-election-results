from __future__ import annotations
import argparse, hashlib, json, zipfile
from pathlib import Path
FIXED=(2026,7,28,0,0,0)
def main():
 p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--prefix',required=True); a=p.parse_args()
 files=sorted(x for x in a.source.rglob('*') if x.is_file() and x.resolve()!=a.output.resolve())
 a.output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(a.output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for f in files:
   rel=Path(a.prefix)/f.relative_to(a.source); info=zipfile.ZipInfo(str(rel).replace('\\','/'),FIXED); info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o100644<<16; z.writestr(info,f.read_bytes())
 result={'file_count':len(files),'byte_count':a.output.stat().st_size,'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest(),'output':a.output.name}
 print(json.dumps(result,sort_keys=True))
if __name__=='__main__': main()
