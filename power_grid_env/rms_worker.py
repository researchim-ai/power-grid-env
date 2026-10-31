"""Private RMS worker entry point. Only study data is accepted, never commands."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from power_grid_env.report import write_json
from power_grid_env.study_spec import run_study_document


def main():
    payload=json.loads(Path(sys.argv[1]).read_text())
    if (payload.get('study'),payload.get('backend')) not in {('rms','andes'),('small_signal','andes'),('motor_start','scipy'),('coupled_rms','scipy'),('coupled_small_signal','scipy')}:
        raise ValueError('worker accepts only supported dynamic studies')
    result=run_study_document(payload)
    result.setdefault('completed',False)
    write_json(Path(sys.argv[2]),result)


if __name__=='__main__':main()
