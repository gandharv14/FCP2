from pathlib import Path
import base64,copy,json,sqlite3,subprocess
import pytest
from scripts.fleet import remote_coordinator_preparation as R


def keep(p,value):
    p.write_text(json.dumps(value));return R.binding(p)


def key(i):return 'ssh-ed25519 '+base64.b64encode(b'\0\0\0\x0bssh-ed25519\0\0\0\x20'+bytes([i])*32).decode()


def selection(tmp_path):
    server=tmp_path/'coordinator.py';server.write_text('# unchanged selected authority\n');receipt=keep(tmp_path/'receipt.json',dict(command=['/python','-B',str(server),'serve'],code_sha256=R.binding(server)['sha256'],url='http://127.0.0.1:8765',database=str(tmp_path/'fleet.sqlite3'),legacy_locks=[str(tmp_path/'legacy.lock')]))
    auth=tmp_path/'authorization.md';auth.write_text('Authorized private VM preparation; no activation.\n')
    network=[dict(name=h,selfLink='https://compute.googleapis.com/compute/v1/projects/saleseng/zones/us-central1-a/instances/'+h,zone='https://compute.googleapis.com/compute/v1/projects/saleseng/zones/us-central1-a',networkInterfaces=[dict(networkIP='10.128.0.'+str(i),network='https://www.googleapis.com/compute/v1/projects/saleseng/global/networks/default',subnetwork='https://www.googleapis.com/compute/v1/projects/saleseng/regions/us-central1/subnetworks/default')])for i,h in enumerate(sorted(R.HOSTS),2)]
    return dict(schema_version=R.SCHEMA,authorization=R.binding(auth),original_receipt=receipt,server=R.binding(server),hub='hhu-server',private_ipv4='10.128.0.2',service_user='fleet_owner',forward_user='fleet_forward',python='/usr/bin/python3',remote_root='/home/fleet_owner/remote-authority',clients=sorted(R.HOSTS),host_key=key(100),transport_keys={h:key(i)for i,h in enumerate(sorted(R.HOSTS-{'hhu-server'}),1)},network_inventory=keep(tmp_path/'network.json',network))


def test_loopback_service_and_private_native_forwarders_keep_client_contract(tmp_path):
    p=selection(tmp_path);a=R.artifacts(p)
    assert len([x for x in a if x.endswith('/forward.service')])==7
    assert 'hhu-server/forward.service'not in a
    assert '--no-fork'in a['coordinator.service']and '"8765"'in a['coordinator.service']
    for name,value in a.items():
        if name.endswith('/forward.service'):
            assert '127.0.0.1:8765:127.0.0.1:8765'in value
            assert 'fleet_forward@10.128.0.2'in value
            assert 'StrictHostKeyChecking=yes'in value and 'IdentityAgent=none'in value
            assert 'ProxyCommand'not in value and 'gcloud'not in value
    sshd=a['sshd-forwarding.conf'];assert 'AllowTcpForwarding local'in sshd and 'PermitListen none'in sshd and 'ForceCommand /usr/bin/false'in sshd
    assert len(a['authorized_keys'].splitlines())==7
    gates=json.loads(a['cutover-requirements.json']);assert gates['status']=='PREPARED_NOT_ACTIVATABLE'and not gates['activation_performed']
    assert 'remote_admin_transaction_routes_qualified'in gates['mandatory_gates']


@pytest.mark.parametrize('change',[lambda p:p.update(private_ipv4='8.8.8.8'),lambda p:p.update(private_ipv4='127.0.0.1'),lambda p:p.update(private_ipv4='10.128.0.3'),lambda p:p.update(forward_user=p['service_user']),lambda p:p.update(clients=p['clients'][:-1]),lambda p:p.update(host_key='ssh-ed25519 AAAA\ncommand=unsafe'),lambda p:p.update(remote_root='/home/fleet_owner/../elsewhere'),lambda p:p.update(python='/usr/bin/python3\nExecStart=/unsafe'),lambda p:p.update(transport_keys={h:key(1)for h in R.HOSTS})])
def test_foreign_public_ambiguous_or_injected_transport_rejected(tmp_path,change):
    p=selection(tmp_path);change(p)
    with pytest.raises(ValueError):R.artifacts(p)


def database(path):
    with sqlite3.connect(path)as db:
        db.executescript("CREATE TABLE workbooks(workbook TEXT PRIMARY KEY,state TEXT);CREATE TABLE operations(kind TEXT,operation_id TEXT,workbook TEXT,state TEXT,identity BLOB);CREATE TABLE quarantine_renewals(id INTEGER,record TEXT);")
        db.execute("INSERT INTO workbooks VALUES('0001','hold')")
        for i in range(5):db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',('model',str(i),'0001','hold',b'opaque\x00token'))
        db.execute("INSERT INTO quarantine_renewals VALUES(1,'original opaque evidence')")


def test_complete_sqlite_copy_preserves_all_unknowns_and_append_ledgers(tmp_path):
    a=tmp_path/'source.sqlite3';b=tmp_path/'copy.sqlite3';database(a);before=R.ledger(a);original=a.read_bytes()
    with sqlite3.connect(a.as_uri()+'?mode=ro',uri=True)as src,sqlite3.connect(b)as dst:src.backup(dst)
    assert R.snapshot_equal(before,R.ledger(b))and R.quiescent(before)
    assert a.read_bytes()==original and len(before['held_operations'])==5
    assert 'opaque'not in json.dumps(before)
    with sqlite3.connect(b)as db:db.execute("UPDATE quarantine_renewals SET record='changed'")
    with pytest.raises(ValueError):R.snapshot_equal(before,R.ledger(b))


@pytest.mark.parametrize('change',["UPDATE operations SET state='active' WHERE operation_id='0'","UPDATE workbooks SET state='active'","UPDATE operations SET kind='publication' WHERE operation_id='0'"])
def test_live_or_unresolved_publication_custody_blocks_migration(tmp_path,change):
    p=tmp_path/'source.sqlite3';database(p)
    with sqlite3.connect(p)as db:db.execute(change)
    with pytest.raises(ValueError):R.quiescent(R.ledger(p))
