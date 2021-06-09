#!/usr/bin/python3
#
# Copyright(c) 2021 Intel Corporation
# SPDX-License-Identifier: BSD-3-Clause-Clear
#

import helper

helper.set_spdk_path("/root/spdk/")

cpu0 = helper.Cpu.get_cpu()
cpu0s = cpu0.get_ht_sibling()

app = helper.App("app1")

cache1 = app.make_drive("66:00.0", "Nvme1", parts_num=8, part_size=102400)
cache2 = app.make_drive("e3:00.0", "Nvme2", parts_num=8, part_size=102400)
core1 = app.make_drive("68:00.0", "Nvme0", parts_num=8)
core2 = app.make_drive("65:00.0", "Nvme3", parts_num=8)

app.add_workload("wla", rw="write", bs=4096, iodepth=128, cpu=cpu0,
                devs=zip(cache1.parts[0:8], core1.parts[0:8]))
app.add_workload("wlb", rw="write", bs=4096, iodepth=128, cpu=cpu0s,
                devs=zip(cache2.parts[0:8], core2.parts[0:8]))

app.produce()
