"""Prepare private VM authority artifacts; never stop, migrate, or activate one.

The unchanged coordinator and clients keep token-authenticated loopback HTTP.
Native SSH forwards terminate on a dedicated forwarding-only VM account. A
cutover must separately prove the old authority fenced and all ledger rows
preserved; this preparer deliberately provides no activation command.
"""
from pathlib import Path
import base64,hashlib,ipaddress,json,re,sqlite3

SCHEMA='fleet-remote-coordinator-preparation/v1'
SSH_PORT=2222
TRANSPORT_ROOT='/etc/harbor-fleet/remote-coordinator-001'
HOSTS={'hhu-server',*[f'hhu-server-{i}'for i in range(2,9)]}
FIELDS={'schema_version','authorization','original_receipt','server','hub','private_ipv4','service_user','forward_user','python','remote_root','clients','host_key','transport_keys','network_inventory'}
GATES=('atomic_renewals_complete','all_root_admissions_frozen','all_native_admissions_frozen',
       'no_active_claims_or_operations','old_process_and_listener_stopped',
       'old_database_and_legacy_starts_durably_fenced','sealed_snapshot_and_all_rows_verified',
       'remote_database_singleton_lock_verified','unchanged_tokens_privately_verified',
       'all_eight_private_forwarders_authenticated','remote_receipt_and_mutex_reader_qualified',
       'remote_admin_transaction_routes_qualified')

def require(ok,message):
    if not ok:raise ValueError(message)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical existing artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def checked(row):
    require(type(row)is dict and set(row)=={'path','sha256'}and binding(row['path'])==row,'selected artifact changed')
    return json.loads(Path(row['path']).read_bytes())

def absolute(value):
    require(type(value)is str and value.startswith('/')and not any(x in value for x in ('\n','\r','\x00','%','$','"','\\'))and '..'not in Path(value).parts,'safe absolute service path required')
    return value

def public_key(value):
    require(type(value)is str and re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}',value)is not None,'bare Ed25519 public key required')
    raw=base64.b64decode(value.split()[1],validate=True)
    require(len(raw)==51 and raw[:4]==b'\x00\x00\x00\x0b'and raw[4:15]==b'ssh-ed25519'and raw[15:19]==b'\x00\x00\x00\x20','invalid Ed25519 public key bytes')
    return value

def ledger(database):
    """Consistent read-only hash of every SQL schema entry, column and row."""
    path=Path(absolute(str(database)));binding(path)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        require(db.execute('PRAGMA quick_check').fetchone()[0]=='ok','SQLite integrity failed')
        schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall();tables={}
        def encode(value):return {'sqlite_blob_hex':value.hex()}if isinstance(value,bytes)else value
        for kind,name,_,_ in schema:
            if kind!='table':continue
            quoted='"'+name.replace('"','""')+'"';cursor=db.execute('SELECT * FROM '+quoted)
            columns=[r[0]for r in cursor.description];rows=sorted(canonical([encode(x)for x in row])for row in cursor)
            digest=hashlib.sha256()
            for row in rows:digest.update(len(row).to_bytes(8,'big'));digest.update(row)
            tables[name]=dict(columns=columns,count=len(rows),sha256=digest.hexdigest())
        active_claims=db.execute("SELECT count(*) FROM workbooks WHERE state='active'").fetchone()[0]
        active_operations=db.execute("SELECT count(*) FROM operations WHERE state='active'").fetchone()[0]
        holds=db.execute("SELECT kind,operation_id,workbook FROM operations WHERE state='hold' ORDER BY kind,operation_id").fetchall()
        return dict(schema_sha256=hashlib.sha256(canonical(schema)).hexdigest(),tables=tables,
                    active_claims=active_claims,active_operations=active_operations,held_operations=[list(x)for x in holds])

def snapshot_equal(before,after):
    require(before==after,'remote snapshot changed schema, ledger rows, claims or operations')
    return True

def quiescent(value):
    require(value['active_claims']==value['active_operations']==0,'active custody forbids authority migration')
    require(all(row[0]=='model'for row in value['held_operations']),'unresolved non-model operation forbids migration')
    return True

