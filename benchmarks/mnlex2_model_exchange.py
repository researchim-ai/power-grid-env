"""Unmodified pinned OpenIPSL MNLEX2 as a two-state FMI Model Exchange block.

Only the declared initial raw-lag parameter is set for inactive equilibrium;
no production state vector, derivatives or event clocks enter the native path.
"""
from pathlib import Path
import json,subprocess,zipfile,sys,math
import xml.etree.ElementTree as ET
import numpy as np
from benchmarks.esst1a_native import NativeExciter,verify as verify_avr,sha
from benchmarks.modelica_reference_setup import environment
from benchmarks.native_uel_roots import indicator_names,POLICIES

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'requirements/mnlex2-reference.json'
REAL=dict(output_pu='VUEL',raw_lag_state_pu='simpleLagLim.state',feedback_pu='derivativeLag.y',feedback_memory_pu='derivativeLag.TF.x[1]',circle_error_pu2='add3_1.y')
INPUTS=('activePower','reactivePower','terminalVoltage')


def verify(root):
    identity=verify_avr(root);lock=json.loads(LOCK.read_text())
    if sha(ROOT/'requirements/esst1a-reference.json')!=lock['AVR_lock_sha256']:raise ValueError('changed native AVR lock')
    lib=Path(root)/'libraries'/('OpenIPSL-'+lock['openipsl_commit'])
    for name,want in lock['files'].items():
        if not (lib/name).is_file() or sha(lib/name)!=want:raise ValueError('changed MNLEX2 source: '+name)
    return identity|dict(MNLEX2_lock_sha256=sha(LOCK),UEL_equations_modified=False,compiler_flags='--tearingMethod=minimalTearing',physical_parameters_final=True,initial_raw_from_native_algebra=True,continuous_native_atom_guards=True)


