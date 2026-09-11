"""Compare retained outputs with the current normalizer's pure projection."""
import copy
import json
import gen_normalizer as generator
from pathlib import Path
from scripts.batch.harbor_admission import require,record,read


def expected_outputs(workbook,draft,rows,run,pipeline_bindings):
    run=Path(run)
    require((run/'first_normalization.json').is_file(),'missing original first-normalization evidence')
    projected=generator.emit(workbook,copy.deepcopy(draft),copy.deepcopy(rows),pipeline_bindings=pipeline_bindings,runs_root=run.parent,dry_run=True)
    # Compile only freshly generated reference text from the trusted installed
    # generator, never bytes read from the agent-authored retained script.
    namespace={'__name__':'harbor_normalization_reference','__file__':str(run/f'normalize_{workbook}.py')}
    exec(compile(projected['script'],'<current-normalizer-reference>','exec'),namespace)
    captured={}
    allowed={run/n for n in ('normalized.json','exclusions.json','normalization_report.json')}
    def capture(path,value):
        path=Path(path)
        require(path in allowed and path not in captured,'unexpected or duplicate normalizer output')
        captured[path]=json.loads(json.dumps(value,allow_nan=False))
    namespace['atomic_json']=capture
    namespace['print']=lambda *args,**kwargs:None
    namespace['main']()
    require(set(captured)==allowed,'incomplete normalizer projection')
    return projected['script'],captured


def validate_emission(workbook,draft,rows,run,pipeline_bindings):
    script,expected=expected_outputs(workbook,draft,rows,run,pipeline_bindings)
    require((Path(run)/f'normalize_{workbook}.py').read_text()==script,'retained normalizer differs from current exact generator')
    for path,value in expected.items():require(read(record(path))==value,'normalization final emission differs: '+path.name)
    return {'outputs':len(expected),'atomic_variables':len(expected[Path(run)/'normalized.json']['variables']),'current_emission_equivalent':True}
