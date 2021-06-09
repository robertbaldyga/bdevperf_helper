#
# Copyright(c) 2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause-Clear
#

from pathlib import Path
import attr
import json
import subprocess


def static_init(cls):
    if getattr(cls, "__static_init__", None):
        cls.__static_init__(cls)
    return cls


spdk_path = None
def set_spdk_path(path):
    global spdk_path
    spdk_path = Path(path)


@attr.s
class Job:
    name = attr.ib()
    rw = attr.ib(kw_only=True, default=None)
    bs = attr.ib(kw_only=True, default=None)
    iodepth = attr.ib(kw_only=True, default=None)
    zipf_theta = attr.ib(kw_only=True, default=None)
    filename = attr.ib(kw_only=True)
    cpumask = attr.ib(kw_only=True)


@attr.s
class Workload:
    name = attr.ib()
    rw = attr.ib(kw_only=True)
    bs = attr.ib(kw_only=True)
    iodepth = attr.ib(kw_only=True)
    zipf_theta = attr.ib(kw_only=True, default=None)
    cpu = attr.ib(kw_only=True)
    devs = attr.ib(kw_only=True)


@attr.s
class Drive:
    addr = attr.ib()
    name = attr.ib()
    parts_num = attr.ib(kw_only=True)
    part_size = attr.ib(kw_only=True, default=None)

    def __attrs_post_init__(self):
        self.parts = [f"{self.name}n1p{i}" for i in range(self.parts_num)]


@static_init
@attr.s(hash=True)
class Cpu:
    cpu_id = attr.ib()

    def __static_init__(cls):
        cpus_num = int(subprocess.Popen(['nproc'],stdout=subprocess.PIPE).stdout.readline())
        cls.free_cpus = set()
        for cpu_id in range(cpus_num):
            cls.free_cpus.add(cls(cpu_id))
        cls.cpus = []

    def __attrs_post_init__(self):
        sys_cpu = Path("/sys/devices/system/cpu/")
        tsl_raw = (sys_cpu / f"cpu{self.cpu_id}/topology/thread_siblings_list").read_text()
        self.tsl = [int(c) for c in tsl_raw.strip().split(',')]

    def __eq__(self, other):
         return self.tsl == other.tsl

    def __hash__(self):
        return sum(1<<c for c in self.tsl)

    @classmethod
    def get_cpu(cls):
        cpu = cls.free_cpus.pop()
        if not cpu:
            return None
        cls.cpus.append(cpu)
        return cpu

    def get_ht_sibling(self):
        if len(self.tsl) == 1:
            return None
        sibling = Cpu(self.tsl[1]) if self.tsl[0] == self.cpu_id else Cpu(self.tsl[0])
        self.__class__.cpus.append(sibling)
        return sibling


@attr.s
class OCF:
        name = attr.ib(kw_only=True)
        cache = attr.ib(kw_only=True)
        core = attr.ib(kw_only=True)
        cpu = attr.ib(kw_only=True)
        line_size = attr.ib(kw_only=True)
        mode = attr.ib(kw_only=True)

class App:
    def __init__(self, name):
        self.name = name
        self.drives = []
        self.workloads = []

    def make_drive(self, *args, **kwargs):
        drive = Drive(*args, **kwargs)
        self.drives.append(drive)
        return drive

    def add_workload(self, *args, **kwargs):
        workload = Workload(*args, **kwargs)
        self.workloads.append(workload)
        return workload

    def produce(self):
        bdev_config = []
        for d in self.drives:
            bdev_config.append({
                "method": "bdev_nvme_attach_controller",
                "params": {
                    "name": d.name,
                    "trtype": "PCIe",
                    "traddr": d.addr
                }
            })
            split_cmd = {
                "method": "bdev_split_create",
                "params": {
                    "base_bdev": f"{d.name}n1",
                    "split_count": d.parts_num
                }
            }
            if d.part_size:
                split_cmd["params"]["split_size_mb"] = d.part_size
            bdev_config.append(split_cmd)

        jobs = []
        ocfs = []
        for w in self.workloads:
            for cache, core in w.devs:
                ocf_name = f"{cache}_ocf"
                ocfs.append(OCF(name=ocf_name, cache=cache, core=core, cpu=w.cpu,
                                line_size=64, mode="wb"))
                jobs.append(Job(f"{w.name}_{ocf_name}", rw=w.rw, bs=w.bs,
                            iodepth=w.iodepth, zipf_theta=w.zipf_theta,
                            filename=ocf_name, cpumask=f"[{w.cpu.cpu_id}]"))

        for ocf in ocfs:
            bdev_config.append({
                "method": "bdev_ocf_create",
                "params": {
                    "name": ocf.name,
                    "mode": ocf.mode,
                    "cache_line_size": ocf.line_size,
                    "cache_bdev_name": ocf.cache,
                    "core_bdev_name": ocf.core,
                    "cpu_mask": f"[{ocf.cpu.cpu_id}]",
                    "create": True,
                    "force": True
                }
            })

        spdk_config = {"subsystems":[{"subsystem": "bdev", "config": bdev_config}]}

        bdevperf_config = ["[global]\n"]
        for j in jobs:
            bdevperf_config.append(f"[{j.name}]")
            bdevperf_config.append(f"rw={j.rw}")
            bdevperf_config.append(f"bs={j.bs}")
            bdevperf_config.append(f"iodepth={j.iodepth}")
            if j.zipf_theta:
                bdevperf_config.append(f"zipf_theta={j.zipf_theta}")
            bdevperf_config.append(f"filename={j.filename}")
            bdevperf_config.append(f"cpumask={j.cpumask}")
            bdevperf_config.append(f"")

        if not spdk_path:
            print("SPDK path not set!")
            exit(1)

        with open(spdk_path / f"{self.name}_spdk_config.json", 'w') as sc:
            json.dump(spdk_config, sc, indent=4)

        with open(spdk_path / f"{self.name}_bdevperf_config.ini", 'w') as bc:
            bc.write("\n".join(bdevperf_config))

        cpumask = 0
        for c in Cpu.cpus:
            cpumask |= 1 << c.cpu_id

        run_cmd = f"./test/bdev/bdevperf/bdevperf " + \
                    f"-c {self.name}_spdk_config.json " + \
                    f"-j {self.name}_bdevperf_config.ini " + \
                    f"-m {hex(cpumask)} -r /var/tmp/spdk.sock -t 300"
        print(run_cmd)
