import copy
import json
import sys
import unittest
import types
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recursion_hardening_amendment as a
import recursion_hardening_revisions as r
import recursion_hardening_campaign as campaign
import recursion_content_successor_verify as verifier
import test_recursion_hardening_content as content_tests
from test_recursion_content_successor_verify import fixture as probe_fixture


class AmendmentTests(unittest.TestCase):
    save = staticmethod(content_tests.ContentTests.save)
    planned = content_tests.ContentTests.planned
    content_activated = content_tests.ContentTests.content_activated

    def setUp(self):
        content_tests.ContentTests.setUp(self)
        self.work = self.work.resolve()
        for m in r.MODELS:
            for path, rc in [(self.work / 'final-images/configs' / m / 'verified.json', 'root-' + m),
                             (self.work / 'successor-configs' / m / 'verified.json', 'first-' + m)]:
                value = r.read(path); value['runConfigId'] = rc; self.save(path, value)
            runtime = r.read(self.runtime_path)
            cp = Path(runtime['models'][m]['configPath'])
            runtime['models'][m]['configSha256'] = r.sha(cp); self.save(self.runtime_path, runtime)
        first_runtime = r.read(self.runtime_path)
        first_proof = r.read(first_runtime['payloadProofPath'])
        for m in r.MODELS:
            first_proof['probeResults'][m]['stages'] = [{'probeRunId': 'parent-'+m+'-'+stage} for stage in ('research','plain','named')]
        self.save(Path(first_runtime['payloadProofPath']), first_proof)
        first_runtime['payloadProofSha256'] = r.sha(first_runtime['payloadProofPath']); self.save(self.runtime_path, first_runtime)
        self.parent = self.content_activated()
        self.before = {str(p): p.read_bytes() for p in self.work.rglob('*') if p.is_file()}
        self.second = self.work / 'new-proof'; self.second.mkdir()
        self.gate_path = self.second / 'gate.json'
        census = self.second / 'census.json'; self.save(census, {'allTasksReviewed': True})
        self.save(self.gate_path, {'status': 'complete', 'taskIds': ['a','b','c','d'],
            'affectedTaskIds': ['a','d'], 'evidenceHashes': {str(census): r.sha(census)}})
        current = {x['taskId']: x for x in r.active_assets(self.rows, self.work)}
        self.amend_patches = []
        self.approved_masks = {}
        for tid in ('a','d'):
            book = self.second / (tid + '-inputs.xlsx'); book.write_text('second mask ' + tid)
            source = self.second / (tid + '-source.json'); self.save(source, {'computedOnly': True})
            validation = self.second / (tid + '-validation.json')
            self.save(validation, {'taskId': tid, 'baseWorkbookSha256': current[tid]['workbookSha256'],
                'workbookSha256': r.sha(book), 'answerKeySha256': current[tid]['answerKeySha256'],
                'sourceEvidencePath': str(source), 'sourceEvidenceSha256': r.sha(source),
                'validation': dict.fromkeys(a.MASK_CHECKS, True)})
            spec = self.second / (tid + '-mask.json')
            predecessor = r.version_for(tid, self.work)
            self.save(spec, {'schemaVersion': 1, 'taskId': tid, 'activeProblemVersionId': predecessor['id'],
                'workbookPath': current[tid]['workbookPath'], 'workbookSha256': current[tid]['workbookSha256'],
                'answerKeyPath': current[tid]['answerKeyPath'], 'answerKeySha256': current[tid]['answerKeySha256'],
                'sourceEvidenceHashes': {str(source): r.sha(source)}})
            self.approved_masks[tid] = {'path': str(spec), 'sha256': r.sha(spec)}
            self.amend_patches.append({'maskSpecificationPath': str(spec), 'maskSpecificationSha256': r.sha(spec), 'taskId': tid, 'baseWorkbookSha256': current[tid]['workbookSha256'],
                'workbookPath': str(book), 'workbookSha256': r.sha(book),
                'validationPath': str(validation), 'validationSha256': r.sha(validation)})
        gate = r.read(self.gate_path); gate['approvedMasks'] = self.approved_masks; self.save(self.gate_path, gate)
        self.amend_path = self.second / 'patches.json'; self.save(self.amend_path, self.amend_patches)
        previous_proof = r.read(self.runtime_path)['payloadProofPath']; parent_path = self.work / 'content-successor/activation.json'
        fields = {'schemaVersion': 2, 'baseAssetsSha256': r.sha(self.work / 'final-assets.json'),
            'frozenRuntimeSha256': 'f'*64, 'predecessorActivationPath': str(parent_path),
            'predecessorActivationSha256': r.sha(parent_path), 'predecessorPayloadProofPath': str(Path(previous_proof).resolve()),
            'predecessorPayloadProofSha256': r.sha(previous_proof)}
        patched = {x['taskId']: x for x in self.amend_patches}
        proof = {**fields, 'kind': 'content_amendment_payload', 'status': 'verified',
            'censusGatePath': str(self.gate_path), 'censusGateSha256': r.sha(self.gate_path),
            'nonWorkbookPayloadPredecessorSha256': 'd'*64, 'nonWorkbookPayloadSuccessorSha256': 'd'*64,
            'workbooks': {x['taskId']: {'rootSha256': x['workbookSha256'],
                'predecessorSha256': current[x['taskId']]['workbookSha256'],
                'successorSha256': patched.get(x['taskId'], current[x['taskId']])['workbookSha256']} for x in self.rows},
            'probeResults': {}, 'registryResults': {}, 'evidenceHashes': {str(census): r.sha(census)}}
        runtime = {**fields, 'kind': 'content_amendment_runtime', 'models': {}}
        for model in r.MODELS:
            old = r.read(self.parent['configs'][model]['successorPath']); cfg = copy.deepcopy(old)
            cfg.update(id='second-' + model, runConfigId='second-rc-' + model)
            cfg['config']['harnessImageUrl'] = old['config']['harnessImageUrl'].replace('b'*64, 'c'*64)
            cp = self.second / 'configs' / model / 'verified.json'; self.save(cp, cfg)
            hashes = {}; stages = {}
            for stage in ('research','plain','named'):
                status, request, _ = probe_fixture(model, stage)
                status.update(id=model+'-'+stage, runConfigVersionId=cfg['id']); request['config'] = cfg['config']
                for name, value in [(stage+'-status.json', status), (stage+'-update.request.json', request),
                                    (stage+'-probe.response.json', {'probeRunId': status['id']})]:
                    path = cp.parent / name; self.save(path, value); hashes[str(path)] = r.sha(path)
                stages[stage] = verifier.verify_probe(status, request, cfg, model, stage)
            image_path = self.second / (model + '-image.json')
            self.save(image_path, {'image': cfg['config']['harnessImageUrl'], 'parent': old['config']['harnessImageUrl'], 'layerDigest': 'sha256:'+'e'*64})
            hashes[str(image_path)] = r.sha(image_path)
            proof['registryResults'][model] = {'baseImage': old['config']['harnessImageUrl'], 'successorImage': cfg['config']['harnessImageUrl'],
                'baseLayerCount': 5, 'successorLayerCount': 6, 'allBaseLayersPreserved': True, 'onlyWorkbookLayerAppended': True,
                'allRuntimeImageConfigPreserved': True, 'registryDigestVerified': True}
            proof['probeResults'][model] = {'status': 'succeeded', 'runConfigVersionId': cfg['id'], 'stages': stages}
            runtime['models'][model] = {'configPath': str(cp), 'configSha256': r.sha(cp),
                'image': cfg['config']['harnessImageUrl'], 'evidenceHashes': hashes}
        self.layer = {'schemaVersion': 2, 'baseAssets': {'sha256': fields['baseAssetsSha256']},
            'runtimeManifest': {'sha256': fields['frozenRuntimeSha256']}, 'digest': 'sha256:'+'e'*64,
            'predecessor': {'activation': {'path': fields['predecessorActivationPath'], 'sha256': fields['predecessorActivationSha256']},
                            'payloadProof': {'path': fields['predecessorPayloadProofPath'], 'sha256': fields['predecessorPayloadProofSha256']}},
            'entries': [{'taskId': x['taskId'], 'sha256': x['workbookSha256']} for x in self.amend_patches],
            'baseImages': {m: {'image': r.read(self.parent['configs'][m]['successorPath'])['config']['harnessImageUrl']} for m in r.MODELS}}
        layer_path = self.second / 'workbook-layer.json'; self.save(layer_path, self.layer)
        proof['evidenceHashes'][str(layer_path)] = r.sha(layer_path)
        patcher = patch('recursion_content_successor_image.verify_payload', return_value=(self.layer, {})); patcher.start(); self.addCleanup(patcher.stop)
        fake_mask = types.SimpleNamespace(verify_mask=lambda spec, output, **kwargs: r.read(self.second / (r.read(spec)['taskId']+'-validation.json')))
        patcher = patch.dict(sys.modules, {'recursion_amendment_mask_validation': fake_mask}); patcher.start(); self.addCleanup(patcher.stop)
        self.proof_path = self.second / 'payload.json'; self.save(self.proof_path, proof)
        self.amend_runtime = self.second / 'runtime.json'
        runtime.update(payloadProofPath=str(self.proof_path), payloadProofSha256=r.sha(self.proof_path)); self.save(self.amend_runtime, runtime)

    def prepare(self):
        return a.prepare(self.amend_path, self.amend_runtime, self.gate_path, self.work)

    def activate(self):
        plan = self.prepare()
        for row in plan['revisions']:
            v = r.read(row['baseVersionPath'])
            v.update(id='second-task-'+row['taskId'], version='v4', solverRunConfigVersionId='second-opus', lockedAt='today')
            self.save(a.directory(self.work) / 'versions' / row['taskId'] / 'verified.json', v)
        self.original_receipts()
        return a.activate(self.work, confirmed=True)

    def original_receipts(self):
        if list(self.work.glob('evaluation-*.response.json')): return
        for part in range(3):
            request = {'problems': [{'problemId': x['problemId'], 'problemVersionId': r.version_for(x['taskId'], self.work)['id']} for x in self.rows[part::3]],
                'solvers': [{'runConfigVersionId': 'new-'+m} for m in r.MODELS], 'metadata': {'attemptsPerProblem': 3}}
            self.save(self.work / f'evaluation-{part+1}.request.json', request)
            self.save(self.work / f'evaluation-{part+1}.response.json', {'id': f'old-{part+1}'})

    def resign_proof(self, proof):
        self.save(self.proof_path, proof); runtime = r.read(self.amend_runtime)
        runtime['payloadProofSha256'] = r.sha(self.proof_path); self.save(self.amend_runtime, runtime)

    def test_prepare_is_inert_and_preserves_all_frozen_bytes(self):
        self.prepare()
        self.assertEqual(r.version_for('a', self.work)['id'], 'content-a')
        self.assertEqual(r.active_configs(self.work)['opus']['id'], 'new-opus')
        self.assertEqual(r.known_configs(self.work)['second-gemini'], 'gemini')
        with self.assertRaises(ValueError): r.validate_run_binding('a','content-a','second-opus',self.work)
        for path, raw in self.before.items(): self.assertEqual(Path(path).read_bytes(), raw)
        self.assertFalse((a.directory(self.work)/'activation.json').exists())

    def test_three_generations_and_unaffected_history_are_preserved(self):
        self.activate(); data = r.activation(self.work)
        self.assertEqual({x['taskId'] for x in data['revisions']}, {'a','b','c','d'})
        self.assertEqual({x['taskId'] for x in data['amendmentDelta']}, {'a','d'})
        context = r.run_binding_context(self.work)
        for version, config in [('base-a','opus'),('content-a','new-opus'),('second-task-a','second-opus')]:
            self.assertTrue(r.validate_run_binding('a',version,config,self.work,context=context))
        for version, config in [('base-a','second-opus'),('content-a','second-opus'),('second-task-a','new-opus')]:
            with self.assertRaises(ValueError): r.validate_run_binding('a',version,config,self.work,context=context)
        self.assertTrue(r.validate_run_binding('c','content-c','new-gemini',self.work,context=context))
        self.assertTrue(r.validate_run_binding('c','content-c','second-gemini',self.work,context=context))
        self.assertEqual(r.versions_for(self.rows,self.work)['c']['id'], 'content-c')
        self.assertEqual(r.historical_versions(self.work,context=context)['content-a'],'a')
        self.assertEqual(r.historical_versions(self.work,context=context)['second-task-a'],'a')
        for path, raw in self.before.items(): self.assertEqual(Path(path).read_bytes(),raw)

    def test_root_source_reversion_is_rejected(self):
        self.amend_patches[0]['baseWorkbookSha256'] = self.rows[0]['workbookSha256']
        self.save(self.amend_path,self.amend_patches)
        with self.assertRaises(ValueError): self.prepare()

    def test_full_gate_required(self):
        gate=r.read(self.gate_path);gate['taskIds'].remove('d');self.save(self.gate_path,gate)
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((a.directory(self.work)/'plan.json').exists())

    def test_forged_stage_summary_does_not_replace_actual_tool_checks(self):
        proof=r.read(self.proof_path);proof['probeResults']['opus']['stages']['plain']['actualToolCheckCount']=999
        self.resign_proof(proof)
        with self.assertRaises(ValueError):self.prepare()

    def test_omitted_probe_evidence_rejected(self):
        data=r.read(self.amend_runtime);data['models']['gemini']['evidenceHashes'].pop(str(self.second/'configs/gemini/named-status.json'))
        self.save(self.amend_runtime,data)
        with self.assertRaises(ValueError):self.prepare()

    def test_incomplete_payload_or_changed_nonworkbook_rejected(self):
        original=r.read(self.proof_path)
        for mutation in ('missing','different'):
            proof=copy.deepcopy(original)
            if mutation=='missing':del proof['workbooks']['c']
            else:proof['nonWorkbookPayloadSuccessorSha256']='e'*64
            self.resign_proof(proof)
            with self.assertRaises(ValueError):self.prepare()

    def test_plan_row_tamper_rejected_before_activation(self):
        self.prepare();path=a.directory(self.work)/'plan.json';plan=r.read(path)
        plan['revisions'][0]['promptPath']=self.rows[0]['promptPath'];self.save(path,plan)
        with self.assertRaises(ValueError):a.validate_plan(self.work)

    def test_activation_is_create_once_and_partial_versions_are_inert(self):
        self.prepare(); self.original_receipts()
        with self.assertRaises(FileNotFoundError):a.activate(self.work,confirmed=True)
        self.assertEqual(r.version_for('a',self.work)['id'],'content-a')
        self.activate()
        with self.assertRaises(ValueError):a.activate(self.work,confirmed=True)

    def test_original_asymmetric_counterpart_blocks_activation(self):
        self.save(self.work/'evaluation-1-opus.request.json',{'problems':[{'problemId':'problem-a','problemVersionId':'content-a'}],
            'solvers':[{'runConfigVersionId':'new-opus'}]})
        self.save(self.work/'evaluation-1-opus.response.json',{'id':'eval'})
        with self.assertRaises(ValueError):self.activate()
        self.assertFalse((a.directory(self.work)/'activation.json').exists())

    def test_delta_only_correction_and_unique_receipt_prefix(self):
        self.activate()
        payload=r.correction_payload(self.work)
        self.assertEqual({x['problemVersionId'] for x in payload['problems']},{'second-task-a','second-task-d'})
        capacity={'checkedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'availableConcurrencyByModel':{'opus':6,'gemini':0}}
        def mutation(folder,name,args,value):
            self.save(folder/(name+'.request.json'),value);self.save(folder/(name+'.response.json'),{'id':'new-'+name});return {'id':'new-'+name}
        with patch.object(r,'fresh_capacity',return_value=capacity),patch.object(r.c,'mutation',side_effect=mutation):
            r.launch_corrections(self.work,confirmed=True)
            capacity['availableConcurrencyByModel']={'opus':0,'gemini':6}
            r.launch_corrections(self.work,confirmed=True)
            result=r.launch_corrections(self.work,confirmed=True)
        self.assertEqual(result['status'],'already_dispatched')
        for m in r.MODELS:
            req=r.read(self.work/('correction-content-2-'+m+'.request.json'))
            self.assertEqual(req['problems'],payload['problems']);self.assertEqual(req['solvers'][0]['runConfigVersionId'],'second-'+m)
        self.assertFalse((self.work/'correction-content-1.response.json').exists())

    def test_collector_preserves_complete_intermediate_attempt_sets(self):
        self.activate();context=r.run_binding_context(self.work);byversion=r.historical_versions(self.work,context=context)
        entries=[]
        for i,version in enumerate(('base-a','content-a','second-task-a')):
            eid='eval-'+str(i);planned={'problems':[{'problemVersionId':version}]}
            attempts=[{'attemptNumber':j,'problemRunId':f'{i}-{j}','score':float(i==0),'status':'completed'} for j in (1,2,3)]
            entries.append((eid,{'solver':'opus'},planned,{'evaluationId':eid,'cells':[{'solverId':'solver','problemVersionId':version,'attempts':attempts}]}))
        active={t:v['id'] for t,v in r.versions_for(self.rows,self.work).items()}
        rows,superseded=campaign.assemble_evaluation_rows(self.rows,entries,active,byversion)
        self.assertEqual(len(rows),4);self.assertEqual(len(superseded),2)
        target=next(x for x in rows if x['taskId']=='a');self.assertEqual({x['problemVersionId'] for x in target['models']['opus']},{'second-task-a'})
        self.assertTrue(all(x['finalScore']==0 for x in target['models']['opus']))

    def test_provision_copies_immediate_predecessor_and_changes_only_config(self):
        plan=self.prepare();created={};updates=[]
        def cli(*args):
            vid=args[-1]
            if vid in created:return created[vid]
            return next(r.read(x['baseVersionPath']) for x in plan['revisions'] if x['baseProblemVersionId']==vid)
        def mutation(folder,name,args,payload=None):
            if name=='create':
                base=cli('get',args[-1]);value=copy.deepcopy(base);value['id']='created-'+folder.name;created[value['id']]=value;return value
            if name=='update':created[args[-1]].update(payload);updates.append(payload)
            return {}
        with patch.object(r.c,'cli',side_effect=cli),patch.object(r.c,'mutation',side_effect=mutation):a.provision(self.work)
        self.assertEqual(updates,[{'solverRunConfigVersionId':'second-opus'}]*2)
        self.assertFalse((a.directory(self.work)/'activation.json').exists())

    def test_registry_flags_and_layer_counts_are_required(self):
        original=r.read(self.proof_path)
        for key,value in [('registryDigestVerified',False),('successorLayerCount',8),('baseImage','wrong')]:
            proof=copy.deepcopy(original);proof['registryResults']['opus'][key]=value;self.resign_proof(proof)
            with self.assertRaises(ValueError):self.prepare()

    def test_verified_local_layer_is_replayed_and_cohort_bound(self):
        with patch('recursion_content_successor_image.verify_payload',side_effect=AssertionError('invalid archive')):
            with self.assertRaises(AssertionError):self.prepare()
        self.layer['entries'].pop()
        with self.assertRaises(ValueError):self.prepare()

    def test_mask_gate_hash_and_exact_validation_flags_required(self):
        gate=r.read(self.gate_path);gate['approvedMasks']['a']['sha256']='0'*64;self.save(self.gate_path,gate)
        proof=r.read(self.proof_path);proof['censusGateSha256']=r.sha(self.gate_path);self.resign_proof(proof)
        with self.assertRaises(ValueError):self.prepare()
        gate['approvedMasks']['a']['sha256']=self.amend_patches[0]['maskSpecificationSha256'];self.save(self.gate_path,gate)
        proof['censusGateSha256']=r.sha(self.gate_path);self.resign_proof(proof)
        vpath=Path(self.amend_patches[0]['validationPath']);v=r.read(vpath);v['validation']['noUnapprovedCellsRestored']=False;self.save(vpath,v)
        self.amend_patches[0]['validationSha256']=r.sha(vpath);self.save(self.amend_path,self.amend_patches)
        with self.assertRaises(ValueError):self.prepare()

    def test_actual_probe_ids_cannot_reuse_parent(self):
        model,stage='opus','research';folder=self.second/'configs'/model
        status=r.read(folder/(stage+'-status.json'));status['id']='parent-opus-research'
        self.save(folder/(stage+'-status.json'),status);self.save(folder/(stage+'-probe.response.json'),{'probeRunId':status['id']})
        runtime=r.read(self.amend_runtime)
        for name in (stage+'-status.json',stage+'-probe.response.json'):
            runtime['models'][model]['evidenceHashes'][str(folder/name)]=r.sha(folder/name)
        self.save(self.amend_runtime,runtime)
        proof=r.read(self.proof_path);proof['probeResults'][model]['stages'][stage]['probeRunId']=status['id'];self.resign_proof(proof)
        with self.assertRaises(ValueError):self.prepare()

    def test_actual_probe_ids_are_globally_distinct(self):
        model,stage='gemini','research';folder=self.second/'configs'/model
        status=r.read(folder/(stage+'-status.json'));status['id']='opus-research'
        self.save(folder/(stage+'-status.json'),status);self.save(folder/(stage+'-probe.response.json'),{'probeRunId':status['id']})
        runtime=r.read(self.amend_runtime)
        for name in (stage+'-status.json',stage+'-probe.response.json'):
            runtime['models'][model]['evidenceHashes'][str(folder/name)]=r.sha(folder/name)
        self.save(self.amend_runtime,runtime)
        proof=r.read(self.proof_path);proof['probeResults'][model]['stages'][stage]['probeRunId']=status['id'];self.resign_proof(proof)
        with self.assertRaises(ValueError):self.prepare()

    def test_no_original_partition_receipts_cannot_activate(self):
        self.prepare()
        with self.assertRaises(ValueError):a.activate(self.work,confirmed=True)
        self.assertFalse((a.directory(self.work)/'activation.json').exists())

    def test_historical_version_identity_cannot_be_reused(self):
        plan=self.prepare();row=plan['revisions'][0];v=r.read(row['baseVersionPath'])
        v.update(id='base-a',solverRunConfigVersionId='second-opus')
        with self.assertRaises(ValueError):a._same(r.read(row['baseVersionPath']),v,row)

    def test_args_change_cannot_be_hidden_by_rehashing_config(self):
        runtime=r.read(self.amend_runtime);path=Path(runtime['models']['opus']['configPath']);cfg=r.read(path)
        cfg['config']['args']='changed reasoning';self.save(path,cfg)
        runtime['models']['opus']['configSha256']=r.sha(path);self.save(self.amend_runtime,runtime)
        with self.assertRaises(ValueError):self.prepare()
