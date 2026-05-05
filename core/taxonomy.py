"""
core/taxonomy.py

Connector taxonomy — domains, categories, and tags.

This is the answer to Alisson's question: how do you scale to 1000
connectors without the UI becoming unusable?

The answer is not a better alphabetical list. It is a taxonomy that
defines how connectors are discovered. Each connector has:

  - One primary category: where it lives in the hierarchy
  - Multiple context tags: where it surfaces in filtered views

Zoom lives under Communications > Conferencing. But when you are
diagnosing a call quality problem, it surfaces under Network too.
Same connector, different discovery path depending on what you are
looking for.

The taxonomy is a first-class data structure — not just strings on
connectors. This means:
  - New domains and categories can be added without touching connectors
  - Connectors surface automatically wherever their tags place them
  - The same connector can have role-aware display names and descriptions
    depending on UserContext (bank customer vs bank associate)

Hierarchy: Domain -> Category -> Connector
Discovery: Tags (a connector can carry tags from multiple domains)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Domain(str, Enum):
    """Top-level domains in the taxonomy."""
    INFRASTRUCTURE = "infrastructure"
    COMMUNICATIONS = "communications"
    BUSINESS = "business"
    SECURITY = "security"


class Category(str, Enum):
    """Categories within domains."""
    # Infrastructure
    NETWORK = "network"
    COMPUTE = "compute"
    STORAGE = "storage"
    FLEET = "fleet"

    # Communications
    CONFERENCING = "conferencing"
    MESSAGING = "messaging"

    # Business
    PROJECT_MANAGEMENT = "project_management"
    CRM = "crm"
    FINANCE = "finance"

    # Security
    ENDPOINT = "endpoint"
    ACCESS = "access"


class Tag(str, Enum):
    """
    Context tags that drive discovery across domain boundaries.

    A connector carries tags from its primary category plus any
    cross-domain contexts where it is relevant. Tags are how a
    connector surfaces under multiple nodes of the tree.
    """
    # Network tags
    NETWORK = "network"
    WIFI = "wifi"
    ISP = "isp"
    LATENCY = "latency"
    PACKET_LOSS = "packet_loss"
    FLEET_NETWORK = "fleet_network"

    # Compute tags
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    SYSTEM = "system"

    # Communications tags
    VIDEO = "video"
    AUDIO = "audio"
    CONFERENCING = "conferencing"
    CALL_QUALITY = "call_quality"

    # Business tags
    PROJECT = "project"
    CRM = "crm"
    PIPELINE = "pipeline"
    TASKS = "tasks"

    # Fleet tags
    FLEET = "fleet"
    MULTI_DEVICE = "multi_device"
    LOCATION = "location"

    # Cross-domain tags
    PERFORMANCE = "performance"
    AVAILABILITY = "availability"
    SECURITY = "security"
    DEMO = "demo"


@dataclass
class CategoryDef:
    """Definition of a category within a domain."""
    category: Category
    domain: Domain
    display_name: str
    description: str


@dataclass
class TaxonomyDef:
    """
    The full taxonomy definition.

    Defines which categories belong to which domains, and provides
    lookup methods for building the UI tree and filtering connectors.
    """
    categories: list[CategoryDef] = field(default_factory=list)

    def get_categories_for_domain(self, domain: Domain) -> list[CategoryDef]:
        return [c for c in self.categories if c.domain == domain]

    def get_domain_for_category(self, category: Category) -> Domain:
        for c in self.categories:
            if c.category == category:
                return c.domain
        raise KeyError(f"Category {category} not in taxonomy")

    def to_dict(self) -> dict:
        result = {}
        for domain in Domain:
            cats = self.get_categories_for_domain(domain)
            result[domain.value] = {
                "display_name": domain.value.replace("_", " ").title(),
                "categories": [
                    {
                        "id": c.category.value,
                        "display_name": c.display_name,
                        "description": c.description,
                    }
                    for c in cats
                ]
            }
        return result


# The global taxonomy definition
TAXONOMY = TaxonomyDef(categories=[
    CategoryDef(
        category=Category.NETWORK,
        domain=Domain.INFRASTRUCTURE,
        display_name="Network",
        description="Network quality, connectivity, and ISP health",
    ),
    CategoryDef(
        category=Category.COMPUTE,
        domain=Domain.INFRASTRUCTURE,
        display_name="Compute",
        description="CPU, memory, disk, and system health",
    ),
    CategoryDef(
        category=Category.FLEET,
        domain=Domain.INFRASTRUCTURE,
        display_name="Fleet",
        description="Device fleet health across locations and organizations",
    ),
    CategoryDef(
        category=Category.STORAGE,
        domain=Domain.INFRASTRUCTURE,
        display_name="Storage",
        description="Disk health, capacity, and I/O performance",
    ),
    CategoryDef(
        category=Category.CONFERENCING,
        domain=Domain.COMMUNICATIONS,
        display_name="Conferencing",
        description="Video and audio call quality across platforms",
    ),
    CategoryDef(
        category=Category.MESSAGING,
        domain=Domain.COMMUNICATIONS,
        display_name="Messaging",
        description="Chat and messaging platform health",
    ),
    CategoryDef(
        category=Category.PROJECT_MANAGEMENT,
        domain=Domain.BUSINESS,
        display_name="Project Management",
        description="Task health, deadlines, and team productivity",
    ),
    CategoryDef(
        category=Category.CRM,
        domain=Domain.BUSINESS,
        display_name="CRM",
        description="Pipeline health, deal risk, and customer cases",
    ),
    CategoryDef(
        category=Category.ENDPOINT,
        domain=Domain.SECURITY,
        display_name="Endpoint Security",
        description="Device security posture and vulnerability status",
    ),
])
