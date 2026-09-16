from scripts.fleet_deploy import ssh


def test_new_ssh_channel_has_bounded_keepalives_and_preserves_exact_command():
    command = "python3 -c 'print(\"a; $literal\")'"
    args = ssh('hhu-server-7', command, 'saleseng', 'us-central1-a')
    assert args == [
        'gcloud', 'compute', 'ssh', 'hhu-server-7',
        '--project=saleseng', '--zone=us-central1-a', '--tunnel-through-iap',
        '--quiet', '--ssh-flag=-oBatchMode=yes', '--ssh-flag=-oConnectTimeout=20',
        '--ssh-flag=-oServerAliveInterval=15', '--ssh-flag=-oServerAliveCountMax=3',
        '--command=' + command,
    ]
