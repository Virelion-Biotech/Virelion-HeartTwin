from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import sys
root=Path(sys.argv[1])
r=json.loads((root/'KIT_circulation_results.json').read_text())
prediction=json.loads((root/'frozen_prediction.json').read_text())
rows=np.genfromtxt(root/'LV_predictions.csv',delimiter=',',names=True,dtype=None,encoding='utf-8')
t=rows['time_s']; observed=rows['observed_LV_mL']; model=rows['predicted_LV_mL']
fig,axes=plt.subplots(2,1,figsize=(9,8),constrained_layout=True)
axes[0].plot(t,observed,'o-',color='#164e63',label='Measured LV volume',markersize=4)
axes[0].plot(t,model,'-',color='#b45309',label='Conditional native 0D model')
axes[0].plot(t[23:],[observed[22]]*12,'--',color='#64748b',label='Persistence baseline')
axes[0].axvspan((t[22]+t[23])/2,t[-1],color='#fef3c7',label='Temporal holdout')
axes[0].set(xlabel='Time (s)',ylabel='LV volume (mL)',title='KIT volunteer: reduced circulation underperforms persistence')
axes[0].legend(fontsize=8,loc='lower right'); axes[0].grid(alpha=.2)
p=np.array(prediction['pressure_mmHg']['LV'])
for scale,color in [(.5,'#0f766e'),(1.,'#64748b'),(2.,'#be123c')]:
    axes[1].plot(t,p*scale,color=color,label=f'Pressure scale {scale:g}; identical volume trajectory')
axes[1].set(xlabel='Time (s)',ylabel='Model LV pressure (mmHg)',title='Pressure is not identified by volume-only fitting')
axes[1].legend(fontsize=8); axes[1].grid(alpha=.2)
fig.suptitle('Exploratory single-volunteer result; no spatial mechanics or clinical validation',fontsize=11)
fig.savefig(root/'KIT_results.png',dpi=160)
fig.savefig(root/'KIT_results.svg')
plt.close(fig)
(root/'plot_environment.json').write_text(json.dumps({'matplotlib':matplotlib.__version__,'numpy':np.__version__},indent=2))
