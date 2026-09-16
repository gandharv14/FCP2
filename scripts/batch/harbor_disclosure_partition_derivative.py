"""Source-proved candidate bytes for an explicit subsequent partition round.

The completed build and rejected Calls remain immutable. A passed aggregate
may later copy this frozen candidate into a new owning stage; this reader never
reserves a review, alters the original stage, or grants semantic approval.
"""
import ast,hashlib
from contextlib import contextmanager
from pathlib import Path
from scripts.batch.harbor_admission import read, record, require

SCHEMA = 'harbor-disclosure-partition-source-derivative/v1'
NAMED_COPY_FUNCTIONS = {'_copied_named_operands': '1c49c2c5619d24b4fc0c391daa1fc7fe857493ad6383a0026e7bde4dc5b5f371', 'copy_lock_note': '57c41929ebae358f10110f34e39fcc10f341119ebdc0a6cc6b951e1e71d0c7bb'}
REPAIR_FUNCTION_SHA256 = '861dabd61d72f2e969f13fd13c40cd24ca03ab88a2e1177a4c4901d8b7ed040a'
KEYS = {'schema_version','implementation','build','prior_rejections','source',
        'original_renderer','renderer','repair','proof','bands','original_instruction'}


def tree(path):
    from scripts.batch.harbor_instruction_worker import static
    return static.tree(Path(path))


def renderer_delta(original, selected):
    """Only the independently tested named-copy wording functions may differ."""
    before=ast.parse(Path(original['path']).read_text())
    after=ast.parse(Path(selected['path']).read_text())
    old=[n for n in before.body if isinstance(n,ast.FunctionDef) and n.name=='copy_lock_note']
    retained=[n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='_cell_copy_lock_note']
    require(len(old)==len(retained)==1,'unique original copy-note function required')
    old[0].name='_cell_copy_lock_note'
    require(ast.dump(old[0],include_attributes=False)==ast.dump(retained[0],include_attributes=False),
            'original cell-copy semantics changed')
    names={'copy_lock_note','_cell_copy_lock_note','_copied_named_operands'}
    def remainder(t):
        return ast.dump(ast.Module(body=[n for n in t.body if not(isinstance(n,ast.FunctionDef)and n.name in names)],type_ignores=[]),include_attributes=False)
    require(remainder(before)==remainder(after),'named-copy candidate changed unrelated renderer code')
    actual={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
            for n in after.body if isinstance(n,ast.FunctionDef)and n.name in NAMED_COPY_FUNCTIONS}
    require(actual==NAMED_COPY_FUNCTIONS,'unreviewed named-copy derivation function')
    require({n.name for n in after.body if isinstance(n,ast.FunctionDef)and n.name in names}==names,
            'complete named-copy renderer required')


@contextmanager
def renderer_context(renderer, root):
    names={'REPO_ROOT':root,'REGISTRY_PATH':root/'.cursor/skills/task-disclosure/REGISTRY.md','_REGISTRY_CACHE':None}
    old={name:getattr(renderer,name)for name in names}
    try:
        for name,value in names.items():setattr(renderer,name,value)
        yield renderer
    finally:
        for name,value in old.items():setattr(renderer,name,value)


def validate(binding, *, worker, selection, build, prior_rejections, inputs=None):
    from scripts.batch import harbor_disclosure_review_round as Q
    from scripts.batch import harbor_model_executor as E
    p=read(binding)
    require(set(p)==KEYS and p['schema_version']==SCHEMA and p['implementation']==record(__file__),
            'closed source derivative authority required')
    require(p['build']==build and p['prior_rejections']==prior_rejections
            and len(prior_rejections)>=1,'derivative detached from original build or spent reviews')
    require(p['source']==selection.content.bindings['original_raw_source'],
            'derivative source differs from owning workbook')
    for key in ('source','original_renderer','renderer','repair','proof','bands','original_instruction'):E.checked_bytes(p[key])
    require(p['original_renderer']==record(worker.root/'.cursor/skills/task-disclosure/scripts/disclose.py'),
            'derivative lost original renderer identity')
    renderer_delta(p['original_renderer'],p['renderer'])
    functions=[n for n in ast.parse(Path(p['repair']['path']).read_text()).body
               if isinstance(n,ast.FunctionDef)and n.name=='corrected_payload']
    require(len(functions)==1 and hashlib.sha256(ast.dump(functions[0],include_attributes=False).encode()).hexdigest()==REPAIR_FUNCTION_SHA256,
            'unreviewed source repair implementation')
    proof=read(p['proof']);stage=selection.content.stage;candidate=Path(proof['candidate'])
    require(proof['status']=='ISOLATED_SOURCE_DISCLOSURE_CANDIDATE' and proof['qualified']is False
            and proof['models']==proof['original_task_mutations']==0
            and proof['original_task']==str(stage) and proof['repair']==p['repair']
            and proof['retained_bands']==p['bands'],'exact isolated source derivation required')
    require(stage!=candidate and not candidate.is_relative_to(stage)
            and not stage.is_relative_to(candidate),'candidate must be separate from original task')
    before=tree(stage);after=tree(candidate)
    require(before==proof['original_tree'] and after==proof['candidate_tree']
            and set(before)==set(after)
            and {k for k in before if before[k]!=after[k]}=={'instruction.md','tests/disclosure.json'},
            'derivative changed original or financial task artifacts')
    require(p['original_instruction']['path']!=str(stage/'instruction.md')
            and p['original_instruction']['sha256']==before['instruction.md'],
            'original build instruction needs an independent exact archive')
    require(proof['candidate_instruction']==record(candidate/'instruction.md')
            and proof['candidate_disclosure']==record(candidate/'tests/disclosure.json'),
            'candidate reviewed bytes moved')
    source=selection.content.bindings['source_file'];golden={k:source[k]for k in ('path','sha256')}
    require(proof['source']==golden,'derivative uses foreign golden source')
    E.checked_bytes(golden)
    old=Q.bound_module(p['original_renderer']);new=Q.bound_module(p['renderer']);repair=Q.bound_module(p['repair'])
    with renderer_context(new,worker.root):
        gold=old.Book(Path(golden['path']));payload=read(record(stage/'tests/disclosure.json'))
        expected,changes=repair.corrected_payload(old,new,gold,payload)
        require(expected==read(proof['candidate_disclosure']) and changes==proof['changes']
                and [r['index']for r in changes]==proof['changed_record_indices'],
                'candidate is not the exact source-derived copied-name correction')
        original=new.strip_agent_sections((stage/'instruction.md').read_text())
        require(original.count(new.OUTPUT_HEADING)==1,'ambiguous original instruction boundary')
        text=original.replace(new.OUTPUT_HEADING,'\n'+new.render_section(new.agent_records(expected['records']))+'\n## Output\n',1)
        require((candidate/'instruction.md').read_text()==text,'derivative changed other task instructions')
    for name in ('faith','verify'):
        require(proof[name]['passed']is True and proof[name]['faults']==[],
                'source candidate lacks complete mechanical proof')
    if inputs is not None:
        require(inputs['instruction']['sha256']==proof['candidate_instruction']['sha256']
                and inputs['disclosure']==proof['candidate_disclosure'] and inputs['golden']==golden,
                'review does not cover the complete corrected candidate')
        require(inputs['delivered']==record(new.find_environment(candidate)),
                'candidate review delivered workbook differs')
    return p,proof,candidate
