"""One admission mutex per canonical coordinator database, wherever receipts live."""
from pathlib import Path


def path(receipt):
    if not isinstance(receipt,dict)or not isinstance(receipt.get('database'),str):
        raise ValueError('coordinator database identity required')
    if 'remote_authority' in receipt:
        # A completed migration deliberately replaces the old database with a
        # durable fence. Only its authenticated successor may reuse this mutex;
        # malformed or incomplete migrations must not fall back to a local DB.
        from scripts.fleet.remote_authority_control import path as remote_path
        return remote_path(receipt)
    database=Path(receipt['database'])
    if (not database.is_absolute()or '..'in database.parts or not database.is_file()
            or any(p.is_symlink()for p in(database,*database.parents))):
        raise ValueError('canonical existing coordinator database required')
    return database.parent/'job-launch.lock'
