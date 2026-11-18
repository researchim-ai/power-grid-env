"""Offline librsvg/Cairo render QA of the exact public generated SVGs."""
import ctypes as C
from pathlib import Path
from benchmarks.network_construction_acceptance import variants
from power_grid_env.network_design_view import single_line_svg
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint


def run(source,out):
    source=Path(source);anchor=verify(source);fp=package_fingerprint()
    if read_json(source/'summary.json')['code_sha256']!=fp:raise ValueError('source runtime differs')
    r=C.CDLL('librsvg-2.so.2');c=C.CDLL('libcairo.so.2');g=C.CDLL('libgobject-2.0.so.0')
    r.rsvg_handle_new_from_file.argtypes=[C.c_char_p,C.c_void_p];r.rsvg_handle_new_from_file.restype=C.c_void_p
    class Dimensions(C.Structure):_fields_=[('width',C.c_int),('height',C.c_int),('em',C.c_double),('ex',C.c_double)]
    r.rsvg_handle_get_dimensions.argtypes=[C.c_void_p,C.POINTER(Dimensions)]
    r.rsvg_handle_render_cairo.argtypes=[C.c_void_p,C.c_void_p];r.rsvg_handle_render_cairo.restype=C.c_int
    c.cairo_image_surface_create.argtypes=[C.c_int,C.c_int,C.c_int];c.cairo_image_surface_create.restype=C.c_void_p
    c.cairo_create.argtypes=[C.c_void_p];c.cairo_create.restype=C.c_void_p
    c.cairo_surface_write_to_png.argtypes=[C.c_void_p,C.c_char_p];c.cairo_surface_write_to_png.restype=C.c_int
    c.cairo_destroy.argtypes=[C.c_void_p];c.cairo_surface_destroy.argtypes=[C.c_void_p];g.g_object_unref.argtypes=[C.c_void_p]
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[]
    for job in variants():
        src=source/(job['id']+'-view')/'single-line.svg'
        if src.read_text()!=single_line_svg(job['task'],job['proposal']):raise ValueError('public SVG differs')
        handle=r.rsvg_handle_new_from_file(str(src.resolve()).encode(),None)
        if not handle:raise ValueError('SVG parser failed')
        d=Dimensions();r.rsvg_handle_get_dimensions(handle,C.byref(d))
        if not 300<=d.width<=4000 or not 150<=d.height<=4000:raise ValueError('diagram dimensions out of bounds')
        surface=c.cairo_image_surface_create(0,d.width,d.height);ctx=c.cairo_create(surface)
        path=root/(job['id']+'.png')
        if not r.rsvg_handle_render_cairo(handle,ctx) or c.cairo_surface_write_to_png(surface,str(path).encode())!=0:raise ValueError('SVG rasterization failed')
        c.cairo_destroy(ctx);c.cairo_surface_destroy(surface);g.g_object_unref(handle)
        rows.append(dict(id=job['id'],width=d.width,height=d.height,svg_sha256=digest(src.read_bytes()),png_sha256=digest(path.read_bytes())))
    if verify(source)!=anchor:raise ValueError('source changed')
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,renderer='system librsvg/Cairo',rows=rows,
                scope='Exact SVG render QA; no image-grounding evaluation or trained VLM claim')
    (root/'receipt.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);a=p.parse_args();print(run(a.source,a.out))