def validate(plan):
    require(type(plan)is dict and set(plan)==FIELDS and plan['schema_version']==SCHEMA,'closed remote authority preparation required')
    require(binding(plan['authorization']['path'])==plan['authorization'],'authorization bytes changed');old=checked(plan['original_receipt']);binding(plan['server']['path'])
    require(plan['server']==binding(old['command'][2])and old['code_sha256']==plan['server']['sha256']
            and old['url']=='http://127.0.0.1:8765'and old['command'][3]=='serve','unchanged original authority required')
    require(plan['hub']in HOSTS and set(plan['clients'])==HOSTS and len(plan['clients'])==8,'complete authorized VM inventory required')
    address=ipaddress.ip_address(plan['private_ipv4'])
    require(address.version==4 and address.is_private and not address.is_loopback and not address.is_link_local and not address.is_unspecified,'private VPC IPv4 required')
    for key in ('service_user','forward_user'):require(re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',plan[key])is not None,'closed local service user required')
    require(plan['service_user']!=plan['forward_user'],'transport account must not own coordinator credentials')
    for key in ('python','remote_root'):absolute(plan[key])
    require(Path(plan['remote_root']).is_relative_to('/home/'+plan['service_user'])or Path(plan['remote_root']).is_relative_to('/var/lib/harbor-fleet'),'dedicated remote authority directory required')
    inventory=checked(plan['network_inventory'])
    require(type(inventory)is list and len(inventory)==8 and {x.get('name')for x in inventory}==HOSTS,'complete native GCE network inventory required')
    for item in inventory:
        require(len(item.get('networkInterfaces',[]))==1,'ambiguous VM network identity')
        nic=item['networkInterfaces'][0]
        require(nic.get('network')=='https://www.googleapis.com/compute/v1/projects/saleseng/global/networks/default'
                and nic.get('subnetwork')=='https://www.googleapis.com/compute/v1/projects/saleseng/regions/us-central1/subnetworks/default',
                'foreign private VM network')
        if 'selfLink'in item:require(item['selfLink'].endswith('/projects/saleseng/zones/us-central1-a/instances/'+item['name']),'foreign VM instance identity')
        if 'zone'in item:require(item['zone'].endswith('/zones/us-central1-a'),'foreign VM zone')
    selected=next(x for x in inventory if x['name']==plan['hub'])
    require(selected['networkInterfaces'][0].get('networkIP')==plan['private_ipv4'],'private destination differs from selected VM')
    public_key(plan['host_key']);require(type(plan['transport_keys'])is dict and set(plan['transport_keys'])==HOSTS-{plan['hub']},'per-VM transport public keys required')
    keys=[public_key(plan['transport_keys'][h])for h in sorted(HOSTS-{plan['hub']})];require(len(set(keys))==7,'distinct transport keys required')
    return old

def unit(argv,*,user,description,write_path=None):
    require(all(type(x)is str and not any(c in x for c in ('\n','\r','\x00','%','$','"','\\'))for x in argv),'unsafe systemd argument')
    command=' '.join('"'+x+'"'for x in argv)
    return ('[Unit]\nDescription='+description+'\nWants=network-online.target\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nType=simple\nUser='+user+'\nUMask=0077\nNoNewPrivileges=true\nExecStart='+command+'\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=45\n\n[Install]\nWantedBy=multi-user.target\n')

def artifacts(plan):
    old=validate(plan);root=Path(plan['remote_root']);server=root/'coordinator.py'
    command=['/usr/bin/flock','--exclusive','--nonblock','--no-fork',str(root/'authority.lock'),plan['python'],'-B',str(server),'serve','--database',str(root/'fleet.sqlite3'),'--port','8765','--worker-token-file',str(root/'private/worker.token'),'--admin-token-file',str(root/'private/admin.token')]
    # The original service's local legacy locks cannot be pretended to exist on
    # another VM. Their durable local fences are a mandatory separate cutover gate.
    result={'coordinator.service':unit(command,user=plan['service_user'],description='Harbor fleet remote coordinator'),
            'sshd-forwarding.conf':('Port '+str(SSH_PORT)+'\nListenAddress '+plan['private_ipv4']+'\nHostKey /etc/ssh/ssh_host_ed25519_key\nPidFile /run/harbor-fleet-transport.pid\nAuthorizedKeysFile '+TRANSPORT_ROOT+'/authorized_keys\nAllowUsers '+plan['forward_user']+'\nUsePAM no\nStrictModes yes\nPermitRootLogin no\nAuthenticationMethods publickey\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nAllowTcpForwarding local\nAllowStreamLocalForwarding no\nPermitOpen 127.0.0.1:8765\nPermitListen none\nGatewayPorts no\nAllowAgentForwarding no\nX11Forwarding no\nPermitTTY no\nPermitUserRC no\nPermitUserEnvironment no\nMaxSessions 0\nForceCommand /usr/bin/false\n'),
            'transport.service':unit(['/usr/sbin/sshd','-D','-e','-f',TRANSPORT_ROOT+'/sshd_config'],user='root',description='Harbor fleet private forwarding service')}
    addresses={x['name']:x['networkInterfaces'][0]['networkIP']for x in checked(plan['network_inventory'])}
    result['authorized_keys']='\n'.join('from="'+addresses[h]+'",restrict,port-forwarding,permitopen="127.0.0.1:8765",command="/usr/bin/false" '+plan['transport_keys'][h]+' fleet-'+h for h in sorted(HOSTS-{plan['hub']}))+'\n'
    for host in sorted(HOSTS):
        if host==plan['hub']:continue  # It already owns the sole loopback listener.
        home=Path('/home')/plan['service_user']/'.config/harbor-fleet/remote-forward'
        argv=['/usr/bin/ssh','-F','/dev/null','-p',str(SSH_PORT),'-N','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','ExitOnForwardFailure=yes','-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null','-o','UserKnownHostsFile='+str(home/'known_hosts'),'-o','ConnectTimeout=10','-o','ConnectionAttempts=1','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-i',str(home/'id_ed25519'),'-L','127.0.0.1:8765:127.0.0.1:8765',plan['forward_user']+'@'+plan['private_ipv4']]
        result[host+'/forward.service']=unit(argv,user=plan['service_user'],description='Harbor fleet private coordinator transport')
        result[host+'/known_hosts']='['+plan['private_ipv4']+']:'+str(SSH_PORT)+' '+plan['host_key']+'\n'
    result['cutover-requirements.json']=json.dumps(dict(schema_version=SCHEMA,status='PREPARED_NOT_ACTIVATABLE',original_receipt=plan['original_receipt'],mandatory_gates=GATES,local_database=old['database'],local_legacy_locks=old['legacy_locks'],account_credentials_transferred=False,activation_performed=False),indent=2)+'\n'
    return result
