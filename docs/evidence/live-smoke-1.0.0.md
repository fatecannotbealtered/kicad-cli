# Live smoke evidence — kicad-cli 1.0.0

CLI-SPEC §11 makes this a precondition for `stable`: at least one recorded live
smoke/E2E run against the real upstream, for the release candidate being
declared. This file is that record, produced by `scripts/record_evidence.py`.

Two parts, because passing the first and failing the second is exactly what
happened once: the suite ran green while the binary it was supposed to vouch
for could not start at all. Testing the source is not testing the artifact.

Output is verbatim except that absolute paths are replaced — `<repo>` for the
checkout, `<kicad>` for the KiCad installation, `<python>` for the interpreter —
so this records a result rather than one machine's filesystem.

| | |
|---|---|
| Tool version | `1.0.0` |
| Recorded | 2026-09-17 |
| Platform | Windows 11 (AMD64) |
| KiCad | 10.0.6 |
| Runner | Python 3.12.10 |
| Suite | **PASS** (exit 0) |
| Frozen binary | **PASS** (exit 0) |
| Overall | **PASS** |

What "live" means here: nothing is stubbed. Each test launches the real
`kicad-cli` process, which launches KiCad's own interpreter and binary, against
KiCad's own demo projects copied into a temp directory. The exception is
`test_mock_upstream.py`, which deliberately substitutes KiCad in order to reach
the failure paths a real KiCad will not produce on demand.

What this evidence does **not** cover, stated here so the level is not read as
more than it is: one platform, one KiCad version. There is no second machine and
no CI run behind it, because CI has no KiCad. `docs/COMPATIBILITY.md` lists what
that leaves unproven.

Reproduce with `pytest tests/ -v --tb=short`, then `python build.py` and
`python scripts/smoke_binary.py dist/kicad-cli[.exe]`.

## 2. Frozen binary

```text
smoking <repo>\dist\kicad-cli.exe
  ok    reference starts and returns an envelope
  ok    version matches package.json
  ok    the command registry survived freezing
  ok    context starts
  ok    doctor starts
  ok    changelog data file was bundled
  ok    unknown command fails as an envelope, not a traceback
  ok    board live reached a running KiCad
smoke: OK
```

## 1. Test suite

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.3, pluggy-1.6.0 -- <python>\python.exe
rootdir: <repo>
configfile: pyproject.toml
plugins: anyio-4.13.0, cov-7.1.0
collecting ... collected 101 items

