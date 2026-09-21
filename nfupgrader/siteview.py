"""siteview.py — compose a whole-site view: the local host in full detail and
each peer read-only (tier-1) over ssh, marking unreachable peers. Never writes
to or drives a peer."""
from nfupgrader import localinfo


def gather_site(hosts, local_name, cnc_cnfg_path, local_host, make_peer_host):
    """hosts: list[HostInfo]. local_host: a Host for this box. make_peer_host:
    callable(name) -> a Host (SshHost in production, a fake in tests)."""
    rows = []
    for h in hosts:
        if h.name == local_name:
            info = localinfo.gather(local_host, h.name, cnc_cnfg_path)
            rows.append({"name": h.name, "role": h.role, "is_local": True,
                         "reachable": True, "info": info})
            continue
        peer = make_peer_host(h.name)
        if peer.run(["true"]).rc != 0:
            rows.append({"name": h.name, "role": h.role, "is_local": False,
                         "reachable": False, "info": None})
        else:
            info = localinfo.gather(peer, h.name, cnc_cnfg_path, tier1_only=True)
            rows.append({"name": h.name, "role": h.role, "is_local": False,
                         "reachable": True, "info": info})
    return {"local": local_name, "hosts": rows}
