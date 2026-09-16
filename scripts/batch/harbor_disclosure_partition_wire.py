"""Lossless wire representation of BOTH complete selected workbook shards.

No cells, metadata, dependency edges, blanks or unknown-scope declarations are
removed. This only shares identical rows and groups consecutive edge targets.
"""
import copy,json,re
SCHEMA='lossless-workbook-shard-pair/v1'
COLUMNAR_SCHEMA='lossless-workbook-shard-pair/v2'
RUN_SCHEMA='lossless-workbook-shard-pair/v3'
RUN_SEMANTICS='Expand shared_cell_columns.address_runs [first_A1,column_step,row_step,count] in order; singleton runs preserve their exact address string. Each sheet cell_row_id_runs [first_index,step,count] reconstructs cell_row_ids in order. For each workbook, reference_sheet_names indexes the first entry of every selected_refs and blank_refs run; replacing that index with the exact sheet string restores its original reference runs. No values, formulas, metadata or dependency edges are changed.'
COLUMNAR_SEMANTICS='The shared_cell_rows table is losslessly columnar. Row i has addresses[i] and values[i] as its exact first two entries. Each attribute_runs entry [count,number_format_index,data_type,has_cached_value] applies to the next count rows. If has_cached_value is true, consume the next cached_values entry; otherwise the fifth entry is absent. Null remains distinct from absent. All cell_row_ids index these exact reconstructed shared rows.'
SEMANTICS=('The golden and delivered workbooks below remain distinct. Each sheet cell_row_ids list indexes shared_cell_rows, in original row order. Each shared row is [address,value,shared_number_format_index,data_type] or has a fifth cached_value entry; absent and null remain different. A row format indexes shared_number_formats. Each dependency_edge_runs row is [source_index,start_target,count,...]; expand each (start,count) inclusive run to original [source_index,target_index] edges, in order. Edge indexes refer to lexically sorted expanded selected_refs within that workbook, exactly as its own semantics declares. All source/sheet metadata, selected_refs, blank_refs and unknown-elsewhere meaning remain unchanged. The retained per-workbook number_formats reproduce its original row format indexes. Sharing a row never implies equality of other cells or source versions.')

def require(ok,message):
    if not ok:raise ValueError(message)
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def edge_runs(edges):
    require(type(edges)is list and all(type(e)is list and len(e)==2 and all(type(i)is int and i>=0 for i in e)for e in edges),'invalid dependency edges')
    require(edges==sorted(edges) and len({tuple(e)for e in edges})==len(edges),'edge order or duplicates')
    out=[]
    for source,target in edges:
        if not out or out[-1][0]!=source:out.append([source,target,1])
        elif out[-1][-2]+out[-1][-1]==target:out[-1][-1]+=1
        else:out[-1].extend([target,1])
    return out

def expand_edges(runs):
    require(type(runs)is list,'edge runs missing');edges=[]
    for row in runs:
        require(type(row)is list and len(row)>=3 and len(row)%2==1 and all(type(i)is int and i>=0 for i in row),'invalid edge run')
        for start,count in zip(row[1::2],row[2::2]):
            require(count>0 and len(edges)+count<=2000000,'edge expansion bound exceeded')
            edges.extend([row[0],target]for target in range(start,start+count))
    require(edge_runs(edges)==runs,'noncanonical edge runs');return edges

def _encode_rows(pair):
    require(type(pair)is dict and set(pair)=={'golden','delivered'},'exact workbook pair required')
    result=copy.deepcopy(pair);rows=[];formats=[];row_index={};format_index={}
    for name in ('golden','delivered'):
        book=result[name];original_formats=book['number_formats']
        for sheet in book['sheets']:
            ids=[]
            for row in sheet.pop('present_rows'):
                require(type(row)is list and len(row)in(4,5) and type(row[2])is int and 0<=row[2]<len(original_formats),'invalid source cell row')
                fmt=original_formats[row[2]]
                require(type(fmt)is str,'format text required')
                if fmt not in format_index:format_index[fmt]=len(formats);formats.append(fmt)
                shared=[*row];shared[2]=format_index[fmt];key=canonical(shared)
                if key not in row_index:row_index[key]=len(rows);rows.append(shared)
                ids.append(row_index[key])
            sheet['cell_row_ids']=ids
        book['dependency_edge_runs']=edge_runs(book.pop('dependency_edges'))
    encoded=dict(schema_version=SCHEMA,semantics=SEMANTICS,shared_cell_rows=rows,shared_number_formats=formats,workbooks=result)
    require(canonical(decode(encoded))==canonical(pair),'wire encoding changed complete shard evidence')
    return encoded

