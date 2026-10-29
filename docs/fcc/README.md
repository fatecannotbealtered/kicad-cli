# Functional Contract Coverage

Whole-product FCC is missing; the actual fixed contract declares release_readiness=unpublishable. The limited internal 1.0.0 profile does not promote that level.

behavior-inventory.json and behavior-assessment.json are development inputs for the FCC collector, not acceptance of the current executable. Their artifact identities and incomplete/non-exhaustive coverage must not be transferred to a new binary. Current qualification requires fresh source/artifact-bound JUnit and complete reviewed public promises through scripts/collect_fcc.py and scripts/check_release.py.

Scoped CI, native engineering examples and Agent scenario files establish only their explicitly recorded scope. A passing subset cannot replace whole-product FCC, native engineering review or manufacturing approval. No historical closure count is advertised as current acceptance.
