"""Carry exact prior inventory approvals forward; never create new allowances.

The former registry must be bound by a complete immutable pipeline snapshot.
Selected approvals must equal original generation documents and inventories.
Current entries remain an unchanged prefix; historical bytes remain untouched.
"""
import argparse
import copy
import json
import os
from pathlib import Path

from scripts import corpus
from scripts.fleet.migration import binding, retain, safe
from xl_inventory_approval import (validate_registry, approval_claims,
    object_hash, canonical_bytes, DEFAULT_REGISTRY)
from xl_source_inventory import validate_inventory_manifest


def extend(current, historical, pairs):
    validate_registry(current); validate_registry(historical)
    result=copy.deepcopy(current)
    added=[]
    for inventory,document in pairs:
        validate_inventory_manifest(inventory)
        approval=document.get('approval',{})
        core={'schema_version':'source-inventory-approval/v1','approval':approval}
        if document != {**core,'inventory_approval_sha256':object_hash(core)}:
            raise ValueError('original approval envelope changed')
        if approval not in historical['approvals']:
            raise ValueError('approval absent from pinned historical registry')
        if approval_claims(inventory,batch_id=approval['batch_id']) != approval:
            raise ValueError('historical approval inventory or source ledger differs')
        identity=(approval['batch_id'],approval['inventory_artifact_sha256'])
        matches=[row for row in result['approvals'] if (row['batch_id'],row['inventory_artifact_sha256'])==identity]
        if matches:
            if matches != [approval]:raise ValueError('current approval conflicts with historical authority')
        else:
            result['approvals'].append(copy.deepcopy(approval));added.append(approval['batch_id'])
    unsigned={k:v for k,v in result.items() if k!='registry_sha256'}
    result['registry_sha256']=object_hash(unsigned)
    validate_registry(result)
    if result['approvals'][:len(current['approvals'])] != current['approvals']:
        raise ValueError('existing approvals changed')
    return result,added


def retain_bytes(path,data):
    with path.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    return binding(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--generation',type=Path,action='append',required=True)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--registry',type=Path,default=DEFAULT_REGISTRY)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    snapshot=safe(a.snapshot.absolute())
    records=corpus._frozen_code_files(snapshot)
    if not records or corpus.object_hash(records)!=snapshot.name:raise ValueError('bound original snapshot required')
    historic_path=snapshot/'verification_manifests/approved_source_inventories.v2.json'
    if records.get(str(historic_path.relative_to(snapshot)))!=binding(historic_path)['sha256']:raise ValueError('historical registry not pinned')
    current=json.loads(a.registry.read_text());historic=json.loads(historic_path.read_text());pairs=[];selected=[]
    a.output=safe(a.output.absolute())
    a.registry=safe(a.registry.absolute())
    for directory in a.generation:
        directory=safe(directory.absolute())
        inv=directory/'inventory.json';approval=directory/'inventory-approval.json'
        pair=(json.loads(inv.read_text()),json.loads(approval.read_text()));pairs.append(pair)
        selected.append({'inventory':binding(inv),'approval':binding(approval)})
        for row in pair[1]['approval']['batch_source_ledger']:
            source=safe(a.source_root/(row['workbook_id']+'.xlsx'))
            if binding(source)['sha256']!=row['original_source']['sha256'] or source.stat().st_size!=row['original_source']['size_bytes']:
                raise ValueError('actual original source differs from historical approval')
    proposed,added=extend(current,historic,pairs)
    a.output.mkdir(parents=True,exist_ok=False)
    prior=retain_bytes(a.output/'previous-registry.json',a.registry.read_bytes())
    history=retain_bytes(a.output/'historical-registry.json',historic_path.read_bytes())
    candidate=retain_bytes(a.output/'proposed-registry.json',canonical_bytes(proposed))
    receipt=retain(a.output/'receipt.json',{'schema_version':'source-approval-carry-forward/v1','snapshot_manifest':binding(snapshot/'.pipeline-code.json'),'snapshot_code_sha256':snapshot.name,'historical_registry':binding(historic_path),'historical_registry_copy':history,'previous_registry':prior,'proposed_registry':candidate,'selected_generations':selected,'carried_forward_batch_ids':added,'new_allowances':False,'historical_artifacts_rewritten':False})
    temporary=a.registry.with_name(a.registry.name+'.carry-forward.tmp')
    with temporary.open('xb') as stream:stream.write(canonical_bytes(proposed));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,a.registry)
    print(json.dumps({'receipt':receipt,'registry':binding(a.registry),'carried_forward':added,'new_allowances':False}))

if __name__=='__main__':main()