def _decode_rows(value):
    require(type(value)is dict and set(value)=={'schema_version','semantics','shared_cell_rows','shared_number_formats','workbooks'} and value['schema_version']==SCHEMA and value['semantics']==SEMANTICS,'unknown workbook wire schema')
    rows=value['shared_cell_rows'];formats=value['shared_number_formats']
    require(type(rows)is list and len(rows)<=2000000 and type(formats)is list and all(type(f)is str for f in formats) and len(set(formats))==len(formats),'invalid shared dictionaries')
    require(len({canonical(r)for r in rows})==len(rows),'duplicate shared cell row')
    pair=copy.deepcopy(value['workbooks']);require(type(pair)is dict and set(pair)=={'golden','delivered'},'distinct workbook pair missing')
    used=set();used_formats=set()
    for book in pair.values():
        original_formats=book['number_formats'];require(len(set(original_formats))==len(original_formats),'duplicate workbook formats')
        for sheet in book['sheets']:
            require('present_rows'not in sheet and type(sheet.get('cell_row_ids'))is list,'invalid row dictionary reference')
            restored=[]
            for index in sheet.pop('cell_row_ids'):
                require(type(index)is int and 0<=index<len(rows),'shared row index out of bounds');used.add(index);row=copy.deepcopy(rows[index])
                require(type(row)is list and len(row)in(4,5) and type(row[2])is int and 0<=row[2]<len(formats),'invalid shared row')
                fmt=formats[row[2]];used_formats.add(row[2]);require(fmt in original_formats,'shared row format absent from selected workbook');row[2]=original_formats.index(fmt);restored.append(row)
            sheet['present_rows']=restored
        require('dependency_edges'not in book,'mixed edge representations');book['dependency_edges']=expand_edges(book.pop('dependency_edge_runs'))
    require(used==set(range(len(rows))) and used_formats==set(range(len(formats))),'unused dictionary evidence')
    return pair


def _columns(rows):
    addresses=[];values=[];attributes=[];cached=[]
    for row in rows:
        require(type(row)is list and len(row)in(4,5),'invalid shared cell row')
        addresses.append(copy.deepcopy(row[0]));values.append(copy.deepcopy(row[1]))
        attr=[row[2],row[3],len(row)==5]
        if attributes and attributes[-1][1:]==attr:attributes[-1][0]+=1
        else:attributes.append([1,*attr])
        if len(row)==5:cached.append(copy.deepcopy(row[4]))
    return dict(addresses=addresses,values=values,attribute_runs=attributes,cached_values=cached)


def _expand_columns(columns):
    require(type(columns)is dict and set(columns)=={'addresses','values','attribute_runs','cached_values'},'unknown shared column fields')
    addresses=columns['addresses'];values=columns['values'];runs=columns['attribute_runs'];cached=columns['cached_values']
    require(all(type(x)is list for x in (addresses,values,runs,cached)) and len(addresses)==len(values)<=2000000
            and len(cached)<=len(addresses) and len(runs)<=len(addresses),'shared column bounds differ')
    rows=[];cache_index=0
    for run in runs:
        require(type(run)is list and len(run)==4,'invalid shared attribute run')
        count,fmt,kind,present=run
        require(type(count)is int and count>0 and len(rows)+count<=len(addresses)
                and type(fmt)is int and fmt>=0 and type(kind)is str and type(present)is bool,'invalid shared attribute bounds')
        for _ in range(count):
            i=len(rows);row=[copy.deepcopy(addresses[i]),copy.deepcopy(values[i]),fmt,kind]
            if present:
                require(cache_index<len(cached),'missing cached column value')
                row.append(copy.deepcopy(cached[cache_index]));cache_index+=1
            rows.append(row)
    require(len(rows)==len(addresses) and cache_index==len(cached),'incomplete shared columns')
    require(canonical(_columns(rows))==canonical(columns),'noncanonical shared attribute runs')
    return rows


def encode(pair,*,encoding=SCHEMA):
    require(encoding in (SCHEMA,COLUMNAR_SCHEMA,RUN_SCHEMA),'unknown selected lossless encoding')
    value=_encode_rows(pair)
    if encoding==SCHEMA:return value
    value['schema_version']=COLUMNAR_SCHEMA
    value['column_semantics']=COLUMNAR_SEMANTICS
    value['shared_cell_columns']=_columns(value.pop('shared_cell_rows'))
    if encoding==RUN_SCHEMA:value=_run_encode(value)
    require(canonical(decode(value))==canonical(pair),'columnar encoding changed complete shard evidence')
    return value


def decode(value):
    if type(value)is dict and value.get('schema_version')==RUN_SCHEMA:value=_run_decode(value)
    if type(value)is dict and value.get('schema_version')==COLUMNAR_SCHEMA:
        require(set(value)=={'schema_version','semantics','column_semantics','shared_cell_columns','shared_number_formats','workbooks'}
                and value['column_semantics']==COLUMNAR_SEMANTICS,'unknown columnar workbook wire schema')
        value=copy.deepcopy(value);value['schema_version']=SCHEMA
        value.pop('column_semantics');value['shared_cell_rows']=_expand_columns(value.pop('shared_cell_columns'))
    return _decode_rows(value)


def _coord(address):
    if type(address)is not str:return None
    m=re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})',address)
    if not m:return None
    col=0
    for c in m[1]:col=col*26+ord(c)-64
    row=int(m[2])
    return (col,row)if col<=16384 and row<=1048576 else None


