"""Prepare immutable opaque custody; never enroll, claim or adopt old quality."""
from pathlib import Path
import argparse,json,time
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_opaque_history as O
from scripts.fleet.migration import retain

def prepare(previous,home,output,*,at_ns=None):
    old=L.read(previous);wb=old['workbook'];now=time.time_ns()if at_ns is None else at_ns
    # The input is an evidence selection, not a validated old quality result.
    p={k:old[k]for k in O.FIELDS-{'opaque_history'}}
    p.update(schema_version=O.SCHEMA,home=str(home),created_at_ns=now,expires_at_ns=now+86400*10**9)
    for key in ('baseline','baseline_sidecar'):p[key]=L.binding(p[key]['path'])
    roots={k:str(Path(p[k+'_manifest']['path']).parent.parent)for k in ('source','segmentation')}
    paths={p['audit_directory'],*p['excluded'].get(wb,[]),*roots.values()}
    rows=[previous,p['tracker'],p['baseline'],p['baseline_sidecar'],*p['curations'],*p['histories'][wb]]
    paths.update(row['path']for row in rows if not any(Path(row['path']).is_relative_to(Path(root))for root in paths))
    namespaces={path:O.tree(path)for path in sorted(paths)}
    p['audit_files']=namespaces[p['audit_directory']]
    counts=O.minimum_consumption(namespaces)
    p.update(retained_opaque_research_calls=counts['research_calls'],new_source_reconstruction_ordinal=counts['source_reconstructions']+1)
    p['opaque_history']=dict(schema_version='harbor-opaque-historical-custody/v1',namespaces=namespaces,generation_roots=roots,consumption_upper_bounds=counts,historical_quality_adopted=False,scope_policy='retain-original-curation-and-ordered-targets',prior_selection=previous)
    row=retain(Path(output),p);O.validate(row,at_ns=now);return row

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--previous',required=True);a.add_argument('--sha256',required=True);a.add_argument('--home',required=True);a.add_argument('--output',required=True);args=a.parse_args();print(json.dumps(prepare(dict(path=str(Path(args.previous).absolute()),sha256=args.sha256),args.home,args.output)))
