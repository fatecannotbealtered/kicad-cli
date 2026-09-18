# Hardware Agent Reference Architecture

Status: design proposal. This document defines direction; it does not claim implementation or validation.

## Goal

`kicad-cli` should not become a collection of AI-triggered scripts. The goal is an AI-native hardware engineering interface that approaches how an engineer uses EDA software:

1. Observe the design.
2. Understand constraints and intent.
3. Propose a bounded change.
4. Execute deterministically.
5. Verify the result.
6. Preserve evidence of what changed and why.

The model proposes. Deterministic tools execute. Verification provides evidence.

## Reference architecture

```
AI Agent
   |
   v
Hardware Intent Layer
   |
   v
Action Plan IR
   |
   v
kicad-cli Contract Layer
   |
   +----------------+
   |                |
Query / Analysis   Execution
   |                |
   +----------------+
            |
            v
     Verification Engine
            |
            v
     Evidence Record
```

## Design principles

### 1. No direct file generation by LLM

The agent should not generate `.kicad_pcb` or `.kicad_sch` text directly. File formats contain many implicit relationships. Changes should flow through typed operations with validation.

### 2. Read before write

The primary workflow is:

```
inspect -> query -> plan -> preview -> apply -> verify
```

Write operations require a known target scope and expected outcome.

### 3. Intent is separate from geometry

A request such as "optimize VCORE" is incomplete. The system should represent engineering intent:

- power delivery
- high speed signal
- RF
- thermal
- manufacturing
- assembly

Geometry changes are consequences of intent, not the intent itself.

### 4. Evidence over confidence

Every automated conclusion should distinguish:

- observed facts
- assumptions
- requested constraints
- unverified recommendations

The tool must prefer "unknown, needs review" over unsupported certainty.

## External project lessons

Projects in the public ecosystem show useful patterns:

- MCP-based KiCad tools: granular tools and machine discoverability.
- Agentic KiCad projects: planner/executor separation.
- Permission-oriented MCP designs: progressive disclosure and safer write boundaries.
- Routing projects: backend abstraction instead of one universal router.

The project should borrow patterns, not copy implementations.

## Proposed capability layers

### Observation layer

Examples:

- board snapshot
- object query
- connectivity inspection
- rule inspection
- manufacturing context

### Planning layer

Produces a machine-readable proposal:

```
intent
scope
expected_effect
risk
verification_plan
```

### Execution layer

Only performs bounded, deterministic operations.

### Verification layer

Checks:

- connectivity unchanged where required
- DRC/ERC status
- manufacturing outputs
- requested engineering criteria

## Explicit non-goals

This architecture does not aim to:

- replace a human hardware engineer immediately
- invent electrical requirements from names alone
- automatically relax design rules
- declare a board correct because DRC passes
- replace professional routing algorithms without evidence

## Future direction

The next implementation priorities should be:

1. Complete machine-readable command contracts.
2. Add board intelligence and object query foundations.
3. Introduce design intent metadata.
4. Add plan/apply/verify workflows.
5. Integrate specialized backends where appropriate.
