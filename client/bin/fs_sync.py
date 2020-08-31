#!/usr/bin/python2

# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Sync all SCSI (USB/SATA), NVMe, and eMMC devices.  All logging is via
stdout and stderr, to avoid creating new disk writes on the DUT that would
then need to be synced.

If --freeze is set, this will also block writes to the stateful partition,
to ensure the disk is in a consistent state before a hard reset.
"""


import argparse
import collections
import logging
import logging.handlers
import os
import subprocess
import sys


STATEFUL_MOUNT = '/mnt/stateful_partition'
ENCSTATEFUL_DEV = '/dev/mapper/encstateful'
ENCSTATEFUL_MOUNT = '/mnt/stateful_partition/encrypted'


Result = collections.namedtuple('Result', ['command', 'rc', 'stdout', 'stderr'])


def run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
    """Run the given command, and return a Result (namedtuple) for it.

    @param cmd: the command to run
    @param stdout: an open file to capture stdout in, or subprocess.PIPE
    @param stderr: an open file to capture stderr in, or subprocess.PIPE
    @type stdout: file | int | None
    @type stderr: file | int | None
    """
    logging.info("+ %s", cmd)

    proc = subprocess.Popen(cmd, shell=True, stdout=stdout, stderr=stderr)
    (stdout, stderr) = proc.communicate()

    if stdout:
        logging.debug('    stdout: %s', repr(stdout))
    if stderr:
        logging.debug('    stderr: %s', repr(stderr))
    logging.debug('    rc: %s', proc.returncode)
    return Result(cmd, proc.returncode, stdout, stderr)


def run_background(cmd):
    """Run a command in the background, with stdout, and stderr detached."""
    logging.info("+ %s &", cmd)
    with open(os.devnull, 'w') as null:
        subprocess.Popen(cmd, shell=True, stdout=null, stderr=null)


def _freeze_fs(fs):
    """Run fsfreeze --freeze or --unfreezeto block writes.

    @param fs: the mountpoint path of the filesystem to freeze
    """
    # ioctl: FIFREEZE
    logging.warn("FREEZING THE FILESYSTEM: %s", fs)
    run('fsfreeze --freeze %s' % fs)


def _unfreeze_fs_later(fs):
    """ Trigger a background (stdin/out/err closed) run of unfreeze later.

    In case a test dies after freeze, this should prevent the freeze from
    breaking the repair logic for a long time.

    @param fs: the mountpoint path of the filesystem to unfreeze
    """
    # ioctl: FITHAW
    run_background('sleep 120 && fsfreeze --unfreeze %s' % fs)


def _flush_blockdev(device):
    """Run /sbin/blockdev to flush buffers"""
    # ioctl: BLKFLSBUF
    run('blockdev --flushbufs %s' % device)


def _do_blocking_sync(device):
    """Run a blocking sync command.

    'sync' only sends SYNCHRONIZE_CACHE but doesn't check the status.
    This function will perform a device-specific sync command.

    @param device: String name (/dev/sda, /dev/nvme0, etc.) to sync
    """
    if 'mmcblk' in device:
        # For mmc devices, use `mmc status get` command to send an
        # empty command to wait for the disk to be available again.

        # flush mmcblk0 and mmcblk0p1, mmcblk0p2, ...
        _flush_blockdev('%s %sp*' % (device, device))

        # mmc status get <device>: Print the response to STATUS_SEND (CMD13)
        # ioctl: MMC_IOC_CMD, <hex value>
        run('mmc status get %s' % device)

    elif 'nvme' in device:
        # For NVMe devices, use `nvme flush` command to commit data
        # and metadata to non-volatile media.

        # flush nvme0n*: nvme0n1, nvme0n1p1, nvme0n1p2, ...
        _flush_blockdev('%sn*' % device)

        # Get a list of NVMe namespaces, and flush them individually.
        # The output is assumed to be in the following format:
        # [ 0]:0x1
        # [ 1]:0x2
        list_result = run("nvme list-ns %s" % device)
        available_ns = list_result.stdout.strip()

        if not available_ns:
            logging.warn("Listing namespaces failed (empty output): %s",
                         list_result)

        for ns in available_ns.splitlines():
            ns = ns.split(':')[-1]

            # ioctl NVME_IOCTL_IO_CMD, <hex value>
            flush_result = run('nvme flush %s -n %s' % (device, ns))

            if flush_result.rc != 0:
                logging.info("Warning: Flushing namespace %s failed:\n%s",
                             ns, flush_result)

    elif 'sd' in device:
        # For other devices, use hdparm to attempt a sync.

        # flush sda*: sda, sda1, sda2, ...
        _flush_blockdev('%s*' % device)

        # -f  Flush buffer cache for device on exit
        #   ioctl: BLKFLSBUF: flush buffer cache
        #   ioctl: HDIO_DRIVE_CMD(0): wait for flush complete (unsupported)
        run('hdparm --verbose -f %s' % device, stderr=subprocess.PIPE)

        # -F  Flush drive write cache (unsupported on many flash drives)
        #   ioctl: SG_IO, ata_op=0xec (ATA_OP_IDENTIFY)
        #   ioctl: SG_IO, ata_op=0xea (ATA_OP_FLUSHCACHE_EXT)
        # run('hdparm --verbose -F %s' % device, stderr=subprocess.PIPE)

    else:
        logging.warn("Unhandled device type: %s", device)
        _flush_blockdev(device)


def blocking_sync(freeze=False):
    """Sync all known disk devices.  If freeze is True, also block writes."""

    # Reverse alphabetical order, to give USB more time: sd*, nvme*, mmcblk*
    ls_result = run('ls /dev/mmcblk? /dev/nvme?n? /dev/sd? | sort -r')

    devices = ls_result.stdout.splitlines()
    if freeze:
        description = 'Syncing and freezing device(s)'
    else:
        description = 'Syncing device(s)'
    logging.info('%s: %s', description, ', '.join(devices) or '(none?)')

    # The double call to sync fakes a blocking call.
    # The first call returns before the flush is complete,
    # but the second will wait for the first to finish.
    run('sync && sync')

    if freeze:
        _unfreeze_fs_later(ENCSTATEFUL_MOUNT)
        _freeze_fs(ENCSTATEFUL_MOUNT)
        _flush_blockdev(ENCSTATEFUL_DEV)

        _unfreeze_fs_later(STATEFUL_MOUNT)
        _freeze_fs(STATEFUL_MOUNT)
        # No need to figure out which partition is the stateful one,
        # because _do_blocking_sync syncs every partition.

    else:
        _flush_blockdev(ENCSTATEFUL_DEV)

    for dev in devices:
        _do_blocking_sync(dev)


def main():
    """Main method (see module docstring for purpose of this script)"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', '--for-reset', '--block-writes',
                        dest='freeze', action='store_true',
                        help='Block writes to prepare for hard reset.')

    logging.root.setLevel(logging.NOTSET)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-5.5s| %(message)s'))
    logging.root.addHandler(stdout_handler)

    opts = parser.parse_args()
    blocking_sync(freeze=opts.freeze)


if __name__ == '__main__':
    sys.exit(main())
