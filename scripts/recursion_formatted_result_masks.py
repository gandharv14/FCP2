"""Narrow byte-preserving removal of audited formatted result strings."""
from __future__ import annotations
import copy,hashlib,json,re,sys
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from scripts.recursion_hardening_workbooks import CELL,VALUE,NS,sheets,sha256
IS=re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?is\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?is>',re.S)
SI=re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?si\b[^>]*?(?:/>|>.*?</(?:[A-Za-z_][\w.-]*:)?si>)',re.S)

def mask_formatted_workbook(source,destination,targets,expected_sha256,orphan_sst_values=(),text_replacements=None):
 source,destination=Path(source),Path(destination)
 if source.resolve()==destination.resolve():raise ValueError('Never mutate frozen source')
 before=sha256(source)
 if before!=expected_sha256:raise ValueError('Frozen source hash mismatch')
 targets=dict(targets);text_replacements=text_replacements or {}
 for k,v in text_replacements.items():
  if k not in targets or v["before"]!=targets[k] or v["after"]==v["before"]:raise ValueError("Invalid text replacement")
 patches={};removed=[];shared=[];live_indices=set();target_shared_indices=set()
 with ZipFile(source) as z:
  paths=sheets(z); reverse={p:s for s,p in paths.items()}
  if 'xl/sharedStrings.xml' in z.namelist():shared=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
  for sheet,path in paths.items():
   raw=z.read(path);root=ET.fromstring(raw);selected={}
   for cell in root.findall('.//s:sheetData/s:row/s:c',NS):
    host=sheet+'!'+cell.attrib['r'];typ=cell.attrib.get('t');value=cell.findtext('s:v',namespaces=NS)
    if typ=='s' and value is not None:text=shared[int(value)]
    elif typ=='inlineStr':text=''.join(cell.find('s:is',NS).itertext()) if cell.find('s:is',NS) is not None else None
    elif typ in ('str','e'):text=value
    else:text=None
    if host in targets:
     if text!=targets[host]:raise ValueError('Audited text mismatch at '+host)
     if cell.find('s:f',NS) is not None:raise ValueError('Unexpected live formula at '+host)
     selected[cell.attrib['r']]=host
     if typ=='s':target_shared_indices.add(int(value))
    elif typ=='s' and value is not None:live_indices.add(int(value))
   def replace(m):
    c=ET.fromstring(m[0]);co=c.attrib.get('r')
    if co not in selected:return m[0]
    host=selected[co]; replacement=text_replacements.get(host)
    payload=('<is><t>'+escape(replacement['after'])+'</t></is>').encode() if replacement else b''
    new,n=IS.subn(payload,m[0]);new,nv=VALUE.subn(payload,new)
    if replacement:
     new=re.sub(rb'\bt=["\'][^"\']+["\']',b't="inlineStr"',new,count=1)
    else:new=re.sub(rb'\s+t=["\'][^"\']+["\']',b'',new,count=1)
    if n+nv!=1:raise ValueError('Exactly one string payload expected')
    removed.append(selected[co]);return new
   if selected:patches[path]=CELL.sub(replace,raw)
  if set(removed)!=set(targets):raise ValueError('Incomplete target coverage')
  orphanvalues=set(orphan_sst_values)|set(targets.values())
  blank_sst={i for i,t in enumerate(shared) if i not in live_indices and (i in target_shared_indices or t in orphanvalues)}
  if blank_sst:
   counter=[-1]
   def blank(m):
    counter[0]+=1
    if counter[0] not in blank_sst:return m[0]
    # Preserve si attributes/prefix and index; remove the entire text/rich-text body.
    raw=m[0];opening=raw[:raw.index(b'>')+1];closing=re.search(rb'</[^>]*si>$',raw)
    if raw.endswith(b'/>'):return raw
    if not closing:raise ValueError('Bad shared string entry')
    return opening+closing[0]
   patches['xl/sharedStrings.xml']=SI.sub(blank,z.read('xl/sharedStrings.xml'))
   if counter[0]+1!=len(shared):raise ValueError('SST matching failed')
  destination.parent.mkdir(parents=True,exist_ok=True)
  with ZipFile(destination,'w') as out:
   out.comment=z.comment
   for info in z.infolist():out.writestr(copy.copy(info),patches.get(info.filename,z.read(info.filename)))
  with ZipFile(destination) as out:
   if z.namelist()!=out.namelist():raise AssertionError('Package topology changed')
   for name in z.namelist():
    if out.read(name)!=patches.get(name,z.read(name)):raise AssertionError('Unexpected change '+name)
   assert out.testzip() is None
   for sheet,path in paths.items():
    for c in ET.fromstring(out.read(path)).findall('.//s:sheetData/s:row/s:c',NS):
     if sheet+'!'+c.attrib['r'] in targets:
      host=sheet+'!'+c.attrib['r']
      if host in text_replacements:
       assert ''.join(c.find('s:is',NS).itertext())==text_replacements[host]['after']
      else:assert c.find('s:v',NS) is None and c.find('s:is',NS) is None
   if blank_sst:
    aftershared=[''.join(e.itertext()) for e in ET.fromstring(out.read('xl/sharedStrings.xml'))]
    assert len(aftershared)==len(shared)
    for i,t in enumerate(aftershared):assert t==('' if i in blank_sst else shared[i])
 if sha256(source)!=before:raise AssertionError('Source modified')
 return {'workbookSourcePath':str(source.resolve()),'workbookSourceSha256':before,'workbookPath':str(destination.resolve()),'workbookSha256':sha256(destination),'textReplacements':text_replacements,'maskedCells':sorted(removed),'maskedCellCount':len(removed),'blankedSharedStringIndices':sorted(blank_sst),'preservedLiveSharedStringIndices':sorted(i for i,t in enumerate(shared) if i in live_indices and t in orphanvalues),'changedEntries':sorted(patches),'validation':{'all_unrelated_entry_bytes_unchanged':True,'all_unrelated_worksheet_bytes_unchanged':True,'shared_string_indices_preserved':True,'no_live_shared_string_changed':True,'no_formulas_added':True,'all_original_target_string_payloads_removed':True,'mixed_labels_preserved':True,'frozen_source_unchanged':True}}
