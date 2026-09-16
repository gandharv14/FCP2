#!/usr/bin/env python3
"""Append the approved task layer directly to existing registry images.

No Docker daemon, remote build service, or base-layer download is required.
Credentials exist only in memory and are sent only to the approved registry.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request

HOST='us-central1-docker.pkg.dev'
ACCEPT=', '.join(['application/vnd.oci.image.index.v1+json',
                 'application/vnd.oci.image.manifest.v1+json',
                 'application/vnd.docker.distribution.manifest.list.v2+json',
                 'application/vnd.docker.distribution.manifest.v2+json'])


def canonical(value):return json.dumps(value,separators=(',',':')).encode()
def digest(data):return 'sha256:'+hashlib.sha256(data).hexdigest()
def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected and urllib.parse.urlparse(newurl).hostname!=HOST:
            redirected.remove_header('Authorization')
        return redirected


class Registry:
    def __init__(self,repository):
        assert repository.startswith('lb-ml-dev/fde-runners/runner-fcp-')
        self.repository=repository
        token=subprocess.run(['gcloud','auth','print-access-token'],capture_output=True,text=True,check=True).stdout.strip()
        self.authorization='Basic '+base64.b64encode(('oauth2accesstoken:'+token).encode()).decode()
        self.opener=urllib.request.build_opener(SafeRedirect())

    def request(self,method,path,data=None,content_type=None):
        url=path if path.startswith('https://') else 'https://'+HOST+path
        assert urllib.parse.urlparse(url).hostname==HOST
        headers={'Authorization':self.authorization,'Accept':ACCEPT}
        if content_type:headers['Content-Type']=content_type
        req=urllib.request.Request(url,data=data,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=180) as response:return response.read(),dict(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code==401 and self.authorization.startswith('Basic '):
                challenge=exc.headers.get('WWW-Authenticate','')
                fields=dict(re.findall(r'(\w+)="([^"]*)"',challenge))
                realm=fields.get('realm','')
                if urllib.parse.urlparse(realm).hostname!=HOST:raise
                query=urllib.parse.urlencode({'service':fields.get('service',HOST),
                       'scope':'repository:'+self.repository+':pull,push'})
                authreq=urllib.request.Request(realm+'?'+query,headers={'Authorization':self.authorization})
                with self.opener.open(authreq,timeout=60) as response:tokens=json.load(response)
                self.authorization='Bearer '+(tokens.get('token') or tokens['access_token'])
                return self.request(method,path,data,content_type)
            raise

    def manifest(self,reference):
        data,_=self.request('GET',f'/v2/{self.repository}/manifests/{reference}')
        if reference.startswith('sha256:'):assert digest(data)==reference
        manifest=json.loads(data)
        if 'manifests' in manifest:
            child=next(x for x in manifest['manifests'] if x.get('platform',{}).get('os')=='linux' and x.get('platform',{}).get('architecture')=='amd64')
            return self.manifest(child['digest'])
        return manifest

    def blob(self,reference):
        data,_=self.request('GET',f'/v2/{self.repository}/blobs/{reference}')
        assert digest(data)==reference
        return data

    def push_blob(self,data):
        reference=digest(data)
        try:self.request('HEAD',f'/v2/{self.repository}/blobs/{reference}');return reference
        except urllib.error.HTTPError as exc:
            if exc.code!=404:raise
        _,headers=self.request('POST',f'/v2/{self.repository}/blobs/uploads/',b'')
        location=next(v for k,v in headers.items() if k.lower()=='location')
        separator='&' if '?' in location else '?'
        self.request('PUT',location+separator+urllib.parse.urlencode({'digest':reference}),data,'application/octet-stream')
        self.request('HEAD',f'/v2/{self.repository}/blobs/{reference}')
        return reference


def layer(folder):
    approval=read(folder/'registry-approval.json')
    assert approval['status']=='approved'
    from recursion_scaffold import validate as validate_scaffold
    scaffold=validate_scaffold(folder/'build')
    for item in approval['files']:
        data=(folder/'build'/item['path']).read_bytes()
        assert len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256']
    target=folder/'task-layer.tar.gz'
    uncompressed=hashlib.sha256()
    class HashWriter:
        def __init__(self,output):self.output=output
        def write(self,data):uncompressed.update(data);return self.output.write(data)
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=HashWriter(gz),mode='w|',format=tarfile.PAX_FORMAT) as archive:
            def add(name,data=b'',mode=0o600,directory=False):
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode
                info.uid=info.gid=0
                if directory:info.type=tarfile.DIRTYPE
                archive.addfile(info,None if directory else io.BytesIO(data))
            # OCI opaque whiteout hides every previous task before adding this
            # generation's tasks. Runtime wrappers clean /app and /workspace
            # input workbooks before the model is invoked.
            add('opt/fcp/tasks',mode=0o700,directory=True)
            add('opt/fcp/tasks/.wh..wh..opq',b'')
            for path in sorted((folder/'build/tasks').rglob('*')):
                name='opt/fcp/tasks/'+path.relative_to(folder/'build/tasks').as_posix()
                if path.is_dir():add(name,mode=0o700,directory=True)
                elif path.is_file():add(name,path.read_bytes(),0o600)
            for item in scaffold['files']:
                add(item['destination'],(folder/'build'/item['path']).read_bytes(),item['mode'])
            add('usr/local/bin/fcp-inactivity-watchdog',(folder/'build/fcp-inactivity-watchdog').read_bytes(),0o755)
    info={'digest':digest(target.read_bytes()),'diffId':'sha256:'+uncompressed.hexdigest(),'size':target.stat().st_size}
    save(folder/'task-layer.json',info)
    return target,info


def main(folder,publish):
    approval=read(folder/'registry-approval.json');assert approval['status']=='approved'
    layer_path,layer_info=layer(folder) if publish else (None,None)
    for model in ['opus']:
        parent=read(folder/(model+'-parent.json'))
        image=parent['config']['harnessImageUrl'];repository,reference=image.removeprefix(HOST+'/').split('@')
        registry=Registry(repository)
        manifest=registry.manifest(reference);config=json.loads(registry.blob(manifest['config']['digest']))
        assert config['architecture']=='amd64' and config['os']=='linux'
        save(folder/(model+'-base-manifest.json'),manifest)
        save(folder/(model+'-base-image-config.json'),config)
        print(model,'base verified; layers=',len(manifest['layers']),'entrypoint=',config['config'].get('Entrypoint'),flush=True)
        if not publish:continue
        tag='generation-'+folder.name
        assert HOST+'/'+repository+':'+tag in approval['destinations']
        registry.push_blob(layer_path.read_bytes())
        config['rootfs']['diff_ids'].append(layer_info['diffId'])
        config['config']['User']='root';config['config']['WorkingDir']='/app'
        created=read(folder/'campaign.json')['createdAt']
        config['created']=created
        config.setdefault('history',[]).append({'created':created,'created_by':'FCP approved generation layer '+folder.name})
        data=canonical(config);config_digest=registry.push_blob(data)
        manifest['config']={**manifest['config'],'digest':config_digest,'size':len(data)}
        layer_media='application/vnd.oci.image.layer.v1.tar+gzip' if 'oci' in manifest['mediaType'] else 'application/vnd.docker.image.rootfs.diff.tar.gzip'
        manifest['layers'].append({'mediaType':layer_media,'digest':layer_info['digest'],'size':layer_info['size']})
        data=canonical(manifest);image_digest=digest(data)
        # Do not replace an unrelated image if this campaign tag already exists.
        try:
            old,_=registry.request('GET',f'/v2/{repository}/manifests/{tag}')
            if digest(old)!=image_digest:raise RuntimeError('Campaign tag already names a different image')
        except urllib.error.HTTPError as exc:
            if exc.code!=404:raise
        registry.request('PUT',f'/v2/{repository}/manifests/{tag}',data,manifest['mediaType'])
        verified,_=registry.request('GET',f'/v2/{repository}/manifests/{image_digest}')
        assert digest(verified)==image_digest
        save(folder/(model+'-image.json'),{'image':HOST+'/'+repository+'@'+image_digest,
              'tag':HOST+'/'+repository+':'+tag,'method':'OCI generation layer appended in approved registry',
              'layer':layer_info,'verifiedAt':datetime.now(timezone.utc).isoformat()})
        print(model,'published and digest verified',image_digest,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--publish',action='store_true');args=parser.parse_args()
    main(args.campaign.resolve(),args.publish)
