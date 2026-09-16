"""Reference hyperlinks must not be confused with live external data sources."""
import zipfile
from xml.etree import ElementTree as ET
import pytest
import xl_release_publication as release

TYPE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


def stage(tmp_path, kind, target, *, external_part=False):
    root = tmp_path / 'task'
    (root / 'environment').mkdir(parents=True)
    rels = ET.Element('Relationships')
    ET.SubElement(rels, 'Relationship', Id='rId1', Type=kind,
                  Target=target, TargetMode='External')
    with zipfile.ZipFile(root / 'environment/case-inputs.xlsx', 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('xl/workbook.xml', '<workbook/>')
        z.writestr('xl/worksheets/_rels/sheet1.xml.rels', ET.tostring(rels))
        if external_part:
            z.writestr('xl/externalLinks/externalLink1.xml', '<externalLink/>')
    return root


@pytest.mark.parametrize('target', [
    'https://comptroller.texas.gov/taxes/publications/96-259.php',
    'http://example.org/reference#clause',
])
def test_passive_reference_preserved_byte_for_byte(tmp_path, target):
    root = stage(tmp_path, TYPE + 'hyperlink', target)
    path = root / 'environment/case-inputs.xlsx'
    before = path.read_bytes()
    release._reject_external_package_artifacts(root)
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind,target', [
    ('externalLink', 'https://example.org/data.xlsx'),
    ('connection', 'https://example.org/data'),
    ('oleObject', 'https://example.org/object'),
    ('hyperlink', 'file:///private/data.xlsx'),
    ('hyperlink', '../outside.xlsx'),
    ('hyperlink', 'javascript:run()'),
    ('hyperlink', 'https://user:password@example.org/data'),
    ('hyperlink', 'https://example.org/\nsecret'),
    ('hyperlink', 'https:///missing-host'),
])
def test_active_or_non_web_external_target_stays_blocked(tmp_path, kind, target):
    root = stage(tmp_path, TYPE + kind, target)
    with pytest.raises(release.ReleasePublicationError, match='relationship'):
        release._reject_external_package_artifacts(root)


def test_hyperlink_does_not_excuse_true_external_package(tmp_path):
    root = stage(tmp_path, TYPE + 'hyperlink', 'https://example.org/reference', external_part=True)
    with pytest.raises(release.ReleasePublicationError, match='package artifacts'):
        release._reject_external_package_artifacts(root)
