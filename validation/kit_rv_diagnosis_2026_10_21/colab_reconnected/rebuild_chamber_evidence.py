from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, time

root=Path(sys.argv[1]); repo=Path(sys.argv[2]); baseline=repo/'validation/kit_edgar_2026_10_20'
root.mkdir(exist_ok=True)
families=[
 ('constitutive-first','results_chamber_laws','diagnose_kit_chamber_laws.py',[]),
 ('bound-refinement','results_chamber_refinement','diagnose_kit_chamber_laws.py',['--refine-calibration']),
 ('profile-phase','results_stiffness_profile','profile_kit_passive_stiffness.py',[]),
 ('profile-fixed-phase','results_stiffness_fixed_phase','profile_kit_passive_stiffness.py',['--fixed-phase'])]
protocol={'scope':'Regeneration of prior development evidence; no unseen data',
'python':sys.version,'source_pin':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
'families':families,'workers':2,'selection':'Lowest inner calibration RMSE; never archived outer error',
'outer_values_previously_seen':True,'independent_patient_validation':False,
'clinical_validation':'NOT_ESTABLISHED'}
(root/'rebuild_protocol.json').write_text(json.dumps(protocol,indent=2))
env=dict(os.environ,MPLBACKEND='Agg',PYTHONUNBUFFERED='1',OPENBLAS_NUM_THREADS='1')
def worker(row):
 name,folder,script,extra=row
 started=time.monotonic()
 command=[sys.executable,str(repo/'scripts'/script),'--baseline',str(baseline),'--output',str(root/folder),*extra]
 with (root/(name+'.log')).open('w') as log:
  try:
   completed=subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
   code=completed.returncode
  except subprocess.TimeoutExpired:
   code=124
 return {'family':name,'folder':folder,'exit_code':code,'seconds':time.monotonic()-started}
rows=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 for result in as_completed([pool.submit(worker,row) for row in families]):
  rows.append(result.result())
  (root/'process_status.json').write_text(json.dumps(rows,indent=2))
  print('FINISHED',json.dumps(rows[-1]),flush=True)
if any(row['exit_code'] for row in rows):
 raise RuntimeError('A family failed; retain all logs and diagnose before selection')
ranking=[]
for name,folder,_,_ in families:
 selected=json.loads((root/folder/'selection.json').read_text())
 ranking.append({'family':name,'folder':folder,'inner_rmse_mL':selected['inner_rmse_mL']})
ranking.sort(key=lambda x:(x['inner_rmse_mL'],x['family']))
final=root/'results_chamber_final'; final.mkdir(exist_ok=True)
choice={'rule':'Lowest inner RMSE across all recorded families; outer never used for choice',
'prior_outer_values_seen':True,'independent_patient_validation':False,'ranking':ranking,
'selected_family':ranking[0]['family']}
(final/'cross_round_selection.json').write_text(json.dumps(choice,indent=2))
selected_dir=root/ranking[0]['folder']
for source,target in [('results.json','selected_results.json'),('frozen_request.json','selected_request.json'),
                      ('frozen_prediction.json','selected_prediction.json')]:
 shutil.copyfile(selected_dir/source,final/target)
choice['prediction_sha256']=hashlib.sha256((final/'selected_prediction.json').read_bytes()).hexdigest()
(final/'cross_round_selection.json').write_text(json.dumps(choice,indent=2))
print('SELECTED',json.dumps(choice),flush=True)
print('RESULT', (final/'selected_results.json').read_text(),flush=True)
