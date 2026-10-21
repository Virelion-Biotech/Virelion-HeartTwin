import importlib.util, json
from copy import deepcopy
from pathlib import Path
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop
root,repo=map(Path,sys.argv[1:3]); out=root/'joint-verification'
spec=importlib.util.spec_from_file_location('rv_diagnostic',repo/'scripts/diagnose_kit_rv.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
request=json.loads((out/'diagnostic_request.json').read_text())
native=json.loads((out/'diagnostic_prediction.json').read_text())
times=np.asarray(native['time_s'])
reference=module.reference(request)
original=np.column_stack([native['volume_mL'][c] for c in COMPARTMENTS])
tight_request=deepcopy(request)
tight_request['solver']={'rtol':1e-9,'atol_mL':1e-10,'max_step_s':.0005}
tight_request['output_dir']=str(out/'artifacts/tolerance-refinement')
tight=simulate_closed_loop(tight_request)
tight_values=np.column_stack([tight['volume_mL'][c] for c in COMPARTMENTS])
error=np.abs(original-reference); index=np.unravel_index(np.argmax(error),error.shape)
target=json.loads((out/'solver_transport_checks.json').read_text())['RV_target_time_s']
target_index=np.flatnonzero(times==target)[0]
report={'original_native_vs_reference_max_mL':float(error.max()),
'maximum_error_compartment':COMPARTMENTS[index[1]],'maximum_error_time_s':float(times[index[0]]),
'RV_at_target_native_minus_reference_mL':float(original[target_index,5]-reference[target_index,5]),
'tight_native_vs_reference_max_mL':float(np.max(np.abs(tight_values-reference))),
'tight_vs_original_max_mL':float(np.max(np.abs(tight_values-original))),
'RV_at_target_tight_minus_reference_mL':float(tight_values[target_index,5]-reference[target_index,5]),
'interpretation':'Dense-output tolerance error is checked separately from 21.87 mL RV model discrepancy; this is numerical consistency, not patient validation'}
(out/'tolerance_refinement.json').write_text(json.dumps(report,indent=2))

base=repo/'validation/kit_edgar_2026_10_20'
joint_root=root/'joint-lv-rv'
joint_results=json.loads((joint_root/'results.json').read_text())
factor=joint_results['selected_loading_factor']
joint=json.loads((joint_root/('loading-'+str(factor))/'frozen_prediction.json').read_text())
lv_only=json.loads((root/'chamber-rebuild/results_chamber_final/selected_prediction.json').read_text())
original=json.loads((base/'frozen_prediction.json').read_text())
cal=json.loads((base/'observations/LV_calibration.json').read_text())
outer=json.loads((base/'observations/LV_temporal_holdout.json').read_text())
observed_t=np.array(cal['time_s']+outer['time_s'])
observed_y=np.array(cal['values']+outer['values'])
fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,constrained_layout=True)
for name,prediction,color,lw in [
 ('Original failed LV fit',original,'#8a99a7',1.1),
 ('LV-only selected fit',lv_only,'#d06b32',1.6),
 ('Conditional joint LV/RV fit',joint,'#177e89',2.0)]:
 axes[0].plot(prediction['time_s'],prediction['volume_mL']['LV'],color=color,lw=lw,label=name)
axes[0].scatter(observed_t,observed_y,color='#172c3c',s=18,zorder=4,label='Observed LV volumes')
boundary=(observed_t[22]+observed_t[23])/2
axes[0].axvspan(boundary,observed_t[-1],color='#d4dee7',alpha=.35)
axes[0].axvline(boundary,color='#7790a5',ls='--',lw=.8)
axes[0].set_ylabel('LV volume (mL)')
axes[0].legend(fontsize=8,loc='upper center')
axes[0].text(.82,.98,'Previously seen LV outer data',ha='left',va='top',fontsize=8,
 transform=axes[0].get_xaxis_transform())
axes[1].plot(lv_only['time_s'],lv_only['volume_mL']['RV'],color='#d06b32',lw=1.6,label='LV-only model; RV unfitted')
axes[1].plot(joint['time_s'],joint['volume_mL']['RV'],color='#177e89',lw=2,label='Conditional joint fit')
axes[1].scatter([0.,target],[194.545622,65.938406],s=48,color='#b82e38',zorder=4,label='RV initial condition and calibration point')
axes[1].annotate('Used for calibration',xy=(target,65.938406),xytext=(.45,90),
 arrowprops={'arrowstyle':'-','color':'#7a8490'},fontsize=8)
axes[1].set_ylabel('RV volume (mL)')
axes[1].set_xlabel('Measured time (s)')
axes[1].legend(fontsize=8,loc='upper center')
for ax in axes:
 ax.grid(alpha=.15)
 ax.spines[['top','right']].set_visible(False)
fig.suptitle('Joint development fit: LV outer RMSE 6.50 mL; RV calibration residual 0.465 mL',fontsize=12)
fig.supxlabel('One volunteer, previously seen data. RV point fitted; no independent RV or clinical validation.',fontsize=9)
fig.savefig(joint_root/'Joint_LV_RV_development.png',dpi=170)
fig.savefig(joint_root/'Joint_LV_RV_development.svg')
import csv
with (joint_root/'LV_comparison.csv').open('w',newline='') as stream:
 writer=csv.writer(stream)
 writer.writerow(['time_s','observed_LV_mL','original_LV_mL','LV_only_mL','joint_LV_mL','partition'])
 predictions=[np.interp(observed_t,p['time_s'],p['volume_mL']['LV']) for p in [original,lv_only,joint]]
 for index,time in enumerate(observed_t):
  writer.writerow([time,observed_y[index],*[p[index] for p in predictions],
      'calibration' if index<23 else 'previously_seen_outer'])
print(json.dumps(report,indent=2))
print('JOINT_POSTPROCESS_COMPLETE')
