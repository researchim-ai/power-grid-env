"""Bounded Studio model/task exchange; data only, explicit conversion reports.

No MATLAB, DSS scripts, pickle, remote includes or client-selected paths execute.
MATPOWER is a declared passive balanced subset, not CIM/CGMES support.
"""
import csv
import hashlib
import io
import json
import math
import re
import sys

from benchmarks.grid_editor import Draft,solve

PACKAGE='power-grid-env.engineering-package/v1'
TASK='power-grid-env.engineering-task/v1'
GROUPS=('nodes','lines','transformers','switches','motors','loads','generators','events')
MAX_BYTES=262144


def strict_json(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate JSON field: '+key)
            result[key]=value
        return result
    return json.loads(text,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def project(value):return Draft.model_validate(value).model_dump(mode='json',by_alias=True)


def task(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'schema','id','title','criteria'} or value['schema']!=TASK:
        raise ValueError('task requires schema, id, title and criteria; no hidden answer fields')
    if not isinstance(value['id'],str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}',value['id']):raise ValueError('invalid task ID')
    if not isinstance(value['title'],str) or not 1<=len(value['title'].strip())<=200:raise ValueError('bounded task title required')
    c=value['criteria']
    if not isinstance(c,dict) or set(c)!={'minimum_voltage_pu','maximum_branch_loading_pct','all_loads_energized'}:
        raise ValueError('task criteria require voltage, loading and energization limits')
    for key,lo,hi in [('minimum_voltage_pu',.1,1.2),('maximum_branch_loading_pct',1,500)]:
        if isinstance(c[key],bool) or not isinstance(c[key],(int,float)) or not math.isfinite(c[key]) or not lo<=c[key]<=hi:raise ValueError('invalid criterion: '+key)
    if type(c['all_loads_energized']) is not bool:raise ValueError('energization criterion must be boolean')
    return value


def package(value):
    if isinstance(value,dict) and value.get('schema')==PACKAGE:
        if set(value) not in ({'schema','project','task'},{'schema','project','task','conversion'}):raise ValueError('package requires schema, project, task and optional conversion provenance')
        conversion=value.get('conversion')
        if conversion is not None:
            if not isinstance(conversion,dict) or set(conversion)!={'source_format','input_sha256','warnings'} or conversion['source_format']!='matpower_v2' or not re.fullmatch(r'[a-f0-9]{64}',str(conversion['input_sha256'])) or not isinstance(conversion['warnings'],list) or any(not isinstance(w,str) or len(w)>1000 for w in conversion['warnings']):raise ValueError('invalid conversion provenance')
        return dict(schema=PACKAGE,project=project(value['project']),task=task(value['task']),conversion=conversion)
    return dict(schema=PACKAGE,project=project(value),task=None,conversion=None)


def package_schema():
    network=Draft.model_json_schema(by_alias=True)
    definitions=network.pop('$defs',{});definitions['StudioProject']=network
    criteria=dict(type='object',additionalProperties=False,required=['minimum_voltage_pu','maximum_branch_loading_pct','all_loads_energized'],properties=dict(
        minimum_voltage_pu=dict(type='number',minimum=.1,maximum=1.2),maximum_branch_loading_pct=dict(type='number',minimum=1,maximum=500),all_loads_energized=dict(type='boolean')))
    definitions['EngineeringTask']=dict(type='object',additionalProperties=False,required=['schema','id','title','criteria'],properties=dict(
        schema=dict(const=TASK),id=dict(type='string',pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$'),title=dict(type='string',minLength=1,maxLength=200,pattern=r'\S'),criteria=criteria))
    conversion=dict(type='object',additionalProperties=False,required=['source_format','input_sha256','warnings'],properties=dict(
        source_format=dict(const='matpower_v2'),input_sha256=dict(type='string',pattern='^[a-f0-9]{64}$'),warnings=dict(type='array',items=dict(type='string',maxLength=1000))))
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:power-grid-env:engineering-package:v1',
        'type':'object','additionalProperties':False,'required':['schema','project','task'],'$defs':definitions,
        'properties':{'schema':{'const':PACKAGE},'project':{'$ref':'#/$defs/StudioProject'},'task':{'anyOf':[{'type':'null'},{'$ref':'#/$defs/EngineeringTask'}]},'conversion':{'anyOf':[{'type':'null'},conversion]}}}


def csv_export(data):
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['section','id','field','value_json'])
    def row(section,id,key,value):writer.writerow([section,id,key,json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))])
    row('exchange','','schema',PACKAGE);row('exchange','','conversion',data['conversion'])
    for key,value in data['project'].items():
        if key not in GROUPS:row('project','',key,value)
    for group in GROUPS:
        for index,item in enumerate(data['project'][group]):
            id=str(index)
            for key,value in item.items():row(group,id,key,value)
    row('task','','definition',data['task']);return out.getvalue()


def csv_import(text):
    rows=[r for r in csv.reader(io.StringIO(text.lstrip('\ufeff'),newline='')) if r]
    if not rows or rows[0]!=['section','id','field','value_json'] or len(rows)>8000:raise ValueError('expected Studio CSV section,id,field,value_json; at most8000 rows')
    records={};seen=set();meta={};definition=None;conversion=None
    for r in rows[1:]:
        if len(r)!=4:raise ValueError('exactly four CSV columns required')
        group,id,key,raw=r
        if (group,id,key) in seen:raise ValueError('duplicate CSV field')
        seen.add((group,id,key));value=strict_json(raw)
        if group in ('exchange','project','task') and id!='':raise ValueError('metadata CSV ID must be empty')
        if group=='exchange':
            if key=='schema' and value==PACKAGE:pass
            elif key=='conversion':conversion=value
            else:raise ValueError('unsupported CSV exchange metadata')
        elif group=='project':meta[key]=value
        elif group=='task':
            if key!='definition':raise ValueError('unknown task CSV field')
            definition=value
        elif group in GROUPS:
            if not re.fullmatch(r'0|[1-9][0-9]{0,3}',id):raise ValueError('bounded canonical CSV row index required')
            records.setdefault(group,{}).setdefault(int(id),{})[key]=value
        else:raise ValueError('unknown CSV section: '+group)
    if ('exchange','','schema') not in seen or ('task','','definition') not in seen:raise ValueError('complete CSV metadata required')
    for group in GROUPS:
        entries=records.get(group,{})
        if sorted(entries)!=list(range(len(entries))):raise ValueError('CSV row indices must be contiguous')
        meta[group]=[entries[i] for i in sorted(entries)]
    return package(dict(schema=PACKAGE,project=meta,task=definition,conversion=conversion))


def matpower_import(text):
    """Literal v2 bus/gen/branch subset; strict rejection prevents silent drops."""
    clean='\n'.join(line.split('%',1)[0] for line in text.splitlines())
    clean=re.sub(r'^\s*function\s+mpc\s*=\s*[A-Za-z]\w*\s*(?:\(\s*\))?\s*\n','',clean,count=1)
    clean=re.sub(r'\bend\s*;?\s*$','',clean)
    fields={}
    pattern=re.compile(r"\s*mpc\.(version|baseMVA|bus|gen|branch|gencost)\s*=\s*(\[.*?\]|['\"]2['\"]|[-+0-9.eE]+)\s*;",re.S)
    pos=0
    while clean[pos:].strip():
        m=pattern.match(clean,pos)
        if not m:raise ValueError('MATPOWER data only: unsupported statement, field or executable expression')
        name,raw=m.groups()
        if name in fields:raise ValueError('repeated MATPOWER assignment')
        fields[name]=raw;pos=m.end()
    if not {'version','baseMVA','bus','gen','branch'}<=fields.keys() or fields['version'][1:-1]!='2':raise ValueError('MATPOWER v2 requires bus/gen/branch/baseMVA')
    def matrix(name):
        value=fields[name]
        if not value.startswith('['):raise ValueError('literal matrix required')
        parts=re.split(r';|\n',value[1:-1]);out=[]
        for part in parts:
            if not part.strip():continue
            if not re.fullmatch(r'[0-9eE+.,\-\s]+',part):raise ValueError('literal finite numeric matrix required')
            row=[float(x) for x in part.replace(',',' ').split()]
            if not all(math.isfinite(x) for x in row):raise ValueError('finite MATPOWER data required')
            out.append(row)
        return out
    base=float(fields['baseMVA'])
    if not math.isfinite(base) or base<=0:raise ValueError('positive finite baseMVA required')
    buses=matrix('bus');gens=matrix('gen');branches=matrix('branch');warnings=[]
    if not 2<=len(buses)<=16 or len(branches)>15:raise ValueError('Studio subset supports2–16 buses and at most15 lines')
    if any(len(b)!=13 for b in buses) or any(len(b)!=13 for b in branches) or any(len(g) not in (10,21) for g in gens):raise ValueError('MATPOWER input-column widths required; solved-result columns unsupported')
    ids=[b[0] for b in buses]
    if any(int(x)!=x or x<1 for x in ids) or len(set(ids))!=len(ids):raise ValueError('unique positive integer bus IDs required')
    slack=[b for b in buses if b[1]==3]
    if len(slack)!=1 or any(b[1] not in (1,3) for b in buses):raise ValueError('one slack, other PQ buses only; PV/isolated buses unsupported')
    voltage=slack[0][9]*1000
    if any(b[9]*1000!=voltage for b in buses):raise ValueError('MATPOWER multi-voltage/transformer conversion not supported')
    if any(b[4]!=0 or b[5]!=0 or b[7]!=1 or b[8]!=0 or b[11]!=1.1 or b[12]!=.9 for b in buses):
        raise ValueError('bus shunts, nonflat initial conditions or custom voltage limits are not represented by this editor subset')
    if any(len(g)==21 and any(g[10:]) for g in gens):raise ValueError('generator ramp/capability fields unsupported')
    if len(gens)!=1 or gens[0][0]!=slack[0][0] or gens[0][7]!=1 or gens[0][5]!=1:raise ValueError('one active source generator at slack with Vg=1 required; distributed generators unsupported')
    if any(gens[0][i]!=0 for i in (1,2)):raise ValueError('specified slack P/Q setpoints are not represented')
    if 'gencost' in fields:
        costs=matrix('gencost')
        if len(costs)!=1 or costs[0]!=[2.,0.,0.,2.,0.,0.]:raise ValueError('nonzero economic objectives are outside the model-exchange profile')
    d=dict(schema='power-grid-env.grid-editor/v5',profile='ac_power_flow',source_node='bus_'+str(int(slack[0][0])),nominal_ll_v=voltage,
        source_r_ohm=.0001,source_x_ohm=.0001,duration_s=1.,step_s=.0025,nodes=[],lines=[],motors=[],loads=[],events=[],transformers=[],generators=[],switches=[],fault=dict(node_id='bus_'+str(int(slack[0][0])),r_ohm=.001,x_ohm=0.))
    for i,b in enumerate(buses):
        id='bus_'+str(int(b[0]));d['nodes'].append(dict(id=id,nominal_ll_v=voltage,x=120.+(i%4)*350.,y=150.+(i//4)*250.))
        if b[2]<0 or b[3]<0:raise ValueError('negative loads require an explicit generator model')
        if b[2] or b[3]:d['loads'].append(dict(id='load_'+str(int(b[0])),node_id=id,p_kw=b[2]*1000,q_kvar=b[3]*1000,model='constant_pq'))
    zbase=voltage**2/(base*1e6)
    for i,b in enumerate(branches):
        if b[0] not in ids or b[1] not in ids or b[4]!=0 or b[8] not in (0,1) or b[9]!=0 or b[10]!=1 or b[11]!=-360 or b[12]!=360:
            raise ValueError('branch charging, taps, phase shifts, open branches or angle constraints are not supported')
        if b[5]<=0 or b[6]!=b[5] or b[7]!=b[5]:raise ValueError('one positive identical RATE_A/B/C required; unlimited/seasonal ratings unsupported')
        d['lines'].append(dict(id='line_'+str(i+1),from_node='bus_'+str(int(b[0])),to_node='bus_'+str(int(b[1])),length_m=1000.,r_ohm_km=b[2]*zbase,x_ohm_km=b[3]*zbase,rating_a=b[5]*1e6/(math.sqrt(3)*voltage)))
    d=project(d)
    warnings=['Идеальный источник MATPOWER заменён источником с R=X=0.0001 Ом. Это меняет расчётную сеть.',
              'RATE_A в МВА переведён в амперы при номинальном напряжении. При отклонении напряжения эти ограничения не эквивалентны.',
              'Координаты редактора созданы автоматически. В этом переносе MATPOWER нет динамики двигателей и условий задачи.',
              'Пределы P/Q источника и метаданные area/zone не применяются в этом профиле редактора.']
    return dict(schema=PACKAGE,project=d,task=None,conversion=dict(source_format='matpower_v2',input_sha256=hashlib.sha256(text.encode()).hexdigest(),warnings=warnings)),warnings


def import_model(format,content):
    if not isinstance(content,str) or len(content.encode('utf-8'))>MAX_BYTES:raise ValueError('model text exceeds256KB')
    if format in ('project_json','package_json'):data=package(strict_json(content));warnings=data['conversion']['warnings'] if data['conversion'] else []
    elif format=='studio_csv':data=csv_import(content);warnings=data['conversion']['warnings'] if data['conversion'] else []
    elif format=='matpower_v2':data,warnings=matpower_import(content)
    else:raise ValueError('unsupported exchange format; CIM/CGMES and DSS execution are not accepted here')
    return dict(package=data,report=dict(format=format,lossless=not warnings,requires_acknowledgement=bool(warnings),warnings=warnings,
        equipment_counts={k:len(data['project'][k]) for k in GROUPS},task_preserved=data['task'] is not None,
        input_sha256=hashlib.sha256(content.encode()).hexdigest(),scope='Studio bounded profile; unsupported equipment rejected, no automatic solver qualification'))


def export_model(format,value):
    data=package(value)
    if format=='package_json':content=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False);filename='network-task.pge.json'
    elif format=='project_json':
        if data['task'] is not None or data['conversion'] is not None:raise ValueError('project JSON would lose task criteria or conversion provenance; use engineering package or CSV')
        content=json.dumps(data['project'],ensure_ascii=False,indent=2,allow_nan=False);filename='network.project.json'
    elif format=='studio_csv':content=csv_export(data);filename='network-task.csv'
    else:raise ValueError('export supports native project/package JSON and lossless Studio CSV; MATPOWER export not yet supported')
    return dict(content=content,filename=filename,mime='text/csv' if format=='studio_csv' else 'application/json',lossless=True)


def evaluate_task(value):
    data=package(value)
    if data['task'] is None:raise ValueError('package has no task criteria')
    if data['project']['profile'] not in ('passive_power_flow','ac_power_flow'):raise ValueError('task evaluation currently supports stationary PF only')
    result=solve(data['project']);c=data['task']['criteria'];violations=[]
    voltages=[p['voltage_pu'] for n in result['nodes'] if n['energized'] for p in n['phases'].values() if p['energized']]
    loading=[b['loading_pct'] for b in result['branches'] if b['loading_pct'] is not None]
    if not result['converged']:violations.append('not_converged')
    if not voltages or min(voltages)<c['minimum_voltage_pu']:violations.append('minimum_voltage')
    if any(v>c['maximum_branch_loading_pct'] for v in loading):violations.append('branch_loading')
    dead={n['id'] for n in result['nodes'] if not n['energized']}
    if c['all_loads_energized'] and any(l['node_id'] in dead for l in data['project']['loads']):violations.append('load_deenergized')
    return dict(passed=not violations,violations=violations,minimum_voltage_pu=min(voltages) if voltages else None,
        maximum_branch_loading_pct=max(loading,default=None),scope='User-supplied stationary study criteria; not a trusted agent reward endpoint or hidden benchmark import')


def main():
    try:
        r=strict_json(sys.stdin.buffer.read(MAX_BYTES+1).decode('utf-8'))
        if r['action']=='schema':result=package_schema()
        elif r['action']=='import':result=import_model(r['format'],r['content'])
        elif r['action']=='export':result=export_model(r['format'],r['package'])
        elif r['action']=='evaluate':result=evaluate_task(r['package'])
        else:raise ValueError('unknown exchange action')
        print(json.dumps(dict(ok=True,result=result),ensure_ascii=False,allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(ok=False,error=str(exc)[:2000]),ensure_ascii=False));sys.exit(1)
if __name__=='__main__':main()
