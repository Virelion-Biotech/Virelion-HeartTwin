"""FHIR R4 observation import with explicit local interpretation and immutable sources."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .patient_ingest import import_observation
from .patient_model import PatientCase
from .patient_observations import verified_bytes


def fetch_fhir_bundle(url, *, output_dir, token_env=None, max_pages=100):
    """Owner-configured read-only HTTPS collection; never follow credential redirects.

    No site is contacted until explicitly configured. Next-page URLs must stay under
    the same initial resource path and origin. Response sizes and page counts bounded.
    Tokens are read from worker environment, never serialized into artifacts.
    """
    import os
    import urllib.request
    from urllib.parse import urljoin, urlparse

    from .patient_geometry import write_artifact

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("FHIR redirects are refused; configure the final endpoint")

    first = urlparse(url)
    if first.scheme != "https" or not first.hostname or first.username or first.password or first.fragment:
        raise ValueError("Configure an HTTPS FHIR resource URL without credentials or fragment")
    if not 1 <= max_pages <= 100:
        raise ValueError("FHIR page limit must be 1..100")
    headers = {"Accept": "application/fhir+json"}
    if token_env:
        token = os.environ[token_env]
        if not token or "\n" in token or "\r" in token:
            raise ValueError("Invalid worker FHIR bearer token")
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(NoRedirect())
    seen, entries, receipts = set(), [], []
    total_bytes = 0
    while url:
        current = urlparse(url)
        if ((current.scheme, current.netloc, current.path) != (first.scheme, first.netloc, first.path)
                or current.fragment or current.username or current.password or url in seen or len(seen) >= max_pages):
            raise ValueError("FHIR pagination changed origin/resource, looped or exceeded page limit")
        seen.add(url)
        with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            raw = response.read(10_000_001)
        total_bytes += len(raw)
        if len(raw) > 10_000_000 or total_bytes > 50_000_000:
            raise ValueError("FHIR response exceeds configured collection limits")
        page = json.loads(raw)
        if page.get("resourceType") != "Bundle" or page.get("type") not in {"searchset", "collection"}:
            raise ValueError("Expected FHIR searchset/collection Bundle")
        receipts.append({"sha256": hashlib.sha256(raw).hexdigest(), "page": len(seen)})
        entries.extend(page.get("entry", []))
        links = [x["url"] for x in page.get("link", []) if x.get("relation") == "next"]
        if len(links) > 1:
            raise ValueError("Ambiguous next page")
        url = urljoin(url, links[0]) if links else None
    artifact = write_artifact(output_dir, "hospital-bundle", {
        "resourceType": "Bundle", "type": "collection", "entry": entries,
    })
    return {"file_id": "fhir-" + artifact["sha256"][:16], **artifact,
            "metadata": {"transport": "read-only-https", "page_receipts": receipts}}


def import_fhir_bundle(case, source, mapping, *, output_dir):
    """Import selected quantities; reject ambiguity rather than guess clinical meaning.

    Mapping names exact subject/encounter references and individual Observation IDs.
    Components require a system/code selector. No automatic terminology/unit mapping.
    Reimport of identical content is a no-op; changed resources need new import IDs.
    """
    case = PatientCase.model_validate(case)
    raw = verified_bytes(source)
    bundle = json.loads(raw)
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") not in {
        "collection", "searchset", "document", "transaction", "batch-response",
    }:
        raise ValueError("Expected a FHIR R4 Bundle")
    if any(x.get("relation") == "next" for x in bundle.get("link", [])):
        raise ValueError("Incomplete paginated Bundle: collect all pages first")
    if not mapping.get("patient_reference") or not mapping.get("observations"):
        raise ValueError("Explicit patient reference and observation mapping required")
    encounters = mapping.get("encounters", {})
    if not encounters or not set(encounters.values()) <= {e.encounter_id for e in case.encounters}:
        raise ValueError("Map hospital encounters to existing patient encounters")
    resources = {}
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") != "Observation":
            continue
        rid = r.get("id")
        if not rid or rid in resources:
            raise ValueError("Observation IDs must be present and unique")
        resources[rid] = r
    root = Path(output_dir).resolve()
    report = {"imported": [], "unchanged": [], "source_sha256": hashlib.sha256(raw).hexdigest()}
    # Validate the entire selection before producing a new case revision.
    selections = []
    seen = set()
    for item in mapping["observations"]:
        key = item["acquisition_id"]
        if key in seen:
            raise ValueError("Duplicate import acquisition ID")
        seen.add(key)
        r = resources[item["observation_id"]]
        if r.get("status") not in {"final", "amended", "corrected"}:
            raise ValueError("Only final/amended/corrected observations can be imported")
        if r.get("subject", {}).get("reference") != mapping["patient_reference"]:
            raise ValueError("Hospital observation belongs to a different patient")
        encounter = encounters.get(r.get("encounter", {}).get("reference"))
        if encounter is None:
            raise ValueError("Observation encounter is absent or unmapped")
        selected = r
        if "component" in item:
            selector = item["component"]
            matches = [c for c in r.get("component", []) if any(
                code.get("system") == selector["system"] and code.get("code") == selector["code"]
                for code in c.get("code", {}).get("coding", [])
            )]
            if len(matches) != 1:
                raise ValueError("FHIR component selector must match exactly once")
            selected = matches[0]
        coding = selected.get("code", {}).get("coding", [])
        if not any(c.get("system") == item["code_system"] and c.get("code") == item["code"] for c in coding):
            raise ValueError("Observation code does not match the reviewed mapping")
        quantity = selected.get("valueQuantity", {})
        value = quantity.get("value")
        if (quantity.get("comparator") or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("Expected an exact finite FHIR quantity; inequalities are not measurements")
        if quantity.get("system") != "http://unitsofmeasure.org" or quantity.get("code") != item["unit"]:
            raise ValueError("FHIR UCUM unit mismatch; no implicit conversion is performed")
        model_unit = item.get("model_unit", item["unit"])
        if model_unit != item["unit"] and (item["unit"], model_unit) not in {("mm[Hg]", "mmHg")}:
            raise ValueError("Only explicitly requested mm[Hg] to mmHg spelling normalization is supported")
        marker = hashlib.sha256(json.dumps(
            {"resource": r, "mapping": item, "encounter_id": encounter},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        old = next((a for a in case.acquisitions if a.acquisition_id == key), None)
        if old:
            if old.metadata.get("fhir_import_sha256") != marker:
                raise ValueError("FHIR acquisition already exists with different content; use a new ID")
            report["unchanged"].append(key)
            continue
        selections.append((item, r, encounter, marker, value))
    for item, r, encounter, marker, value in selections:
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"value": value, "unit": item.get("model_unit", item["unit"])}, allow_nan=False).encode()
        digest = hashlib.sha256(payload).hexdigest()
        path = root / (digest + ".json")
        path.write_bytes(payload)
        spec = {k: item[k] for k in ("acquisition_id", "evidence_id", "variable", "unit", "role", "modality")}
        spec["unit"] = item.get("model_unit", item["unit"])
        spec.update(format="json", encounter_id=encounter, acquired_at=r.get("effectiveDateTime"))
        if item.get("input_id"):
            spec.update(input_id=item["input_id"], domain=item["domain"])
        case, _ = import_observation(case, {"file_id": digest, "uri": path.as_uri(), "sha256": digest}, spec, output_dir=root)
        updated = case.model_dump(mode="json")
        acq = updated["acquisitions"][-1]
        acq["source_files"].append(source)
        acq["metadata"].update(fhir_import_sha256=marker, fhir_observation_id=r["id"], fhir_version="4.0.1")
        case = PatientCase.model_validate(updated)
        report["imported"].append(item["acquisition_id"])
    return case, report
