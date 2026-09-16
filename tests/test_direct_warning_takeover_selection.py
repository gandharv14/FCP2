from dataclasses import replace
from pathlib import Path
from scripts.batch import harbor_lease_takeover as T


def test_real_original_takeover_omits_null_control_migration():
 s=T.Selection(workbook='0055',campaign_root=Path('/original/run'),tracker={},worker_kind='instruction',guard={},checkpoint={},owner='same')
 serialized=T._selection_value(s)
 assert 'controls_migration'not in serialized
 assert T._selection(serialized)==T._selection({**serialized,'controls_migration':None})
 assert T._selection(serialized)!=T._selection({**serialized,'controls_migration':{'path':'different','sha256':'a'*64}})
 assert T._selection(serialized)!=replace(s,owner='different')
