from copy import deepcopy
import json
from pathlib import Path
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from hearttwin.patient_closed_loop import COMPARTMENTS, simulate_closed_loop
root,repo=map(Path,sys.argv[1:3]); base=repo/'validation/kit_edgar_2026_10_20'
out=root/'chamber-rebuild/results_chamber_final'
cal=json.loads((base/'observations/LV_calibration.json').read_text())
outer=json.loads((base/'observations/LV_temporal_holdout.json').read_text())
t=np.array(cal['time_s']+outer['time_s']); y=np.array(cal['values']+outer['values'])
original=json.loads((base/'frozen_prediction.json').read_text())
selected=json.loads((out/'selected_prediction.json').read_text())
conditional=json.loads((root/'rv-selected-LV/constraint-1.0-prediction.json').read_text())
series={'Original failed LV fit':np.interp(t,original['time_s'],original['volume_mL']['LV']),
'Selected LV-only development fit':np.interp(t,selected['time_s'],selected['volume_mL']['LV']),
'RV constraint; no joint LV refit':np.interp(t,conditional['time_s'],conditional['volume_mL']['LV'])}
metrics={}
for name,prediction in series.items():
 error=prediction-y
 metrics[name]={'calibration_RMSE_mL':float(np.sqrt(np.mean(error[:23]**2))),
 'outer_development_RMSE_mL':float(np.sqrt(np.mean(error[23:]**2))),
 'outer_bias_mL':float(error[23:].mean()),'outer_MAE_mL':float(np.mean(np.abs(error[23:]))),
 'outer_max_absolute_error_mL':float(np.max(np.abs(error[23:])))}
request=json.loads((out/'selected_request.json').read_text())
scale_checks=[]
for scale in [.5,2.]:
 p=deepcopy(request);p['output_dir']=str(out/'pressure-scale-artifacts')
 p['edge_resistance_mmHg_s_per_mL']=(np.array(p['edge_resistance_mmHg_s_per_mL'])*scale).tolist()
 for part in p['compartments'].values():
  part['external_pressure_mmHg']*=scale
  if 'emax_mmHg_per_mL' in part:
   part['emin_mmHg_per_mL']*=scale;part['emax_mmHg_per_mL']*=scale
  else:part['compliance_mL_per_mmHg']/=scale
 alternative=simulate_closed_loop(p)
 scale_checks.append({'scale':scale,'maximum_volume_change_mL':max(float(np.max(np.abs(
  np.array(alternative['volume_mL'][c])-selected['volume_mL'][c]))) for c in COMPARTMENTS),
  'LV_peak_pressure_mmHg':float(max(alternative['pressure_mmHg']['LV']))})
report={'metrics':metrics,'pressure_scale_checks':scale_checks,
'interpretation':'Same previously seen data. RV constraint fits its former assessment point; no LV refit. No independent or clinical validation.'}
(out/'comparison_metrics.json').write_text(json.dumps(report,indent=2))
fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,constrained_layout=True)
colors=['#6c7d8f','#d06b32','#8356a1']
for (name,prediction),color in zip(series.items(),colors):
 axes[0].plot(t,prediction,color=color,lw=1.8,label=name)
 axes[1].plot(t,prediction-y,color=color,lw=1.5)
axes[0].scatter(t,y,s=20,c='#172c3c',zorder=4,label='Observed LV volumes')
boundary=(t[22]+t[23])/2
for ax in axes:
 ax.axvspan(boundary,t[-1],color='#d4dee7',alpha=.35)
 ax.axvline(boundary,color='#7790a5',ls='--',lw=.8)
 ax.grid(alpha=.15);ax.spines[['top','right']].set_visible(False)
axes[0].set_ylabel('LV volume (mL)');axes[0].legend(fontsize=8)
axes[0].text(.82,.98,'Previously seen outer data',ha='left',va='top',fontsize=8,
 transform=axes[0].get_xaxis_transform())
axes[1].axhline(0,color='#172c3c',lw=.8);axes[1].set_ylabel('Predicted minus observed (mL)')
axes[1].set_xlabel('Measured time (s)')
fig.suptitle('LV development error reproduced: 22.87 to 6.63 mL; RV adjustment changes the fit',fontsize=12)
fig.supxlabel('One volunteer; original calibration split retained. Reproduction is not independent validation.',fontsize=9)
fig.savefig(out/'LV_RV_development_comparison.png',dpi=170)
fig.savefig(out/'LV_RV_development_comparison.svg')
print(json.dumps(report,indent=2))
