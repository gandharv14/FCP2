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

def grouped_thousands(prose):
    """Normalize only complete comma-thousands tokens, never comma lists."""
    token=r'(?<![\w.])[+-]?\d+(?:,\d+)+(?:\.\d+)?(?:[eE][+-]?\d+)?(?!\w|\.\d)'
    def normalize(match):
        value=match[0]
        require(re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:[eE][+-]?\d+)?',value)is not None,
                'malformed comma grouping in numeric prose')
        return value.replace(',','')
    result=re.sub(token,normalize,prose)
    require(re.search(r'\d,\d',result)is None,'ambiguous comma grouping in numeric prose')
    return result


def spoken_literals(senior):
    # A sentence-final full stop is punctuation, while a dot followed by a
    # digit is part of a decimal. Do not count digits embedded in labels.
    prose = grouped_thousands(re.sub(r'"[^"\n]*"', '', senior))
    require(re.search(r'(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+(?!\w)',prose)is None,
            'exponent numeric prose is unsupported for source warnings')
    return Counter(float(x) for x in re.findall(r'(?<![\w.])'+NUMBER+r'(?!\w|\.\d)', prose))


def accuracy_findings(extras, count, claims=None, *, senior_roles=None):
    """Parse only closed allegations; source/prose checks prove each operand."""
    if len(extras)==1 and claims is not None:
        prefix=re.fullmatch(r'The first (one|two|three|four|five|six|seven|eight|nine|ten) senior turns disclose numeric literals that look like graded targets, including the detailed calculation inputs and fixed values\.',extras[0])
        if prefix is not None:
            amount=('one','two','three','four','five','six','seven','eight','nine','ten').index(prefix[1])+1
            require(len(claims)==count and amount<=count,'invalid numeric source prefix')
            require(all(c.get('reviewer_only',{}).get('source')=='custom_method_detector'for c in claims[:amount]),
                    'numeric prefix includes a non-formula claim')
            return {i:None for i in range(amount)}, [], 'nongraded_source_input_prefix'
    if len(extras)==3 and claims is not None and senior_roles is not None:
        numeric=r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
        patterns=[r'The VP leaks the numeric target literals ('+numeric+r') and ('+numeric+r')\.',
                  r'The Director leaks the numeric target literal ('+numeric+r')\.',
                  r'The Managing Director leaks the numeric formula literal ('+numeric+r')\.']
        matches=[re.fullmatch(pattern,text)for pattern,text in zip(patterns,extras)]
        if all(matches):
            require(len(claims)==count and set(senior_roles)==set(range(count)),'complete senior role mapping required')
            found={}
            for role,match in zip(('VP','Director','Managing Director'),matches):
                indices=[i for i,row in enumerate(claims)if senior_roles[i]==role
                         and row.get('reviewer_only',{}).get('source')=='custom_method_detector']
                require(len(indices)==1 and indices[0]not in found,'numeric finding has ambiguous formula role')
                found[indices[0]]=[float(grouped_thousands(x))for x in match.groups()]
            return found,[], 'three_role_formula_operands'
    if len(extras)==1 and claims is not None:
        pair=re.fullmatch(r'The senior turns for (.+) and (.+) disclose the numeric literals ('+NUMBER+r') and ('+NUMBER+r'), which look like graded targets and are not must_say clauses\.',extras[0])
        if pair is not None:
            require(len(claims)==count,'numeric finding claim count differs')
            found={}
            for identity,number in ((pair[1],pair[3]),(pair[2],pair[4])):
                if re.fullmatch(r'::\d{3}',identity):
                    require(re.search(r'::\d{3}$',pair[1]) is not None,'numeric shorthand lacks full first identity')
                    identity=pair[1].rsplit('::',1)[0]+identity
                indices=[i for i,c in enumerate(claims)if c['record_id']==identity]
                require(len(indices)==1 and indices[0]not in found,'ambiguous numeric finding identity')
                found[indices[0]]=[float(number)]
            return found, [], 'named_source_operand_pair'
    if len(extras)==2:
        group=re.fullmatch(r'Senior turns for claims (.+) expose the numeric formula literal "('+NUMBER+r')"\.',extras[0])
        single=re.fullmatch(r'The senior turn for claim (\d+) exposes the numeric modelling target "('+NUMBER+r')"\.',extras[1])
        if group is not None and single is not None:
            found={i:[float(group[2])] for i in _indices(group[1],count)}
            index=_indices(single[1],count)[0]
            require(index not in found,'duplicate numeric claim')
            found[index]=[float(single[2])]
            return found, [], 'grouped_formula_constants'
    if len(extras)==1 and claims is not None:
        span=re.fullmatch(r'The senior turns for claims (\d{3}) through (\d{3}) disclose numeric literals that look like graded targets, including the calculation inputs and resulting signed assumptions\.',extras[0])
        if span is not None:
            start,end=map(int,span.groups())
            require(len(claims)==count and 1<=start<=end<=count,'invalid numeric claim interval')
            return {i:None for i in range(start-1,end)}, [], 'nongraded_source_input_interval'
    if len(extras)==1 and claims is not None:
        single=re.fullmatch(r"The Director's answer for the ([^\n]+) claim leaks the numeric graded target ("+NUMBER+r")\.",extras[0])
        if single is not None:
            require(len(claims)==count,'numeric finding claim count differs')
            matches=[i for i,row in enumerate(claims) if row.get('row_label')==single[1]]
            require(len(matches)==1,'numeric finding has ambiguous or missing row label')
            return {matches[0]:[float(single[2])]}, [], 'single_row_formula_operand'
    if count == 2 and len(extras) == 1:
        numeric = re.fullmatch(r'The senior turns leak numeric calculation literals: ('+NUMBER+r') and ('+NUMBER+r') in the first claim and ('+NUMBER+r') in the second claim\.', extras[0])
        require(numeric is not None, 'unrecognized accuracy finding')
        return {0: [float(numeric[1]), float(numeric[2])], 1: [float(numeric[3])]}, [], 'two_claim_formula_operands'
    require(len(extras) == 2, 'unrecognized extra finding')
    constants=re.fullmatch(r'Senior turns for claims (.+) include the numeric literal ('+NUMBER+r'), and claim (\d+) includes ('+NUMBER+r') and ('+NUMBER+r'); these formula constants are not must_say clauses and look like graded-target leakage\.',extras[0])
    prefixes=re.fullmatch(r'The frozen junior-scope prefixes are altered in claims (.+) by inserting a sheet-name preamble before the required scope prefix\.',extras[1])
    require(constants is not None and prefixes is not None,'unrecognized accuracy finding')
    formula_claims={i:[float(constants[2])] for i in _indices(constants[1],count)}
    second=_indices(constants[3],count)[0];require(second not in formula_claims,'duplicate numeric claim')
    formula_claims[second]=[float(constants[4]),float(constants[5])]
    return formula_claims, _indices(prefixes[1],count), 'formula_and_frozen_prefixes'