tests/test_argument_gate.py::test_an_option_from_another_command_is_refused PASSED [  0%]
tests/test_argument_gate.py::test_a_misspelled_option_is_refused PASSED  [  1%]
tests/test_argument_gate.py::test_hyphenated_options_actually_reach_the_command PASSED [  2%]
tests/test_argument_gate.py::test_declared_and_global_options_still_work[argv0-declared option] PASSED [  3%]
tests/test_argument_gate.py::test_declared_and_global_options_still_work[argv1-hyphenated] PASSED [  4%]
tests/test_argument_gate.py::test_declared_and_global_options_still_work[argv2-global option] PASSED [  5%]
tests/test_argument_gate.py::test_every_declared_option_is_accepted_by_its_own_command PASSED [  6%]
tests/test_board_plane.py::test_the_stack_is_reported_in_physical_order PASSED [  7%]
tests/test_board_plane.py::test_every_layer_gets_an_adjacent_reference PASSED [  8%]
tests/test_board_plane.py::test_gaps_are_grouped_by_place PASSED         [  9%]
tests/test_changelog.py::test_unreleased_is_an_entry PASSED              [ 10%]
tests/test_changelog.py::test_a_wrapped_bullet_arrives_whole PASSED      [ 11%]
tests/test_changelog.py::test_since_keeps_unreleased PASSED              [ 12%]
tests/test_changelog.py::test_the_repos_own_changelog_parses PASSED      [ 13%]
tests/test_contract.py::test_every_command_declares_a_schema_that_exists PASSED [ 14%]
tests/test_contract.py::test_every_write_command_is_declared_as_a_write PASSED [ 15%]
tests/test_contract.py::test_self_describing_commands_match_their_contract[argv0] PASSED [ 16%]
tests/test_contract.py::test_self_describing_commands_match_their_contract[argv1] PASSED [ 17%]
tests/test_contract.py::test_self_describing_commands_match_their_contract[argv2] PASSED [ 18%]
tests/test_contract.py::test_self_describing_commands_match_their_contract[argv3] PASSED [ 19%]
tests/test_contract.py::test_read_commands_match_their_contract[command0] PASSED [ 20%]
tests/test_contract.py::test_read_commands_match_their_contract[command1] PASSED [ 21%]
tests/test_contract.py::test_read_commands_match_their_contract[command2] PASSED [ 22%]
tests/test_contract.py::test_read_commands_match_their_contract[command3] PASSED [ 23%]
tests/test_contract.py::test_read_commands_match_their_contract[command4] PASSED [ 24%]
tests/test_contract.py::test_read_commands_match_their_contract[command5] PASSED [ 25%]
tests/test_contract.py::test_write_commands_match_their_contract[command0-extra0] PASSED [ 26%]
tests/test_contract.py::test_write_commands_match_their_contract[command1-extra1] PASSED [ 27%]
tests/test_contract.py::test_write_commands_match_their_contract[command2-extra2] PASSED [ 28%]
tests/test_contract.py::test_write_commands_match_their_contract[command3-extra3] PASSED [ 29%]
tests/test_contract.py::test_write_commands_match_their_contract[command4-extra4] PASSED [ 30%]
tests/test_contract.py::test_write_commands_match_their_contract[command5-extra5] PASSED [ 31%]
tests/test_contract.py::test_write_commands_match_their_contract[command6-extra6] PASSED [ 32%]
tests/test_contract.py::test_a_stale_token_is_refused PASSED             [ 33%]
tests/test_contract.py::test_a_write_refuses_while_the_project_is_open_in_kicad PASSED [ 34%]
tests/test_mock_upstream.py::test_the_fixture_board_needs_no_kicad PASSED [ 35%]
tests/test_mock_upstream.py::test_a_payload_envelope_is_relayed PASSED   [ 36%]
tests/test_mock_upstream.py::test_strict_mode_rejects_a_payload_that_does_not_match_its_schema PASSED [ 37%]
tests/test_mock_upstream.py::test_an_unusable_official_binary_is_e_io_after_retrying[launch_fail] PASSED [ 38%]
tests/test_mock_upstream.py::test_an_unusable_official_binary_is_e_io_after_retrying[no_output] PASSED [ 39%]
tests/test_mock_upstream.py::test_retrying_actually_recovers PASSED      [ 40%]
tests/test_mock_upstream.py::test_an_unusable_interpreter_is_e_io[launch_fail-E_IO] PASSED [ 41%]
tests/test_mock_upstream.py::test_an_unusable_interpreter_is_e_io[no_output-E_IO] PASSED [ 42%]
tests/test_mock_upstream.py::test_an_unusable_interpreter_is_e_io[garbage-E_IO] PASSED [ 43%]
tests/test_mock_upstream.py::test_noise_before_the_envelope_is_stepped_over PASSED [ 44%]
tests/test_mock_upstream.py::test_a_hanging_upstream_becomes_e_timeout PASSED [ 45%]
tests/test_mock_upstream.py::test_an_empty_netlist_is_refused_rather_than_counted PASSED [ 46%]
tests/test_mock_upstream.py::test_a_project_with_no_symbols_is_not_found_not_zero PASSED [ 47%]
tests/test_mock_upstream.py::test_diagnostics_never_land_on_stdout PASSED [ 48%]
tests/test_mock_upstream.py::test_every_error_here_maps_to_its_declared_exit_code PASSED [ 49%]
tests/test_mock_upstream.py::test_our_own_binary_on_path_is_not_mistaken_for_kicads PASSED [ 50%]
tests/test_mock_upstream.py::test_a_real_kicad_layout_on_path_is_accepted PASSED [ 51%]
tests/test_relink_roundtrip.py::test_relink_reproduces_kicads_own_paths[complex_hierarchy] PASSED [ 52%]
tests/test_relink_roundtrip.py::test_relink_reproduces_kicads_own_paths[ecc83] PASSED [ 53%]
tests/test_relink_roundtrip.py::test_relink_reproduces_kicads_own_paths[interf_u] PASSED [ 54%]
tests/test_relink_roundtrip.py::test_relink_reproduces_kicads_own_paths[kit-dev-coldfire-xilinx_5213] PASSED [ 55%]
tests/test_relink_roundtrip.py::test_relink_reproduces_kicads_own_paths[cm5_minima] PASSED [ 56%]
tests/test_relink_roundtrip.py::test_relink_on_an_already_linked_board_is_a_noop PASSED [ 57%]
tests/test_relink_roundtrip.py::test_relink_refuses_when_a_footprint_has_no_symbol PASSED [ 58%]
tests/test_sch_audit.py::test_a_rare_finding_survives_sampling PASSED    [ 59%]
tests/test_sch_audit.py::test_repeated_messages_collapse_to_their_shape PASSED [ 60%]
tests/test_sch_audit.py::test_a_filter_with_a_library_part_is_matched_against_the_whole_id PASSED [ 61%]
tests/test_sch_audit.py::test_unflattening_recovers_the_original_library_id PASSED [ 62%]
tests/test_sch_audit.py::test_reports_shipped_defaults_as_defaults PASSED [ 63%]
tests/test_sch_audit.py::test_a_locally_disabled_rule_is_called_out PASSED [ 64%]
tests/test_sch_audit.py::test_the_unmuted_run_never_reports_less PASSED  [ 65%]
tests/test_skill.py::test_name_is_the_directory_and_is_well_formed PASSED [ 66%]
tests/test_skill.py::test_version_matches_the_tool_in_all_three_places PASSED [ 67%]
tests/test_skill.py::test_metadata_declares_the_binary_as_a_string_array PASSED [ 68%]
tests/test_skill.py::test_description_is_third_person_and_says_when PASSED [ 69%]
tests/test_skill.py::test_no_template_scaffolding_survives PASSED        [ 70%]
tests/test_skill.py::test_every_command_the_skill_names_exists PASSED    [ 71%]
tests/test_skill.py::test_skill_does_not_claim_a_self_update_command PASSED [ 72%]
tests/test_skill.py::test_every_option_the_parameters_page_documents_is_declared PASSED [ 73%]
tests/test_skill.py::test_status_values_the_skill_documents_are_the_real_ones PASSED [ 74%]
tests/test_skill.py::test_the_confirm_token_path_is_stated_correctly PASSED [ 75%]
tests/test_skill.py::test_destructive_switches_carry_a_checkpoint[--mode full] PASSED [ 76%]
tests/test_skill.py::test_destructive_switches_carry_a_checkpoint[--no-verify] PASSED [ 77%]
tests/test_skill.py::test_destructive_switches_carry_a_checkpoint[--ignore-lock] PASSED [ 78%]
tests/test_skill.py::test_destructive_switches_carry_a_checkpoint[--no-restore] PASSED [ 79%]
tests/test_skill.py::test_both_causes_of_conflict_are_explained PASSED   [ 80%]
tests/test_skill.py::test_test_prompts_are_specific_to_this_tool PASSED  [ 81%]
tests/test_skill.py::test_referenced_files_exist PASSED                  [ 82%]
tests/test_skill.py::test_skill_stays_short PASSED                       [ 83%]
tests/test_skill.py::test_the_install_block_names_what_this_repo_publishes PASSED [ 84%]
tests/test_skill.py::test_the_install_block_has_no_machine_local_path PASSED [ 85%]
tests/test_skill.py::test_the_binary_installed_is_the_binary_declared PASSED [ 86%]
tests/test_sync_preview.py::test_linked_board_predicts_no_changes[complex_hierarchy] PASSED [ 87%]
tests/test_sync_preview.py::test_linked_board_predicts_no_changes[interf_u] PASSED [ 88%]
tests/test_sync_preview.py::test_linked_board_predicts_no_changes[kit-dev-coldfire-xilinx_5213] PASSED [ 89%]
tests/test_sync_preview.py::test_stripped_links_predict_a_destructive_update PASSED [ 90%]
tests/test_sync_preview.py::test_renaming_a_reference_is_not_an_add PASSED [ 91%]
tests/test_sync_preview.py::test_board_only_footprints_are_never_removed PASSED [ 92%]
tests/test_sync_preview.py::test_refuses_on_an_unannotated_schematic PASSED [ 93%]
tests/test_uncovered_commands.py::test_each_plot_format_writes_files[gerber] PASSED [ 94%]
tests/test_uncovered_commands.py::test_each_plot_format_writes_files[pdf] PASSED [ 95%]
tests/test_uncovered_commands.py::test_each_plot_format_writes_files[svg] PASSED [ 96%]
tests/test_uncovered_commands.py::test_each_plot_format_writes_files[dxf] PASSED [ 97%]
tests/test_uncovered_commands.py::test_a_plot_refuses_an_unknown_layer PASSED [ 98%]
tests/test_uncovered_commands.py::test_board_live_reports_a_usable_fix_when_kicad_is_unreachable PASSED [ 99%]
tests/test_fcc_guard.py::test_fcc_every_leaf_command_has_test PASSED     [100%]

======================= 101 passed in 166.09s (0:02:46) =======================
```
