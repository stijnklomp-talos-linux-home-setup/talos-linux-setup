# BIOS power settings for new hardware

Goal: minimum idle power + guaranteed auto-start after a power loss. Device-agnostic
checklist — exact menu paths vary per vendor (HP/Dell/Lenovo specifics: `TEMP.md` at the
repo root, where these were applied to all 4 nodes).

## After a power outage (non-negotiable)

| Setting | Value | Why |
|---------|-------|-----|
| AC recovery / "After Power Loss" | **Power On** | Node boots itself after an outage |
| Wake on LAN | Disabled | AC recovery is the boot path; WOL conflicts with low-power modes |

## Idle power

| Setting | Value |
|---------|-------|
| ErP / Enhanced Power Saving / Deep Sleep (S5) | **Enabled** |
| C-states | deepest option (C6/C7/C8/C10…) if exposed |
| Smart Power On / keyboard-mouse power-on | Disabled |
| Intel SpeedStep | Enabled (if exposed) |
| Turbo Mode | Leave enabled (burst perf > saved watts) |

## Unused hardware (headless nodes)

- **Disable**: onboard audio, Bluetooth, WLAN, serial, unused USB ports (keep ≥1 for keyboard/BIOS access)
- **Keep enabled**: the main NIC, and **PXE / boot option ROM** — the zero-touch PXE re-provisioning path (rack doc §10) depends on it; the ~0.1 W saving is not worth it

## Hardware choice

Prefer T-series (low-TDP) CPUs for workers — the Lenovos/Dell run i5-8500T (35 W), the
HP control plane is stuck at 65 W (no TDP adjustment exposed).

## Verify after each device

1. Boots back into Talos and re-joins: `kubectl get nodes` → `Ready`
2. OS-level idle (Talos): low-power kernel args are baked into the Image Factory
   schematics (`schematics/` — `pcie_aspm=force pcie_aspm.policy=power`). Use the
   schematic installer images from the start; `apply-config` alone is ignored.