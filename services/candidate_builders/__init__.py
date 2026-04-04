# services/candidate_builders/__init__.py
# Family-first candidate builders for hybrid retrieval.
#
# Each builder produces a CandidateSet using the right index first:
#   type family     -> OCR/structure retrieval first
#   people_event    -> person/face retrieval first
#   pet family      -> animal evidence first
#   scenic family   -> multimodal semantic retrieval first
#   utility family  -> metadata/state retrieval first

from services.candidate_builders.base_candidate_builder import (
    BaseCandidateBuilder,
    CandidateSet,
)
from services.candidate_builders.document_candidate_builder import (
    DocumentCandidateBuilder,
)
from services.candidate_builders.people_candidate_builder import (
    PeopleCandidateBuilder,
)
from services.candidate_builders.screenshot_candidate_builder import (
    ScreenshotCandidateBuilder,
)
from services.candidate_builders.scenic_candidate_builder import (
    ScenicCandidateBuilder,
)

# Dispatch map for orchestrator.
# Families NOT in this map fall through to the legacy CLIP pipeline,
# which is logged as a FAMILY_FALLBACK structured event.
CANDIDATE_BUILDERS = {
    "type": DocumentCandidateBuilder,
    "people_event": PeopleCandidateBuilder,
    "scenic": ScenicCandidateBuilder,
    # "utility" is intentionally absent from builder dispatch.
    # Metadata-only utility presets are handled by the dedicated
    # orchestrator fast path, not by builders and not by CLIP fallback.
    # "animal_object" uses CLIP-first with pets gate until a PetCandidateBuilder exists.
}

# Preset-specific builder overrides.
# When a preset has an entry here, it takes priority over the family-level
# CANDIDATE_BUILDERS dispatch.  This lets "screenshots" use its own builder
# while "documents" still uses DocumentCandidateBuilder via the "type" family.
PRESET_BUILDERS = {
    "screenshots": ScreenshotCandidateBuilder,
}

__all__ = [
    "BaseCandidateBuilder",
    "CandidateSet",
    "DocumentCandidateBuilder",
    "PeopleCandidateBuilder",
    "ScreenshotCandidateBuilder",
    "ScenicCandidateBuilder",
    "CANDIDATE_BUILDERS",
    "PRESET_BUILDERS",
]
