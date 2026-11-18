"""Deterministic proposed SLD, numeric passport and past-only waveform PNGs."""
from pathlib import Path
import ctypes as C
import tempfile
import textwrap
from power_grid_env.design_frame import extrema_indices


def rasterize_generated_svg(svg,path):
    """Render internally generated SVG only; no external document ingestion."""
    try:r=C.CDLL('librsvg-2.so.2');c=C.CDLL('libcairo.so.2');g=C.CDLL('libgobject-2.0.so.0')
    except OSError as exc:raise ValueError('SLD PNG export requires system librsvg and Cairo') from exc
    r.rsvg_handle_new_from_file.argtypes=[C.c_char_p,C.c_void_p];r.rsvg_handle_new_from_file.restype=C.c_void_p
    class Dim(C.Structure):_fields_=[('width',C.c_int),('height',C.c_int),('em',C.c_double),('ex',C.c_double)]
    r.rsvg_handle_get_dimensions.argtypes=[C.c_void_p,C.POINTER(Dim)]
    r.rsvg_handle_render_cairo.argtypes=[C.c_void_p,C.c_void_p];r.rsvg_handle_render_cairo.restype=C.c_int
    c.cairo_image_surface_create.argtypes=[C.c_int,C.c_int,C.c_int];c.cairo_image_surface_create.restype=C.c_void_p
    c.cairo_create.argtypes=[C.c_void_p];c.cairo_create.restype=C.c_void_p
    c.cairo_surface_write_to_png.argtypes=[C.c_void_p,C.c_char_p];c.cairo_surface_write_to_png.restype=C.c_int
    c.cairo_destroy.argtypes=[C.c_void_p];c.cairo_surface_destroy.argtypes=[C.c_void_p];g.g_object_unref.argtypes=[C.c_void_p]
    with tempfile.TemporaryDirectory(prefix='pge-sld-') as directory:
        src=Path(directory)/'diagram.svg';src.write_text(svg);handle=r.rsvg_handle_new_from_file(str(src).encode(),None)
        if not handle:raise ValueError('generated SVG did not parse')
        surface=ctx=None
        try:
            d=Dim();r.rsvg_handle_get_dimensions(handle,C.byref(d))
            if not 1<=d.width<=4096 or not 1<=d.height<=4096:raise ValueError('SVG pixel budget exceeded')
            surface=c.cairo_image_surface_create(0,d.width,d.height);ctx=c.cairo_create(surface)
            if not surface or not ctx or not r.rsvg_handle_render_cairo(handle,ctx) or c.cairo_surface_write_to_png(surface,str(path).encode())!=0:raise ValueError('SVG PNG rendering failed')
            return dict(width=d.width,height=d.height)
        finally:
            if ctx:c.cairo_destroy(ctx)
            if surface:c.cairo_surface_destroy(surface)
            g.g_object_unref(handle)


def pyplot():
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:raise ValueError('PNG data export requires the vlm-data optional dependencies') from exc
    return plt


def passport_data(task,proposal,case):
    c=task.catalog
    selected={p.cable_id for p in proposal.branches}
    t=next(t for t in c.transformers if t.id==proposal.transformer_id)
    return dict(transformer=dict(id=t.id,rating_kva=t.rating_kva.value,secondary_v=t.secondary_v.value,
        impedance_percent=t.impedance_percent.value,load_loss_w=t.load_loss_w.value,
        short_time_a=t.short_time_a.value,short_time_s=t.short_time_s.value,
        short_time_evidence=next(d.kind for d in c.documents if d.id==t.short_time_a.document_id)),
        cables=[dict(id=p.id,area_mm2=p.area_mm2.value,air30_ampacity_a=p.ampacity_air30_a.value,
                     dc_r20_ohm_km=p.dc_r20_ohm_km.value,rated_voltage_v=p.rated_voltage_v.value) for p in c.cables if p.id in selected],
        motors=[dict(id=m.id,node_id=m.node_id,rated_va=m.rated_va,rated_ll_v=m.rated_ll_v,
                     stator_r_pu=m.stator_r_pu,rotor_r_pu=m.rotor_r_pu,magnetizing_x_pu=m.magnetizing_x_pu,
                     inertia_s=m.inertia_s) for m in sorted(case.study.induction.motors,key=lambda m:m.id)],
        assumptions={k:getattr(c.assumptions,k).value for k in ('conductor_temperature_c','ac_dc_resistance_factor','cable_reactance_ohm_km','temperature_factor','grouping_factor','other_factor')},
        scope='Public static numeric passport/model data; authored motor assumptions, no connection state or event schedule; not OEM authentication')


