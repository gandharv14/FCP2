"""Read-only, source-proved classification of narrowly described review warnings.

The original model review remains rejected and immutable. This proof does not
apply a draft, authorize continuation, grant an attempt, or manufacture a Call.
"""
from collections import Counter
from pathlib import Path
import re
from scripts.batch.harbor_admission import read,record,require
SCHEMA='harbor-dialogue-source-warning-proof/v1'
NUMBER=r'-?\d+(?:\.\d+)?'

def _indices(text,count):
    require(re.fullmatch(r'\d+(?:(?:, |, and | and )\d+)*',text) is not None,'unknown claim index syntax')
    values=[int(x)-1 for x in re.findall(r'\d+',text)]
    require(values and len(values)==len(set(values)) and all(0<=x<count for x in values),'invalid claim indices')
    return values

def _blocks(text):
    chunks=re.split(r'<!-- claim:([^\n]+?) -->',text)
    require(not chunks[0].strip(),'unexpected dialogue preamble')
    require(len(chunks)%2==1,'incomplete claim blocks')
    return [(chunks[i],chunks[i+1]) for i in range(1,len(chunks),2)]

def _juniors(text):
    result=[]
    for identity,chunk in _blocks(text):
        match=re.search(r'^\*\*(Analyst|Associate):\*\*',chunk,re.M)
        require(match is not None,'missing junior block')
        rest=chunk[match.start():];end=rest.find('<!-- slot:')
        require(end>=0,'filled scaffold lacks senior slot marker')
        result.append((identity,rest[:end]))
    return result

def _literals(node):
    if node.kind=='const' and node.name=='number':return Counter({float(node.value):1})
    if node.kind=='prefix' and node.name in ('+','-') and len(node.args)==1:
        child=node.args[0]
        if child.kind=='const' and child.name=='number':return Counter({float(child.value)*(-1 if node.name=='-' else 1):1})
    result=Counter()
    for child in node.args:result.update(_literals(child))
    return result

