"""Unmodified pinned OpenIPSL ESST1A as an FMI 2 Model Exchange component."""
from pathlib import Path
import json,hashlib,zipfile,subprocess,sys,math
import numpy as np
from benchmarks.modelica_reference_setup import verify as verify_base,environment

ROOT=Path(__file__).resolve().parents[1];LOCK=ROOT/'requirements/esst1a-reference.json'
REAL=dict(effective_oel_ceiling_pu='VOEL',effective_voltage_input_pu='VOTHSG',sensor_pu='TransducerDelay.y',lead1_memory_pu='imLeadLag.TF.x[1]',lead2_memory_pu='imLeadLag1.TF.x[1]',regulator_raw_pu='simpleLagLim.state',washout_memory_pu='imDerivativeLag.x',
 field_pu='EFD',feedback_pu='imDerivativeLag.y',error_pu='add3_1.y',error_limited_pu='imLimited.y',error_selected_pu='hV_GATE.y',lead1_output_pu='imLeadLag.y',lead2_output_pu='imLeadLag1.y',regulator_output_pu='simpleLagLim.y',current_limit_pu='imLimited1.y',field_demand_pu='add3_2.y',field_floor_selected_pu='hV_GATE1.y',takeover_selected_pu='lV_GATE.y',rectifier_lower_pu='imGain1.y',rectifier_upper_pu='add3.y')
INPUTS=('terminalVoltage','fieldCurrent','referenceIncrement','oelCeiling','uelErrorFloor','uelFieldFloor','uelSum','supplementaryVoltage','supplementaryField')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def verify(root):
    root=Path(root).resolve();identity=verify_base(root);lock=json.loads(LOCK.read_text())
    if sha(ROOT/'requirements/modelica-reference.json')!=lock['base_lock_sha256']:raise ValueError('changed ESST1A base lock')
    library=root/'libraries'/('OpenIPSL-'+lock['openipsl_commit'])
    for name,want in lock['files'].items():
        if not (library/name).is_file() or sha(library/name)!=want:raise ValueError('changed ESST1A source: '+name)
    package=root/'packages'/lock['fmpy']['filename']
    if not package.is_file() or sha(package)!=lock['fmpy']['sha256']:raise ValueError('missing or changed native FMI master archive')
    with zipfile.ZipFile(package) as z:
        for name in z.namelist():
            if name.endswith('/'):continue
            target=root/'fmpy'/name
            if not target.is_file() or sha(target)!=hashlib.sha256(z.read(name)).hexdigest():raise ValueError('changed FMI master: '+name)
    return identity|dict(ESST1A_lock_sha256=sha(LOCK),equations_modified=False,fmpy_version=lock['fmpy']['version'])


