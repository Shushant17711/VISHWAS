"""Layer 1 - TrustChain-SBOM: a static, pre-flight trust prior.

Purpose: establish which drones in the swarm were *more likely to be
compromisable* before they ever took off.

The pipeline is deliberately narrow in scope, and built on existing
open-source tooling rather than pretending to reinvent it:

1. Ingest the firmware build tree / binary for each airframe variant.
2. Generate a Software Bill of Materials with Syft (or any CycloneDX/SPDX
   producer), extracting the full third-party dependency graph.
3. Cross-reference every component against NVD/CVE with Grype or the NVD API,
   counting and severity-weighting known vulnerabilities.
4. Score provenance per component: maintainer activity, last audit date,
   licence class, unverified/unknown-origin flag.
5. Aggregate to a single ``tau_static[j]`` in [0, 1] per airframe, plus a
   human-readable procurement-style report.

**Honest scoping note.**  The scanning is not ours - Syft and Grype do it, and
they do it better than a hackathon reimplementation would.  The contribution
here is the *scoring function* and its use as a consensus prior: this is the
first component of accuser credibility ``w_i``, which is what decides whose
vote counts when the swarm votes on expelling one of its own.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ..config import PriorConfig

# Licence classes ranked by supply-chain review burden, not by ideology:
# permissive components are easier to audit and re-source than components
# under unclear or proprietary terms.
LICENCE_RISK: dict[str, float] = {
    "mit": 0.0,
    "bsd-2-clause": 0.0,
    "bsd-3-clause": 0.0,
    "apache-2.0": 0.05,
    "isc": 0.05,
    "mpl-2.0": 0.2,
    "lgpl-2.1": 0.3,
    "gpl-2.0": 0.4,
    "gpl-3.0": 0.4,
    "agpl-3.0": 0.5,
    "proprietary": 0.7,
    "unknown": 1.0,
}


@dataclass
class Vulnerability:
    cve_id: str
    cvss: float
    component: str
    fixed_available: bool = False

    @classmethod
    def from_grype(cls, match: dict[str, Any]) -> "Vulnerability":
        vuln = match.get("vulnerability", {})
        severity = 0.0
        for entry in vuln.get("cvss", []) or []:
            metrics = entry.get("metrics", {}) or {}
            severity = max(severity, float(metrics.get("baseScore", 0.0) or 0.0))
        if severity == 0.0:
            severity = _severity_fallback(str(vuln.get("severity", "unknown")))
        fix = (vuln.get("fix", {}) or {}).get("state", "unknown")
        artifact = (match.get("artifact", {}) or {}).get("name", "unknown")
        return cls(str(vuln.get("id", "UNKNOWN")), severity, str(artifact), fix == "fixed")


@dataclass
class Component:
    name: str
    version: str = "unknown"
    licence: str = "unknown"
    maintainer_active: bool = True
    last_audit: date | None = None
    unverified_origin: bool = False
    direct_dependency: bool = True

    @classmethod
    def from_syft(cls, artifact: dict[str, Any]) -> "Component":
        licences = artifact.get("licenses") or artifact.get("licences") or []
        if licences and isinstance(licences[0], dict):
            licence = str(licences[0].get("value", "unknown"))
        elif licences:
            licence = str(licences[0])
        else:
            licence = "unknown"
        return cls(
            name=str(artifact.get("name", "unknown")),
            version=str(artifact.get("version", "unknown")),
            licence=licence.lower(),
            unverified_origin=not bool(artifact.get("foundBy")),
        )


@dataclass
class SbomReport:
    airframe: str
    tau_static: float
    cve_score: float
    provenance_score: float
    maintenance_score: float
    n_components: int
    critical: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "airframe": self.airframe,
            "tau_static": round(self.tau_static, 4),
            "cve": round(self.cve_score, 4),
            "provenance": round(self.provenance_score, 4),
            "maintenance": round(self.maintenance_score, 4),
            "components": self.n_components,
            "critical_cves": list(self.critical),
        }

    def procurement_note(self) -> str:
        verdict = (
            "cleared" if self.tau_static >= 0.75
            else "acceptable with monitoring" if self.tau_static >= 0.5
            else "elevated risk - review before fielding"
        )
        lines = [
            f"Airframe variant : {self.airframe}",
            f"Static trust     : {self.tau_static:.3f}  ({verdict})",
            f"Components       : {self.n_components}",
            f"CVE subscore     : {self.cve_score:.3f}",
            f"Provenance       : {self.provenance_score:.3f}",
            f"Maintenance      : {self.maintenance_score:.3f}",
        ]
        if self.critical:
            lines.append("Critical CVEs    : " + ", ".join(self.critical[:6]))
        lines.extend(self.notes)
        return "\n".join(lines)


# ----------------------------------------------------------------------
class TrustChainScorer:
    """Turns an SBOM plus a vulnerability match list into ``tau_static``."""

    def __init__(self, cfg: PriorConfig, today: date | None = None) -> None:
        self.cfg = cfg
        self.today = today or datetime.utcnow().date()

    # ------------------------------------------------------------------
    def score(
        self,
        airframe: str,
        components: Iterable[Component],
        vulnerabilities: Iterable[Vulnerability] = (),
    ) -> SbomReport:
        comps = list(components)
        vulns = list(vulnerabilities)
        n = max(1, len(comps))

        cve = self._cve_score(vulns, n)
        prov = self._provenance_score(comps)
        maint = self._maintenance_score(comps)

        c = self.cfg
        total = c.sbom_w_cve + c.sbom_w_provenance + c.sbom_w_maintenance
        tau = (c.sbom_w_cve * cve + c.sbom_w_provenance * prov + c.sbom_w_maintenance * maint) / total
        critical = sorted({v.cve_id for v in vulns if v.cvss >= c.cvss_critical})
        notes = []
        if critical:
            notes.append(
                f"NOTE: {len(critical)} component(s) carry unpatched critical CVEs; "
                "this airframe's accusations are down-weighted in consensus."
            )
        return SbomReport(airframe, float(tau), cve, prov, maint, len(comps), critical, notes)

    # ------------------------------------------------------------------
    def _cve_score(self, vulns: list[Vulnerability], n_components: int) -> float:
        """Severity-weighted vulnerability burden, normalised per component."""
        if not vulns:
            return 1.0
        c = self.cfg
        burden = 0.0
        for v in vulns:
            if v.cvss >= c.cvss_critical:
                w = 1.0
            elif v.cvss >= c.cvss_high:
                w = 0.45
            else:
                w = 0.12 * (v.cvss / max(c.cvss_high, 1e-6))
            if v.fixed_available:
                # A fix exists and was not applied: that is a maintenance
                # failure as much as a vulnerability, so it costs more.
                w *= 1.25
            burden += w
        density = burden / math.sqrt(n_components)
        return float(math.exp(-1.15 * density))

    def _provenance_score(self, comps: list[Component]) -> float:
        if not comps:
            return 0.5
        risk = 0.0
        for comp in comps:
            r = LICENCE_RISK.get(comp.licence.lower(), 1.0)
            if comp.unverified_origin:
                r = max(r, 0.85)
            if not comp.direct_dependency:
                r *= 0.7          # transitive components carry less weight
            risk += r
        return float(max(0.0, 1.0 - risk / len(comps)))

    def _maintenance_score(self, comps: list[Component]) -> float:
        if not comps:
            return 0.5
        score = 0.0
        for comp in comps:
            part = 1.0 if comp.maintainer_active else 0.35
            if comp.last_audit is not None:
                age_days = max(0, (self.today - comp.last_audit).days)
                part *= float(math.exp(-age_days / 900.0))
            else:
                part *= 0.55
            score += part
        return float(score / len(comps))


# ----------------------------------------------------------------------
def load_syft(path: str | Path) -> list[Component]:
    """Read a Syft JSON SBOM (also accepts CycloneDX ``components``)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    artifacts = data.get("artifacts") or data.get("components") or []
    return [Component.from_syft(a) for a in artifacts]


def load_grype(path: str | Path) -> list[Vulnerability]:
    """Read a Grype JSON vulnerability report."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Vulnerability.from_grype(m) for m in data.get("matches", [])]


def _severity_fallback(label: str) -> float:
    return {
        "critical": 9.5,
        "high": 7.5,
        "medium": 5.0,
        "low": 2.5,
        "negligible": 1.0,
    }.get(label.lower(), 0.0)
