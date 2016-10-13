#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from os import path
import sys
import pdb
import traceback
import psycopg2
import re
import time
from urllib import request
import subprocess
from shellutils import shell
from psql_firmware import psql

FIRMWARE_DIR = path.dirname(path.dirname(path.realpath(__file__)))
BINARY_DIR=path.join(FIRMWARE_DIR,'binaries')
TARBALL_DIR=path.join(FIRMWARE_DIR,'images')
SCRATCH_DIR=path.join(FIRMWARE_DIR,'scratch')
SCRIPT_DIR=path.join(FIRMWARE_DIR,'scripts')

def gl(localvars):
    d=globals()
    d.update(localvars)
    return d


def get_kernel(archend):
    if archend=='armel':
        return path.join(BINARY_DIR,'zImage.%s'%archend)
    elif archend=='mipsel' or archend=='mipseb':
        return path.join(BINARY_DIR,'vmlinux.%s'%archend)


def get_qemu_machine(archend):
    if archend=='armel':
        return 'virt'
    elif archend=='mipseb' or archend=='mipsel':
        return 'malta'


def get_qemu_disk(archend):
    if archend=='armel':
        return '/dev/vda1'
    elif archend=='mipseb' or archend=='mipsel':
        return '/dev/sda1'


def get_qemu(archend):
    if archend=='armel':
        return 'qemu-system-arm'
    elif archend=='mipseb':
        return 'qemu-system-mips'
    elif archend=='mipsel':
        return 'qemu-system-mipsel'


def get_scratch(iid):
    return path.join(SCRATCH_DIR, '%d'%iid)


def get_fs(iid):
    return path.join(get_scratch(iid),'image.raw')


def closeIp(ip):
    tups = [int(x) for x in ip.split(".")]
    if tups[3] != 1:
        tups[3] -= 1
    else:
        tups[3] = 2
    return ".".join([str(x) for x in tups])


def ifaceNo(dev):
    g = re.match(r"[^0-9]+([0-9]+)", dev)
    return int(g.group(1))


def qemuNetworkConfig(netdev, iid):
    tapdev='tap%d'%iid
    result = ""
    for i in range(0, 4):
        if i == ifaceNo(netdev):
            result += "-net nic,vlan=%(i)d -net tap,vlan=%(i)d,ifname=%(tapdev)s,script=no "%locals()
        else:
            result += "-net nic,vlan=%(i)d -net socket,vlan=%(i)d,listen=:200%(i)d "% locals()
    return result


def get_qemu_cmd_line(iid, archend):
    IMAGE=get_fs(iid)
    KERNEL=get_kernel(archend)
    QEMU=get_qemu(archend)
    QEMU_MACHINE=get_qemu_machine(archend)
    QEMU_ROOTFS=get_qemu_disk(archend)
    WORK_DIR=get_scratch(iid)

    if archend=='mipsel' or archend=='mipseb':
        qemuEnvVars=""
        qemuDisk = "-drive if=ide,format=raw,file={IMAGE}".format(**locals())
    elif archend=='armel':
        qemuEnvVars = "QEMU_AUDIO_DRV=none "
        qemuDisk = "-drive if=none,file={IMAGE},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs".format(**locals())
    netdev = psql("SELECT netdev FROM image WHERE id=%d"%iid)
    if not netdev:
        raise Exception('netdev for id=%(iid)d is empty'%locals())
    
    qemuNetwork=qemuNetworkConfig(netdev, iid)
    return ('''{qemuEnvVars} {QEMU} -m 256 -M {QEMU_MACHINE} -kernel {KERNEL} \
            {qemuDisk} -append "root={QEMU_ROOTFS} console=ttyS0 \
            nandsim.parts=64,64,64,64,64,64,64,64,64,64 rdinit=/preInit.sh rw debug ignore_loglevel \
            print-fatal-signals=1 user_debug=31 firmadyne.syscall=8" \
            -serial file:{WORK_DIR}/qemu.final.serial.log \
            -display none \
            -daemonize \
            {qemuNetwork}'''.format(**locals()))


def try_ip(ip_addr, loopcount=3, timeout=20):
    for _i in range(loopcount):
        try:
            print('test http://%s/'%ip_addr)
            with request.urlopen('http://%s/'%ip_addr, timeout=timeout) as fin:
                return True
        except Exception as ex:
            pass
    return False


def construct(iid):
    archend = psql("SELECT arch FROM image WHERE id=%d"%iid)
    guestip = psql("SELECT guest_ip FROM image WHERE id=%d"%iid)
    netdev = psql("SELECT netdev FROM image WHERE id=%d"%iid)
    netdevip=closeIp(guestip)
    tapdev='tap%d'%iid
    print("Creating TAP device %(tapdev)s..."%locals())
    shell('sudo tunctl -t %(tapdev)s -u $USER'%locals())
    print("Bringing up TAP device...")
    shell('sudo ifconfig %(tapdev)s %(netdevip)s/24 up'%locals())
    print("Adding route to %(guestip)s..."%locals())
    shell('sudo route add -host %(guestip)s gw %(guestip)s %(tapdev)s'%locals())
    print("Starting emulation of firmware... ")
    WORK_DIR=get_scratch(iid)
    shell('sudo rm -f {WORK_DIR}/qemu.final.serial.log'.format(**locals()))
    ret, _ = shell(get_qemu_cmd_line(iid, archend))
    if ret!=0:
        print('failed to launch %s'%get_qemu(archend), file=sys.stderr)
        return False
    return True


def destruct(iid):
    archend = psql("SELECT arch FROM image WHERE id=%d"%iid)
    guestip = psql("SELECT guest_ip FROM image WHERE id=%d"%iid)
    netdev = psql("SELECT netdev FROM image WHERE id=%d"%iid)
    tapdev='tap%d'%iid
    QEMU=get_qemu(archend)
    shell('killall %(QEMU)s'%locals())
    print( "Deleting route...")
    shell('sudo route del -host %(guestip)s gw %(guestip)s %(tapdev)s'%locals())
    print( "Bringing down %(tapdev)s..."%locals())
    shell('sudo ifconfig %(tapdev)s down'%locals())
    print("Deleting TAP device %(tapdev)s... "%locals())
    shell('sudo tunctl -d %(tapdev)s'%locals())
    return True


def main():
    if len(sys.argv)<2:
        print("""
        usage: 
                {0} <IID> [test] 
                {0} <IID> [emulate] 
                {0} <IID> [construct]
                {0} <IID> [destruct]
                """.format(sys.argv[0]))
        return
    iid = int(sys.argv[1])
    purpose = sys.argv[2] if len(sys.argv)>2 else "test"
    if purpose=='test':
        if not construct(iid):
            destruct(iid)
            return False
        guestip = psql("SELECT guest_ip FROM image WHERE id=%d"%iid)
        time.sleep(10)
        network_reachable = try_ip(guestip)
        print('network_reachable=%s'%network_reachable)
        psql("UPDATE image SET network_reachable=%s WHERE id=%s", (network_reachable, iid))
        return destruct(iid)
    elif purpose=='emulate':
        if not construct(iid):
            destruct(iid)
            return False
        guestip = psql("SELECT guest_ip FROM image WHERE id=%d"%iid)
        print('open http://%(guestip)s/'%locals())
        print('press any key to stop')
        input()
        return destruct(iid)
    elif purpose=='construct':
        return construct(iid)
    elif purpose=='destruct':
        return destruct(iid)


if __name__=="__main__":
    main()

