import subprocess,sys
from pathlib import Path
import pytest
from scripts.fleet import python_entry as entry
from scripts.fleet import worker as W, migration as M


def test_actual_inspect_shadow_fails_legacy_and_isolated_entry_succeeds(tmp_path):
    fleet=tmp_path/'scripts/fleet';fleet.mkdir(parents=True)
    (fleet/'inspect.py').write_text('SHADOW=True\n')
    driver=fleet/'agent.py';driver.write_text('from inspect import isgenerator\nassert isgenerator((i for i in ()))\nprint("ISOLATED_PASS")\n')
    legacy=[sys.executable,'-B',str(driver)]
    failure=subprocess.run(legacy,capture_output=True,text=True)
    assert failure.returncode==1 and "cannot import name 'isgenerator'" in failure.stderr
    current=entry.command(sys.executable,driver)
    success=subprocess.run(current,capture_output=True,text=True)
    assert success.returncode==0 and success.stdout.strip()=='ISOLATED_PASS'
    with pytest.raises(ValueError,match='isolated -I'):entry.validate(legacy,str(driver))
    with pytest.raises(ValueError,match='isolated -I'):entry.validate([*legacy,'-I'],str(driver))
    entry.validate(current,str(driver))


def test_actual_worker_rejects_direct_fleet_before_custody(tmp_path):
    fleet=tmp_path/'scripts/fleet';fleet.mkdir(parents=True);driver=fleet/'agent.py';driver.write_text('raise AssertionError("not executed")')
    source=tmp_path/'source.xlsx';source.write_bytes(b'original')
    job=dict(schema_version=W.SCHEMA,workbook='0010',source=M.binding(source),version='0'*64,driver=M.binding(driver),inputs=[],command=[sys.executable,'-B',str(driver)],cwd=str(tmp_path),run=str(tmp_path/'run'),result=str(tmp_path/'result.json'),memory_limit_bytes=8*1024**3,minimum_disk_bytes=12*1024**3,timeout_seconds=60)
    with pytest.raises(ValueError,match='isolated -I'):W.validate(job)
    assert not Path(job['run']).exists()
    job['command']=entry.command(sys.executable,driver);W.validate(job)


@pytest.mark.parametrize('driver,argv',[
 ('/tmp/legacy.py',['python','/tmp/legacy.py']),
 ('/tmp/scripts/fleet/agent.py',['bash','/tmp/scripts/fleet/agent.py']),
 ('/tmp/scripts/fleet/agent.py',['python','-I','-c','declared_loader','/tmp/scripts/fleet/agent.py']),
])
def test_unrelated_legacy_and_declared_wrappers_preserved(driver,argv):
    entry.validate(argv,driver)


def test_actual_child_resolves_lexical_runtime_docker_wrapper(tmp_path):
    import os
    runtime=tmp_path/'venv/bin';runtime.mkdir(parents=True)
    # A symlinked interpreter must keep the lexical venv wrapper directory.
    (runtime/'python').symlink_to(sys.executable)
    wrapper=runtime/'docker';wrapper.write_text('#!/bin/sh\nprintf selected-runtime\n');wrapper.chmod(0o755)
    system=tmp_path/'system';system.mkdir();other=system/'docker';other.write_text('#!/bin/sh\nprintf wrong-system\n');other.chmod(0o755)
    inherited={**os.environ,'PATH':str(system)+os.pathsep+os.defpath}
    env=entry.environment(runtime/'python',inherited)
    result=subprocess.run(entry.command(runtime/'python','-c','import subprocess;print(subprocess.check_output(["docker","info"]).decode())'),capture_output=True,text=True,env=env)
    assert result.returncode==0 and result.stdout.strip()=='selected-runtime'
    assert inherited['PATH'].startswith(str(system))
