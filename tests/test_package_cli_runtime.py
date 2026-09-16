from pathlib import Path
from types import SimpleNamespace
import json,subprocess,sys
import pytest
from scripts.fleet import package_cli_runtime as R


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/'root';package=root/'mcp_env';package.mkdir(parents=True);(package/'__init__.py').write_text('')
    original=package/'build.py';original.write_text('def source_id():return "old"\ndef stable():return 1\n')
    import importlib.util,mcp_env
    spec=importlib.util.spec_from_file_location('mcp_env.build',original);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules,'mcp_env.build',module);monkeypatch.setattr(mcp_env,'build',module,raising=False)
    selected=tmp_path/'selected.py';selected.write_text('def source_id():return "new"\ndef stable():return 1\n')
    cli=root/'xl_variable_mcp.py';cli.write_text('import sys\nfrom pathlib import Path\nfrom mcp_env.build import source_id\np=Path(sys.argv[3]);p.mkdir();(p/"result").write_text(source_id())\n')
    source=tmp_path/'0316.xlsx';source.write_bytes(b'original');normalized=tmp_path/'normalized.json';normalized.write_text('{}');work=tmp_path/'work';work.mkdir()
    p=dict(schema_version=R.SCHEMA,helper=R.binding(R.__file__),original=R.binding(original),selected=R.binding(selected),cli=R.binding(cli),workbook='0316',normalized=R.binding(normalized),source=R.binding(source),work=str(work))
    def keep(path,value):path=Path(path);path.write_text(json.dumps(value));return R.binding(path)
    row=keep(tmp_path/'profile.json',p);argv=[sys.executable,str(cli),'build',str(normalized),str(work/'build-a'),'--workbook','0316','--source',str(tmp_path)]
    intent=keep(work/'build-a.intent.json',dict(argv=argv,entry=p['cli'],model_calls_permitted=False));producer=SimpleNamespace(ROOT=root,subprocess=subprocess,journal=SimpleNamespace(save=keep))
    return p,row,intent,argv,producer,keep


def test_actual_subprocess_receives_selected_source_id_with_declared_commands(setup):
    p,row,intent,argv,producer,keep=setup;original=subprocess.run
    import mcp_env.build as builder
    assert builder.source_id()=='old'
    with R.installed(producer,row):
        assert builder.source_id()=='new'
        result=producer.subprocess.run(argv,capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert (Path(p['work'])/'build-a/result').read_text()=='new'
    side=R.read(R.binding(Path(p['work'])/'build-a.runtime.json'))
    assert side['logical_command']==argv and side['intent']==intent and not side['automatic_retry']
    assert subprocess.run is original and builder.source_id()=='old'
    with R.installed(producer,row):
        with pytest.raises(ValueError,match='already dispatched'):producer.subprocess.run(argv)


@pytest.mark.parametrize('field,value',[('workbook','other'),('work','/foreign'),('normalized',{'path':'/wrong','sha256':'0'*64})])
def test_changed_source_scope_rejected(setup,field,value):
    p,row,intent,argv,producer,keep=setup;p[field]=value;row=keep(Path(row['path']).with_name('changed.json'),p)
    with pytest.raises(ValueError):R.command(R.profile(row),intent)


def test_code_delta_outside_source_id_is_rejected(setup):
    p,row,intent,argv,producer,keep=setup;path=Path(p['selected']['path']);path.write_text(path.read_text().replace('return 1','return 2'));p['selected']=R.binding(path);row=keep(Path(row['path']).with_name('code.json'),p)
    with pytest.raises(ValueError,match='outside source_id'):R.profile(row)


def test_unsealed_or_foreign_cli_cannot_dispatch(setup):
    p,row,intent,argv,producer,keep=setup;changed=list(argv);changed[3]='/foreign'
    with R.installed(producer,row):
        with pytest.raises(ValueError,match='differs'):producer.subprocess.run(changed)
    assert not (Path(p['work'])/'build-a.runtime.json').exists()
