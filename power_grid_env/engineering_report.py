"""Offline engineering traces, numerical comparisons and a standalone SVG viewer."""
from pathlib import Path
import hashlib,html,json,math
from power_grid_env.study_artifacts import read_json,encoded,MAX_FILE
from power_grid_env.report import write_json

MAX_SCALARS=2_000_000


def unit_for(key):
    name=key.rsplit('.',1)[-1]
    for suffix,unit in [('_rad_s','rad/s'),('_rad','rad'),('_relative_residual','1'),('_temperature_c','degC'),('_ohm','ohm'),('_vs','V s'),('_nm','N m'),('_hz','Hz'),('_pu','pu'),('_v','V'),('_a','A'),('_var','var'),('_w','W'),('_j','J')]:
        if name.endswith(suffix):return unit
    return {'omega':'pu','delta':'rad','a':'rad','v':'pu','speed_pu':'pu','progress':'1','active':'1','latched':'1','closed':'1','connected':'1','thermal_level':'1'}.get(name,'not declared')


def phase_values(result):
    values={}
    for row in result.get('voltages',[]):
        key='node:'+row['bus_id']+':'+row['phase'];v=complex(row['v_real_v'],row['v_imag_v'])
        values[key+'.voltage_pu']=abs(v)/row['v_base_ln_v'];values[key+'.angle_rad']=math.atan2(v.imag,v.real)
    for row in result.get('flows',[]):
        key='equipment:'+row['equipment_id']+':'+str(row['terminal_index'])+':'+row['phase']
        values[key+'.current_a']=abs(complex(row['i_into_real_a'],row['i_into_imag_a']))
        values[key+'.p_w']=row['p_into_w'];values[key+'.q_var']=row['q_into_var']
    return values


def result_series(report,root):
    if 'time_s' in report and isinstance(report.get('time_s'),list) and isinstance(report.get('channels'),dict):
        times=report['time_s'];channels=report['channels'];sides=report.get('sample_side',['regular']*len(times))
    elif 'result' in report and isinstance(report['result'],dict):
        times=[0.];channels={k:[v] for k,v in phase_values(report['result']).items()};sides=['snapshot']
    elif (Path(root)/'engine/intervals.jsonl').exists():
        rows=[];times=[];max_channels=0
        with (Path(root)/'engine/intervals.jsonl').open('rb') as stream:
            for raw in stream:
                if len(raw)>64_000_000:raise ValueError('QSTS row byte budget exceeded')
                row=json.loads(raw);rows.append(phase_values(row['result']));times.append(float(row['step']['time_s']))
                max_channels=max(max_channels,len(rows[-1]))
                if len(rows)*max_channels>MAX_SCALARS:raise ValueError('engineering series scalar budget exceeded')
        keys=sorted(set().union(*(r.keys() for r in rows))) if rows else []
        channels={k:[r.get(k) for r in rows] for k in keys};sides=['interval_end']*len(times)
    else:return dict(time_s=[],sample_side=[],channels={},units={},available=False,reason='this result has no supported time/phase series; inspect result.json')
    if len(times)*len(channels)>MAX_SCALARS or len(sides)!=len(times):raise ValueError('series shape/budget')
    if not all(isinstance(t,(int,float)) and math.isfinite(t) for t in times) or any(a>b for a,b in zip(times,times[1:])):raise ValueError('invalid time axis')
    for key,row in channels.items():
        if len(row)!=len(times) or any(v is not None and (not isinstance(v,(int,float)) or not math.isfinite(v)) for v in row):raise ValueError('invalid numeric series '+key)
    return dict(time_s=times,sample_side=sides,channels=channels,units={k:unit_for(k) for k in channels},available=bool(channels) and bool(times),
        missing_samples={k:sum(v is None for v in row) for k,row in channels.items()})


