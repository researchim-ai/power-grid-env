"""Independent MSL block graphs for absolute PI takeover and canonical IEEEST.

No production ODE, state vector or event clock is used. Physical parameters
are final Modelica parameters; initialization uses only the independent plant.
"""
from pathlib import Path
import json,subprocess,zipfile,sys,math
import xml.etree.ElementTree as ET
import numpy as np
from benchmarks.esst1a_native import NativeExciter,verify,sha
from benchmarks.modelica_reference_setup import environment


def source_oel(p,current0):
    parameters='\n'.join(' final parameter Real '+k+'='+format(v,'.17g')+';' for k,v in dict(limit=p.current_limit_pu,Ts=p.sensor_time_s,Kp=p.proportional_gain,Ki=p.integral_gain,Tt=p.tracking_time_s,lo=p.minimum_field_pu,hi=p.maximum_field_pu,I0=current0).items())
    return '''model AuxReference
'''+parameters+'''
 input Real current(start=I0);
 Modelica.Blocks.Continuous.FirstOrder sensor(k=1,T=Ts,initType=Modelica.Blocks.Types.Init.InitialOutput,y_start=I0);
 Modelica.Blocks.Sources.Constant target(k=limit);
 Modelica.Blocks.Math.Feedback error;
 Modelica.Blocks.Math.Gain proportional(k=Kp);
 Modelica.Blocks.Math.Gain integralGain(k=Ki);
 Modelica.Blocks.Continuous.Integrator memory(k=1,initType=Modelica.Blocks.Types.Init.InitialOutput,y_start=hi+(Ki*Tt-Kp)*(limit-I0));
 Modelica.Blocks.Math.Add demand;
 Modelica.Blocks.Nonlinear.Limiter cap(uMin=lo,uMax=hi,strict=true);
 Modelica.Blocks.Math.Feedback back;
 Modelica.Blocks.Math.Gain tracking(k=1/Tt);
 Modelica.Blocks.Math.Add integralInput;
 output Real out_filtered_current_pu=sensor.y;
 output Real out_integrator_pu=memory.y;
 output Real out_field_current_pu=current;
 output Real out_error_pu=error.y;
 output Real out_raw_ceiling_pu=demand.y;
 output Real out_ceiling_pu=cap.y;
equation
 sensor.u=current;connect(target.y,error.u1);connect(sensor.y,error.u2);
 connect(error.y,proportional.u);connect(error.y,integralGain.u);
 connect(memory.y,demand.u1);connect(proportional.y,demand.u2);connect(demand.y,cap.u);
 connect(cap.y,back.u1);connect(demand.y,back.u2);connect(back.y,tracking.u);
 connect(integralGain.y,integralInput.u1);connect(tracking.y,integralInput.u2);connect(integralInput.y,memory.u);
end AuxReference;
'''


def source_pss(p,voltage0):
    params='\n'.join(' final parameter Real '+k+'='+format(v,'.17g')+';' for k,v in p.model_dump(exclude={'model','MODE'}).items())
    return '''model AuxReference
'''+params+'''
 input Real speedDeviation(start=0);
 input Real voltage(start='''+format(voltage0,'.17g')+''');
 Modelica.Blocks.Continuous.TransferFunction F1(a={A2,A1,1},b={1},initType=Modelica.Blocks.Types.Init.InitialState,x_start={0,0});
 Modelica.Blocks.Continuous.TransferFunction F2(a={A4,A3,1},b={A6,A5,1},initType=Modelica.Blocks.Types.Init.InitialState,x_start={0,0});
 Modelica.Blocks.Continuous.FirstOrder L1(T=T2,initType=Modelica.Blocks.Types.Init.InitialOutput,y_start=0);
 Modelica.Blocks.Continuous.FirstOrder L2(T=T4,initType=Modelica.Blocks.Types.Init.InitialOutput,y_start=0);
 Modelica.Blocks.Continuous.FirstOrder W(k=KS,T=T6,initType=Modelica.Blocks.Types.Init.InitialOutput,y_start=0);
 Modelica.Blocks.Nonlinear.Limiter limiter(uMin=LSMIN,uMax=LSMAX,strict=true);
 Real shaped=F2.y;
 Real lead1=T1/T2*shaped+(1-T1/T2)*L1.y;
 Real lead2=T3/T4*lead1+(1-T3/T4)*L2.y;
 Real raw=T5/T6*(KS*lead2-W.y);
 output Real out_sig=speedDeviation;
 output Real out_raw_pu=raw;
 output Real out_limited_pu=limiter.y;
 output Real out_voltage_gate=if noEvent(VCL<voltage and voltage<VCU) then 1 else 0;
 output Real out_output_pu=if noEvent(VCL<voltage and voltage<VCU) then limiter.y else 0;
equation
 F1.u=speedDeviation;connect(F1.y,F2.u);L1.u=shaped;L2.u=lead1;W.u=lead2;limiter.u=raw;
end AuxReference;
'''


