"""Prospective RawWorkbooks4 entrypoint; build private context on the owning VM.

Existing fresh reconstruction and authoring gates remain the execution route.
The context is private and outside immutable code, outputs, and producer roots.
Only a root-selected corpus manifest may select original source/response pairs.
"""
from pathlib import Path
import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.batch import harbor_response_guidance as G
from scripts.fleet import fresh
from scripts.fleet.migration import binding, checked, retain


def prepare_index(plan_binding, manifest_binding, output):
    plan, manifest = checked(plan_binding), checked(manifest_binding)
    G.require(manifest["corpus"] == "RawWorkbooks4", "explicit RawWorkbooks4 manifest required")
    pairs = [p for p in manifest["pairs"] if p["workbook_id"] == plan["workbook"]]
    G.require(len(pairs) == 1, "unique source-response pair required")
    pair = pairs[0]
    source = next(s for s in checked(plan["inventory"])["sources"] if s["workbook_id"] == plan["workbook"])
    G.require(pair["staged_source"] == {k: source[k] for k in ("path", "sha256")}
              and pair["original_source"]["sha256"] == pair["staged_source"]["sha256"]
              and pair["original_response"]["sha256"] == pair["private_response"]["sha256"],
              "corpus source/response lineage changed")
    G.checked(pair["staged_source"]); G.checked(pair["private_response"])
    output = Path(output).absolute()
    for root in (Path(plan["home"]), Path(plan["code_root"]), Path(plan["output_root"]), Path(plan["publication_root"])):
        G.require(output != root and not output.is_relative_to(root) and not root.is_relative_to(output),
                  "private guidance must be separate from code, producer, and publication roots")
    G.require(not output.exists(), "private context already attempted; reconcile without replay")
    output.mkdir(parents=True, mode=0o700)
    retain(output / "intent.json", {"plan": plan_binding, "manifest": manifest_binding,
                                   "implementation": binding(__file__), "automatic_retry": False})
    context = G.prepare(plan["workbook"], pair["staged_source"]["path"], pair["private_response"]["path"],
                        original_filename=pair["original_filename"])
    context_binding = retain(output / "context.json", context)
    return retain(output / "index.json", {"schema_version": "harbor-response-guidance-index/v1",
                                           "contexts": {plan["workbook"]: context_binding}})


def run(plan_binding, manifest_binding, guidance_home):
    output = Path(guidance_home).absolute()
    G.require(not output.exists(), "private context already attempted; reconcile without replay")
    try:
        index = prepare_index(plan_binding, manifest_binding, output)
        os.environ["FCP_RESPONSE_GUIDANCE_INDEX"] = index["path"]
        os.environ["FCP_RESPONSE_GUIDANCE_INDEX_SHA256"] = index["sha256"]
        result = fresh.run(plan_binding)
        retain(output / "result.json", {"status": "AUTHORING_RETURNED", "result": result,
                "plan": plan_binding, "index": index, "publication_performed": False,
                "response_review_required_before_publication": True, "automatic_retry": False})
        return result
    except Exception as exc:
        if output.exists() and not (output / "result.json").exists():
            retain(output / "result.json", {"status": "RECONCILIATION_REQUIRED", "plan": plan_binding,
                    "failure_type": type(exc).__name__, "publication_performed": False,
                    "automatic_retry": False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "prepare-index"))
    for name in ("plan", "plan-sha256", "corpus-manifest", "corpus-manifest-sha256", "guidance-home"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    plan = {"path": str(Path(args.plan).absolute()), "sha256": args.plan_sha256}
    manifest = {"path": str(Path(args.corpus_manifest).absolute()), "sha256": args.corpus_manifest_sha256}
    result = (run if args.action == "run" else prepare_index)(plan, manifest, args.guidance_home)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