def validate(review,claims,draft,template,writer_filled,paraphraser_filled,source,*,task_dir,root):
    from scripts.batch import harbor_instruction_producer as producer
    from xl_ast_graph import parse_formula
    import openpyxl
    bindings=(review,claims,draft,template,writer_filled,paraphraser_filled,source)
    for binding in bindings:require(record(binding['path'])==binding,'warning evidence changed')
    r=read(review);payload=read(claims);rows=payload['claims'];count=len(rows)
    require(count>0 and not (payload.get('shared_source_links') or {}).get('groups'),'individual claim proof required')
    validator=producer.skill_module(Path(root),'additional-assumptions-dialogue','validate_dialogue')
    text=Path(draft['path']).read_text();frozen=Path(template['path']).read_text()
    writer=Path(writer_filled['path']).read_text();paraphraser=Path(paraphraser_filled['path']).read_text()
    require(validator.fill_faults(frozen,writer)==[] and validator.fill_faults(frozen,paraphraser)==[],'frozen scaffold changed')
    require(_juniors(writer)==_juniors(paraphraser),'protected junior bytes changed')
    require(validator.strip_slot_scaffold(paraphraser)==text,'draft differs from sealed paraphraser')
    mechanics=validator.check_draft(text,payload,Path(task_dir),source_workbook=source);require(mechanics['passed'],'owning draft mechanics failed')
    accuracy=r['accuracy'];checks=accuracy['claims']
    require([x['record_id'] for x in checks]==[x['record_id'] for x in rows],'review claim coverage differs')
    require(all(x['must_say']=='entailed' and x.get('findings')==[] for x in checks),'substantive claim finding')
    require(accuracy.get('cell_refs_in_senior_turns')==[] and accuracy['verdict']=='fail' and r['passed'] is False,'unexpected rejected review shape')
    extras=accuracy['extras'];require(len(extras)==2,'unrecognized extra finding')
    constants=re.fullmatch(r'Senior turns for claims (.+) include the numeric literal ('+NUMBER+r'), and claim (\d+) includes ('+NUMBER+r') and ('+NUMBER+r'); these formula constants are not must_say clauses and look like graded-target leakage\.',extras[0])
    prefixes=re.fullmatch(r'The frozen junior-scope prefixes are altered in claims (.+) by inserting a sheet-name preamble before the required scope prefix\.',extras[1])
    require(constants is not None and prefixes is not None,'unrecognized accuracy finding')
    formula_claims={i:[float(constants[2])] for i in _indices(constants[1],count)}
    second=_indices(constants[3],count)[0];require(second not in formula_claims,'duplicate numeric claim')
    formula_claims[second]=[float(constants[4]),float(constants[5])]
    template_blocks=dict(_blocks(frozen));draft_blocks=dict(_blocks(text));require(list(draft_blocks)==[x['record_id'] for x in rows],'draft claim ordering changed')
    warnings=[]
    for i in _indices(prefixes[1],count):
        claim=rows[i];identity=claim['record_id'];prefix=next((line for line in template_blocks[identity].splitlines() if line.startswith('**')),None)
        actual=next((line for line in draft_blocks[identity].splitlines() if line.startswith('**')),None)
        scope=validator.junior_scope_text(claim)
        require(prefix and actual and scope and actual.startswith(prefix),'frozen junior prefix changed')
        require(prefix.split('** ',1)[-1]==claim['sheet']+': '+scope+':','prefix is not exact own sheet and scope')
        warnings.append(dict(code='frozen_junior_prefix',record_id=identity,finding=extras[1]))
    book=openpyxl.load_workbook(source['path'],read_only=True,data_only=False)
    try:
        for i,numbers in formula_claims.items():
            claim=rows[i];evidence=claim['reviewer_only'];require(evidence['source']=='custom_method_detector','unsupported numeric source')
            sheet=claim['sheet'];cell=evidence['cells'][0].rsplit('!',1)[-1]
            require(re.fullmatch(r'\$?[A-Z]+\$?[1-9]\d*',cell) is not None and validator.disclose.parse_ref(evidence['cells'][0],sheet)==validator.disclose.key(sheet,cell),'formula scope is not one own-sheet cell')
            formula=book[sheet][cell].value;require(formula==evidence['evidence'],'source formula differs from claims')
            literals=_literals(parse_formula(formula));needed=Counter(numbers)
            require(not needed-literals,'claimed constant absent from source AST')
            turns=validator.parse_turns(draft_blocks[claim['record_id']]);senior=' '.join(t['text'] for t in turns if validator.is_senior(t['speaker']))
            # Quoted labels can contain dates/numbers; they are not operand uses.
            prose=re.sub(r'"[^"\n]*"','',senior);spoken=Counter(float(x) for x in re.findall(r'(?<![\w.])'+NUMBER+r'(?![\w.])',prose))
            require(all(spoken[n]==needed[n] for n in needed),'source constant occurrence differs in senior prose')
            warnings.append(dict(code='source_formula_operand',record_id=claim['record_id'],formula=formula,cell=evidence['cells'][0],numbers=numbers,finding=extras[0]))
    finally:book.close()
    natural=r['naturalness'];expected=[f'Across {count} exchanges, the junior questions cycle through near-identical source-link templates and the senior answers cycle through interchangeable whole/full/entire-value constructions, making the file read as a checklist.', 'The uninterrupted sequence of isolated two-line claim blocks reads like generated documentation rather than a natural Slack conversation.']
    require(natural['verdict']=='fail' and natural['findings']==expected,'unrecognized naturalness finding')
    warnings.extend(dict(code='dialogue_style_only',finding=f) for f in expected)
    return dict(schema_version=SCHEMA,claim_count=count,warnings=warnings,evidence=list(bindings),mechanics=mechanics,root=str(Path(root).resolve()),task_dir=str(Path(task_dir).resolve()))

def revalidate(proof,review_payload,claims_payload,draft_text,*,root):
    """Reconsume the exact proof against the actual owning validator inputs."""
    require(proof['root']==str(Path(root).resolve()),'warning proof belongs to different current code')
    b=proof['evidence'];require(len(b)==7,'warning evidence count changed')
    require(read(b[0])==review_payload and read(b[1])==claims_payload and Path(b[2]['path']).read_text()==draft_text,'warning proof differs from reviewed inputs')
    require(validate(*b,task_dir=proof['task_dir'],root=root)==proof,'warning proof changed')
    return proof