def compare_runs(left,right,*,absolute=1e-8,relative=1e-6,time_absolute_s=1e-9):
    if not all(math.isfinite(v) and v>=0 for v in [absolute,relative,time_absolute_s]):raise ValueError('finite nonnegative comparison tolerances required')
    for root in [left,right]:
        from power_grid_env.study_artifacts import verify
        verify(root)
    a=read_json(Path(left)/'series.json');b=read_json(Path(right)/'series.json')
    jobs=[read_json(Path(p)/'run.json') for p in [left,right]]
    if not all(j['completed'] for j in jobs):return dict(passed=False,reason='incomplete_or_failed_run')
    if not a['available'] or not b['available']:return dict(passed=False,reason='unsupported_or_empty_series')
    if a['channels'].keys()!=b['channels'].keys() or a['units']!=b['units']:return dict(passed=False,reason='channel_or_unit_inventory_differs')
    if len(a['time_s'])!=len(b['time_s']) or a['sample_side']!=b['sample_side'] or any(abs(x-y)>time_absolute_s for x,y in zip(a['time_s'],b['time_s'])):return dict(passed=False,reason='sample_axes_or_event_sides_differ_no_interpolation')
    checks={}
    for key,x in a['channels'].items():
        y=b['channels'][key]
        if any(v is None for v in x+y):return dict(passed=False,reason='missing_or_unavailable_samples',channel=key)
        errors=[abs(i-j) for i,j in zip(x,y)];allowed=[absolute+relative*abs(i) for i in x]
        checks[key]=dict(passed=all(e<=tol for e,tol in zip(errors,allowed)),maximum_absolute=max(errors,default=0.),rms=math.sqrt(sum(e*e for e in errors)/max(1,len(errors))),unit=a['units'][key])
    return dict(passed=all(c['passed'] for c in checks.values()),channels=checks,absolute=absolute,relative=relative,time_absolute_s=time_absolute_s,
        same_study=jobs[0]['study_sha256']==jobs[1]['study_sha256'],same_producer=jobs[0]['code_sha256']==jobs[1]['code_sha256'],scope='numeric agreement under stated tolerances; not equipment acceptance')


JS=r'''
const payload=JSON.parse(document.getElementById('payload').textContent),s=payload.series;
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
function node(tag,attrs,text){let n=document.createElementNS(NS,tag);for(let [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
const keys=Object.keys(s.channels);for(const k of keys){let o=document.createElement('option');o.value=k;o.textContent=k+' ['+s.units[k]+']';$('channel').appendChild(o);}
function plot(){let svg=$('plot');svg.replaceChildren();let k=$('channel').value;if(!k){$('stats').textContent=s.reason||'No series';return;}
let raw=s.channels[k],good=raw.filter(x=>x!==null),t=s.time_s;if(!good.length)return;
let lo=Infinity,hi=-Infinity;for(let v of good){lo=Math.min(lo,v);hi=Math.max(hi,v);}let a=t[0],b=t[t.length-1],scale=hi-lo||Math.max(1,Math.abs(hi)*.02);
const X=x=>55+880*(x-a)/(b-a||1),Y=y=>260-210*(y-lo)/scale;
svg.appendChild(node('path',{d:'M55 35V260H935',fill:'none',stroke:'#9eabb8'}));
svg.appendChild(node('text',{x:55,y:290,fill:'#b7c6d3'},String(a)+' s'));svg.appendChild(node('text',{x:845,y:290,fill:'#b7c6d3'},String(b)+' s'));
svg.appendChild(node('text',{x:55,y:24,fill:'#b7c6d3'},String(hi)+' '+s.units[k]));svg.appendChild(node('text',{x:55,y:278,fill:'#b7c6d3'},String(lo)));
if(t.length===1)svg.appendChild(node('circle',{cx:X(t[0]),cy:Y(good[0]),r:4,fill:'#63d8b5'}));
// Min/max buckets preserve extrema; missing samples split paths rather than becoming zero.
let keep=new Set([0,raw.length-1]),step=Math.max(1,Math.ceil(raw.length/1500));
for(let i=0;i<raw.length;i+=step){let end=Math.min(i+step,raw.length),min=-1,max=-1;for(let j=i;j<end;j++){if(raw[j]===null){keep.add(j);continue;}if(min<0||raw[j]<raw[min])min=j;if(max<0||raw[j]>raw[max])max=j;}if(min>=0)keep.add(min);if(max>=0)keep.add(max);}
let d='',open=false;for(let i of [...keep].sort((a,b)=>a-b)){if(raw[i]===null){open=false;continue;}d+=(open?'L':'M')+X(t[i])+' '+Y(raw[i]);open=true;}
svg.appendChild(node('path',{d,fill:'none',stroke:'#70dfba','stroke-width':2}));
$('stats').textContent='min '+lo+' · max '+hi+' · samples '+raw.length+' · unavailable '+raw.filter(x=>x===null).length;
}
$('channel').addEventListener('change',plot);plot();
function download(name,data,type){let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([data],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
$('csv').onclick=()=>{let k=$('channel').value;if(!k)return;let rows=['time_s,event_side,value'];for(let i=0;i<s.time_s.length;i++)rows.push([s.time_s[i],JSON.stringify(s.sample_side[i]),s.channels[k][i]??''].join(','));download('channel.csv',rows.join('\n'),'text/csv');};
$('svg').onclick=()=>download('plot.svg',new XMLSerializer().serializeToString($('plot')),'image/svg+xml');
$('definition').value=JSON.stringify(payload.study,null,2);
$('save').onclick=()=>{try{const spec=JSON.parse($('definition').value);download('study.json',JSON.stringify(spec,null,2),'application/json');$('edit-status').textContent='Saved for local workbench revise/check. This editor does not run a solver.';}catch(e){$('edit-status').textContent='Invalid JSON: '+e.message;}};
let graph=$('network'),nodes=payload.study.network.connectivity_nodes,terms=Object.fromEntries(payload.study.network.terminals.map(t=>[t.id,t.connectivity_node_id]));
let xy=Object.fromEntries(nodes.map((n,i)=>[n.id,[55+(i%8)*120,45+Math.floor(i/8)*80]]));
graph.setAttribute('viewBox','0 0 1000 '+Math.max(150,Math.ceil(nodes.length/8)*80+20));
for(const e of [...payload.study.network.lines,...payload.study.network.switches]){let a=xy[terms[e.terminal_from]],b=xy[terms[e.terminal_to]];if(a&&b)graph.appendChild(node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#7797b2','stroke-dasharray':e.closed===false?'5 5':''}));}
for(const e of payload.study.network.transformers){let points=e.windings.map(w=>xy[terms[w.terminal_id]]).filter(Boolean);if(points.length<2)continue;let cx=points.reduce((s,p)=>s+p[0],0)/points.length,cy=points.reduce((s,p)=>s+p[1],0)/points.length;for(let p of points)graph.appendChild(node('line',{x1:p[0],y1:p[1],x2:cx,y2:cy,stroke:'#eebc72'}));graph.appendChild(node('circle',{cx,cy,r:11,fill:'#192a3c',stroke:'#eebc72'}));graph.appendChild(node('text',{x:cx+14,y:cy-8,fill:'#eebc72','font-size':11},e.id));}
for(let n of nodes){let [x,y]=xy[n.id];graph.appendChild(node('circle',{cx:x,cy:y,r:8,fill:'#70dfba'}));graph.appendChild(node('text',{x:x-28,y:y+26,fill:'#c7d3dd','font-size':12},n.id));}
'''


