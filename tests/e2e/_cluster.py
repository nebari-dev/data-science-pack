"""kind cluster + chart lifecycle for the e2e harness.

Hides:
  - kind CLI invocations + kubeconfig re-export when reusing a cluster
  - helm-install timing
  - kind-specific NFS DNS workaround (host /etc/hosts hack)
  - hub+proxy readiness wait
  - force-cleanup fallback when `kind delete` fails (Docker Desktop
    occasionally hangs on container removal).
"""

import logging
import os
import shutil
import subprocess
import time

import pytest

from _process import NAMESPACE, kctl, kctl_out, run, step

log = logging.getLogger("e2e")


def require_binaries(*names):
    for n in names:
        if not shutil.which(n):
            pytest.exit(f"{n} not found on PATH", returncode=2)


def cluster_exists(name):
    cp = run("kind", "get", "clusters", check=False, quiet=True)
    return name in (cp.stdout or "").splitlines()


def ensure_cluster(name):
    """Create a kind cluster or attach to an existing one. Returns True if
    we created it (caller is responsible for teardown)."""
    if cluster_exists(name):
        # `kind delete` can wipe the kubeconfig entry but leave the
        # container — re-export so kubectl/helm can reach the cluster.
        run("kind", "export", "kubeconfig", "--name", name, quiet=True)
        return False
    run("kind", "create", "cluster", "--name", name, "--wait", "60s")
    return True


def teardown_cluster(name):
    """Best-effort delete with force-cleanup fallback.

    Docker Desktop occasionally fails to kill a container with active
    mounts ("did not receive an exit event"). We force-stop+rm orphan
    node containers so the next run starts clean.
    """
    cp = run("kind", "delete", "cluster", "--name", name,
             check=False, quiet=True)
    if cp.returncode == 0:
        return
    log.warning("kind delete failed; force-removing node containers")
    cp2 = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}-",
         "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    for container in (cp2.stdout or "").strip().splitlines():
        subprocess.run(["docker", "stop", "--time=0", container],
                       capture_output=True)
        subprocess.run(["docker", "rm", "-f", "-v", container],
                       capture_output=True)
    subprocess.run(["docker", "network", "rm", "kind"],
                   capture_output=True)


def helm_install(release, chart_dir, values_file):
    run("helm", "dependency", "update")
    t0 = time.time()
    run("helm", "upgrade", "--install", release, chart_dir,
        "--namespace", NAMESPACE,
        "--set", "nebariapp.enabled=false",
        "--values", str(values_file))
    log.info("helm install completed in %ds", int(time.time() - t0))


def patch_nfs_hosts_entry(release, cluster_name):
    """Append `<NFS-svc-ClusterIP> <NFS-svc-FQDN>` to each kind node's
    /etc/hosts.

    kubelet's mount.nfs runs in the host mount namespace using the host's
    /etc/resolv.conf, which on kind only knows Docker DNS — cluster-internal
    FQDNs don't resolve. Patching the PV's `nfs.server` to the IP would
    work but is rejected as immutable once the PV is Bound. Hosts entry
    is post-bind, idempotent, and bypasses DNS entirely.

    Production clusters don't need this — their kubelets have cluster DNS.
    """
    # On a freshly-helm-installed cluster the PV controller can take
    # 20-60s to bind the NFS PV. Poll up to ~90s; each kctl_out call
    # has its own 10s default timeout (kubectl itself is allowed to
    # stall while the API server is still warming up on cold kind).
    pv_name = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            rc, pv_name, _ = kctl_out(
                "get", "pv", "-l", f"app.kubernetes.io/instance={release}",
                "-o", "jsonpath={.items[?(@.spec.nfs)].metadata.name}",
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            pv_name = ""
        if pv_name:
            break
        time.sleep(2)
    if not pv_name:
        log.info("no NFS-backed PV found after 90s — skipping hosts entry")
        return

    _, fqdn, _ = kctl_out("get", "pv", pv_name,
                          "-o", "jsonpath={.spec.nfs.server}")
    svc_name = fqdn.split(".", 1)[0]
    _, svc_ip, err = kctl_out("get", "svc", svc_name,
                              "-o", "jsonpath={.spec.clusterIP}")
    if not svc_ip:
        log.error("could not resolve NFS svc %s ClusterIP: %s", svc_name, err)
        return

    cp = subprocess.run(
        ["kind", "get", "nodes", "--name", cluster_name],
        capture_output=True, text=True, check=True,
    )
    for node in cp.stdout.strip().splitlines():
        cmd = (f"grep -q '{fqdn}$' /etc/hosts || "
               f"echo '{svc_ip} {fqdn}' >> /etc/hosts")
        log.info("hosts entry on %s: %s -> %s", node, fqdn, svc_ip)
        subprocess.run(["docker", "exec", node, "sh", "-c", cmd], check=True)


def wait_for_hub(timeout_s=300, poll_s=5):
    """Poll until JupyterHub's hub + proxy pods are Ready."""
    deadline = time.time() + timeout_s
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        elapsed = int(timeout_s - (deadline - time.time()))
        log.info("hub-wait attempt=%d elapsed=%ds", attempt, elapsed)
        kctl("get", "pods")
        kctl("logs", "-l", "component=hub", "--tail=5", check=False)
        if _component_ready("hub") and _component_ready("proxy"):
            log.info("hub+proxy ready after %ds", elapsed)
            return
        time.sleep(poll_s)
    log.error("timeout after %ds; dumping last 200 hub log lines", timeout_s)
    kctl("logs", "-l", "component=hub", "--tail=200", check=False)
    pytest.fail(f"hub/proxy not ready within {timeout_s}s")


def _component_ready(component):
    cp = run("kubectl", "wait", "--for=condition=ready", "pod",
             "-l", f"component={component}", "-n", NAMESPACE,
             "--timeout=2s", check=False, quiet=True)
    return cp.returncode == 0
