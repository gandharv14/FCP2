"""Publish evidence-pinned audit overlays without downloading the base layers."""
from __future__ import annotations
import argparse,copy,gzip,hashlib,io,json,tarfile
from datetime import datetime,timezone
from pathlib import Path
import recursion_campaign as c
import recursion_registry_image as registry
from recursion_hardening_campaign import AUDIT,WORK,sha

def make_layer(folder, assets=None):
    folder.mkdir(parents=True,exist_ok=True)
    overlay=c.read(WORK/'runtime/overlay-manifest.json')
    target=folder/'overlay.tar.gz';uncompressed=hashlib.sha256();entries=[]
    class Writer:
        def __init__(self,out):self.out=out
        def write(self,data):uncompressed.update(data);return self.out.write(data)
    with target.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=Writer(gz),mode='w|',format=tarfile.PAX_FORMAT) as archive:
            def add(name,data=b'',mode=0o644,directory=False):
                assert name not in [e['name'] for e in entries],name
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode
                if directory:info.type=tarfile.DIRTYPE
                archive.addfile(info,None if directory else io.BytesIO(data))
                entries.append({'name':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mode':oct(mode)})
            for d in overlay['directories']:add(d['destination'].lstrip('/'),mode=int(d['mode'],8),directory=True)
            for f in overlay['files']:
                p=Path(f['source']);assert sha(p)==f['sha256'],p
                add(f['destination'].lstrip('/'),p.read_bytes(),int(f['mode'],8))
            if assets is not None:
                add('opt/fcp/tasks/.wh..wh..opq')
                for r in assets:
                    prefix='opt/fcp/tasks/'+r['taskId']+'/'
                    p=Path(r['workbookPath']);assert sha(p)==r['workbookSha256'];add(prefix+p.name,p.read_bytes(),0o600)
                    if r['additionalAssumptionsPath']:
                        p=Path(r['additionalAssumptionsPath'])
                        if 'additionalAssumptionsSha256' in r:assert sha(p)==r['additionalAssumptionsSha256']
                        add(prefix+'additional-assumptions.md',p.read_bytes(),0o600)
                    if r['researchRoot']:
                        root=Path(r['researchRoot'])
                        if 'researchFiles' in r:
                            actual={str(p) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
                            assert actual==set(r['researchFiles']),'Research file set changed: '+r['taskId']
                        for p in sorted(root.rglob('*')):
                            if not p.is_file() or '__pycache__' in p.parts:continue
                            assert not p.is_symlink(),p
                            if 'researchFiles' in r:assert sha(p)==r['researchFiles'][str(p)],p
                            name=p.relative_to(root).as_posix();data=p.read_bytes()
                            if p.suffix=='.gz':name=name[:-3];data=gzip.decompress(data)
                            assert not any(x in name for x in ('answer_key','solution_oracle','grader')),name
                            add(prefix+'mcp-server/'+name,data,0o600)
    info={'digest':'sha256:'+sha(target),'diffId':'sha256:'+uncompressed.hexdigest(),'size':target.stat().st_size,'entries':entries}
    c.save(folder/'overlay.json',info)
    return target,info

def verified_base_images(base_folder, assets_digest):
    assert assets_digest, 'Runtime-only publication requires pinned task assets'
    images={model:c.read(base_folder/(model+'-image.json')) for model in ('opus','gemini')}
    for model, previous in images.items():
        assert previous['assetsManifestSha256']==assets_digest, 'Base task assets differ: '+model
        assert previous.get('verifiedAt') and '@sha256:' in previous['image']
    return images


def publish(folder,assets=None,assets_digest=None,base_folder=None):
    runtime_digest=sha(WORK/'runtime/overlay-manifest.json')
    base_images=verified_base_images(base_folder,assets_digest) if base_folder else None
    path,layer=make_layer(folder,None if base_images else assets)
    assert runtime_digest==sha(WORK/'runtime/overlay-manifest.json')
    parents={'opus':c.read(c.REPORTS/'opus-max-20260915/verified.json'),
             'gemini':next(v for v in c.read(c.REPORTS/'remote/gemini-versions.json') if v['id']=='abc2c159-e97f-483c-8cca-358ac228b7ee')}
    for model,parent in parents.items():
        c.save(folder/(model+'-parent.json'),parent);(folder/(model+'-parent.json')).chmod(0o600)
        image=base_images[model]['image'] if base_images else parent['config']['harnessImageUrl']
        repo,ref=image.removeprefix(registry.HOST+'/').split('@')
        assert repo==parent['config']['harnessImageUrl'].removeprefix(registry.HOST+'/').split('@')[0]
        client=registry.Registry(repo);manifest=client.manifest(ref)
        config=json.loads(client.blob(manifest['config']['digest']))
        assert config['architecture']=='amd64' and config['os']=='linux'
        assert client.push_blob(path.read_bytes())==layer['digest']
        config['rootfs']['diff_ids'].append(layer['diffId']);config['config']['User']='root'
        config['config']['WorkingDir']='/app'
        created='2026-09-15T00:00:00Z'
        config['created']=created;config.setdefault('history',[]).append({'created':created,'created_by':'FCP audit hardening '+layer['digest']})
        data=registry.canonical(config);d=client.push_blob(data)
        manifest['config']={**manifest['config'],'digest':d,'size':len(data)}
        media='application/vnd.oci.image.layer.v1.tar+gzip' if 'oci' in manifest['mediaType'] else 'application/vnd.docker.image.rootfs.diff.tar.gzip'
        manifest['layers'].append({'mediaType':media,**{k:layer[k] for k in ('digest','size')}})
        data=registry.canonical(manifest);d=registry.digest(data);tag='audit-hardening-'+d.split(':')[1][:20]
        client.request('PUT',f'/v2/{repo}/manifests/{tag}',data,manifest['mediaType'])
        verified,_=client.request('GET',f'/v2/{repo}/manifests/{d}');assert registry.digest(verified)==d
        c.save(folder/(model+'-image.json'),{'image':registry.HOST+'/'+repo+'@'+d,'parent':image,'layerDigest':layer['digest'],
            'assetsManifestSha256':assets_digest,'runtimeManifestSha256':runtime_digest,'verifiedAt':datetime.now(timezone.utc).isoformat()})
        print(model,'published',d,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--assets',type=Path)
    p.add_argument('--base-folder',type=Path,help='Append only runtime fixes to verified images with identical pinned task assets')
    a=p.parse_args();publish(a.folder,c.read(a.assets) if a.assets else None,sha(a.assets) if a.assets else None,a.base_folder)
