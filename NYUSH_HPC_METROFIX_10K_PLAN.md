# NYUSH HPC 10,000-person metro-transfer context package

Last updated: 2026-07-18 (Asia/Shanghai)

## Purpose

Prepare a new 10,000-person CPU/no-LLM context set in which every available
transport option exposes total transfers, line transfers, mode transfers, and
transfer time to the Agent. This is a new structural version, not a replacement
for the accepted Phase 1/2 outputs.

## Scientific boundary

- Seed: 47
- Parent population: 100,000
- Prepared Agent IDs: 1 through 10,000
- Partitions: 10 SQLite files of 1,000 Agents each
- Scenario arms: 10, exactly 1,000 Agents per arm
- CPU: 16
- Memory: 32 GiB
- GPU: 0
- LLM/API/Secret: none
- Incremental API cost: 0
- T10 pre-choice excess flow: 0
- Actual train boarding, capacity competition, dispatch, and endogenous traffic
  remain outside the model.

## Version identity

- GitHub repository: `JennyXi/UrbanCup_2026_Jenny-Yoyo`
- Source branch: `Yoyo`
- GitHub commit: `3e7361f` (`Expose metro transfers to agents`)
- Composite runtime source SHA256:
  `28fb6efa243f9a492214c44cc092112003a3ccb28f028f208d4ef9bd62bfb035`
- Historical Phase 1/2 source SHA256, retained only for comparison:
  `e92f79e11920a98b2e169d4e52befa844028dda2b269cca695f06e8e029aa82f`

The composite hash changed because these two runtime files changed:

- `AgentSociety-local/experiments/urban_github_50_agents.py`
- `UrbanCup_2026_Jenny-Yoyo-reference/custom/transport/network.py`

## Immutable HPC paths

New source root:

```text
/scratch/tz2882/agentsociety_source/urban100k_metrofix_20260718_01
```

New output root:

```text
/scratch/tz2882/agentsociety_urban100k/urban100k_cpu10k_metrofix_20260718_01
```

The job refuses to start if the new output root already exists. It does not
read, modify, resume, or overwrite either historical output:

```text
/scratch/tz2882/agentsociety_urban100k/urban100k_cpu1k_20260718_01
/scratch/tz2882/agentsociety_urban100k/urban100k_cpu10k_20260718_01
```

## Upload artifact

- ZIP: `hpc/upload/urban100k_metrofix_cpu10k_20260718_01.zip`
- ZIP SHA256: recorded in the adjacent sidecar after packaging
- Sidecar: `hpc/upload/urban100k_metrofix_cpu10k_20260718_01.zip.sha256`

The package contains clean source files only; Python bytecode and
`__pycache__` directories are excluded.

## Manual HPC procedure

These commands are for the user to copy to the HPC login shell. Codex must not
SSH to the cluster or submit the job.

```bash
cd /scratch/tz2882
sha256sum urban100k_metrofix_cpu10k_20260718_01.zip
```

Stop if the printed hash differs from this document or the `.sha256` sidecar.

```bash
SOURCE_ROOT=/scratch/tz2882/agentsociety_source/urban100k_metrofix_20260718_01
test ! -e "$SOURCE_ROOT"
mkdir -p "$SOURCE_ROOT"
unzip -q /scratch/tz2882/urban100k_metrofix_cpu10k_20260718_01.zip -d "$SOURCE_ROOT"
```

Read-only preflight:

```bash
test -f "$SOURCE_ROOT/AgentSociety-local/hpc/slurm/urban_100k_metrofix_cpu10k.sbatch.draft"
test -f "$SOURCE_ROOT/AgentSociety-local/hpc/scripts/audit_metrofix_contexts.py"
grep -F 'EXPECTED_CODE_SHA256="28fb6efa243f9a492214c44cc092112003a3ccb28f028f208d4ef9bd62bfb035"' \
  "$SOURCE_ROOT/AgentSociety-local/hpc/slurm/urban_100k_metrofix_cpu10k.sbatch.draft"
test ! -e /scratch/tz2882/agentsociety_urban100k/urban100k_cpu10k_metrofix_20260718_01
```

Only after all preflight commands succeed, the user may manually submit exactly
one job:

```bash
cd "$SOURCE_ROOT/AgentSociety-local"
sbatch hpc/slurm/urban_100k_metrofix_cpu10k.sbatch.draft
```

Do not submit a duplicate while the job is pending or running.

## Acceptance conditions

The job is accepted only if all of the following hold:

- Slurm state `COMPLETED` and exit code `0:0`;
- `METROFIX_SOURCE_AUDIT=PASS`;
- final JSON contains `"status": "METROFIX_CONTEXT_AUDIT_PASS"`;
- exactly 10 context partitions and one manifest;
- exactly 10,000 unique contiguous Agent IDs, 1 through 10,000;
- ten scenario arms, exactly 1,000 Agents each;
- every available mode has non-null `transfers`, `line_transfer_count`,
  `mode_transfer_count`, and `transfer_time_min`;
- `transfers = line_transfer_count + mode_transfer_count` for every available
  option;
- at least one cross-line metro option and at least one feeder-to-metro mode
  transfer are present;
- all SQLite integrity checks and manifest file hashes pass;
- no `.partial` files and no API/LLM/GPU use.

If any condition fails, preserve the fixed output directory and logs for
diagnosis. Do not delete it, resume into it, or switch to another directory
automatically.
