import importlib.util, json
from copy import deepcopy
from pathlib import Path
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop
root,repo=map(Path,sys.argv[1:3]); out=root/'rv-results'
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
fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,constrained_layout=True)
axes[0].plot(times,native['volume_mL']['RV'],color='#324761',lw=2.2,label='Original RV assumptions')
colors=['#128a83','#cc8144','#8b5d9c']
for factor,color in zip([.5,1.,2.],colors):
 p=json.loads((out/('constraint-'+str(factor)+'-prediction.json')).read_text())
 label='Fit to RV point; outflow resistance x'+str(factor)
 axes[0].plot(p['time_s'],p['volume_mL']['RV'],color=color,lw=1.3,label=label)
 axes[1].plot(p['time_s'],np.asarray(p['volume_mL']['LV'])-native['volume_mL']['LV'],color=color,lw=1.5,label='Resistance x'+str(factor))
axes[0].scatter([0.,target],[194.545622,65.938406],color='#c2383b',s=55,zorder=5,label='Two observed RV volumes')
axes[0].set_ylabel('RV volume (mL)')
axes[0].legend(fontsize=8,loc='upper right')
axes[1].axhline(0,color='#64748b',lw=.7)
axes[1].set_ylabel('Change in LV volume (mL)')
axes[1].set_xlabel('Measured time (s)')
axes[1].legend(fontsize=8)
for ax in axes:
 ax.grid(alpha=.15);ax.spines[['top','right']].set_visible(False)
fig.suptitle('One RV point admits different conditional fits; LV behavior changes',fontsize=12)
fig.supxlabel('Previously seen data, one volunteer. Conditional fits are not validation or identified patient parameters.',fontsize=9)
fig.savefig(out/'RV_diagnosis.png',dpi=170)
fig.savefig(out/'RV_diagnosis.svg')
print(json.dumps(report,indent=2))
