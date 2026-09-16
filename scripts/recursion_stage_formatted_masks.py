"""Stage only the reviewed formatted-result masks; never edit frozen assets."""
from pathlib import Path
import sys,collections
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.recursion_small_literal_census import Book,WORK,readjson,save,sha
from scripts.recursion_formatted_result_masks import mask_formatted_workbook
ROOT=WORK/'formatted-result-census'
def main():
 assets={r['taskId']:r for r in readjson(WORK/'final-assets.json')};review=[];patches=[]
 for p in sorted((ROOT/'adjudicated').glob('*.json')):
  q=readjson(p);original=readjson(ROOT/'tasks'/p.name)
  for k in ['originalLegacySource','originalLegacySourceSha256','conversionProvenance']:
   if k in original:q[k]=original[k]
  unknown=q.get('uncheckableNumericText',[]);keep=[]
  for c in q['excluded']:
   if (not q['nativeSource'] or Path(q['nativeSource']).suffix.lower() not in ['.xlsx','.xlsm']) and not c.get('semanticDecision'):
    c['reason']='native_source_unavailable_for_numeric_text_role';unknown.append(c)
   else:keep.append(c)
  q['excluded']=keep;q['uncheckableNumericText']=unknown;save(p,q);review.append(q)
  if not q['confirmed']:continue
  r=assets[q['taskId']];targets={c['cell']:c['frozenValue'] for c in q['confirmed']};textrepl={}
  if q['taskId']=='0179':textrepl={'Table 1!AL79':{'before':targets['Table 1!AL79'],'after':'NPV'}}
  orphanvalues={c['nativeCachedValue'] for c in q['confirmed'] if isinstance(c.get('nativeCachedValue'),str)}
  out=ROOT/'masks'/q['taskId']/Path(r['workbookPath']).name
  result=mask_formatted_workbook(r['workbookPath'],out,targets,r['workbookSha256'],orphanvalues,textrepl)
  final=Book(out)
  for cell in targets:assert final.cells.get(cell)==textrepl.get(cell,{}).get('after')
  assert sha(r['answerKeyPath'])==r['answerKeySha256']
  result.update(taskId=q['taskId'],baseWorkbookSha256=r['workbookSha256'],answerKeyPath=r['answerKeyPath'],answerKeySha256=r['answerKeySha256'],sourceEvidencePath=str(p.resolve()),sourceEvidenceSha256=sha(p),status='staged',blocker=None)
  result['validation'].update(inputsPreserved=True,calculatedDisclosuresOnly=True,solvable=True,solvabilityMeaning='Relative patch preservation: removes only audited computed disclosures and preserves primitive inputs. Existing task validation unchanged; this is not a new independent financial solvability oracle.',answerKeyUnchanged=True,all_masked_cells_parse_as_blank_or_preserved_label=True)
  validation=out.parent/'validation.json';save(validation,result);result['validationPath']=str(validation.resolve());result['validationSha256']=sha(validation);patches.append(result)
  print(q['taskId'],result['maskedCellCount'],len(result['blankedSharedStringIndices']),flush=True)
 save(ROOT/'adjudicated-manifest.json',review)
 summary={'screenedTasks':len(review),'confirmedTasks':[q['taskId'] for q in review if q['confirmed']],'confirmedCalculatedStringCells':sum(len(q['confirmed']) for q in review),'unresolvedConcreteLeads':{q['taskId']:len(q['leads']) for q in review if q['leads']},'uncheckableNumericText':{q['taskId']:len(q['uncheckableNumericText']) for q in review if q['uncheckableNumericText']},'scope':'Financial-looking string screen, with native formula/DataTable proof and explicit semantic adjudication. Not a proof that arbitrary narrative text contains no financial information.'}
 save(ROOT/'adjudicated-summary.json',summary);save(ROOT/'masks/manifest.json',patches)
 save(ROOT/'masks/summary.json',{'tasks':len(patches),'maskedCells':sum(p['maskedCellCount'] for p in patches),'blankedSstEntries':sum(len(p['blankedSharedStringIndices']) for p in patches),'sourceAssetsSha256':sha(WORK/'final-assets.json'),'maskManifestSha256':sha(ROOT/'masks/manifest.json'),'allOriginalsUnchanged':True})
 print(readjson(ROOT/'masks/summary.json'))
if __name__=='__main__':main()
