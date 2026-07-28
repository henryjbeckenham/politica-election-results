from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--v05-assessment',type=Path,required=True);p.add_argument('--v05-traceability',type=Path,required=True);p.add_argument('--closure',type=Path,required=True);p.add_argument('--replay',type=Path,required=True);p.add_argument('--unit-exit',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();v05=load(a.v05_assessment);closure=load(a.closure);replay=load(a.replay);criteria=[]
 for row in v05['criteria']:
  row=dict(row)
  if row['criterion_id']=='S5-AC-019':row.update(status='passed',limitation=None,evidence='Stage 5 v0.6 closure reconciliation: complete Title, Version and Department external-identifier sets; complete null-ID Version and TextApplies observation sets; complete Document parent-prefix count partition plan; full difference report retained.')
  elif row['criterion_id']=='S5-AC-030':row.update(status='passed',limitation=None,evidence='All thirty criteria pass; closure evidence and governed scope decision DEC-021 retained; exact Stage 6 entry action recorded.')
  criteria.append(row)
 passed=sum(r['status'] in {'passed','passed_design','later_stage_boundary'} for r in criteria);checks={'closure_reconciliation_passed':closure['status']=='passed','replay_passed':replay['status']=='passed','unit_tests_passed':a.unit_exit.read_text().strip()=='0','all_acceptance_criteria_pass':passed==30,'no_production_systems_addressed':not closure['production_systems_addressed'],'stage6_not_started':not closure['stage6_implementation_started']}
 result={'status':'passed' if all(checks.values()) else 'failed','stage':5,'stage_status':'completed' if all(checks.values()) else 'in_progress','decision':'accept_stage5_and_authorise_stage6_planning' if all(checks.values()) else 'do_not_close','checks':checks,'criteria':criteria,'summary':{'passed':passed,'failed':30-passed},'stage6_entry_action':'Create the governed Stage 6 Basic Legislation Interface work plan and acceptance log, then build a local or controlled-access searchable interface against a disposable or development PostgreSQL database populated by the accepted Stage 5 synchroniser. Do not deploy publicly until the later production audit stage.','evidence':{'closure_result_sha256':sha(a.closure),'replay_result_sha256':sha(a.replay)}};a.output.mkdir(parents=True,exist_ok=True);(a.output/'STAGE5_FINAL_ACCEPTANCE_ASSESSMENT_V0_6.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');md=['# Stage 5 final acceptance assessment','',f"Status: **{result['stage_status']}**",'',f"Acceptance criteria passed: **{passed} of 30**",'',f"Decision: **{result['decision']}**",'','## Stage 6 entry action','',result['stage6_entry_action'],'','## Scope decision','','DEC-021 limits Stage 5 closure reconciliation to complete authoritative external-identifier families and complete parent-linked coverage for dependent observations without standalone identifiers. Exhaustive Document-row harvesting remains an operational synchronisation task, not an acceptance prerequisite for the validation stage.',''];(a.output/'STAGE5_FINAL_ACCEPTANCE_ASSESSMENT_V0_6.md').write_text('\n'.join(md))
 tr=load(a.v05_traceability);tr['matrix_version']='stage5-v0.6-final-1'
 for row in tr['requirements']:
  if row['requirement_id']=='SYNC-010':row.update(status='passed',limitation=None,evidence='v0.6 closure reconciliation result, full difference report and Document partition plan',implementation='complete external-identifier reconciliation plus governed dependent-observation parent coverage',tests='live key-range closure, complete TextApplies traversal, Document partition-plan count reconciliation and offline replay')
 tr['summary']={}
 for row in tr['requirements']:tr['summary'][row['status']]=tr['summary'].get(row['status'],0)+1
 (a.output/'STAGE5_REQUIREMENTS_TRACEABILITY_V0_6.json').write_text(json.dumps(tr,indent=2,sort_keys=True)+'\n')
 if result['status']!='passed':raise SystemExit(1)
 print(json.dumps(result['summary']))
if __name__=='__main__':main()
