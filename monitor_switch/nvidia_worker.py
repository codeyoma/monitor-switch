"""NVIDIA child dispatch and durable switch outcomes, without console presentation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import struct
import sys
from uuid import uuid4

from .child_process import supervise
from .startup import data_directory


def save_json(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_switch(config, target, *, timeout=20, diagnostics=None):
    from .nvidia import LG_INPUTS
    base = {'platform':platform.platform(), 'python':platform.python_version(),
            'time_utc':datetime.now(timezone.utc).isoformat(), 'target':target,
            'physical_switch_confirmed':False}
    job = {'target':target}
    if 'monitors' in config:
        job['monitors'] = config['monitors']
    else:
        job['monitor_id'] = config['monitor_id']
        base['input_code'] = LG_INPUTS[target]
    with supervise(job, kind='nvidia', timeout=timeout) as child:
        report = child.payload or {'status':'uncertain', 'write_attempted':None,
            'error':'Worker did not return a complete report. Input state is unknown.'}
        if child.failure:
            report = dict(report, **{'worker_warning' if report.get('status')=='sent' else 'error':child.failure})
        report = base | report
        prefix = 'monitor-switch' if 'monitors' in config else 'lg-switch'
        output = Path(diagnostics or data_directory()/'diagnostics')/f'{prefix}-{target}-{uuid4().hex[:8]}.json'
        report['report_path'] = str(output.resolve())
        save_json(output, report)
    return report


def worker_main(request, result):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('The NVIDIA worker requires 64-bit Windows.')
    from .nvidia import switch_lg, switch_group
    job = json.loads(Path(request).read_text(encoding='utf-8'))
    if 'monitors' in job:
        switch_group(job['monitors'], job['target'], journal=lambda report:save_json(result, report))
    else:
        switch_lg(job['monitor_id'], job['target'], journal=lambda report:save_json(result, report))
