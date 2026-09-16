"""Independent read-back of amendment masks, historical masks, and payload scope."""
import collections,hashlib,json,tarfile
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from scripts.recursion_residual_result_census import WORK,Book,sha,readjson,save,NS
from scripts.recursion_amendment_mask_validation import verify_mask
GATE_SHA='47d0e268f4e11174316f44090b66ec2ac18701936da5b9f66126070a8b550db0'

def strings(path):
 with ZipFile(path) as z:return [''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []

def main():
 out=WORK/'content-amendments/0002';gp=WORK/'content-amendment-review/gate.json';assert sha(gp)==GATE_SHA;gate=readjson(gp)
 for path,digest in gate['evidenceHashes'].items():assert sha(path)==digest,('Frozen gate evidence changed',path)
 rows=readjson(out/'workbook-manifest.json');shardrows=readjson(out/'numeric-shard.json')+readjson(out/'status-shard.json');assert len(rows)==len(shardrows)==18;shards={x['taskId']:x for x in shardrows};assert len(shards)==18
 oldtext={x['taskId']:x for x in readjson(WORK/'formatted-result-census/masks/manifest.json')};oldnum={x['task']:x for x in readjson(WORK/'workbook-fixes/manifest.json')};checks=[];classes=collections.Counter();replaced=[];orphanCount=0
 for row in rows:
  tid=row['taskId'];assert tid in shards
  for k in ['workbookSourceSha256','workbookSha256','maskSpecificationSha256','validationSha256']:assert row[k]==shards[tid][k]
  approved=gate['approvedMasks'][tid];assert row['maskSpecificationPath']==approved['path'] and row['maskSpecificationSha256']==approved['sha256'];spec=readjson(approved['path']);assert sha(approved['path'])==approved['sha256'];fresh=verify_mask(approved['path'],row['workbookPath'],expected_spec_sha256=approved['sha256'],expected_output_sha256=row['workbookSha256']);assert fresh['validation']['passed'];assert sha(row['validationPath'])==row['validationSha256'];before,after=Book(row['workbookSourcePath']),Book(row['workbookPath'])
  for c in spec['cells']:
   host=c['sheet']+'!'+c['cell'];assert before.cells.get(host)==c['expectedValue'];assert after.cells.get(host)==c['replacement'];assert host not in after.formulas;classes[c['classification']]+=1
   if c['replacement'] is not None:replaced.append({'taskId':tid,'cell':host,'replacement':c['replacement']})
  history=set(oldtext.get(tid,{}).get('maskedCells',[]))
  for sheet,cells in oldnum.get(tid,{}).get('changed_cells',{}).items():history.update(sheet+'!'+x['cell'] for x in cells)
  changedHistorical=[]
  for host in history:
   assert before.cells.get(host)==after.cells.get(host),('Historical masked cell changed',tid,host)
   if after.cells.get(host) is not None:changedHistorical.append({'cell':host,'retainedValue':after.cells[host]})
  beforeSST,afterSST=strings(row['workbookSourcePath']),strings(row['workbookPath']);historicalSST=oldtext.get(tid,{}).get('blankedSharedStringIndices',[])
  for i in historicalSST:assert beforeSST[i]==afterSST[i]=='',('Prior orphan string restored',tid,i)
  for item in spec['orphanSharedStrings']:assert beforeSST[item['index']]==item['expectedValue'] and afterSST[item['index']]==''
  orphanCount+=len(spec['orphanSharedStrings']);checks.append({'taskId':tid,'workbookSourceSha256':sha(row['workbookSourcePath']),'workbookSha256':sha(row['workbookPath']),'answerKeySha256':sha(spec['answerKeyPath']),'maskSpecificationSha256':approved['sha256'],'validationSha256':row['validationSha256'],'approvedCellsReadBack':len(spec['cells']),'historicalMaskCellsUnchanged':len(history),'historicalMaskCellsWithRetainedValue':changedHistorical,'historicalBlankSSTEntriesStillBlank':len(historicalSST),'approvedOrphanSSTEntriesScrubbed':len(spec['orphanSharedStrings']),'exactZipXmlVerifierPassed':True})
 # Independent local layer inventory: every file is one of these workbooks; no runtime/key files.
 lp=out/'images/workbook-layer.json';layer=readjson(lp);assert layer['kind']=='workbook-only-content-successor';assert layer['patchManifest']['sha256']==sha(out/'workbook-manifest.json');assert sha(layer['runtimeManifest']['path'])==layer['runtimeManifest']['sha256'];expected={x['name']:x for x in layer['entries']};assert len(expected)==18;assert {x['taskId'] for x in expected.values()}==set(shards)
 archive=out/'images/workbook-layer.tar.gz';assert 'sha256:'+sha(archive)==layer['digest'];names=[]
 with tarfile.open(archive,'r:gz') as t:
  for member in t:
   assert member.isfile() and member.name in expected;entry=expected[member.name];data=t.extractfile(member).read();assert hashlib.sha256(data).hexdigest()==entry['sha256']==shards[entry['taskId']]['workbookSha256'];names.append(member.name)
 assert set(names)==set(expected) and len(names)==18
 assert sha(gp)==GATE_SHA
 result={'schemaVersion':1,'status':'passed','workbooks':18,'approvedCellsAbsentOrReplaced':sum(x['approvedCellsReadBack'] for x in checks),'cellClasses':dict(classes),'approvedOrphanSSTEntriesScrubbed':orphanCount,'preservedBannerLabels':replaced,'historicalMaskCellsRechecked':sum(x['historicalMaskCellsUnchanged'] for x in checks),'historicalBlankSSTEntriesRechecked':sum(x['historicalBlankSSTEntriesStillBlank'] for x in checks),'noHistoricalMaskRestoration':True,'all18AnswerKeysUnchanged':True,'frozenGateSha256':GATE_SHA,'frozenGateEvidenceHashCount':len(gate['evidenceHashes']),'frozenGateEvidenceUnchanged':True,'workbookManifestSha256':sha(out/'workbook-manifest.json'),'numericShardSha256':sha(out/'numeric-shard.json'),'statusShardSha256':sha(out/'status-shard.json'),'localLayer':{'manifestSha256':sha(lp),'digest':layer['digest'],'fileCount':len(names),'onlyApprovedWorkbooks':True,'runtimeManifestSha256':layer['runtimeManifest']['sha256'],'noRuntimeOrAnswerKeyEntries':True},'tasks':checks,'limits':'Read-back verifies the exact approved semantic scope and preservation. It is not a new financial oracle or proof that no other task defects exist. Publication and evaluation are handled separately.','remoteMutations':0};save(out/'post-mask-review.json',result);print(json.dumps({k:result[k] for k in ['status','workbooks','approvedCellsAbsentOrReplaced','cellClasses','approvedOrphanSSTEntriesScrubbed','historicalMaskCellsRechecked','historicalBlankSSTEntriesRechecked','frozenGateEvidenceHashCount']},indent=2))
if __name__=='__main__':main()
