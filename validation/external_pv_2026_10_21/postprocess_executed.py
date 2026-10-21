import json, numpy as np, matplotlib.pyplot as plt
from scipy.io import loadmat
from pathlib import Path
R=ROOT/"results"
models=json.loads((R/"frozen_models.json").read_text())
result=json.loads((R/"results.json").read_text())
data=loadmat(R/"m1_timeseries_final.mat")
pred=json.loads((R/"frozen_outer_predictions.json").read_text())
diagnosis={"purpose":"Post-score diagnosis only; no model or outer-score-selected refit.",
"source":"HeartTwin native chamber_pressure; no CardiFlow/CardiMech/CardiEP engine called",
"outer_predictions_frozen_before_pressure_scoring":True,"chambers":{}}
fig,axes=plt.subplots(2,2,figsize=(11,7),constrained_layout=True)
phase=np.linspace(0,1,50)
for row,ch in enumerate(["LV","RV"]):
    m=models[ch]; p=m["parameters"]; T=.1034
    V=data["V"+ch+"_stack"][:,40:50]/1000
    measured=data["P"+ch+"_stack"][:,40:50]
    elapsed=np.remainder(phase*T-p["activation_start_s"],T)
    duration=p["activation_duration_s"]; peak=duration*p.get("activation_peak_fraction",.5)
    a=np.where(elapsed<duration,np.sin(np.pi/2*np.where(elapsed<=peak,elapsed/peak,(duration-elapsed)/(duration-peak)))**2,0)
    delta=V-p["unstressed_volume_mL"]
    k=p.get("passive_exponent_per_mL",0.)
    passive=p["emin_mmHg_per_mL"]*delta if k==0 else p["emin_mmHg_per_mL"]*np.expm1(k*delta)/k
    reference=p["external_pressure_mmHg"]+(1-a[:,None])*passive+a[:,None]*p["emax_mmHg_per_mL"]*delta
    native=np.array(pred[ch])
    maxdiff=float(np.max(abs(reference-native)))
    assert maxdiff<1e-10
    selected=m["families"][m["selected_index"]]
    training=data["P"+ch+"_stack"][:,:40].mean(axis=1)
    diagnosis["chambers"][ch]={
      "independent_formula_max_difference_mmHg":maxdiff,
      "selected_stiffness_times_vref":selected["stiffness_times_vref"],
      "selected_asymmetric":selected["asymmetric"],
      "inner_rmse_all_families_mmHg":[f["inner_pressure_rmse_mmHg"] for f in m["families"]],
      "refit_active_mask":m["refit"]["active_mask"],
      "converged_trials":sum(t["success"] for f in m["families"] for t in f["calibration_trials"])+sum(t["success"] for t in m["refit_trials"]),
      "total_trials":sum(len(f["calibration_trials"]) for f in m["families"])+len(m["refit_trials"]),
      "pressure_range_mmHg":[float(measured.min()),float(measured.max())],
      "volume_range_mL":[float(V.min()),float(V.max())],
      "mean_error_by_phase_mmHg":(native-measured).mean(axis=1).tolist(),
      "diagnosis":"Formula agrees; remaining mismatch consistent with restricted phase/volume constitutive model and processed-beat variation. No unique physiological cause identified.",
      "baseline_comparison":"A repeating training waveform beats the fitted constitutive model on these processed beats; no predictive superiority established."}
    ax=axes[row,0]
    ax.plot(phase,measured,color=".75",alpha=.5)
    ax.plot(phase,measured.mean(axis=1),"k",label="Reserved-beat mean")
    ax.plot(phase,native.mean(axis=1),"C3",label="Native conditional pressure")
    ax.plot(phase,training,"C0",ls="--",label="Training waveform baseline")
    ax.set(xlabel="Publisher-normalized phase",ylabel=ch+" pressure (mmHg)")
    ax.legend(fontsize=8)
    ax=axes[row,1]
    ax.plot(phase,native-measured,alpha=.4,color="C3")
    ax.axhline(0,color="k",lw=.7)
    ax.set(xlabel="Publisher-normalized phase",ylabel=ch+" pressure residual (mmHg)")
fig.suptitle("External murine PV challenge: one animal, 10 reserved processed beats\nMeasured volume is an input; no circulation or human accuracy claim",fontsize=11)
fig.savefig(R/"external_pv_challenge.png",dpi=160)
(R/"diagnosis.json").write_text(json.dumps(diagnosis,indent=2,allow_nan=False))
print(json.dumps(diagnosis,indent=2))
display(fig)