def build(p,field0,voltage0,current0,out,root,tolerance=1e-11):
    root=Path(root).resolve();identity=verify(root);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    lock=json.loads(LOCK.read_text());lib=root/'libraries'/('OpenIPSL-'+lock['openipsl_commit']);msl=root/'libraries/ModelicaStandardLibrary-4.0.0'
    params=','.join(k+'='+format(v,'.17g') for k,v in p.native_parameters().items())
    inputs='\n'.join('  input Real '+n+'(start='+format(v,'.17g')+');' for n,v in zip(INPUTS,[voltage0,current0,0.,p.oel_ceiling_pu,p.uel_error_floor_pu,p.uel_field_floor_pu,0.,0.,0.]))
    outputs='\n'.join('  output Real out_'+n+' = avr.'+source+';' for n,source in REAL.items())
    outputs+='\n  output Real out_reference_pu = avr.VoltageReference.y + referenceIncrement;'
    outputs+='\n  output Real out_oel_active = if avr.hV_GATE1.y > oelCeiling then 1 else 0;'
    outputs+='\n  output Real out_uel2_active = if uelErrorFloor > avr.imLimited.y then 1 else 0;'
    outputs+='\n  output Real out_uel3_active = if uelFieldFloor > avr.add3_2.y then 1 else 0;'
    outputs+='\n  output Real out_rectifier_upper_active = if avr.lV_GATE.y > avr.add3.y then 1 else 0;'
    outputs+='\n  output Real out_rectifier_lower_active = if avr.imGain1.y > avr.lV_GATE.y then 1 else 0;'
    source=f'''model ESSTReference
{inputs}
{outputs}
  OpenIPSL.Electrical.Controls.PSSE.ES.ESST1A avr({params});
equation
  avr.EFD0={field0:.17g}; avr.ECOMP=terminalVoltage; avr.VT=terminalVoltage;
  avr.XADIFD=fieldCurrent; avr.VOTHSG=referenceIncrement+supplementaryVoltage;
  avr.VOTHSG2=supplementaryField; avr.VUEL=uelSum; avr.VUEL2=uelErrorFloor;
  avr.VUEL3=uelFieldFloor; avr.VOEL=oelCeiling;
end ESSTReference;
'''
    (out/'Reference.mo').write_text(source)
    paths=[msl/'Complex.mo',msl/'ModelicaServices/package.mo',msl/'Modelica/package.mo',lib/'OpenIPSL/package.mo',out/'Reference.mo']
    path=str(msl)+':'+str(lib);script='setCompiler("gcc");\nsetCXXCompiler("g++");\nsetModelicaPath('+json.dumps(path)+');\n'
    script+=''.join('loadFile('+json.dumps(str(f))+');\n' for f in paths)
    script+='buildModelFMU(ESSTReference,version="2.0",fmuType="me");\ngetErrorString();\n';(out/'run.mos').write_text(script)
    env=environment(root);env['OPENMODELICALIBRARY']=path
    with (out/'compiler.log').open('w') as log:subprocess.run([str(root/'runtime/usr/bin/omc'),'run.mos'],cwd=out,env=env,check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
    result=out/'ESSTReference.fmu'
    if not result.is_file():raise ValueError('native ESST1A compilation failed: '+str(out))
    return result,identity


class NativeExciter:
    def __init__(self,p,field0,voltage0,current0,out,root,stop,tolerance=1e-11):
        import xml.etree.ElementTree as ET
        root=Path(root).resolve();out=Path(out).resolve();archive,self.identity=build(p,field0,voltage0,current0,out,root,tolerance)
        path=str(root/'fmpy')
        if path not in sys.path:sys.path.insert(0,path)
        from fmpy.fmi2 import FMU2Model
        import fmpy
        if fmpy.__version__!=self.identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(root/'fmpy'):raise ValueError('wrong FMI runtime')
        folder=out/'unpacked';folder.mkdir()
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe native archive')
            z.extractall(folder)
        xml=ET.parse(folder/'modelDescription.xml').getroot();variables=list(xml.find('ModelVariables'))
        self.real={v.get('name'):int(v.get('valueReference')) for v in variables if v.find('Real') is not None}
        self.native_state_names=[variables[int(variables[int(e.get('index'))-1].find('Real').get('derivative'))-1].get('name') for e in xml.find('ModelStructure/Derivatives')]
        self.nx=len(self.native_state_names);self.nz=int(xml.get('numberOfEventIndicators'));self.p=p;self.next_time=math.inf
        if self.nx!=5:raise ValueError('five-state ESST1A native profile required: '+str(self.native_state_names))
        self.f=FMU2Model(guid=xml.get('guid'),modelIdentifier=xml.find('ModelExchange').get('modelIdentifier'),unzipDirectory=str(folder),instanceName=p.generator_id)
        self.f.instantiate();self.f.setupExperiment(startTime=0.,stopTime=stop,tolerance=tolerance);self.f.enterInitializationMode()
        self.set_inputs([voltage0,current0,0.,p.oel_ceiling_pu,p.uel_error_floor_pu,p.uel_field_floor_pu,0.,0.,0.]);self.f.exitInitializationMode();self.update(initial=True)
        rates=np.zeros(self.nx);self.f.getDerivatives(np.ctypeslib.as_ctypes(rates),self.nx)
        self.initial_residual=float(max(abs(rates)))
        if not np.isfinite(rates).all() or self.initial_residual>1e-8 or abs(self.output()-field0)>1e-8:raise ValueError('native ESST1A initialization is not stationary')
        self.a=None;self.z=None
    def set_inputs(self,values):self.f.setReal([self.real[n] for n in INPUTS],list(map(float,values)))
    def update(self,initial=False):
        if not initial:self.f.enterEventMode()
        for _ in range(100):
            info=self.f.newDiscreteStates()
            if info[1]:raise ValueError('native ESST1A termination requested')
            if not info[0]:break
        else:raise ValueError('native ESST1A event iteration budget')
        self.next_time=info[5] if info[4] else math.inf;self.f.enterContinuousTimeMode()
    def output(self):return self.f.getReal([self.real['out_field_pu']])[0]
    def signals(self):
        names=[*REAL,'reference_pu','oel_active','uel2_active','uel3_active','rectifier_upper_active','rectifier_lower_active']
        return dict(zip(names,self.f.getReal([self.real['out_'+n] for n in names])))
    def close(self):self.f.terminate();self.f.freeInstance()
