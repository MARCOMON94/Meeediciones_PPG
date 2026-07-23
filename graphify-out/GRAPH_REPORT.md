# Graph Report - mtestv2  (2026-07-23)

## Corpus Check
- 66 files · ~119,916 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1091 nodes · 3180 edges · 60 communities (44 shown, 16 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 92 edges (avg confidence: 0.55)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `4106e2eb`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- AnimalsWindow
- SensorConfig
- AppController
- ModeSelectDialog
- FourierAnalysisWindow
- PPGSuite
- atomic_write_json
- RelationExplorerWindow
- CaptureRecord
- VacuumExperimentWindow
- _as_float
- relations_window.py
- Experiment3MWindow
- measurement_window.py
- .current_animal_type
- .value
- DictTableModel
- What You Must Do When Invoked
- What You Must Do When Invoked
- .build_ui
- AnimalPhotoCell
- mtestv2
- git_auto_update.py
- graphify reference: extra exports and benchmark
- graphify reference: extra exports and benchmark
- CollapsibleSection
- build_icon
- graphify reference: query, path, explain
- graphify reference: query, path, explain
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- AGENTS.md
- CLAUDE.md
- .claude/CLAUDE.md
- .claude/skills/graphify/references/extraction-spec.md
- .codex/skills/graphify/references/extraction-spec.md
- CaptureRecord
- .select_animal
- BleSerialAdapter
- .populate_animal_list
- _read_csv
- _base_from_row
- test_animals_window.py
- .update_photo
- fourier_window.py
- SensorConfig
- ._build_ui
- .open_history_raw

## God Nodes (most connected - your core abstractions)
1. `PPGSuite` - 122 edges
2. `RelationExplorerWindow` - 120 edges
3. `AnimalsWindow` - 105 edges
4. `SensorConfig` - 59 edges
5. `AnalysisConfig` - 57 edges
6. `CaptureRecord` - 46 edges
7. `fmt()` - 44 edges
8. `AppController` - 40 edges
9. `VacuumExperimentWindow` - 37 edges
10. `estimate_hz()` - 34 edges

## Surprising Connections (you probably didn't know these)
- `LoadingDialog` --uses--> `ModeSelectDialog`  [INFERRED]
  controller.py → ppg_suite/menu.py
- `LoadingDialog` --uses--> `AnimalsWindow`  [INFERRED]
  controller.py → ppg_suite/windows/animals_window.py
- `LoadingDialog` --uses--> `FourierAnalysisWindow`  [INFERRED]
  controller.py → ppg_suite/windows/fourier_window.py
- `LoadingDialog` --uses--> `RelationExplorerWindow`  [INFERRED]
  controller.py → ppg_suite/windows/relations_window.py
- `LoadingDialog` --uses--> `Experiment3MWindow`  [INFERRED]
  controller.py → ppg_suite/windows/scheduled_window.py

## Import Cycles
- None detected.

## Communities (60 total, 16 thin omitted)

### Community 1 - "SensorConfig"
Cohesion: 0.22
Nodes (27): AnalysisConfig, block_bpm(), bpm_from_peak_indices(), compute_ac_dc(), detect_artifacts(), estimate_bpm_autocorr(), estimate_bpm_fft(), estimate_bpm_peaks() (+19 more)

### Community 2 - "AppController"
Cohesion: 0.05
Nodes (18): AppController, LoadingDialog, Pantalla independiente para reajustes/larga duración.      Importante: ya no se, ReajustesWindow, RealWindow, ConfigTableWidget, ConfigurationsWindow, QKeyEvent (+10 more)

### Community 3 - "ModeSelectDialog"
Cohesion: 0.08
Nodes (26): DataImportResult, import_resultados_folder(), Path, validate_resultados_folder(), arduino_cli_path(), available_firmware_ports(), compile_firmware(), FirmwarePort (+18 more)

### Community 4 - "FourierAnalysisWindow"
Cohesion: 0.11
Nodes (11): _aggregate_stem_for_child(), _apply_header_tooltips(), FourierAnalysisWindow, datetime, Path, QCloseEvent, QModelIndex, QTableWidget (+3 more)

### Community 5 - "PPGSuite"
Cohesion: 0.10
Nodes (3): PPGSuite, ndarray, QKeyEvent

### Community 6 - "atomic_write_json"
Cohesion: 0.19
Nodes (9): _as_ref_pulse(), _cap_temp_final(), _read_csv(), SelectionRecord, SessionGroup, _strip_prefix(), Path, test_remove_capture_rows_from_sessions_uses_atomic_rewrite() (+1 more)

### Community 7 - "RelationExplorerWindow"
Cohesion: 0.11
Nodes (4): QCloseEvent, RelationExplorerWindow, _select_first_row(), QTableView

### Community 10 - "VacuumExperimentWindow"
Cohesion: 0.11
Nodes (5): ndarray, Path, QCloseEvent, PPG + microphone capture for post-run vacuum/notch analysis., VacuumExperimentWindow

### Community 11 - "_as_float"
Cohesion: 0.14
Nodes (3): PenStyle, _as_float(), ndarray

### Community 12 - "relations_window.py"
Cohesion: 0.10
Nodes (24): CaptureState, Metrics, Short live warning for SpO2 reliability, while keeping BPM usable., spo2_support_message(), fmt(), mean_valid_reference(), now_stamp(), Mean of manually-entered reference BPM values, ignoring blanks, non-numeric (+16 more)

### Community 13 - "Experiment3MWindow"
Cohesion: 0.09
Nodes (10): build_12_config_steps(), build_3m_search_space(), build_64_config_steps(), Experiment3MWindow, make_3m_step(), ndarray, _ref_pulse(), ScheduledConfigWindow (+2 more)

### Community 14 - "measurement_window.py"
Cohesion: 0.15
Nodes (3): AppMode, QComboBox, QWidget

### Community 15 - ".current_animal_type"
Cohesion: 0.15
Nodes (15): active_temp_channels_for_animal(), default_mapping_for_animal(), default_position_for_animal(), display_mapping(), display_position(), inverted_mapping_for_animal(), iter_position_prefixes(), mapping_from_assignments() (+7 more)

### Community 16 - ".value"
Cohesion: 0.23
Nodes (4): _cap_first(), _csv_row_count(), _mean_ref_pulse(), _mode_label()

### Community 17 - "DictTableModel"
Cohesion: 0.16
Nodes (3): Orientation, DictTableModel, QModelIndex

### Community 18 - "What You Must Do When Invoked"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 19 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 20 - ".build_ui"
Cohesion: 0.12
Nodes (3): Exception, BleSerialAdapter, QCloseEvent

### Community 21 - "AnimalPhotoCell"
Cohesion: 0.13
Nodes (8): AnimalPhotoCell, BulkPhotoDialog, QWidget, Drag-and-droppable image slot used by BulkPhotoDialog's table., Additional window to assign one photo per checked animal, oldest-first., QDragEnterEvent, QDropEvent, QMouseEvent

### Community 22 - "mtestv2"
Cohesion: 0.15
Nodes (12): Animales, sensores y temperatura, Arranque, Datos generados, Empaquetado Windows, Estructura del proyecto, Firmware Arduino, Instalacion de desarrollo, Modos principales (+4 more)

### Community 23 - "git_auto_update.py"
Cohesion: 0.46
Nodes (7): CompletedProcess, git_env(), is_dirty(), main(), Path, run_git(), update_timeout_seconds()

### Community 24 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 25 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 26 - "CollapsibleSection"
Cohesion: 0.16
Nodes (7): AnimalMeasurement, _load_csv_rows(), Path, Read a project CSV keeping the header order, so it can be rewritten in place., Rewrite the id/animal_type columns of a capture CSV so it re-associates with the, Re-point every raw/session/summary file for `old_key` to the new crotal., _base_from_row()

### Community 27 - "build_icon"
Cohesion: 0.47
Nodes (5): QImage, build_icon(), main(), png_bytes(), Path

### Community 31 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 32 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 33 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 34 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 35 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 36 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 37 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 38 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 48 - "CaptureRecord"
Cohesion: 0.20
Nodes (4): animal_label(), normalize_animal_type(), animal_key(), safe_file_part()

### Community 50 - "BleSerialAdapter"
Cohesion: 0.07
Nodes (57): AnalysisMode, AgreementAnalysisConfig, AgreementExclusion, AgreementPair, _as_float(), bland_altman_auto(), bland_altman_classic(), bland_altman_repeated() (+49 more)

### Community 53 - "_base_from_row"
Cohesion: 0.29
Nodes (4): load_oriented_pixmap(), QPixmap, Load an image applying its EXIF orientation, so it matches how the     file look, QResizeEvent

### Community 54 - "test_animals_window.py"
Cohesion: 0.36
Nodes (8): AnimalSelectionRecord, make_window(), Path, test_animal_table_row_selection_uses_animal_key_from_checkbox_metadata(), test_recommended_alerts_exclude_measurements_without_stable_bpm(), test_remove_capture_rows_from_sessions_uses_atomic_rewrite(), test_selected_animal_mail_paths_use_raws(), test_selection_column_header_is_compact_like_statistics()

### Community 55 - ".update_photo"
Cohesion: 0.11
Nodes (28): DictWriter, main(), atomic_csv_dict_writer(), atomic_csv_writer(), atomic_text_file(), atomic_write_json(), atomic_write_text(), Path (+20 more)

### Community 56 - "fourier_window.py"
Cohesion: 0.17
Nodes (21): open_folder(), Path, _ac_dc_pi(), _analytic_signal(), analyze_raw_file(), _as_float(), _as_int(), _as_ref_pulse() (+13 more)

### Community 57 - "SensorConfig"
Cohesion: 0.18
Nodes (15): Counter, SensorConfig, compute_blind_and_assisted_stable(), Blind and assisted stable-BPM segments for a capture.      Blind (reference_bp, stable_bpm_segment(), _stable_reason_summary(), _stable_reference_tolerance(), test_blind_stable_segment_ignores_manual_reference() (+7 more)

## Knowledge Gaps
- **97 isolated node(s):** `graphify`, `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed` (+92 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `RelationExplorerWindow` connect `RelationExplorerWindow` to `SensorConfig`, `AppController`, `atomic_write_json`, `CaptureRecord`, `_as_float`, `relations_window.py`, `.value`, `DictTableModel`, `BleSerialAdapter`, `_read_csv`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Why does `PPGSuite` connect `PPGSuite` to `SensorConfig`, `AppController`, `BleSerialAdapter`, `VacuumExperimentWindow`, `relations_window.py`, `Experiment3MWindow`, `measurement_window.py`, `.current_animal_type`, `CaptureRecord`, `.build_ui`?**
  _High betweenness centrality (0.120) - this node is a cross-community bridge._
- **Why does `AnimalsWindow` connect `AnimalsWindow` to `AppController`, `CollapsibleSection`, `CaptureRecord`, `.select_animal`, `BleSerialAdapter`, `.populate_animal_list`, `AnimalPhotoCell`, `_base_from_row`, `test_animals_window.py`, `._build_ui`, `.open_history_raw`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Are the 11 inferred relationships involving `PPGSuite` (e.g. with `ReajustesWindow` and `RealWindow`) actually correct?**
  _`PPGSuite` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `RelationExplorerWindow` (e.g. with `AppController` and `.show_relations()`) actually correct?**
  _`RelationExplorerWindow` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `AnimalsWindow` (e.g. with `AppController` and `.show_animals()`) actually correct?**
  _`AnimalsWindow` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `SensorConfig` (e.g. with `AgreementAnalysisConfig` and `AgreementExclusion`) actually correct?**
  _`SensorConfig` has 13 INFERRED edges - model-reasoned connections that need verification._