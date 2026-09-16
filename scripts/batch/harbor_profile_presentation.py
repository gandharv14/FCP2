"""Lossless word wrapping of profile list items; no acceptance or model effects."""
import copy


def split_conventions(fragment):
    """Keep each original string recoverable by joining its ordered pieces."""
    value=copy.deepcopy(fragment);items=value['field_conventions'];output=[];mapping=[]
    if not isinstance(items,list)or not all(isinstance(x,str)for x in items):
        raise ValueError('original field convention list required')
    for index,text in enumerate(items):
        if len(text)<=160:output.append(text);continue
        words=text.split(' ')
        if any(not x or len(x)>160 or any(c.isspace()for c in x)for x in words):
            raise ValueError('lossless unambiguous word boundaries required')
        pieces=[];current=''
        for word in words:
            candidate=current+' '+word if current else word
            if len(candidate)>160:pieces.append(current);current=word
            else:current=candidate
        pieces.append(current)
        if ' '.join(pieces)!=text or not all(1<=len(x)<=160 for x in pieces):
            raise ValueError('field convention projection is not lossless')
        mapping.append(dict(original_index=index,output_start=len(output),pieces=len(pieces),original=text))
        output.extend(pieces)
    if not mapping:raise ValueError('no overlong field convention to project')
    value['field_conventions']=output
    return value,mapping
