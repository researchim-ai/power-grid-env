"""Public visual grounding:readable aliases,complete IDs and paired controls.

Counterfactuals are passport-readout examples,not new qualified power studies.
No hidden cause,teacher action,future solver result or raw filename enters pixels.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from benchmarks.engineering_rollout import parse_action

FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
MONO='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'


def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def scene_from_messages(messages):
    initial=json.loads(messages[1]['content']);task=initial['task'];project=None;seen_revision=None;revision=initial['revision']
    for i in range(2,len(messages),2):
        reply=json.loads(messages[i+1]['content']);revision=reply.get('observation',{}).get('revision',revision)
        try:action=parse_action(messages[i]['content'],task['tools'])
        except (ValueError,TypeError):continue
        if action['name']=='inspect_project' and reply.get('ok') is True:project=reply['result'];seen_revision=revision
    if project is None:raise ValueError('public inspection required')
    if seen_revision!=revision:raise ValueError('stale public snapshot;inspect again after mutation')
    req=task['requirements']
    if len(project['nodes'])>8 or len(req['routes'])>7 or len(project['lines'])>7 or len(req['loads'])>7:raise ValueError('qualified scene size exceeded')
    nodes={n['id']:'N'+str(i+1) for i,n in enumerate(sorted(project['nodes'],key=lambda n:n['id']))}
    lines={l['id']:'L'+str(i+1) for i,l in enumerate(sorted(project['lines'],key=lambda l:l['id']))}
    routes={r['id']:'R'+str(i+1) for i,r in enumerate(sorted(req['routes'],key=lambda r:r['id']))}
    loads={l['id']:'D'+str(i+1) for i,l in enumerate(sorted(req['loads'],key=lambda l:l['id']))}
    scene=dict(schema='power-grid-env.public-grounding-scene/v1',revision=revision,source=nodes[req['source_node']],nominal_ll_v=f"{req['nominal_ll_v']:.0f}",
        nodes=sorted(nodes.values()),lines=[],routes=[],loads=[],catalog=[],scope='Passport observation only;no solved quantities or physical outcome labels')
    for l in sorted(project['lines'],key=lambda l:l['id']):scene['lines'].append(dict(id=lines[l['id']],from_node=nodes[l['from_node']],to_node=nodes[l['to_node']],
        length_m=f"{l['length_m']:.1f}",r_ohm_km=f"{l['r_ohm_km']:.3f}",x_ohm_km=f"{l['x_ohm_km']:.3f}",rating_a=f"{l['rating_a']:.2f}"))
    for r in sorted(req['routes'],key=lambda r:r['id']):
        found=[l for l in project['lines'] if {l['from_node'],l['to_node']}=={r['from_node'],r['to_node']}]
        if len(found)>1:raise ValueError('parallel-line scene is not qualified')
        scene['routes'].append(dict(id=routes[r['id']],from_node=nodes[r['from_node']],to_node=nodes[r['to_node']],length_m=f"{r['length_m']:.1f}",installed_line=lines[found[0]['id']] if found else 'absent'))
    for l in sorted(req['loads'],key=lambda l:l['id']):
        current=next((x for x in project['loads'] if x['id']==l['id']),None)
        scene['loads'].append(dict(id=loads[l['id']],required_node=nodes[l['node_id']],p_kw=f"{l['p_kw']:.2f}",q_kvar=f"{l['q_kvar']:.2f}",installed_node=nodes[current['node_id']] if current else 'absent'))
    for name,c in sorted(req['catalog'].items()):scene['catalog'].append(dict(id=name,r_ohm_km=f"{c['r_ohm_km']:.3f}",x_ohm_km=f"{c['x_ohm_km']:.3f}",rating_a=f"{c['rating_a']:.2f}"))
    aliases=dict(nodes=nodes,lines=lines,routes=routes,loads=loads)
    return scene,aliases


def render_scene(scene):
    img=Image.new('RGB',(1500,1100),'#f8fafc');d=ImageDraw.Draw(img)
    fonts={n:ImageFont.truetype(FONT,n) for n in (18,22,28)};mono=ImageFont.truetype(MONO,19)
    def text(x,y,value,size=22):d.text((x,y),str(value),font=fonts[size],fill='#152738')
    def table(x,y,header,rows):
        d.text((x,y),header,font=mono,fill='#1b5777')
        for i,row in enumerate(rows):d.text((x,y+33*(i+1)),row,font=mono,fill='#152738')
    text(30,18,'PUBLIC GRID / PASSPORT OBSERVATION',28);text(30,58,f"Revision {scene['revision']} | Source {scene['source']} | {scene['nominal_ll_v']} V | values as displayed",18)
    children={n:[] for n in scene['nodes']}
    for r in scene['routes']:children[r['from_node']].append(r['to_node'])
    depths={}
    def depth(n,k):
        depths[n]=k
        for c in children[n]:depth(c,k+1)
    depth(scene['source'],0)
    pos={};slot=[0];spacing=min(65,325/max(1,sum(not c for c in children.values())-1))
    def place(n,depth):
        ys=[]
        for c in sorted(children[n]):place(c,depth+1);ys.append(pos[c][1])
        y=sum(ys)/len(ys) if ys else 145+slot[0]*spacing
        if not ys:slot[0]+=1
        pos[n]=(60+depth*620/max(1,max(depths.values())),y)
    place(scene['source'],0)
    for r in scene['routes']:
        a,b=pos[r['from_node']],pos[r['to_node']];color='#235e80' if r['installed_line']!='absent' else '#8a98a5'
        d.line((*a,*b),fill=color,width=4 if r['installed_line']!='absent' else 1)
        text((a[0]+b[0])/2,(a[1]+b[1])/2-26,r['id']+('/'+r['installed_line'] if r['installed_line']!='absent' else '/absent'),18)
    for n,(x,y) in pos.items():
        d.ellipse((x-9,y-9,x+9,y+9),fill='#ffffff',outline='#235e80',width=3);text(x-18,y+12,n)
    text(730,110,'INSTALLED LINES',22)
    table(730,150,f"{'ID':<3}{'FROM':>4}{'TO':>3}{'L[m]':>7}{'R[ohm/km]':>10}{'X[ohm/km]':>10}{'I_rated[A]':>10}",[
        f"{l['id']:<3}{l['from_node']:>4}{l['to_node']:>3}{l['length_m']:>7}{l['r_ohm_km']:>10}{l['x_ohm_km']:>10}{l['rating_a']:>10}" for l in scene['lines']])
    text(30,540,'REQUIRED ROUTES / CURRENT CONNECTIONS')
    table(30,580,'ID FROM TO   L[m] INSTALLED',[
        f"{r['id']:<3}{r['from_node']:>4}{r['to_node']:>3}{r['length_m']:>7} {r['installed_line']:>9}" for r in scene['routes']])
    text(730,540,'REQUIRED LOADS / CURRENT PLACEMENT')
    table(730,580,'ID NEED   P[kW] Q[kvar] CURRENT',[
        f"{l['id']:<3}{l['required_node']:>5}{l['p_kw']:>8}{l['q_kvar']:>8} {l['installed_node']:>7}" for l in scene['loads']])
    text(30,900,'PUBLIC CATALOG')
    for i,c in enumerate(scene['catalog']):text(30,940+i*32,f"{c['id']}: R={c['r_ohm_km']} ohm/km  X={c['x_ohm_km']} ohm/km  I={c['rating_a']} A",18)
    text(30,1040,'No solved measurements or hidden fault labels. Aliases are public display handles.',18)
    out=io.BytesIO();img.save(out,format='PNG');return out.getvalue()


def focus_line(scene,line_id):
    """Question-selected zoom,not a hidden-fault-selected crop."""
    l=next(l for l in scene['lines'] if l['id']==line_id)
    img=Image.new('RGB',(1000,360),'white');d=ImageDraw.Draw(img)
    f=ImageFont.truetype(FONT,32);large=ImageFont.truetype(MONO,48)
    d.text((30,20),'PUBLIC EQUIPMENT VIEW / '+l['id'],font=f,fill='black')
    d.text((30,85),f"{l['from_node']} -> {l['to_node']}    L = {l['length_m']} m",font=f,fill='black')
    d.text((30,145),'Rated current [A]',font=f,fill='black');d.text((30,200),l['rating_a'],font=large,fill='black')
    d.text((30,285),f"R={l['r_ohm_km']} ohm/km   X={l['x_ohm_km']} ohm/km",font=f,fill='black')
    out=io.BytesIO();img.save(out,format='PNG');return out.getvalue()


def question_records(scene):
    for l in scene['lines']:
        yield dict(kind='line_rating',subject=l['id'],question=f"Read installed line {l['id']}. Return its rated current in A as JSON {{\"value\":\"printed decimal\"}}.",answer=dict(value=l['rating_a']))
        yield dict(kind='line_endpoints',subject=l['id'],question=f"Read installed line {l['id']}. Return JSON with from_node and to_node.",answer=dict(from_node=l['from_node'],to_node=l['to_node']))
    for r in scene['routes']:
        yield dict(kind='route_connection',subject=r['id'],question=f"Read required route {r['id']}. Return its installed line alias or absent as JSON {{\"value\":\"...\"}}.",answer=dict(value=r['installed_line']))
    for l in scene['loads']:
        yield dict(kind='required_load_node',subject=l['id'],question=f"Read required load {l['id']}. Return its required node alias as JSON {{\"value\":\"...\"}}.",answer=dict(value=l['required_node']))


def counterfactual(scene,line_id):
    out=copy.deepcopy(scene);line=next(l for l in out['lines'] if l['id']==line_id)
    line['rating_a']=f"{float(line['rating_a'])+37.:.2f}"
    return out


def resolve_action(scene,aliases,action,current_revision):
    """Experimental public-handle interface;does not choose equipment or edits."""
    if not isinstance(action,dict) or set(action)!={'name','arguments'} or not isinstance(action['arguments'],dict):raise ValueError('one handle action required')
    name=action['name'];args=copy.deepcopy(action['arguments'])
    fields={'inspect_project':set(),'validate':set(),'simulate':set(),'submit':{'cause','location'},'inspect_equipment':{'equipment_id'},
        'add_line':{'route_id','catalog_id'},'add_load':{'load_id'},'set_line_catalog':{'equipment_id','catalog_id'},'remove_line':{'equipment_id'}}
    if name not in fields or not isinstance(args,dict) or set(args)-fields[name]:raise ValueError('unknown handle action/arguments')
    if name!='inspect_project' and current_revision!=scene['revision']:raise ValueError('stale handle revision')
    inverse={alias:raw for group in aliases.values() for raw,alias in group.items()}
    for key in ('equipment_id','load_id','route_id','location'):
        if key in args:
            if args[key] not in inverse:raise ValueError('unknown public handle')
            if key=='route_id' and not args[key].startswith('R'):raise ValueError('route handle required')
            if key=='load_id' and not args[key].startswith('D'):raise ValueError('required load handle required')
            args[key]=inverse[args[key]]
    if name=='add_line':
        if set(args)!= {'route_id','catalog_id'}:raise ValueError('route and catalog required')
        args['equipment_id']='ln_'+hashlib.sha256(args['route_id'].encode()).hexdigest()[:12]
    return dict(name=name,arguments=args)