class NativeAux(NativeExciter):
    def __init__(self,p,kind,current0,voltage0,out,root,stop,tolerance,*,generator_id=None,prepared_archive=None,prepared_identity=None):
        if kind not in ('oel','pss'):raise ValueError('known EX02 native block required')
        if not all(math.isfinite(v) for v in (current0,voltage0,stop,tolerance)) or stop<=0 or not 0<tolerance<=1e-6:raise ValueError('finite initial values, positive horizon and native tolerance required')
        if kind=='oel' and (p.current_limit_pu-current0<=1e-7 or p.integral_gain*p.tracking_time_s*(p.current_limit_pu-current0)<=1e-8):raise ValueError('native initial takeover OEL limit or boundary')
        if kind=='pss' and min(abs(voltage0-p.VCL),abs(voltage0-p.VCU),abs(p.LSMIN),abs(p.LSMAX))<=1e-7:raise ValueError('native initial PSS limiter or voltage boundary')
        self.generator_id=generator_id or getattr(p,'generator_id','pss')
        self.kind=kind;self.p=p;self.identity=verify(root) if prepared_archive is None else prepared_identity;root=Path(root).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
        source=source_oel(p,current0) if kind=='oel' else source_pss(p,voltage0)
        (out/'Reference.mo').write_text(source)
        msl=root/'libraries/ModelicaStandardLibrary-4.0.0'
        files=[msl/'Complex.mo',msl/'ModelicaServices/package.mo',msl/'Modelica/package.mo',out/'Reference.mo']
        archive=out/'AuxReference.fmu'
        if prepared_archive is None:
            script='setCompiler("gcc");\nsetCXXCompiler("g++");\nsetCommandLineOptions("--tearingMethod=minimalTearing");\nsetModelicaPath('+json.dumps(str(msl))+');\n'
            script+=''.join('loadFile('+json.dumps(str(f))+');\n' for f in files)
            script+='buildModelFMU(AuxReference,version="2.0",fmuType="me");\ngetErrorString();\n';(out/'run.mos').write_text(script)
            with (out/'compiler.log').open('w') as log:subprocess.run([str(root/'runtime/usr/bin/omc'),'run.mos'],cwd=out,env=environment(root),stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
        else:
            import shutil
            shutil.copyfile(prepared_archive,archive)
        if not archive.is_file():raise ValueError('EX02 native compilation failed: '+str(out))
        folder=out/'unpacked';folder.mkdir()
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe FMU archive')
            z.extractall(folder)
        xml=ET.parse(folder/'modelDescription.xml').getroot();variables=list(xml.find('ModelVariables'))
        self.real={v.get('name'):int(v.get('valueReference')) for v in variables if v.find('Real') is not None}
        self.native_state_names=[variables[int(variables[int(e.get('index'))-1].find('Real').get('derivative'))-1].get('name') for e in xml.find('ModelStructure/Derivatives')]
        self.nx=len(self.native_state_names);self.native_nz=int(xml.get('numberOfEventIndicators','0'));self._z=np.empty(self.native_nz)
        if self.nx!=(2 if kind=='oel' else 7):raise ValueError('unexpected native EX02 state inventory: '+str(self.native_state_names))
        self.nz=2 if kind=='oel' else 4;self.next_time=math.inf;self.a=None;self.z=None
        path=str(root/'fmpy')
        if path not in sys.path:sys.path.insert(0,path)
        from fmpy.fmi2 import FMU2Model
        self.f=FMU2Model(guid=xml.get('guid'),modelIdentifier=xml.find('ModelExchange').get('modelIdentifier'),unzipDirectory=str(folder),instanceName=kind+'_'+self.generator_id)
        self.f.instantiate();self.f.setupExperiment(startTime=0.,stopTime=stop,tolerance=tolerance);self.f.enterInitializationMode()
        self.set_inputs([current0] if kind=='oel' else [0.,voltage0]);self.f.exitInitializationMode();self.update(initial=True)
        rates=np.empty(self.nx);self.f.getDerivatives(np.ctypeslib.as_ctypes(rates),self.nx);self.initial_residual=float(max(abs(rates)))
        if not np.isfinite(rates).all() or self.initial_residual>1e-8:raise ValueError('native auxiliary initial state not stationary')
        import shutil
        shutil.copyfile(msl/'LICENSE' if prepared_archive is None else Path(prepared_archive).parent/'MSL-LICENSE.txt',out/'MSL-LICENSE.txt')
        self.identity=self.identity|dict(auxiliary_kind=kind,MSL_blocks_unmodified=True,wrapper_sha256=sha(out/'Reference.mo'),model='declared_absolute_PI_back_calculation_v1' if kind=='oel' else 'canonical_IEEEST_MODE1_MSL_cascade')

    def set_inputs(self,values):
        names=['current'] if self.kind=='oel' else ['speedDeviation','voltage']
        self.f.setReal([self.real[k] for k in names],list(map(float,values)))

    def output(self):return float(self.f.getReal([self.real['out_ceiling_pu' if self.kind=='oel' else 'out_output_pu']])[0])

    def signals(self):
        if self.kind=='oel':
            keys=['filtered_current_pu','integrator_pu','field_current_pu','error_pu','raw_ceiling_pu','ceiling_pu']
            q=dict(zip(keys,self.f.getReal([self.real['out_'+k] for k in keys])))
            q.update(lower_active=float(q['raw_ceiling_pu']<self.p.minimum_field_pu),upper_active=float(q['raw_ceiling_pu']>self.p.maximum_field_pu));return q
        keys=['sig','raw_pu','limited_pu','output_pu','voltage_gate'];q=dict(zip(keys,self.f.getReal([self.real['out_'+k] for k in keys])))
        x=np.empty(self.nx);self.f.getContinuousStates(np.ctypeslib.as_ctypes(x),self.nx);state=dict(zip(self.native_state_names,x))
        mapping={'F1.rate':'F1.x_scaled[1]','F1.output':'F1.x_scaled[2]','F2.rate':'F2.x_scaled[1]','F2.state':'F2.x_scaled[2]','LL1.state':'L1.y','LL2.state':'L2.y','WO.state':'W.y'}
        q.update({k:float(state[v]) for k,v in mapping.items()});return q

    def event_indicators(self,array):
        if self.native_nz:self.f.getEventIndicators(np.ctypeslib.as_ctypes(self._z),self.native_nz)
        q=self.signals()
        if self.kind=='oel':array[:]=[q['raw_ceiling_pu']-self.p.minimum_field_pu,q['raw_ceiling_pu']-self.p.maximum_field_pu]
        else:
            voltage=self.f.getReal([self.real['voltage']])[0];array[:]=[voltage-self.p.VCL,voltage-self.p.VCU,q['raw_pu']-self.p.LSMIN,q['raw_pu']-self.p.LSMAX]