def write_engineering_report(root,spec,result,meta):
    root=Path(root);series=result_series(result,root);write_json(root/'series.json',series)
    payload=dict(study=spec.model_dump(mode='json'),series=series)
    data=encoded(payload).decode().replace('<','\\u003c').replace('&','\\u0026')
    script_hash=__import__('base64').b64encode(hashlib.sha256(JS.encode()).digest()).decode()
    event_text=html.escape(json.dumps(result.get('event_log',[]),ensure_ascii=False,indent=2))
    summary=html.escape(json.dumps(meta,ensure_ascii=False,indent=2));title=html.escape(spec.id)
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{title} — Engineering study</title><style>body{{background:#101c29;color:#d9e4ec;font:15px system-ui;margin:24px auto;max-width:1100px;padding:0 20px}}section{{background:#192a3c;padding:20px;margin:18px 0;border-radius:10px}}h1,h2{{color:#8ee0c2}}select,button,textarea{{background:#243b50;color:#eef6ff;border:1px solid #54748f;padding:9px;border-radius:5px}}textarea{{width:96%;height:280px;font-family:monospace}}svg{{width:100%;max-height:420px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}button{{cursor:pointer;margin:8px 5px 0 0}}.note{{color:#aabecb}}</style>
<h1>{title}</h1><p class="note">Локальный инженерный отчёт · {html.escape(meta['status'])} · Исходные единицы сохранены. Численная сходимость не равна пригодности оборудования.</p>
<section><h2>Результаты</h2><select id="channel" aria-label="Канал"></select><p id="stats"></p><svg id="plot" viewBox="0 0 1000 310" role="img" aria-label="График выбранного канала"></svg><button id="csv">Скачать CSV канала</button><button id="svg">Скачать SVG</button></section>
<section><h2>Схема исходной ревизии</h2><p class="note">Узлы, линии, выключатели и трансформаторы до событий. Остальные паспорта доступны в определении исследования.</p><svg id="network" role="img" aria-label="Связность исходной сети"></svg></section>
<section><h2>События</h2><pre>{event_text}</pre></section><section><h2>Определение исследования</h2><p>Измените JSON, сохраните study.json и примените через workbench revise/check. Python-модули редактировать не нужно.</p><textarea id="definition" aria-label="StudySpec JSON"></textarea><br><button id="save">Сохранить study.json</button><p id="edit-status"></p></section>
<section><h2>Происхождение запуска</h2><pre>{summary}</pre></section><script id="payload" type="application/json">{data}</script><script>{JS}</script></html>'''
    if len(page.encode())>MAX_FILE:raise ValueError('HTML report byte budget exceeded')
    (root/'report.html').write_text(page,encoding='utf-8')