def nongraded_input_literals(validator,claim,literals,senior,task_dir):
    """Prove every spoken number is an original operand outside graded targets."""
    require(bool(literals),'numeric source claim has no operands')
    require(validator.licensed_formula_literals(claim,Path(task_dir))==literals,
            'numeric source claim references a graded target')
    require(spoken_literals(senior)==literals,'senior numbers differ from exact source operands')
    targets=validator.disclose.numeric_targets(Path(task_dir))
    require(not any(validator.disclose.same_number(number,float(target))
                    for number in literals for target in targets),
            'source operand equals a graded target')
    return list(literals.elements())


def explicit_negative_literals(senior):
    # This is only lexical normalization of an explicit sign, never inference
    # from subtraction, "minus", or a request to flip a computed result.
    return re.sub(r'\bnegative (\d+(?:\.\d+)?)(?!\w|\.\d)',r'-\1',senior,flags=re.I)


def grouped_formula_style(natural,claims,senior_texts):
    """Closed presentation findings, including the actual quoted senior phrase."""
    findings=natural.get('findings',[])
    require(natural.get('verdict')=='fail' and len(findings)==2,'unrecognized naturalness finding')
    copied=re.fullmatch(r"Several senior answers are near-copies of their cards' spoken lines, especially claims (.+)\.",findings[0])
    phrase=re.fullmatch(r'The phrase "([^"\n]+)" in claim (\d+) sounds rubric-driven rather than like natural Slack dialogue\.',findings[1])
    require(copied is not None and phrase is not None,'unrecognized naturalness finding')
    _indices(copied[1],len(claims))
    index=_indices(phrase[2],len(claims))[0]
    require(phrase[1] in senior_texts[claims[index]['record_id']],
            'style phrase absent from selected senior turn')
    return findings


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
    require(accuracy.get('cell_refs_in_senior_turns')==[] and accuracy['verdict']=='fail' and r['passed'] is False,'unexpected rejected review shape')
    template_blocks=dict(_blocks(frozen));draft_blocks=dict(_blocks(text));require(list(draft_blocks)==[x['record_id'] for x in rows],'draft claim ordering changed')
    senior_roles={}
    for i,claim in enumerate(rows):
        turns=[t for t in validator.parse_turns(draft_blocks[claim['record_id']])if validator.is_senior(t['speaker'])]
        require(len(turns)==1,'exactly one senior turn per source claim required')
        senior_roles[i]=turns[0]['speaker']
    extras=accuracy['extras'];formula_claims,prefix_claims,variant=accuracy_findings(extras,count,rows,senior_roles=senior_roles)
    for i,check in enumerate(checks):
        permitted=[]
        if variant=='single_row_formula_operand' and i in formula_claims:
            literal=re.fullmatch(r"The Director's answer for the ([^\n]+) claim leaks the numeric graded target ("+NUMBER+r")\.",extras[0])[2]
            permitted=['The senior answer includes the prohibited numeric graded target '+literal+'.']
        require(check['must_say']=='entailed' and check.get('findings') in ([],permitted),'substantive claim finding')
    warnings=[]
    for i in prefix_claims:
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
            literals=_literals(parse_formula(formula))
            turns=validator.parse_turns(draft_blocks[claim['record_id']]);senior=' '.join(t['text'] for t in turns if validator.is_senior(t['speaker']))
            if variant in ('nongraded_source_input_interval','nongraded_source_input_prefix'):
                senior=explicit_negative_literals(senior)
                numbers=nongraded_input_literals(validator,claim,literals,senior,task_dir)
            if variant in ('grouped_formula_constants','named_source_operand_pair','three_role_formula_operands'):
                require(not Counter(numbers)-literals,'reported constant absent from source AST')
                numbers=nongraded_input_literals(validator,claim,literals,senior,task_dir)
            needed=Counter(numbers)
            require(not needed-literals,'claimed constant absent from source AST')
            # Quoted labels can contain dates/numbers; they are not operand uses.
            spoken=spoken_literals(senior)
            require(all(spoken[n]==needed[n] for n in needed),'source constant occurrence differs in senior prose')
            warnings.append(dict(code='source_formula_operand',record_id=claim['record_id'],formula=formula,cell=evidence['cells'][0],numbers=numbers,finding=extras[0]))
    finally:book.close()
    natural=r['naturalness'];expected=[f'Across {count} exchanges, the junior questions cycle through near-identical source-link templates and the senior answers cycle through interchangeable whole/full/entire-value constructions, making the file read as a checklist.', 'The uninterrupted sequence of isolated two-line claim blocks reads like generated documentation rather than a natural Slack conversation.']
    if variant=='two_claim_formula_operands':
        expected=['Both senior answers follow the corresponding card spoken lines very closely in wording, sequence, and calculation structure rather than reading as independently phrased Slack responses.']
    if variant=='single_row_formula_operand':
        expected=['The long source-selection section reads like an exhaustive cell-by-cell checklist rather than a natural Slack conversation.', 'Despite minor wording changes, the junior questions repeatedly use the same scoped-cell source-request template across dozens of consecutive exchanges.']
    if variant=='nongraded_source_input_interval':
        expected=[]
        require(natural['verdict']=='pass' and natural['findings']==[], 'unrecognized naturalness finding')
    elif variant=='nongraded_source_input_prefix':
        expected=["The senior answers are near-copies of the cards' spoken lines, retaining the same row-label, single-cell, operation, and source formulations with only light reordering.",
                  'Across the file, the repeated scoped question followed by a formula or source instruction reads like a checklist of claim cards rather than a natural Slack conversation.']
        require(natural['verdict']=='fail'and natural['findings']==expected,'unrecognized source-prefix style finding')
    elif variant=='named_source_operand_pair':
        expected=['Across the file, the junior questions and senior answers cycle through a small bank of near-identical source-selection templates. At this scale the conversation reads like generated documentation or a checklist rather than a natural Slack exchange.']
        require(sum(c.get('reviewer_only',{}).get('entry')=='source_selection' for c in rows)>=24,
                'named operand style finding lacks source-selection sequence')
        require(natural['verdict']=='fail' and natural['findings']==expected,'unrecognized naturalness finding')
    elif variant=='grouped_formula_constants':
        seniors={identity:' '.join(t['text'] for t in validator.parse_turns(block)
                    if validator.is_senior(t['speaker'])) for identity,block in draft_blocks.items()}
        expected=grouped_formula_style(natural,rows,seniors)
    elif variant=='three_role_formula_operands':
        expected=["Several senior answers are near-copies of the cards' spoken lines, particularly the residual-value and held-flat forecast answers."]
        require(natural['verdict']=='fail'and natural['findings']==expected
            and any(c.get('reviewer_only',{}).get('entry')=='method_discounting'for c in rows)
            and any(c.get('chosen_alternative')=='hold level'for c in rows),
            'unrecognized formula-role presentation finding')
    else:
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
