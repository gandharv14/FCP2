import pathlib,json,hashlib,collections,re,xml.etree.ElementTree as ET
from zipfile import ZipFile
from scripts.recursion_residual_result_census import WORK,OUT,sha,readjson,save,STATUS,TargetSource,all_source_tables,NS,sheets,active_assets
A=WORK/'content-amendment-review';base=readjson(OUT/'manifest.json');generic={x['taskId']:x for x in readjson(OUT/'generic-manifest.json')};assets={x['taskId']:x for x in active_assets()};sem=[];specs={}
def evidence(p):return {str(pathlib.Path(p).resolve()):sha(p)}
preserve={
'0047':'Dated source-document annotation; the extra trailing marker is not a financial output.',
'0107':'Fiscal-year timeline labels.', '0111':'Parenthesized source/as-of dates.', '0115':'Fiscal-year labels.', '0116':'Fiscal-year labels in capacity forecasts.',
'0123':'Explicit 30/360 day-count convention input.', '0129':'Commodity oil/gas price scenario pairs and their copied axis labels, not calculated returns.',
'0133':'Source/as-of dates and commodity oil/gas price assumption pairs.', '0166':'Literal three-way cost allocation convention options and selections in the soft-cost budget schedule.',
'0218':'Literal date annotation (including an invalid April31 typo), not numeric financial results.', '0236':'Explicit 30/360 day-count convention.',
'0253':'Month/year period labels.', '0263':'Fiscal-year concatenations from timeline values; formatting of period axes rather than financial results.',
'0284':'Date annotations with trailing marker digits, not a financial calculation.', '0287':'Fiscal-year labels in capacity forecasts.',
'0340':'Literal multi-variable scenario combinations for salary, commission/ACV assumptions and axes.', '0412':'Oil/gas price assumption pairs, directly formatted from pricing inputs or copied as scenario labels.',
'0417':'Literal retained 5/1 annotation in account-label columns; no native calculation. Preserve the source convention.',
'0431':'Fiscal-year axis/period labels.', '0432':'Fiscal-year axis/period labels.', '0465':'Literal debt call-premium convention 102/101.',
'0495':'Transaction identifiers with dates.', '0651':'Literal maturity-year pair.', '0691':'Date annotations using 20XX year placeholders.'}
# Supplementary native conversion evidence, original unchanged and exact cached banner matched.
old470=next(x for x in base if x['taskId']=='0470');p470=A/'native-conversions/0470.xlsx';n470=TargetSource(p470,{'Summary!B4'});assert n470.cells['Summary!B4']==old470['leads'][0]['activeValue'];assert not any(all_source_tables(p470).values())
conversion={'taskId':'0470','sourcePath':old470['nativeSource'],'sourceSha256':sha(old470['nativeSource']),'convertedPath':str(p470.resolve()),'convertedSha256':sha(p470),'method':'Installed LibreOffice headless conversion to separate local copy; original unchanged. Conversion establishes formula role, not numerical oracle.','cells':{'Summary!B4':{'value':n470.cells['Summary!B4'],'formula':n470.formulas['Summary!B4']}},'nativeTableCount':0};save(A/'0470/conversion-evidence.json',conversion)
for b in base:
 tid=b['taskId'];g=generic[tid];assert b['workbookSha256']==g['workbookSha256']==assets[tid]['workbookSha256']==sha(b['workbookPath'])
 # Normalize immutable provenance fields after the completed read-only pass.
 b['originalSourceSha256']=sha(b['originalSource']) if b['originalSource'] else None
 for c in b['leads']:
  if isinstance(c['activeValue'],bool) and c['nativeValue'] in ['0','1']:raise AssertionError('Boolean mismatch requires exact native recheck')
 save(OUT/'tasks'/f'{tid}.json',b)
 q={k:b[k] for k in ['taskId','workbookPath','workbookSha256','activeProblemVersionId','nativeSource','nativeSourceSha256','originalSource','originalSourceSha256','sourceSupported']};q.update(confirmedNumericOutputs=[],confirmedCalculatedStatuses=[],preserved=[],unresolved=[],maskScopeStatus='no_masks_proposed',evidenceHashes={**evidence(OUT/'tasks'/f'{tid}.json'),**evidence(OUT/'generic-tasks'/f'{tid}.json')})
 if b['nativeSource']:q['evidenceHashes'].update(evidence(b['nativeSource']))
 if b['originalSource']:q['evidenceHashes'].update(evidence(b['originalSource']))
 for c in b['confirmed']:
  if tid=='0679':q['preserved'].append({**c,'semanticReason':'Native literal descriptive labels/units inside an unusually broad E12:AXS13 table reference; structural text is not a calculated numeric or status output.'});continue
  (q['confirmedCalculatedStatuses'] if isinstance(c['activeValue'],str) and STATUS.fullmatch(c['activeValue']) else q['confirmedNumericOutputs']).append({**c,'semanticReason':'Exact native calculated sensitivity result body; scenario axes lie outside the targeted coordinates.'})
 for c in b['leads']:
  if tid in ['0091','0111','0447','0475','0470']:
   nc=dict(c)
   if tid=='0470':nc.update(nativeFormula=n470.formulas[c['cell']],nativeValue=n470.cells[c['cell']]);q['evidenceHashes'].update(evidence(A/'0470/conversion-evidence.json'));q['sourceSupported']=True
   nc['semanticReason']={'0091':'GAAP dollar/percentage accretion output and direct corner copies, supported by converted native formulas.','0111':'Concatenated IRR-or-NM and MOIC results.','0447':'Case/horizon banner includes calculated five-year IRR and MOIC. Preserve the literal case/horizon label.','0475':'Scenario returns or incremental IRR/MOIC contribution displays; original scenario numeric result cells are already blank active.','0470':'Case/horizon banner includes calculated five-year IRR and MOIC. Preserve the literal case/horizon label.'}[tid];q['confirmedNumericOutputs'].append(nc)
  elif tid in ['0133','0218']:
   assert all(isinstance(x['activeValue'],str) and x['activeValue'].startswith('#') for x in b['leads']);q['unresolved'].append({**c,'semanticReason':'Error placeholder in a native result scope, but active cache differs from source. No matching numerical answer disclosure is established; preserve pending explicit source/version adjudication.'})
  elif tid in ['0249','0326','Vertex_Quantum_Options_Strategy_Model','aquila_alpha_pnl_model']:
   q['preserved'].append({**c,'semanticReason':{'0249':'Copied sensitivity assumption-change labels (cash interest/lifecycle/cost inflation), not result values.','0326':'Income/expense growth assumption display: native literal D79=.03 and blank D108 rendered0, preserve provided primitive display.','Vertex_Quantum_Options_Strategy_Model':'Volatile scenario +/-20% label; supplied scenario assumption, no financial result.','aquila_alpha_pnl_model':'Supplied +/-40% scenario assumption label, no financial result.'}[tid]})
  elif tid in ['0127','0366','0709']:
   q['unresolved'].append({**c,'semanticReason':'Visible text is consistent with supplied scenario/loan-pricing labels; preserve. Native source remains unconverted, so source-role confirmation is unavailable.'})
  else:raise AssertionError(('Unhandled mixed lead',tid,c))
 q['preserved'].extend({**c,'semanticReason':'Native literal label/input or exact literal-text formula; preserve.'} for c in b['excluded'])
 for c in g['leads']:
  if tid=='0221' and c['cell']=='Hyp!E471':
   assert c['activeValue']=='30/360';q['preserved'].append({**c,'semanticReason':'Explicit 30/360 day-count convention input; independent review confirms this is not an output.'})
  elif tid in ['0221','0326']:
   q['confirmedNumericOutputs'].append({**c,'semanticReason':'Calculated average/maximum monthly cashflow split display and direct copies.' if tid=='0221' else 'Calculated effective-rent summary from SUM-based underlying PSF/per-unit outputs, which are blank in the active workbook.'})
  elif tid in preserve:q['preserved'].append({**c,'semanticReason':preserve[tid]})
  else:raise AssertionError(('Unhandled generic lead',tid,c))
 q['preserved'].extend(g['excluded'])
 if b['sharedStringLeads'] or g['sharedStringLeads']:
  orphans=[x for x in b['sharedStringLeads']+g['sharedStringLeads'] if not x.get('liveCellReferences')];assert not orphans
 if tid=='0111':
  src=readjson(A/'0111/proposed-mask.json');extra=src['additionalCalculatedStatusMask'];print('0111extra',extra)
  # Independent source review identifies exactly one additional pure calculated status.
  q['confirmedCalculatedStatuses'].append({'cell':'JV_Operations!D86','activeValue':'NM','nativeValue':'NM','semanticReason':'Independent source review: native calculated IRR-or-NM output, not a primitive assumption.'})
  q['evidenceHashes'].update(evidence(A/'0111/evidence.json'))
 for p in (A/tid).glob('independent-source-review.json'):q['evidenceHashes'].update(evidence(p))
 masks=q['confirmedNumericOutputs']+q['confirmedCalculatedStatuses'];q['maskScopeStatus']='proposed_source_confirmed_masks' if masks else ('unresolved_preserve' if q['unresolved'] else 'no_masks_needed_for_screened_candidates');q['sourceCoverageStatus']='native_or_hash_bound_conversion_checked' if q['sourceSupported'] else 'native_source_uncheckable';save(OUT/'adjudicated'/f'{tid}.json',q);sem.append(q)
 if masks:
  cells=[];seen=set()
  for c in masks:
   assert c['cell'] not in seen;seen.add(c['cell']);sheet,cell=c['cell'].rsplit('!',1);rep='Base Case | 5-Year Returns' if tid in ['0447','0470'] else None
   cells.append({'sheet':sheet,'cell':cell,'expectedValue':c['activeValue'],'replacement':rep,'classification':'calculated_status_disclosure' if c in q['confirmedCalculatedStatuses'] else 'calculated_numeric_output_disclosure','reason':c['semanticReason']})
  with ZipFile(b['workbookPath']) as z:
   strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else [];live=collections.Counter();targetIndices=set()
   for sh,p in sheets(z).items():
    for c in ET.fromstring(z.read(p)).findall('.//s:sheetData/s:row/s:c',NS):
     v=c.findtext('s:v',namespaces=NS)
     if c.attrib.get('t')=='s' and v is not None:
      if sh+'!'+c.attrib['r'] in seen:targetIndices.add(int(v))
      else:live[int(v)]+=1
   values={c['expectedValue'] for c in cells};orphans=[{'index':i,'expectedValue':v} for i,v in enumerate(strings) if not live[i] and (i in targetIndices or v in values)]
  row=assets[tid];spec={'schemaVersion':1,'taskId':tid,'status':'proposed_pending_root_semantic_gate','activeProblemVersionId':b['activeProblemVersionId'],'workbookPath':b['workbookPath'],'workbookSha256':b['workbookSha256'],'answerKeyPath':row['answerKeyPath'],'answerKeySha256':sha(row['answerKeyPath']),'cells':cells,'orphanSharedStrings':orphans,'sourceEvidenceHashes':{**q['evidenceHashes'],**evidence(OUT/'adjudicated'/f'{tid}.json')},'scope':'Remove only enumerated source-confirmed computed outputs/statuses; preserve exact banner labels, primitive inputs, axes, unrelated XML, and answer key.'};sp=A/'mask-specifications'/f'{tid}.json';save(sp,spec);specs[tid]={'path':str(sp.resolve()),'sha256':sha(sp),'numericOutputCells':len(q['confirmedNumericOutputs']),'calculatedStatusCells':len(q['confirmedCalculatedStatuses']),'orphanSharedStringCount':len(orphans)}
save(OUT/'manifest.json',base);save(OUT/'adjudicated-manifest.json',sem);save(A/'proposed-mask-specifications.json',{'status':'proposed_pending_root_semantic_gate','tasks':specs,'sourceRosterSha256':sha(WORK/'final-assets.json'),'activationSha256':sha(WORK/'content-successor/activation.json')})
summary={'tasks':len(sem),'genericScreenTasks':len(generic),'sourceSupported':sum(q['sourceSupported'] for q in sem),'sourceUncheckable':sum(not q['sourceSupported'] for q in sem),'proposedMaskTasks':len(specs),'numericOutputTasks':{q['taskId']:len(q['confirmedNumericOutputs']) for q in sem if q['confirmedNumericOutputs']},'calculatedStatusTasks':{q['taskId']:len(q['confirmedCalculatedStatuses']) for q in sem if q['confirmedCalculatedStatuses']},'unresolvedTasks':{q['taskId']:len(q['unresolved']) for q in sem if q['unresolved']},'preservedCandidateCells':sum(len(q['preserved']) for q in sem),'noRemoteMutations':True,'noWorkbookEdits':True,'noSharedLedgerEdits':True};save(A/'semantic-summary.json',summary);print(json.dumps(summary,indent=2))