def passport_png(data,path):
    plt=pyplot()
    with plt.rc_context({'font.family':'DejaVu Sans','font.size':10}):
        fig=plt.figure(figsize=(12,7.2),dpi=100,facecolor='white');ax=fig.add_axes([.04,.03,.92,.92]);ax.axis('off')
        ax.text(0,.98,'EQUIPMENT PASSPORT / MODEL PARAMETERS',fontsize=17,weight='bold',va='top')
        ax.text(0,.91,'Public numeric extracts and explicit modeling assumptions',color='#42566c',fontsize=11)
        t=data['transformer']
        ax.text(0,.84,f'Transformer {t["id"]}  |  {t["rating_kva"]:g} kVA  |  {t["secondary_v"]:g} V  |  uk={t["impedance_percent"]:g}%  |  load loss={t["load_loss_w"]:g} W',fontsize=11)
        ax.text(0,.79,f'Short-time {t["short_time_a"]:g} A / {t["short_time_s"]:g} s; evidence: {t["short_time_evidence"]}',color='#6e411e')
        cable_rows=[[p['id'],f'{p["area_mm2"]:g}',f'{p["air30_ampacity_a"]:g}',f'{p["dc_r20_ohm_km"]:g}',f'{p["rated_voltage_v"]:g}'] for p in data['cables']]
        table=ax.table(cellText=cable_rows,colLabels=['Cable ID','Area mm2','Air30 A','R20 ohm/km','Rated V'],bbox=[0,.57,1,.17]);table.auto_set_font_size(False);table.set_fontsize(10)
        motor_rows=[[p['id'],p['node_id'],f'{p["rated_va"]/1000:g}',f'{p["rated_ll_v"]:g}',f'{p["stator_r_pu"]:g}',f'{p["rotor_r_pu"]:g}',f'{p["magnetizing_x_pu"]:g}',f'{p["inertia_s"]:g}'] for p in data['motors']]
        table=ax.table(cellText=motor_rows,colLabels=['Motor','Site','kVA','V','Rs pu','Rr pu','Xm pu','H s'],bbox=[0,.25,1,.25]);table.auto_set_font_size(False);table.set_fontsize(10)
        a=data['assumptions']
        lines=[f'Conductor {a["conductor_temperature_c"]:g} C; AC/DC factor {a["ac_dc_resistance_factor"]:g}; generic X={a["cable_reactance_ohm_km"]:g} ohm/km.',
               f'Derating factors: temperature {a["temperature_factor"]:g}, grouping {a["grouping_factor"]:g}, other {a["other_factor"]:g}.',
               'Motor parameters are authored assumptions. Short-time limits may be owner assumptions. No future commands or feasibility verdict.']
        for y,line in zip((.16,.11,.055),lines):ax.text(0,y,'\n'.join(textwrap.wrap(line,150)),fontsize=9,color='#42566c',va='top')
        fig.savefig(path,dpi=100,metadata={'Software':'power-grid-env public passport'});plt.close(fig)


def waveform_png(frame,path):
    plt=pyplot();history=frame['history'];times=history['time_s'];sides=history['sample_side']
    if not 1<=len(frame['motors'])<=8:raise ValueError('one to eight motor indications required')
    with plt.rc_context({'font.family':'DejaVu Sans','font.size':10}):
        fig,axes=plt.subplots(3,1,figsize=(12,9),dpi=100,sharex=True,facecolor='white')
        fig.subplots_adjust(left=.08,right=.72,top=.90,bottom=.09,hspace=.22)
        fig.suptitle(f'OBSERVED MOTOR INDICATIONS | through {frame["cutoff_s"]:.3f} s | {frame["side"]}',fontsize=14,weight='bold',y=.97)
        colors=['#117c99','#c25c2a','#6755a5','#348155','#ae4875','#546574','#9a7615','#2946a0']
        for ax,(suffix,label) in zip(axes,[('bus_voltage_pu','Bus voltage / pu'),('current_pu','Motor current / pu'),('speed_pu','Speed / pu')]):
            for j,m in enumerate(frame['motors']):
                values=history['channels'][m['motor_id']+'.'+suffix];indices=extrema_indices(times,values,sides)
                ax.plot([times[i] for i in indices],[values[i] for i in indices],lw=1.3,label=m['motor_id'],color=colors[j%len(colors)])
                ax.plot(times[-1],values[-1],marker='o',ms=3.5,color=colors[j%len(colors)],clip_on=False,zorder=5)
            ax.set_ylabel(label);ax.grid(alpha=.25);ax.set_xlim(0,max(.001,frame['cutoff_s']));ax.legend(loc='upper right',fontsize=8,ncol=min(3,len(frame['motors'])))
        axes[-1].set_xlabel('Observed time / s')
        axes[-1].axhline(frame['rule']['running_speed_pu'],color='#777777',ls='--',lw=1,label='indication threshold')
        fig.text(.75,.90,'CURRENT INDICATION',weight='bold',fontsize=12)
        fig.text(.75,.865,'Speed resolution: 0.001 pu',fontsize=10,color='#42566c')
        for j,m in enumerate(frame['motors']):
            block=min(.17,.65/len(frame['motors']));step=block/4.8;y=.80-j*block
            fig.text(.75,y,f'{m["motor_id"]} / {m["node_id"]}',weight='bold',color=colors[j%len(colors)])
            fig.text(.75,y-step,f'Connected: {"ON" if m["connected"] else "OFF"}')
            fig.text(.75,y-2*step,f'Speed: {m["speed_indication_pu"]:.3f} pu')
            fig.text(.75,y-3*step,f'I: {m["current_indication_pu"]:.3f} pu | U: {m["voltage_indication_pu"]:.3f} pu',fontsize=9)
        fig.text(.75,.12,'Threshold: 0.900 pu\nRead displayed indication.\nNo design-acceptance label.',fontsize=9,color='#42566c')
        fig.savefig(path,dpi=100,metadata={'Software':'power-grid-env past-only waveform'});plt.close(fig)
