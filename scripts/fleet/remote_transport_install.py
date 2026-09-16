"""Install the dedicated private forwarding service without moving authority.

The ordinary SSH daemon, production client forwards, database and tokens are
untouched. This installer is once-only; partial outcomes require inspection.
"""
from pathlib import Path
import hashlib,json,os,pwd,socket,subprocess


def run(plan,preparation,artifacts,journal):
    P=preparation;P.validate(plan);expected=P.artifacts(plan)
    assert socket.gethostname().split('.')[0]==plan['hub']
    home=Path(journal);assert home.is_absolute()and not home.exists();home.mkdir(mode=0o700)
    def keep(name,value):
        p=home/name
        with p.open('x')as f:json.dump(value,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        p.chmod(0o600)
    def command(argv):
        result=subprocess.run(argv,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,dict(command=argv,rc=result.returncode,stderr=result.stderr[-2000:])
        return result.stdout
    selected={}
    for name in ('sshd-forwarding.conf','authorized_keys','transport.service'):
        path=Path(artifacts)/name;assert path.read_text()==expected[name];selected[name]=P.binding(path)
    actual=' '.join(Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()[:2]);assert actual==plan['host_key']
    with socket.socket()as s:assert s.connect_ex((plan['private_ipv4'],P.SSH_PORT))!=0,'dedicated private port occupied'
    root=Path(P.TRANSPORT_ROOT);unit=Path('/etc/systemd/system/harbor-fleet-transport.service')
    assert not root.exists()and not unit.exists(),'transport install namespace already used'
    try:pwd.getpwnam(plan['forward_user'])
    except KeyError:pass
    else:raise ValueError('dedicated forwarding account already exists; inspect custody')
    # Validate syntax before creating the account or installing system files.
    command(['sudo','-n','/usr/sbin/sshd','-t','-f',str(Path(artifacts)/'sshd-forwarding.conf')])
    keep('intent.json',dict(plan=plan,selected=selected,automatic_retry=False,authority_migration=False))
    command(['sudo','-n','/usr/sbin/useradd','--system','--no-create-home','--shell','/usr/sbin/nologin','--password','*',plan['forward_user']])
    account=pwd.getpwnam(plan['forward_user']);assert account.pw_uid!=0 and account.pw_shell=='/usr/sbin/nologin'
    command(['sudo','-n','/usr/bin/install','-d','-o','root','-g','root','-m','0755',str(root)])
    for source,target in [('sshd-forwarding.conf',root/'sshd_config'),('authorized_keys',root/'authorized_keys'),('transport.service',unit)]:
        command(['sudo','-n','/usr/bin/install','-o','root','-g','root','-m','0644',str(Path(artifacts)/source),str(target)])
    command(['sudo','-n','/usr/sbin/sshd','-t','-f',str(root/'sshd_config')])
    effective=command(['sudo','-n','/usr/sbin/sshd','-T','-f',str(root/'sshd_config')])
    for line in ('port 2222','allowtcpforwarding local','allowstreamlocalforwarding no','permitopen 127.0.0.1:8765','permitlisten none','maxsessions 0','passwordauthentication no','permittty no'):assert line in effective.splitlines(),line
    keep('validated.json',dict(status='PRIVATE_FORWARDING_CONFIGURATION_VALIDATED',selected=selected,account=plan['forward_user'],uid=account.pw_uid,effective=effective,ordinary_sshd_unchanged=True))
    command(['sudo','-n','/usr/bin/systemctl','daemon-reload'])
    command(['sudo','-n','/usr/bin/systemctl','enable','--now','harbor-fleet-transport.service'])
    state=command(['sudo','-n','/usr/bin/systemctl','show','harbor-fleet-transport.service','--property=ActiveState,SubState,MainPID'])
    assert 'ActiveState=active'in state and 'SubState=running'in state
    result=dict(status='PRIVATE_TRANSPORT_SERVICE_RUNNING',state=state,private_ip=plan['private_ipv4'],port=P.SSH_PORT,authority_migrated=False,production_client_forwarders_changed=False,credentials_transferred=False)
    keep('complete.json',result);return result