def _address(col,row):
    require(1<=col<=16384 and 1<=row<=1048576,'address run exceeds Excel bounds')
    letters=''
    while col:col,r=divmod(col-1,26);letters=chr(65+r)+letters
    return letters+str(row)


def _address_runs(values):
    require(type(values)is list and all(type(x)is str for x in values),'exact string addresses required')
    out=[];i=0
    while i<len(values):
        start=values[i];first=_coord(start);step=(0,0);count=1
        if first is not None and i+1<len(values)and (second:=_coord(values[i+1]))is not None:
            step=(second[0]-first[0],second[1]-first[1]);count=2
            while i+count<len(values)and _coord(values[i+count])==(first[0]+step[0]*count,first[1]+step[1]*count):count+=1
        out.append([start,*step,count]);i+=count
    return out


def _expand_addresses(runs):
    require(type(runs)is list,'address runs required');out=[]
    for run in runs:
        require(type(run)is list and len(run)==4 and type(run[0])is str and all(type(x)is int for x in run[1:]),'invalid address run')
        first,dc,dr,count=run;require(0<count<=2000000-len(out),'address run expansion exceeds bound')
        if count==1:require(dc==dr==0,'singleton address step differs');out.append(first)
        else:
            coord=_coord(first);require(coord is not None,'noncanonical multi-address run')
            out.extend(_address(coord[0]+i*dc,coord[1]+i*dr)for i in range(count))
    require(_address_runs(out)==runs,'noncanonical address runs');return out


def _index_runs(values):
    require(type(values)is list and all(type(x)is int and x>=0 for x in values),'exact nonnegative row indexes required')
    out=[];i=0
    while i<len(values):
        step=values[i+1]-values[i]if i+1<len(values)else 0;count=2 if i+1<len(values)else 1
        while i+count<len(values)and values[i+count]==values[i]+step*count:count+=1
        out.append([values[i],step,count]);i+=count
    return out


def _expand_indexes(runs):
    require(type(runs)is list,'index runs required');out=[]
    for run in runs:
        require(type(run)is list and len(run)==3 and all(type(x)is int for x in run),'invalid index run')
        first,step,count=run;require(0<count<=2000000-len(out)and first>=0 and first+step*(count-1)>=0,'row index expansion exceeds bounds')
        out.extend(first+step*i for i in range(count))
    require(_index_runs(out)==runs,'noncanonical index runs');return out


def _run_encode(value):
    value=copy.deepcopy(value);value['schema_version']=RUN_SCHEMA;value['run_semantics']=RUN_SEMANTICS
    columns=value['shared_cell_columns'];columns['address_runs']=_address_runs(columns.pop('addresses'))
    for book in value['workbooks'].values():
        names=[]
        for key in ('selected_refs','blank_refs'):
            require(type(book[key])is dict and set(book[key])=={'schema_version','runs'}and book[key]['schema_version']=='cell-reference-runs/v1'and type(book[key]['runs'])is list,'original reference encoding differs')
            for run in book[key]['runs']:
                require(type(run)is list and len(run)==4 and type(run[0])is str,'original reference run differs')
                if run[0]not in names:names.append(run[0])
                run[0]=names.index(run[0])
        book['reference_sheet_names']=names
        for sheet in book['sheets']:sheet['cell_row_id_runs']=_index_runs(sheet.pop('cell_row_ids'))
    return value


def _run_decode(value):
    require(set(value)=={'schema_version','semantics','column_semantics','run_semantics','shared_cell_columns','shared_number_formats','workbooks'}and value['run_semantics']==RUN_SEMANTICS,'unknown run workbook schema')
    value=copy.deepcopy(value);value['schema_version']=COLUMNAR_SCHEMA;value.pop('run_semantics')
    columns=value['shared_cell_columns'];require(set(columns)=={'address_runs','values','attribute_runs','cached_values'},'mixed address representations')
    columns['addresses']=_expand_addresses(columns.pop('address_runs'))
    require(type(value['workbooks'])is dict and set(value['workbooks'])=={'golden','delivered'},'distinct workbooks required')
    for book in value['workbooks'].values():
        names=book.pop('reference_sheet_names');require(type(names)is list and all(type(x)is str for x in names)and len(set(names))==len(names),'exact unique reference sheet names required')
        used=[]
        for key in ('selected_refs','blank_refs'):
            require(type(book[key])is dict and set(book[key])=={'schema_version','runs'}and book[key]['schema_version']=='cell-reference-runs/v1'and type(book[key]['runs'])is list,'reference runs required')
            for run in book[key]['runs']:
                require(type(run)is list and len(run)==4 and type(run[0])is int and 0<=run[0]<len(names),'reference sheet dictionary index out of bounds')
                if run[0]not in used:used.append(run[0])
                run[0]=names[run[0]]
        require(used==list(range(len(names))),'unused or noncanonical reference sheet dictionary')
        for sheet in book['sheets']:
            require('cell_row_ids'not in sheet,'mixed row-index representation')
            sheet['cell_row_ids']=_expand_indexes(sheet.pop('cell_row_id_runs'))
    return value
