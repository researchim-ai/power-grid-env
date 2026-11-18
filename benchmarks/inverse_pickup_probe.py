"""Python-I installed-runtime inverse-curve probe against 80-digit Decimal."""
import argparse
import math
import sys
from decimal import Decimal,localcontext
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--installed-dir',required=True);p.add_argument('--out',required=True)
a=p.parse_args();installed=Path(a.installed_dir).resolve();sys.path.insert(0,str(installed))
from power_grid_env.protection import iec_operate_time
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,digest

rows=[]
for pickup in (1.,150.,1e6):
    current=math.nextafter(pickup,math.inf)
    for curve,k,alpha in (('standard_inverse',.14,.02),('very_inverse',13.5,1.),('extremely_inverse',80.,2.)):
        with localcontext() as ctx:
            ctx.prec=80
            ratio=Decimal(current)/Decimal(pickup)
            expected=float(Decimal(.0002)*Decimal(k)/((Decimal(alpha)*ratio.ln()).exp()-1))
        row=dict(pickup_a=pickup,current_a=current,curve=curve,reference_seconds=expected)
        try:
            value=float(iec_operate_time(current,pickup,curve,.0002))
            if not math.isfinite(value):row.update(passed=False,error='nonfinite')
            else:
                error=abs(value-expected)/expected
                row.update(actual_seconds=value,relative_error=error,passed=error<=3e-15)
        except (ValueError,ArithmeticError) as exc:row.update(passed=False,error=type(exc).__name__)
        rows.append(row)
for name,module in sys.modules.items():
    if name.startswith('power_grid_env') and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(installed):
        raise ValueError('mixed checkout/runtime imports')
result=dict(passed=all(r['passed'] for r in rows),code_sha256=package_fingerprint(),probe_sha256=digest(Path(__file__).read_bytes()),
    failed_checks=sum(not r['passed'] for r in rows),rows=rows,independent_host=False)
out=Path(a.out)
if out.exists():raise ValueError('fresh probe output required')
out.write_bytes(encoded(result));print(dict(passed=result['passed'],failed_checks=result['failed_checks']))
