# Hierarchy reader regression input

H3 observation and native reports were read from the owned synthetic public-CLI
development run after its declared shared-sheet connection. Expectations remain
the earlier frozen `bench/hierarchy_edit/requirements/FROZEN.json`; they were not
derived from this output. The observation's `raw_footprints` was independently
parsed from the same saved board, whose hash is in the observation. No product
writer is used by the consumer tests.

The native input has three pre-existing ERC findings and nine unconnected PCB
items at H3, with zero other DRC/parity findings. Test mutations of this reader
record are synthetic negative controls, not new native ERC/DRC executions.
Raw process evidence remains under the local hierarchy delivery, indexed in its
handoff; this directory is portable test data with no developer path dependency.
