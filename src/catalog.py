"""Transformation catalog for the modernization web app.

Single source of truth for the transformations offered in the UI dropdown,
seeded from the canonical list of AWS-managed transformations:
https://docs.aws.amazon.com/transform/latest/userguide/transform-aws-customs.html

Each entry is executable via the AWSTransformCustomExecuteTransformations
policy (no publishing required). `needs_target` marks transformations that
accept a target version/framework passed to atx via additionalPlanContext.
`modifies_code=False` marks analysis-only transformations that emit reports
rather than modernized source.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class Transformation(BaseModel):
    id: str                      # UI/key, e.g. "java-version-upgrade"
    name: str                    # atx -n value, e.g. "AWS/java-version-upgrade"
    label: str                   # human-friendly dropdown label
    language: str                # Java / Python / Node.js / ...
    category: str                # dropdown optgroup
    status: str = "Generally available"
    needs_target: bool = False   # show a target field in the UI
    target_hint: str = ""        # placeholder for the target field
    target_default: str = ""     # prefilled default
    modifies_code: bool = True   # False => analysis/report only


# Ordered so the UI can group by `category` while preserving this order.
_CATALOG: list[Transformation] = [
    # --- Runtime upgrades ---
    Transformation(id="java-version-upgrade", name="AWS/java-version-upgrade", label="Java version upgrade (any JDK → any JDK)",
                   language="Java", category="Runtime upgrades", needs_target=True, target_hint="e.g. Java 17 or Java 21", target_default="Java 17"),
    Transformation(id="python-version-upgrade", name="AWS/python-version-upgrade", label="Python version upgrade (3.8/3.9 → 3.11/3.12/3.13)",
                   language="Python", category="Runtime upgrades", needs_target=True, target_hint="e.g. python3.12", target_default="python3.12"),
    Transformation(id="nodejs-version-upgrade", name="AWS/nodejs-version-upgrade", label="Node.js version upgrade (any → any)",
                   language="Node.js", category="Runtime upgrades", needs_target=True, target_hint="e.g. Node.js 22", target_default="Node.js 22"),
    Transformation(id="lambda-nodejs-runtime-upgrade", name="AWS/lambda-nodejs-runtime-upgrade", label="AWS Lambda Node.js runtime upgrade (→ nodejs24.x)",
                   language="Node.js (Lambda)", category="Runtime upgrades", status="Early access"),
    Transformation(id="oracle-java-to-corretto", name="AWS/oracle-java-to-corretto", label="Oracle JDK → Amazon Corretto",
                   language="Java", category="Runtime upgrades", status="Early access"),
    Transformation(id="ruby-upgrade", name="AWS/ruby-upgrade", label="Ruby upgrade (2.x → 4.0, Rails 8 / Sinatra 4.1)",
                   language="Ruby", category="Runtime upgrades", status="Early access", needs_target=True, target_hint="e.g. Ruby 4.0", target_default="Ruby 4.0"),

    # --- SDK migrations ---
    Transformation(id="java-aws-sdk-v1-to-v2", name="AWS/java-aws-sdk-v1-to-v2", label="Java: AWS SDK v1 → v2",
                   language="Java", category="SDK migrations"),
    Transformation(id="python-boto2-to-boto3", name="AWS/python-boto2-to-boto3", label="Python: boto2 → boto3",
                   language="Python", category="SDK migrations"),
    Transformation(id="nodejs-aws-sdk-v2-to-v3", name="AWS/nodejs-aws-sdk-v2-to-v3", label="Node.js: AWS SDK v2 → v3",
                   language="Node.js", category="SDK migrations"),

    # --- Framework upgrades and migrations ---
    Transformation(id="spring-boot-version-upgrade", name="AWS/spring-boot-version-upgrade", label="Spring Boot version upgrade",
                   language="Java / Spring Boot", category="Framework upgrades", needs_target=True, target_hint="e.g. Spring Boot 3.3", target_default="Spring Boot 3.3"),
    Transformation(id="jboss-to-spring-boot", name="AWS/JBoss-to-Spring-Boot", label="JBoss / WildFly → Spring Boot",
                   language="Java", category="Framework upgrades", status="Early access"),
    Transformation(id="angular-to-react-migration", name="AWS/early-access-angular-to-react-migration", label="Angular → React",
                   language="Angular → React", category="Framework upgrades", status="Early access"),
    Transformation(id="angular-version-upgrade", name="AWS/angular-version-upgrade", label="Angular version upgrade",
                   language="Angular", category="Framework upgrades", status="Early access", needs_target=True, target_hint="e.g. Angular 18", target_default="Angular 18"),
    Transformation(id="vuejs-version-upgrade", name="AWS/vue.js-version-upgrade", label="Vue.js 2 → 3",
                   language="Vue.js", category="Framework upgrades", status="Early access"),

    # --- Language-to-language migrations ---
    Transformation(id="vba-to-python-migration", name="AWS/vba-to-python-migration", label="Excel VBA → Python",
                   language="VBA → Python", category="Language migrations", status="Early access"),

    # --- Observability ---
    Transformation(id="log4j-to-slf4j-migration", name="AWS/early-access-log4j-to-slf4j-migration", label="Java: Log4j → SLF4J + Logback",
                   language="Java", category="Observability", status="Early access"),
    Transformation(id="datadog-to-cloudwatch", name="AWS/datadog-monitors-to-cloudwatch-alarms", label="DataDog monitors → CloudWatch alarms",
                   language="IaC", category="Observability", status="Early access"),

    # --- Architecture ---
    Transformation(id="java-performance-optimization", name="AWS/java-performance-optimization", label="Java performance optimization (JFR-guided)",
                   language="Java", category="Architecture"),
    Transformation(id="java-x86-to-graviton", name="AWS/early-access-java-x86-to-graviton", label="Java: x86 → Arm64 / Graviton readiness",
                   language="Java", category="Architecture", status="Early access"),
    Transformation(id="oracle-service-bus-to-aws", name="AWS/oracle-service-bus-to-aws", label="Oracle Service Bus → AWS serverless",
                   language="Java", category="Architecture", status="Early access"),
    Transformation(id="mulesoft-to-aws-native", name="AWS/mulesoft-to-aws-native", label="MuleSoft → AWS serverless",
                   language="Java / MuleSoft", category="Architecture", status="Early access"),
    Transformation(id="payshield-hsm-to-payment-cryptography", name="AWS/payshield-hsm-to-aws-payment-cryptography", label="Thales PayShield HSM → AWS Payment Cryptography",
                   language="Java", category="Architecture"),

    # --- Codebase analysis (report only, does not modify code) ---
    Transformation(id="comprehensive-codebase-analysis", name="AWS/comprehensive-codebase-analysis", label="Comprehensive codebase analysis",
                   language="Multiple", category="Codebase analysis (report only)", modifies_code=False),
    Transformation(id="agentic-readiness-analysis", name="AWS/agentic-readiness-analysis", label="Agentic readiness analysis",
                   language="Multiple", category="Codebase analysis (report only)", status="Early access", modifies_code=False),
    Transformation(id="modernization-readiness-analysis", name="AWS/modernization-readiness-analysis", label="Modernization readiness analysis",
                   language="Multiple", category="Codebase analysis (report only)", status="Early access", modifies_code=False),
    Transformation(id="business-rules-extraction", name="AWS/business-rules-extraction", label="Business rules extraction",
                   language="Multiple", category="Codebase analysis (report only)", status="Early access", modifies_code=False),
    Transformation(id="genai-to-bedrock-assessment", name="AWS/GenAI-to-Bedrock-Migration-Assessment", label="GenAI → Amazon Bedrock migration assessment",
                   language="GenAI", category="Codebase analysis (report only)", status="Early access", modifies_code=False),
]

_BY_ID = {t.id: t for t in _CATALOG}

# Default when a request omits the transformation (backward compatibility).
DEFAULT_TRANSFORMATION_ID = "python-version-upgrade"


def all_transformations() -> list[Transformation]:
    return list(_CATALOG)


def get(transformation_id: str) -> Optional[Transformation]:
    return _BY_ID.get(transformation_id)


def is_valid(transformation_id: str) -> bool:
    return transformation_id in _BY_ID


def expected_source_exts(t: Transformation) -> list[str]:
    """Return file signals (extensions like ".java" or basenames like
    "package.json") the uploaded archive should contain for this transformation's
    SOURCE language. An empty list means "no language gate" - accept any archive
    (used for language-agnostic analysis/IaC transformations, or where the signal
    is too ambiguous to enforce).
    """
    if not t.modifies_code:
        return []  # analysis/report-only: language-agnostic
    tid = t.id
    lang = (t.language or "").lower()
    if tid == "vba-to-python-migration":
        return [".bas", ".cls", ".frm", ".xlsm"]  # source is VBA, not Python
    if "java" in lang:
        return [".java"]
    if "python" in lang:
        return [".py"]
    if "node" in lang or "javascript" in lang:
        return [".js", ".ts", ".mjs", ".cjs", "package.json"]
    if "ruby" in lang:
        return [".rb", "gemfile", ".gemspec"]
    if "angular" in lang:
        return [".ts", ".js", ".html", "package.json"]
    if "vue" in lang:
        return [".vue", ".js", ".ts", "package.json"]
    return []  # IaC / GenAI / multiple / unknown -> no gate


def source_matches(filenames: list[str], exts: list[str]) -> bool:
    """True if any archive entry matches one of the expected signals.

    A signal starting with "." matches by extension; otherwise it matches an
    exact (case-insensitive) file basename. An empty signal list matches anything.
    """
    if not exts:
        return True
    signals = [s.lower() for s in exts]
    for name in filenames:
        f = name.lower()
        base = f.rsplit("/", 1)[-1]
        for s in signals:
            if s.startswith("."):
                if f.endswith(s):
                    return True
            elif base == s:
                return True
    return False
