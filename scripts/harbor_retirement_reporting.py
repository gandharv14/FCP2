"""Explicit root-selected historical terminal observations for read-only reports.

Never scans for retirement seals. An absent selection retains ordinary strict
current-evidence behavior; an invalid selected observation fails without fallback.
This adapter grants no current qualification, promotion, admission or source verdict.
"""
from dataclasses import dataclass
from pathlib import Path
from scripts import harbor_retirement_history as history
from scripts import harbor_parallel_policy as policy
from scripts.batch import harbor_tracker as schema

SCHEMA = 'harbor-report-historical-retirements/v1'
FIELDS = {'workbook_id','harbor_root','original','tracker','seal','contract','activation'}
require = policy.require


@dataclass(frozen=True)
class SelectedRetirement:
    manifest: dict
    inventory_sha256: str
    entry: dict


def _document(binding):
    value = policy.read(binding)
    require(type(value) is dict and set(value) == {'schema_version','inventory_sha256','entries'}
            and value['schema_version'] == SCHEMA and type(value['entries']) is list
            and bool(value['entries']), 'closed nonempty historical retirement selection required')
    require(type(value['inventory_sha256']) is str and schema.HEX.fullmatch(value['inventory_sha256']),
            'original inventory hash required')
    ids = []
    for entry in value['entries']:
        require(type(entry) is dict and set(entry) == FIELDS, 'closed historical terminal entry required')
        wb = entry['workbook_id']
        require(type(wb) is str and wb not in ('','.','..') and Path(wb).name == wb,
                'invalid selected original identity')
        policy.safe(entry['harbor_root'])
        for name in ('original','tracker','seal','contract','activation'):
            record = entry[name]
            require(type(record) is dict and set(record) == {'path','sha256'} and
                    type(record['sha256']) is str and schema.HEX.fullmatch(record['sha256']),
                    'exact selected evidence record required')
            policy.safe(record['path'])
        ids.append(wb)
    require(len(ids) == len(set(ids)), 'duplicate historical terminal selections')
    return value


def load(binding, inventory, roots):
    """Validate explicit report configuration; proof validation remains per row."""
    document = _document(binding)
    require(document['inventory_sha256'] == inventory['inventory_sha256'],
            'historical selection belongs to another original inventory')
    originals = {r['workbook_id']:r for r in inventory['sources']}
    result = {}
    for entry in document['entries']:
        wb = entry['workbook_id']
        require(wb in originals, 'historical selection outside original denominator')
        source = originals[wb]
        require(entry['original'] == {k:source[k] for k in ('path','sha256')},
                'historical selection original differs')
        require(Path(entry['harbor_root']) in roots, 'historical namespace was not explicitly selected')
        require(Path(entry['tracker']['path']) == Path(entry['harbor_root'])/'workbooks'/(wb+'.json'),
                'historical tracker namespace differs')
        result[wb] = SelectedRetirement(binding, document['inventory_sha256'], entry)
    return result


def validate(source, tracker_path, harbor_root, selected):
    require(type(selected) is SelectedRetirement, 'typed root-selected historical observation required')
    document = _document(selected.manifest)
    require(document['inventory_sha256'] == selected.inventory_sha256 and
            selected.entry in document['entries'], 'selected historical entry detached')
    entry = selected.entry
    require(entry['workbook_id'] == source['workbook_id'] and
            entry['original'] == {k:source[k] for k in ('path','sha256')} and
            Path(entry['harbor_root']) == Path(harbor_root) and
            Path(entry['tracker']['path']) == Path(tracker_path), 'historical row/namespace mismatch')
    require(history.record(tracker_path) == entry['tracker'], 'historical tracker selection is stale')
    result = history.validate(entry['seal'], contract=entry['contract'], activation=entry['activation'],
                              workbook=entry['workbook_id'],original=entry['original'],tracker=entry['tracker'])
    tracker = policy.read(entry['tracker'])
    require(result['terminal'] == tracker['execution_state'] == 'STOPPED' and
            result['historical_observation'] is True and result['current_qualification'] is False,
            'historical retirement cannot grant current qualification')
    return dict(state='STOPPED',tracker=str(tracker_path),tracker_sha256=entry['tracker']['sha256'],
                failure=tracker['promotion_failure'],generation_ids=tracker['generation_ids'],
                terminal_evidence='root_selected_historical_retirement',historical_terminal=True,
                current_qualification=False,verified_promoted=False,source_invalidity=False,
                historical_retirement=dict(selection=selected.manifest,seal=entry['seal'],
                                           code_changes=result['code_changes']))
