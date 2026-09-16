"""Apply only the eight approved numeric amendment specs, then independently verify."""
from pathlib import Path
import json
from scripts.recursion_formatted_result_masks import mask_formatted_workbook
from scripts.recursion_amendment_mask_validation import verify_mask
from scripts.recursion_residual_result_census import WORK,sha,readjson,save
TASKS=('0091','0111','0211','0221','0326','0447','0470','0475')
GATE_SHA='47d0e268f4e11174316f44090b66ec2ac18701936da5b9f66126070a8b550db0'

def main():
 gate_path=WORK/'content-amendment-review/gate.json';assert sha(gate_path)==GATE_SHA;gate=readjson(gate_path);assert gate['status']=='complete';out=WORK/'content-amendments/0002';rows=[]
 for tid in TASKS:
  approved=gate['approvedMasks'][tid];spec_path=Path(approved['path']);assert sha(spec_path)==approved['sha256'];spec=readjson(spec_path);assert spec['taskId']==tid
  taskdir=out/'workbooks'/tid;dest=taskdir/f'{tid}-inputs.xlsx';targets={c['sheet']+'!'+c['cell']:c['expectedValue'] for c in spec['cells']};replacements={c['sheet']+'!'+c['cell']:{'before':c['expectedValue'],'after':c['replacement']} for c in spec['cells'] if c['replacement'] is not None}
  if not dest.exists():
   receipt=mask_formatted_workbook(spec['workbookPath'],dest,targets,spec['workbookSha256'],orphan_sst_values=[x['expectedValue'] for x in spec['orphanSharedStrings']],text_replacements=replacements);save(taskdir/'masker-receipt.json',receipt)
  verified=verify_mask(spec_path,dest,expected_spec_sha256=approved['sha256'],expected_output_sha256=sha(dest));assert verified['validation']['passed'];save(taskdir/'validation.json',verified)
  row={k:verified[k] for k in ['taskId','activeProblemVersionId','workbookSourcePath','workbookSourceSha256','workbookPath','workbookSha256','maskSpecificationPath','maskSpecificationSha256','maskedCellCount','answerKeyPath','answerKeySha256','validation']};row.update(baseWorkbookSha256=verified['workbookSourceSha256'],validationPath=str((taskdir/'validation.json').resolve()),validationSha256=sha(taskdir/'validation.json'),gatePath=str(gate_path.resolve()),gateSha256=GATE_SHA,maskerReceiptPath=str((taskdir/'masker-receipt.json').resolve()),maskerReceiptSha256=sha(taskdir/'masker-receipt.json'));rows.append(row);print(json.dumps({'taskId':tid,'cells':row['maskedCellCount'],'validated':True}),flush=True)
 assert sha(gate_path)==GATE_SHA;save(out/'numeric-shard.json',rows);print(json.dumps({'manifestPath':str((out/'numeric-shard.json').resolve()),'manifestSha256':sha(out/'numeric-shard.json'),'tasks':len(rows),'cells':sum(x['maskedCellCount'] for x in rows)}))
if __name__=='__main__':main()
