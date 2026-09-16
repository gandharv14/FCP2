"""One resumable Gemini probe step; never retry an uncertain mutation."""
import os,json
from pathlib import Path
import recursion_campaign as c
import recursion_hardening_probe as probe
from recursion_content_successor_verify import verify_probe
FOLDER=Path('reports/recursion/gemini-high-score-audit-20260915/refactor/content-amendments/0002/images')
def main():
 os.umask(0o077);work=FOLDER/'configs/gemini';created=c.read(work/'create.response.json');vid=created['initialVersionId'];config=None
 for stage in ['research','plain','named']:
  response=work/(stage+'-probe.response.json')
  if not response.exists():break
  run=c.read(response)['probeRunId'];status=c.cli('probe-runs','get','--run-config-probe-run-id',run);c.save(work/(stage+'-status.json'),status)
  if status['status']!='succeeded':
   print(json.dumps({'stage':stage,'probeRunId':run,'status':status['status'],'willNotCreateReplacementProbe':True}));return
  if config is None:config=c.cli('run-config-versions','get','--run-config-version-id',vid);c.save(work/'config-current.private.json',config)
  request=c.read(work/(stage+'-update.request.json'));receipt=verify_probe(status,request,config,'gemini',stage);c.save(work/(stage+'-actual-verification.json'),receipt);print(json.dumps(receipt),flush=True)
 # Previously launched attempts all succeeded and actual trusted output passed. Resume next stage or lock.
 probe.configure(FOLDER,models=['gemini'],trusted_identity=True)
 if (work/'verified.json').exists():
  verified=c.read(work/'verified.json');assert verified['id']==vid and verified['lockedAt'] and verified['verifiedAt'];print(json.dumps({'runConfigVersionId':vid,'lockedAndVerified':True}))
if __name__=='__main__':main()
