from __future__ import annotations

from slurm_avail.collect import expand_hostlist, parse_filesystems, parse_nodes


def test_expand_hostlist() -> None:
    assert expand_hostlist("gpu[01-02,05],login7") == (
        "gpu01",
        "gpu02",
        "gpu05",
        "login7",
    )


def test_parse_filesystem_posix_kilobytes() -> None:
    filesystems = parse_filesystems("/home|storage 1000 250 750 25% /home\n")

    assert filesystems["/home"].total == 1000 * 1024
    assert filesystems["/home"].available == 750 * 1024


def test_parse_gpu_node_capacity() -> None:
    output = (
        "NodeName=gpu01 CPUTot=64 CPUEfctv=64 CPUAlloc=8 "
        "RealMemory=500000 AllocMem=100000 Gres=gpu:a100:4 "
        "CfgTRES=cpu=64,mem=500000M,gres/gpu=4 "
        "AllocTRES=cpu=8,mem=100000M,gres/gpu=1 "
        "State=MIXED Owner=N/A Partitions=gpu\n"
    )

    nodes = parse_nodes(output, "researcher")

    assert len(nodes) == 1
    node = nodes[0]
    assert node.gpu_total == 4
    assert node.gpu_alloc == 1
    assert node.gpu_free == 3
    assert node.status == "available"


def test_exclusive_node_has_no_reported_free_capacity() -> None:
    output = (
        "NodeName=cpu01 CPUTot=32 CPUAlloc=0 RealMemory=128000 AllocMem=0 "
        "Gres=(null) CfgTRES=cpu=32,mem=128000M AllocTRES= "
        "State=ALLOCATED Owner=another-user Partitions=cpu\n"
    )

    node = parse_nodes(output, "researcher")[0]

    assert node.status == "exclusive"
    assert node.cpu_free == 0
    assert node.mem_free == 0
