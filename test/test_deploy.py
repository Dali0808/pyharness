"""Exercise maintenance failure paths without a Docker daemon or network."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux deployment script")
@pytest.mark.parametrize("scenario", ["backup", "update", "failed_backup", "failed_update", "health_error"])
def test_maintenance_recovers_or_persists_release(tmp_path, scenario):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "deploy/manage.sh", deploy / "manage.sh")
    (deploy / ".env").write_text("LARIO_IMAGE=old\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
a=sys.argv[1:]
p=pathlib.Path(os.environ['FAKE_STATE'])
s=json.loads(p.read_text())
s['commands'].append([os.environ.get('LARIO_IMAGE', ''), *a])
code=0
if a[0]=='inspect': print('old-image')
elif a[0]=='compose':
    a=a[3:]
    if a[:2]==['ps','-q']: print('container')
    elif a[0]=='stop': s['stopped']=True
    elif a[0]=='up': s['stopped']=False
    elif a[0]=='exec':
        if os.environ['SCENARIO']=='health_error': code=1
        elif os.environ['SCENARIO']=='failed_update' and os.environ.get('LARIO_IMAGE')!='old-image': code=1
        else: print(0)
    elif a[0]=='run':
        if 'p.touch()' in a[-1]: s['flag']=True
        elif 'p.unlink' in a[-1]: s['flag']=False
        elif 'backup' in a:
            s['backups']+=1
            if os.environ['SCENARIO']=='failed_backup': code=1
p.write_text(json.dumps(s))
sys.exit(code)
''')
    docker.chmod(0o755)
    # Avoid minute-long health retries in the simulated failure case.
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    import json
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"commands": [], "stopped": False, "flag": False, "backups": 0}))
    command = "backup" if scenario in {"backup", "failed_backup", "health_error"} else "update"
    image = "registry.example/lario:sha-" + "a" * 40
    result = subprocess.run(["bash", str(deploy / "manage.sh"), command, image],
                            env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                 "SCENARIO": scenario, "FAKE_STATE": str(state)},
                            capture_output=True, text=True, timeout=30)
    final = json.loads(state.read_text())
    assert (result.returncode == 0) == (scenario in {"backup", "update"}), result.stderr
    assert not final["stopped"] and not final["flag"]
    assert final["backups"] == (0 if scenario == "health_error" else 1)
    if scenario == "update":
        assert image in (deploy / "release.env").read_text()
    else:
        assert not (deploy / "release.env").exists()
    if scenario in {"failed_backup", "failed_update"}:
        assert any(cmd[0] == "old-image" and "up" in cmd for cmd in final["commands"])
