"""Compare complete code inventories independently of JSON row ordering."""
from pathlib import PurePosixPath
import re

def inventory(value):
    if not isinstance(value,dict) or set(value)!={'root','files'} or not isinstance(value['root'],str) or not PurePosixPath(value['root']).is_absolute() or not isinstance(value['files'],list):
        raise ValueError('closed absolute code inventory required')
    result={}
    for row in value['files']:
        if not isinstance(row,dict) or set(row)!={'relative','sha256'} or not isinstance(row['relative'],str) or not isinstance(row['sha256'],str):raise ValueError('closed code file binding required')
        name=row['relative'];p=PurePosixPath(name)
        if not name or p.is_absolute() or '..' in p.parts or str(p)!=name or name in result or not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise ValueError('duplicate or malformed code file binding')
        result[name]=row['sha256']
    if not result:raise ValueError('empty code inventory')
    return value['root'],result

def equivalent(actual,selected):
    return inventory(actual)==inventory(selected)

def ordered_reader(original,selected):
    """Declared adapter for an immutable caller's list-equality boundary."""
    inventory(selected)
    def read(root):
        actual=original(root)
        if str(root)==selected['root'] and equivalent(actual,selected):return selected
        return actual
    return read
