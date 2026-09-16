"""Durable fail-closed retirement of one stopped local SQLite authority.

No process is signalled here. The owning caller must hold both admission locks,
prove native admission freeze and stop the exact old listener before sealing.
Atomic file/directory exchange leaves the original database path unusable by
all historical SQLite clients even if this process stops at any later step.
"""
from pathlib import Path
import ctypes,errno,hashlib,json,os,sqlite3,sys
from scripts.fleet.remote_coordinator_preparation import binding,ledger,quiescent,require,snapshot_equal

SCHEMA='fleet-retired-coordinator-database/v1'

def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def retain(path,value):
    path=Path(path)
    with path.open('xb')as f:
        f.write((json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode());f.flush();os.fsync(f.fileno())
    path.chmod(0o600);fsync_dir(path.parent);return binding(path)

def exchange(first,second):
    """Atomic exchange on the Mac owner or a Linux fixture; never rename+mkdir."""
    first,second=Path(first),Path(second)
    require(first.parent==second.parent,'exchange must remain on the same directory/filesystem')
    libc=ctypes.CDLL(None,use_errno=True)
    if sys.platform=='darwin':
        function=libc.renamex_np;function.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint];args=[os.fsencode(first),os.fsencode(second),2]
    elif sys.platform=='linux':
        function=libc.renameat2;function.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];args=[-100,os.fsencode(first),-100,os.fsencode(second),2]
    else:raise ValueError('atomic exchange unavailable; no retirement performed')
    function.restype=ctypes.c_int
    if function(*args):raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
    fsync_dir(first.parent)

def seal_database(database,expected_ledger,authorization,stopped_evidence,*,suffix):
    """Owning primitive: reject live rows, preserve bytes, install a directory fence.

    All external checks are retained bindings. The production controller performs
    process/listener/lock/native-freeze checks immediately before this primitive.
    An existing destination or intent is terminal until explicitly reconciled.
    """
    database=Path(database);original=binding(database)
    for row in (authorization,stopped_evidence):require(binding(row['path'])==row,'retirement evidence changed')
    require(type(suffix)is str and suffix and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-'for c in suffix),'closed retirement identity required')
    sealed=database.with_name(database.name+'.retired-'+suffix)
    require(not sealed.exists()and not sealed.is_symlink(),'retirement namespace consumed')
    # A stopped SQLite authority must have no uncheckpointed sidecar state.
    for tail in ('-wal','-journal'):
        p=Path(str(database)+tail);require(not p.exists()or p.stat().st_size==0,'checkpoint the stopped authority before retirement')
    actual=ledger(database);quiescent(actual);snapshot_equal(expected_ledger,actual)
    sealed.mkdir(mode=0o700)
    intent=dict(schema_version=SCHEMA,database=str(database),sealed_database=str(sealed),original=original,ledger=actual,authorization=authorization,stopped_evidence=stopped_evidence,automatic_retry=False)
    retain(sealed/'retirement.json',intent)
    require(binding(database)==original,'database changed before atomic fence')
    exchange(database,sealed)
    # At every point from exchange onward, old SQLite constructors fail closed.
    require(database.is_dir()and binding(sealed)['sha256']==original['sha256'],'retirement exchange did not preserve bytes')
    snapshot_equal(actual,ledger(sealed))
    return retain(database/'complete.json',dict(status='LOCAL_AUTHORITY_DURABLY_FENCED',retirement=binding(database/'retirement.json'),sealed_database=binding(sealed),ledger=actual,automatic_restart=False))

def validate_fence(database):
    database=Path(database);require(database.is_dir()and not any(p.is_symlink()for p in(database,*database.parents)),'retired authority directory required')
    complete=json.loads((database/'complete.json').read_bytes());r=complete['retirement'];require(binding(r['path'])==r,'retirement intent changed');intent=json.loads(Path(r['path']).read_bytes())
    require(set(complete)=={'status','retirement','sealed_database','ledger','automatic_restart'} and complete['status']=='LOCAL_AUTHORITY_DURABLY_FENCED' and complete['automatic_restart'] is False,'closed completed retirement required')
    require(intent['schema_version']==SCHEMA and intent['database']==str(database)and complete['sealed_database']['path']==intent['sealed_database'] and intent['automatic_retry'] is False,'foreign retirement fence')
    require(binding(intent['sealed_database'])==complete['sealed_database']and complete['sealed_database']['sha256']==intent['original']['sha256'],'sealed local authority changed')
    for row in (intent['authorization'],intent['stopped_evidence']):require(binding(row['path'])==row,'retirement authority evidence changed')
    snapshot_equal(intent['ledger'],complete['ledger']);snapshot_equal(intent['ledger'],ledger(intent['sealed_database']));quiescent(complete['ledger'])
    return complete
