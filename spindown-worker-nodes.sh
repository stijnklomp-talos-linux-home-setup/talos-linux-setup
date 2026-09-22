#!/usr/bin/env bash

set -Eeuo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

CONTROL_PLANE_IP="192.168.1.107"

DRAIN_SCRIPT="$(dirname -- "$(readlink -f -- "$0")")/drain-worker-nodes.yaml"

# How long to wait for workers to become NotReady after shutdown was requested.
WORKER_SHUTDOWN_TIMEOUT=300

# How often to check worker state.
POLL_INTERVAL=5

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

log() {
    printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

error() {
    printf '[%s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

die() {
    error "$*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

get_ready_worker_ips() {
    kubectl get nodes -o json |
        jq -r '
            .items[]
            | select(.metadata.labels["node-role.kubernetes.io/control-plane"] == null)
            | select(any(.status.conditions[]; .type == "Ready" and .status == "True"))
            | .status.addresses[]
            | select(.type == "InternalIP")
            | .address
        ' |
        sort -u
}

# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------

require_command kubectl
require_command jq
require_command talosctl

[[ -x "$DRAIN_SCRIPT" ]] ||
    die "Drain script does not exist or is not executable: $DRAIN_SCRIPT"

# -----------------------------------------------------------------------------
# 1. Drain workers
# -----------------------------------------------------------------------------

log "Draining worker nodes..."
log "Running: $DRAIN_SCRIPT"

if ! "$DRAIN_SCRIPT"; then
    die "Worker drain failed. Control plane will NOT be shut down."
fi

log "Worker drain completed successfully."

# -----------------------------------------------------------------------------
# 2. Find Ready workers
# -----------------------------------------------------------------------------

log "Finding Ready worker nodes..."

mapfile -t WORKER_IPS < <(get_ready_worker_ips)

if (( ${#WORKER_IPS[@]} == 0 )); then
    log "No Ready worker nodes found. Workers may already be shut down."
else
    log "Workers to shut down:"
    printf '  %s\n' "${WORKER_IPS[@]}"

    # -------------------------------------------------------------------------
    # 3. Request worker shutdown
    # -------------------------------------------------------------------------

    log "Sending shutdown requests to workers..."

    for ip in "${WORKER_IPS[@]}"; do
        log "Shutting down worker $ip..."

        if ! talosctl shutdown -n "$ip"; then
            die "Failed to send shutdown request to worker $ip. Control plane will NOT be shut down."
        fi
    done

    log "Shutdown requests sent."
fi

# -----------------------------------------------------------------------------
# 4. Wait until all workers are no longer Ready
# -----------------------------------------------------------------------------

if (( ${#WORKER_IPS[@]} > 0 )); then
    log "Waiting for all workers to become NotReady..."

    deadline=$((SECONDS + WORKER_SHUTDOWN_TIMEOUT))

    while (( SECONDS < deadline )); do
        mapfile -t READY_WORKERS < <(get_ready_worker_ips)

        still_ready=()

        for worker_ip in "${WORKER_IPS[@]}"; do
            if printf '%s\n' "${READY_WORKERS[@]}" | grep -Fxq "$worker_ip"; then
                still_ready+=("$worker_ip")
            fi
        done

        if (( ${#still_ready[@]} == 0 )); then
            log "All requested workers are now NotReady."
            break
        fi

        log "Still waiting for workers: ${still_ready[*]}"
        sleep "$POLL_INTERVAL"
    done

    # Final verification after timeout / successful completion.
    mapfile -t READY_WORKERS < <(get_ready_worker_ips)

    still_ready=()

    for worker_ip in "${WORKER_IPS[@]}"; do
        if printf '%s\n' "${READY_WORKERS[@]}" | grep -Fxq "$worker_ip"; then
            still_ready+=("$worker_ip")
        fi
    done

    if (( ${#still_ready[@]} > 0 )); then
        die "Timed out waiting for workers to shut down: ${still_ready[*]}. Control plane will NOT be shut down."
    fi
fi

# -----------------------------------------------------------------------------
# 5. Shut down control plane
# -----------------------------------------------------------------------------

log "All workers are down."
log "Shutting down control plane $CONTROL_PLANE_IP..."

talosctl shutdown -n "$CONTROL_PLANE_IP"

log "Cluster shutdown complete."
