"""Preserve the exact verifier implementation of a completed source generation.

The segmentation evidence fingerprints ordered verifier files, including unused
package modules and xl_segment.py. A package overlay/union is not equivalent.
This builder utility keeps task evidence immutable and selects old verifier
bytes explicitly inside a new, independently identified authoring snapshot.
"""
from pathlib import PurePosixPath

ROOT_FILES=('xl_ast_graph.py','xl_graph_store.py','xl_segment.py')

def require(value,message):
    if not value:raise ValueError(message)

def verifier_names(files):
    names=set(files)
    require(all(name in names for name in ROOT_FILES),'complete root verifier files required')
    package=sorted(name for name in names if PurePosixPath(name).parent==PurePosixPath('xl_seg') and name.endswith('.py'))
    require('xl_seg/publication.py' in package and 'xl_seg/diagnostics.py' in package,'complete segmentation verifier package required')
    return (*ROOT_FILES,*package)

def preserve_verifier(base,source):
    """Return new code bytes with the exact successful source verifier set.

    Callers must verify both complete input snapshots against their manifests.
    Unrelated current pipeline files retain their original bytes. Source
    producer files are selected explicitly, never a substituted hash witness.
    """
    require(isinstance(base,dict) and isinstance(source,dict),'explicit byte inventories required')
    for inventory in (base,source):
        require(all(isinstance(k,str) and not PurePosixPath(k).is_absolute() and '..' not in PurePosixPath(k).parts and isinstance(v,bytes) for k,v in inventory.items()),'canonical code byte inventory required')
    expected=verifier_names(source)
    result={name:raw for name,raw in base.items() if not name.startswith('xl_seg/') and name not in ROOT_FILES}
    result.update({name:raw for name,raw in source.items() if name.startswith('xl_seg/') or name in ROOT_FILES})
    require(verifier_names(result)==expected and all(result[k]==source[k] for k in expected),'selected verifier inventory changed')
    return result
