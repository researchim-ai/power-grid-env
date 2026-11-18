"""Deterministic public single-line SVG for a constructed radial design.

Shows the proposed equipment and owner load sites only. No verdict, hidden
labels, future events or simulated trajectory values are included.
"""
from html import escape
from pathlib import Path
from power_grid_env.network_design import compile_network
from power_grid_env.study_artifacts import encoded,seal
from power_grid_env.evidence import package_fingerprint


def observation(task,proposal):
    task,proposal,plan,c=compile_network(task,proposal)
    n=plan.cases[0].study.network
    return dict(schema='power-grid-env.network-design-observation/v1',root_node=c['static']['root_node'],
        nominal_ll_v=n.voltage_levels[0].nominal_ll_v,
        transformer_id=proposal.transformer_id,
        nodes=[dict(node_id=node,demand_kva=task.node_demand_kva[node],
                    motor_ids=sorted({m.id for case in plan.cases for m in case.study.induction.motors if m.node_id==node})) for node in sorted(task.node_demand_kva)],
        branches=[dict(corridor_id=r['corridor_id'],parent_node=r['parent_node'],child_node=r['child_node'],cable_id=r['cable_id'],
                       length_m=next(x.installation.length_m for x in task.corridors if x.id==r['corridor_id'])) for r in c['static']['branches']],
        scope='Public proposed single-line connection and catalog IDs; load-site envelope, no future events or acceptance labels.')


def single_line_svg(task,proposal):
    data=observation(task,proposal);root=data['root_node'];children={r['node_id']:[] for r in data['nodes']}
    for branch in data['branches']:children[branch['parent_node']].append(branch['child_node'])
    points={};leaf=0
    def place(node,depth):
        nonlocal leaf
        values=[place(child,depth+1) for child in sorted(children[node])]
        if values:y=sum(values)/len(values)
        else:y=150+leaf*170;leaf+=1
        points[node]=(210+depth*310,y);return y
    place(root,0)
    width=max(x for x,y in points.values())+250;height=max(y for x,y in points.values())+150
    def label(value,limit=24):
        value=str(value)
        return '<title>'+escape(value)+'</title>'+escape(value if len(value)<=limit else value[:limit-1]+'…')
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Proposed radial power network">',
        '<rect width="100%" height="100%" fill="#0b1423"/>',
        '<style>text{font-family:DejaVu Sans,sans-serif;fill:#dce9f7;font-size:13px}.wire{stroke:#58d7d0;stroke-width:3;fill:none}.muted{fill:#98adc6;font-size:12px}.title{font-size:20px;font-weight:bold}</style>',
        '<text x="24" y="35" class="title">PROPOSED POWER NETWORK</text>',
        f'<text x="24" y="61" class="muted">{data["nominal_ll_v"]:g} V · 50 Hz · catalog-constrained radial design</text>']
    rx,ry=points[root]
    out += [f'<path class="wire" d="M 24 {ry} H {rx}"/>',f'<circle cx="88" cy="{ry}" r="20" fill="#0b1423" stroke="#58d7d0" stroke-width="3"/>',
            f'<circle cx="113" cy="{ry}" r="20" fill="#0b1423" stroke="#58d7d0" stroke-width="3"/>',
            f'<text x="24" y="{ry-38}">{label(data["transformer_id"])}</text>']
    for b in data['branches']:
        x0,y0=points[b['parent_node']];x1,y1=points[b['child_node']];middle=x0+40
        out.append(f'<path class="wire" d="M {x0} {y0} H {middle} V {y1} H {x1}"/>')
        out.append(f'<text x="{middle+12}" y="{y1-30}">{label(b["corridor_id"],16)} · {b["length_m"]:g} m</text>')
        out.append(f'<text class="muted" x="{middle+12}" y="{y1-12}">{label(b["cable_id"],28)}</text>')
    for n in data['nodes']:
        x,y=points[n['node_id']]
        out.append(f'<path d="M {x} {y-18} V {y+18}" stroke="#eef6ff" stroke-width="7"/>')
        out.append(f'<text x="{x-7}" y="{y-24}">{label(n["node_id"],12)}</text>')
        if n['motor_ids']:
            out.append(f'<path class="wire" d="M {x} {y+18} V {y+37}"/><circle cx="{x}" cy="{y+54}" r="17" fill="#14283d" stroke="#58d7d0" stroke-width="2"/>')
            out.append(f'<text x="{x-6}" y="{y+59}">M</text><text x="{x+27}" y="{y+51}">{label(", ".join(n["motor_ids"]),24)}</text>')
            out.append(f'<text class="muted" x="{x+27}" y="{y+70}">site demand {n["demand_kva"]:g} kVA</text>')
    out.append(f'<text class="muted" x="24" y="{height-25}">Proposed topology and load sites only · no simulation verdict or future events</text></svg>')
    return '\n'.join(out)


def export_view(task,proposal,out):
    data=observation(task,proposal);svg=single_line_svg(task,proposal)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'observation.json').write_bytes(encoded(data));(root/'single-line.svg').write_text(svg)
    result=dict(schema='power-grid-env.network-design-view/v1',code_sha256=package_fingerprint(),public_observation=True,
                nodes=len(data['nodes']),branches=len(data['branches']),contains_verdict=False,contains_future_events=False,
                scope='Proposed radial network diagram; SVG public observations, no VLM training or image-grounding qualification')
    (root/'receipt.json').write_bytes(encoded(result));seal(root);return result
