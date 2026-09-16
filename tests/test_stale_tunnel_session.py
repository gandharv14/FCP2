from pathlib import Path
import pytest
from scripts.fleet import stale_tunnel_session as S

@pytest.fixture
def proc(tmp_path):
    p=tmp_path/'123';(p/'fd').mkdir(parents=True);(p/'fd/7').symlink_to('socket:[456]')
    (p/'cmdline').write_bytes(b'sshd: gmahajan_labelbox_com\0');(p/'status').write_text('Uid:\t1000 1000 1000 1000\n')
    fields=['S','10']+['0']*17+['999'];(p/'stat').write_text('123 (sshd) '+' '.join(fields))
    boot=tmp_path/'sys/kernel/random';boot.mkdir(parents=True);(boot/'boot_id').write_text('boot')
    return tmp_path

def inspect(p,listener=None):
    return S.identity(123,8765,proc=p,uid=1000,listener=listener or 'LISTEN 0 128 127.0.0.1:8765 0.0.0.0:* users:(("sshd",pid=123,fd=7))')

def test_exact_leaf_socket_and_process_bound(proc):
    r=inspect(proc);assert r['socket']=='socket:[456]'and r['start_ticks']==999 and r['children']==[]

@pytest.mark.parametrize('change',['child','privileged','daemon','foreign-pid','second-listener','foreign-socket'])
def test_rejects_sessions_that_could_own_useful_work(proc,change):
    listener=None
    if change=='child':
        p=proc/'456';p.mkdir();(p/'stat').write_text('456 (python) S 123 '+'0 '*18)
    elif change=='privileged':(proc/'123/status').write_text('Uid: 0 0 0 0\n')
    elif change=='daemon':(proc/'123/cmdline').write_bytes(b'sshd: /usr/sbin/sshd -D [listener]\0')
    elif change=='foreign-pid':listener='LISTEN 0 128 127.0.0.1:8765 x users:(("sshd",pid=124,fd=7))'
    elif change=='second-listener':listener='x\nx'
    else:(proc/'123/fd/7').unlink();(proc/'123/fd/7').symlink_to('/file')
    with pytest.raises(ValueError):inspect(proc,listener)
