"""Explicit selected reader for criterion pre-review authorities.

Original Q, W, P, R and their files stay immutable. A cold runtime installs this
reader only for its exact selected authority. No admission or lease operation is
implemented here; original lease validators must authenticate current custody.
"""
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from scripts.fleet import criterion_pre_review as core
from scripts.fleet.source_card_profile import binding, checked, validate_lease

PERMIT_SCHEMA='harbor-dialogue-pre-review-repair/v1'


class Reader:
    def __init__(self, profile, authority, *, W, P, C, R, T, A, codec, renderers, validator):
        self.profile_binding=profile;self.plan=core.load(profile);self.authority=authority
        self.W,self.P,self.C,self.R,self.T,self.A,self.codec=W,P,C,R,T,A,codec
        self.renderers,self.validator=renderers,validator
        core.require(authority['path']==str(Path(self.plan['directory'])/'permit.json'),
                     'exact selected criterion authority required')

    def dependencies(self):
        for row in self.plan['dependencies']:
            core.require(binding(row['path'])==row,'criterion runtime dependency changed')
        core.require(binding(__file__) in self.plan['dependencies'],'criterion reader omitted from inventory')
        return list(self.plan['dependencies'])

    def is_card(self,value):
        return getattr(value,'dialogue_pre_review_repair',None)==self.authority

    def old_worker(self):
        p=self.plan;d=checked(p['old_descriptor']);w=self.C.worker_decode(d['worker'])
        core.require(d['code']==binding(self.C.__file__) and
                     w.dialogue_pre_review_repair is None and w.lease_renewal is not None and
                     all(getattr(w,k,None) is None for k in self.R.CONTINUATIONS if k!='lease_renewal'),
                     'unselected original criterion worker history')
        self.R._historical_inventory(w,p['snapshot'])
        return w

    def reproduce(self):
        w=self.old_worker();failure=checked(self.plan['blocked']);s=self.codec.decode(failure['selection'])
        old,new,extractor,formulas=self.renderers(s.content)
        result=core.project(self.plan,old_renderer=old,new_renderer=new,extractor=extractor,
                            formulas=formulas,validator=self.validator,stage=s.content.stage)
        original=checked(checked(self.plan['original_pair'])['original_claims'])
        core.require(self.P.skill_module(w.root,'additional-assumptions-dialogue','extract_claims').extract(s.content.stage)==original,
                     'original selected stage extraction changed')
        return result

    def authors(self):
        return core.original_pair(self.plan,W=self.W,P=self.P,A=self.A,worker=self.old_worker())

    def owned(self):
        self.dependencies();p=checked(self.authority)
        fields={'schema_version','correction_kind','profile','phase','round','semantic_budget_reset',
                'model_calls_added','sentence_replacements','worker_run','initial','reservation',
                'old_descriptor','old_snapshot','blocked','predecessor','old_selection','selection',
                'lease','tracker','prior_continuations','source','original','original_claims','claims',
                'old_draft','old_check','corrected_draft','sentence_proof','dialogue_directory',
                'author_calls','author_requests','policy','current_code','history','prefix','source_proof'}
        core.require(set(p)==fields and p['schema_version']==PERMIT_SCHEMA and
                     p['correction_kind']==core.SCHEMA and p['profile']==self.profile_binding and
                     p['phase']=='dialogue-review' and type(p['round']) is int and p['round']==1 and
                     p['semantic_budget_reset'] is False and type(p['model_calls_added']) is int and
                     p['model_calls_added']==0,'closed deterministic criterion permit required')
        old=self.old_worker();run=old.run;plan=self.plan;pair=checked(plan['original_pair'])
        expected=dict(schema_version=core.SCHEMA,profile=self.profile_binding,directory=plan['directory'],
                      blocked=plan['blocked'],budget=plan['budget'])
        core.require(p['reservation']==binding(run/'dialogue-criterion-reservation.json') and
                     checked(p['reservation'])==expected and
                     checked(binding(run/'dialogue-pre-review-repair.json'))==self.authority and
                     p['initial']==binding(run/'initial.json') and p['worker_run']==str(run),
                     'criterion permanent reservation or worker differs')
        for key,selected in dict(old_descriptor=plan['old_descriptor'],old_snapshot=plan['snapshot'],
                blocked=plan['blocked'],predecessor=plan['blocked'],source=plan['published_source'],
                original=plan['source'],original_claims=pair['original_claims'],claims=plan['corrected_claims'],
                old_draft=pair['old_draft'],old_check=pair['old_check'],source_proof=plan['projection']).items():
            core.require(p[key]==selected,'criterion ancestry changed: '+key)
        core.require(p['prior_continuations']=={'lease_renewal':old.lease_renewal} and
                     p['author_calls']==pair['author_calls'] and p['author_requests']==pair['author_requests'],
                     'criterion predecessor or authors changed')
        selected=self.codec.decode(p['old_selection'])
        core.require(p['old_selection']==checked(plan['blocked'])['selection'] and
                     p['selection']==self.codec.encode(replace(selected,content=replace(selected.content,
                         dialogue_claims=plan['corrected_claims']))),'criterion changed another selection field')
        claims,draft,phrases,report=self.reproduce()
        core.require(p['corrected_draft']==binding(Path(p['dialogue_directory'])/'round-01/draft.md') and
                     Path(p['corrected_draft']['path']).read_text()==draft and p['sentence_proof']==phrases and
                     type(p['sentence_replacements']) is int and p['sentence_replacements']==len(phrases)>0 and
                     Path(p['dialogue_directory'])==Path(plan['directory'])/'producer/dialogue',
                     'owning deterministic derivative differs')
        for row in [p['tracker'],*p['history']]:core.require(binding(row['path'])==row,'criterion retained history changed')
        self.authors()
        return p

    def historical_lease(self,worker,before):
        p=self.owned();old=self.old_worker()
        core.require(self.is_card(worker) and worker.lease_renewal is not None,
                     'criterion retained lease link changed')
        if worker.lease_renewal!=old.lease_renewal:
            # Ordinary R authenticates every later CAS and recursively validates
            # its exact prior Q link through this reader. No new lease authority
            # is granted here, and a foreign renewal fails original R validation.
            complete=checked(worker.lease_renewal);renewed=checked(complete['permit'])
            core.require(renewed['prior_continuations'].get('dialogue_pre_review_repair')==self.authority,
                         'ordinary renewal omitted criterion authority')
            return self.R.validate(worker,before)
        from scripts.batch.harbor_workbook_bootstrap import Lease
        tracker=binding(old.context.tracker_root/(self.plan['workbook']+'.json'))
        core.require(checked(tracker)==before,'criterion validation requires current exact tracker')
        lease=Lease(owner=before['leases']['task_agent']['owner'],token=before['leases']['task_agent']['token'],
                    revision=before['revision'],sha256=tracker['sha256'])
        prior=validate_lease(self.plan,old,before,lease,renewal=self.R,takeover=self.T,exact_boundary=False)
        # Return the original R permit unchanged, including its exact checkpoint.
        return prior

    def validate(self,worker,before):
        p=self.owned()
        core.require(self.is_card(worker) and str(worker.run)==p['worker_run'] and
                     before['workbook_id']==self.plan['workbook'] and
                     self.W.code(worker)==p['current_code'],'criterion owning worker changed')
        self.historical_lease(worker,before)
        return p

    def validate_task(self,task):
        p=self.owned();s=self.codec.decode(p['selection'])
        core.require(self.is_card(task) and task.run==Path(p['worker_run'])/'producer' and
                     task.root==self.old_worker().root and task.stage==s.content.stage and
                     task.policy==s.content.model_policy,'criterion task context differs')
        return p

    def context(self,task,round,*,root):
        p=self.validate_task(task);core.require(type(round) is int and round==1 and root==task.root,
                                               'only unused first criterion review is selected')
        claims=checked(p['claims']);home=Path(p['dialogue_directory'])
        composer=self.P.skill_module(root,'additional-assumptions-dialogue','compose_draft')
        template,slots=composer.compose(claims,claims['writer_pack'])
        core.require(checked(binding(home/'claims.json'))==claims and
                     (home/'draft_template.md').read_text()==template and
                     checked(binding(home/'slots.json'))==slots,'criterion scaffold changed')
        return home,claims,template,slots

    def review_projection(self,bound,claims,draft,*,root):
        core.require(bound==self.authority and root==self.old_worker().root,'foreign correction reviewer')
        p=self.owned();body=core.review_body(self.plan,bound,claims=claims,draft=draft)
        return body['source_proved_correction']

    def verify_pair(self,bound,calls,*,policy,claims,draft,round,root,workbook,original_sha256):
        p=self.owned();self.review_projection(bound,claims,draft,root=root)
        core.require(type(round) is int and round==1 and workbook==self.plan['workbook'] and
                     original_sha256==self.plan['source']['sha256'] and asdict(policy)==p['policy'],
                     'criterion reviewer source or original policy changed')
        original,proof=self.authors();core.require(tuple(calls)==original,'review substituted retained authors')
        return proof

    def validate_retained_request(self,task,request):
        p=self.validate_task(task);context=checked(request.context)
        old=checked(self.plan['snapshot']);inventory={r['relative']:r['sha256']for r in old['files']}
        core.require(request.prepared.ordinal==1 and request.prepared.limit==2 and
                     context['kind'] in ('dialogue-writer','dialogue-paraphraser') and
                     context['request']==request.prepared.request and
                     context['budget_evidence']==list(request.prepared.budget_evidence),
                     'retained criterion author request changed')
        original_requests=[self.W.request_saved(Path(row['path']))for row in p['author_requests']]
        core.require(request in original_requests,'foreign retained request')
        for row in context['code']:
            path=Path(row['path']);core.require(path.is_relative_to(Path(old['root'])) and
                inventory.get(str(path.relative_to(old['root'])))==row['sha256'],'retained request code changed')
        for row in [*context['code'],*context['evidence'],*context['budget_evidence']]:
            core.require(binding(row['path'])==row,'retained request evidence changed')
        return context

    def validate_review_call(self,bound,call):
        self.owned();core.require(bound==self.authority,'foreign criterion review authority')
        submitted=checked(call.submitted)
        core.require(submitted.get('scope')==dict(workbook_id=self.plan['workbook'],
            original_sha256=self.plan['source']['sha256'],lane='harbor-dialogue-author',budget_id='dialogue-review') and
            submitted['semantic_ordinal']==1 and submitted['semantic_limit']==2,
            'criterion independent review scope or consumption changed')
        raw=Path(bound['path']).read_bytes()
        core.require(any(binding(b['path'])==b and Path(b['path']).read_bytes()==raw
                         for b in submitted['budget_evidence']),'criterion review omitted owning authority')

    def extractor(self):
        """Reproduce all cards for the exact selected stage without source edits."""
        def extract(stage):
            p=self.owned();old=self.codec.decode(p['old_selection']).content
            core.require(stage==old.stage or (old.pre_dialogue_witness is not None and stage==old.pre_dialogue_witness.stage),
                         'criterion extraction selected another stage')
            return self.reproduce()[0]
        return SimpleNamespace(extract=extract)