class NativeUEL(NativeExciter):
    """Reuse FMI event iteration, with a distinct model and state inventory."""
    def __init__(self,p,active0,reactive0,voltage0,out,root,stop,tolerance=1e-11,*,root_policy='compound_and_atoms'):
        if root_policy not in POLICIES:raise ValueError('unknown native UEL root policy')
        if not math.isfinite(tolerance) or not 0<tolerance<=1e-6 or not math.isfinite(stop) or not 0<stop<=5:raise ValueError('native UEL tolerance/horizon outside profile')
        from power_grid_env.generator_underexcitation import UnderexcitationLimiter
        p=UnderexcitationLimiter.model_validate(p.model_dump())
        if p.initialization!='inactive_equilibrium':raise ValueError('native joint UEL requires inactive_equilibrium')
        values=np.array([active0,reactive0,voltage0],dtype=float)
        if not np.isfinite(values).all() or voltage0<=0:raise ValueError('finite native UEL inputs required')
        error=active0**2+(reactive0-p.Q_0*voltage0**2)**2-(p.Radius*voltage0**2)**2
        if error>=-64*np.finfo(float).eps*max(1.,p.Radius**2):raise ValueError('native joint UEL requires strictly inactive initial circle')
        if min(p.T_M,p.T_F2,p.K_F2)<1e-6:raise ValueError('native joint UEL excludes algebraic/bypass block variants')
        root=Path(root).resolve();out=Path(out).resolve();self.identity=verify(root);out.mkdir(parents=True,exist_ok=False)
        lock=json.loads(LOCK.read_text());lib=root/'libraries'/('OpenIPSL-'+lock['openipsl_commit']);msl=root/'libraries/ModelicaStandardLibrary-4.0.0'
        params=','.join('final '+k+'='+format(v,'.17g') for k,v in p.model_dump(exclude={'model','generator_id','initialization','event_detection'}).items())
        # Normal initialization parameter evaluated from the FMU's own
        # algebraic branch, before leaving initialization mode. This avoids a
        # rounded Python/constant-folded circle value firing SimpleLagLim reset.
        params+=',simpleLagLim(y_start=rawInitial)'
        source='model UELReference\n parameter Real rawInitial=0;\n output Real out_equilibrium_raw_pu=limiter.K_M*limiter.feedback.y;\n'+''.join(' input Real '+k+'(start='+format(v,'.17g')+');\n' for k,v in zip(INPUTS,values))
        source+=''.join(' output Real out_'+key+' = limiter.'+name+';\n' for key,name in REAL.items())
        source+=' OpenIPSL.Electrical.Controls.PSSE.UEL.MNLEX2 limiter('+params+');\nequation\n limiter.PELEC=activePower; limiter.QELEC=reactivePower; limiter.Eterm=terminalVoltage;\nend UELReference;\n'
        (out/'Reference.mo').write_text(source)
        paths=[msl/'Complex.mo',msl/'ModelicaServices/package.mo',msl/'Modelica/package.mo',lib/'OpenIPSL/package.mo',out/'Reference.mo']
        script='setCompiler("gcc");\nsetCXXCompiler("g++");\nsetCommandLineOptions("--tearingMethod=minimalTearing");\nsetModelicaPath('+json.dumps(str(msl)+':'+str(lib))+');\n'
        script+=''.join('loadFile('+json.dumps(str(f))+');\n' for f in paths)
        script+='buildModelFMU(UELReference,version="2.0",fmuType="me");\ngetErrorString();\n';(out/'run.mos').write_text(script)
        with (out/'compiler.log').open('w') as log:subprocess.run([str(root/'runtime/usr/bin/omc'),'run.mos'],cwd=out,env=environment(root),stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
        archive=out/'UELReference.fmu'
        if not archive.is_file():raise ValueError('native MNLEX2 compilation failed: '+str(out))
        folder=out/'unpacked';folder.mkdir()
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe native archive')
            z.extractall(folder)
        xml=ET.parse(folder/'modelDescription.xml').getroot();variables=list(xml.find('ModelVariables'))
        if xml.get('fmiVersion')!='2.0' or xml.find('ModelExchange') is None:raise ValueError('FMI2 Model Exchange required')
        self.real={v.get('name'):int(v.get('valueReference')) for v in variables if v.find('Real') is not None}
        self.native_state_names=[variables[int(variables[int(e.get('index'))-1].find('Real').get('derivative'))-1].get('name') for e in xml.find('ModelStructure/Derivatives')]
        self.nx=len(self.native_state_names);self.native_nz=int(xml.get('numberOfEventIndicators'))
        if self.native_nz!=2:raise ValueError('pinned MNLEX2 requires two compound FMI indicators')
        self.root_policy=root_policy;self.root_indicator_names=indicator_names(root_policy,self.native_nz)
        self.nz=len(self.root_indicator_names);self._native_indicator_buffer=np.empty(self.native_nz)
        self.p=p;self.next_time=math.inf;self.a=None;self.z=None
        if self.nx!=2:raise ValueError('two-state MNLEX2 profile required')
        path=str(root/'fmpy')
        if path not in sys.path:sys.path.insert(0,path)
        from fmpy.fmi2 import FMU2Model
        import fmpy
        if fmpy.__version__!=self.identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(root/'fmpy'):raise ValueError('wrong FMI master')
        self.f=FMU2Model(guid=xml.get('guid'),modelIdentifier=xml.find('ModelExchange').get('modelIdentifier'),unzipDirectory=str(folder),instanceName=p.generator_id+'_UEL')
        self.f.instantiate()
        self.f.setupExperiment(startTime=0.,stopTime=stop,tolerance=tolerance);self.f.enterInitializationMode();self.set_inputs(values)
        self.initial_raw=self.f.getReal([self.real['out_equilibrium_raw_pu']])[0]
        self.f.setReal([self.real['rawInitial']],[self.initial_raw])
        self.f.exitInitializationMode();self.update(initial=True)
        rates=np.zeros(self.nx);self.f.getDerivatives(np.ctypeslib.as_ctypes(rates),self.nx);self.initial_residual=float(max(abs(rates)))
        if not np.isfinite(rates).all() or self.initial_residual>1e-8 or abs(self.output())>1e-12:raise ValueError('native UEL initialization not stationary: '+str(dict(derivatives=rates.tolist(),signals=self.signals(),initial_circle=error)))
        self.initial_inputs=values.tolist()
        self._refresh_continuous()
        self.a_end=1. # pinned DerivativeLag denominator {T_dummy,1}; final parameters are folded
        # Ship license notices beside generated FMUs.
        import shutil
        shutil.copyfile(lib/'LICENSE',out/'OpenIPSL-LICENSE.txt');shutil.copyfile(msl/'LICENSE',out/'MSL-LICENSE.txt')
    def set_inputs(self,values):self.f.setReal([self.real[k] for k in INPUTS],list(map(float,values)))
    def _refresh_continuous(self):
        # OMC 1.27.2 getReal's updateIfNeeded stores pre-values after algebraics.
        # At a root that consumes a when edge before newDiscreteStates. The
        # MNLEX2 ODE dependency closure contains all exposed continuous values;
        # evaluate it first, without committing discrete pre-values.
        derivative=np.empty(self.nx)
        self.f.getDerivatives(np.ctypeslib.as_ctypes(derivative),self.nx)
        if not np.isfinite(derivative).all():raise ValueError('nonfinite native UEL derivative')
    def output(self):
        self._refresh_continuous()
        return self.f.getReal([self.real['out_output_pu']])[0]
    def signals(self):
        self._refresh_continuous()
        result=dict(zip(REAL,self.f.getReal([self.real['out_'+k] for k in REAL])))
        x=np.empty(self.nx);self.f.getContinuousStates(np.ctypeslib.as_ctypes(x),self.nx)
        result['raw_lag_state_pu']=float(x[self.native_state_names.index('limiter.simpleLagLim.state')])
        result['feedback_memory_pu']=float(x[self.native_state_names.index('limiter.derivativeLag.TF.x_scaled[1]')]/getattr(self,'a_end',1.))
        return result

    def guard_snapshot(self):
        """Audit the original when operands without committing discrete pre-values.

        OMC may expand the ODE while leaving K*u-state in the when relation.
        Report both; their floating-point signs need not agree near equilibrium.
        """
        self._refresh_continuous()
        x=np.empty(self.nx);dx=np.empty(self.nx)
        self.f.getContinuousStates(np.ctypeslib.as_ctypes(x),self.nx)
        self.f.getDerivatives(np.ctypeslib.as_ctypes(dx),self.nx)
        feedback=self.f.getReal([self.real['limiter.feedback.y']])[0]
        j=self.native_state_names.index('limiter.simpleLagLim.state')
        raw=float(x[j]);target=float(self.p.K_M*feedback);residual=target-raw
        if not np.isfinite([raw,target,residual,dx[j]]).all():raise ValueError('nonfinite native UEL guard audit')
        return dict(native_inputs=list(map(float,self.f.getReal([self.real[k] for k in INPUTS]))),raw_state_pu=raw,native_lag_input_pu=float(feedback),target_pu=target,
            source_guard_numerator_pu=residual,native_ode_rate_pu_per_s=float(dx[j]),
            strict_lower_condition=bool(raw<0. and residual>0.),
            strict_upper_condition=bool(raw>self.p.MEL_MAX and residual<0.),
            source_guard_reconstructed_from_native_operands=True)

    def event_indicators(self,array):
        if len(array)!=self.nz:raise ValueError('native UEL root vector size differs')
        # Query the original indicators in both profiles. The continuous-only
        # profile does not feed their Boolean +/-1 discontinuities to CVODE.
        prefix=self.native_nz if self.root_policy=='compound_and_atoms' else 0
        native=array[:prefix] if prefix else self._native_indicator_buffer
        self.f.getEventIndicators(np.ctypeslib.as_ctypes(native),self.native_nz)
        if not np.isfinite(native).all():raise ValueError('nonfinite native compound indicator')
        x=np.empty(self.nx);dx=np.empty(self.nx)
        self.f.getContinuousStates(np.ctypeslib.as_ctypes(x),self.nx)
        self.f.getDerivatives(np.ctypeslib.as_ctypes(dx),self.nx)
        j=self.native_state_names.index('limiter.simpleLagLim.state');eps=1e-8 if self.p.event_detection=='conditioned_1e-8' else 64*np.finfo(float).eps
        array[prefix:]=[x[j],x[j]-self.p.MEL_MAX,dx[j]-eps,dx[j]+eps]
