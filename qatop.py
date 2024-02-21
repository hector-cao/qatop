#!/usr/bin/env python3

from enum import Enum
import pathlib
import re
import subprocess
import time
import urwid as u
from urwid import LineBox

class StateCat(Enum):
    CIPHER = 'cph'
    AUTHENTICATION = 'ath'
    PUBLIC_KEY_ENCRYPT = 'pke'
    UNIFIED_CRYPTO_SLICE = 'ucs'
    COMPRESSION = 'cpr'
    DECOMPRESSION = 'dcpr'
    TRANSLATOR = 'xlt'

def urwid_progressbar_get_text(value, gb):
    def get_text():
        if value is None:
            return 'N/A'
    if value is None:
        gb.get_text = get_text

class PkeStat():
    def __init__(self):
        self.len = 0
        self.util_regex = re.compile('util_pke(\\d+)')
        self.avg = None

    def refresh(self, lines):
        len = 0
        total = 0.0
        self.util_pke = []
        for l in lines:
            m = self.util_regex.match(l)
            if m:
                len = len + 1
                v = int(l.split()[1])
                total = total + v
                self.util_pke.append(v)

        if self.len == 0:
            self.len = len
        assert len == self.len

        self.avg = total / len

class Qat4xxxDevice():
    def __init__(self, desc : str, virtual=False):
        self.virtual = virtual
        self.pci_id = desc.split(' ')[0]
        self.virtual_functions = []
        self.sys_path = pathlib.Path(f'/sys/bus/pci/devices/0000:{self.pci_id}/')
        self.debugfs_path = pathlib.Path(f'/sys/kernel/debug/qat_4xxx_0000:{self.pci_id}')
        try:
            telemetry_path.open('w+')
            self.debugfs_enabled = True
        except:
            self.debugfs_enabled = False
        if self.virtual:
            return
        device = '4941'
        args: List[str] = ['lspci', '-d', f':{device}']
        vfs = subprocess.check_output(args, universal_newlines=True).splitlines()
        for v in vfs:
            qat_dev = Qat4xxxDevice(v, virtual=True)
            if self.check_vf(qat_dev):
                self.virtual_functions.append(qat_dev)

        self.pke = PkeStat()

    def debugfs_fn(func):
        def debugfs_wrapper(self):
            if self.debugfs_enabled:
                self.func()
        return debugfs_wrapper

    @debugfs_fn
    def enable_telemetry(self):
        assert self.virtual == False, "Telemetry only available for PF"
        telemetry_path = self.debugfs_path  / 'telemetry' / 'control'
        with telemetry_path.open('w+') as f:
            # 1 to enabled, 2,3,4 enabled and collect 2,3,4 values 
            f.write('1\n')

    @debugfs_fn
    def collect_telemetry(self):
        telemetry_path = self.debugfs_path  / 'telemetry' / 'device_data'
        with telemetry_path.open() as f:
            data = f.read()
            lines = data.splitlines()
            self.pke.refresh(lines)

    @debugfs_fn
    def telemetry_control(self):
        assert self.virtual == False, "Telemetry only available for PF"
        telemetry_path = self.debugfs_path  / 'telemetry' / 'control'
        with telemetry_path.open() as f:
            return f.read()

    def dev_cfg(self):
        dev_cfg_path = self.debugfs_path  / 'dev_cfg'
        with dev_cfg_path.open() as f:
            return f.read()
        
    def check_vf(self, vf):
        # ed:00.0
        pci_ids=self.pci_id.split(':')
        vf_pci_ids=vf.pci_id.split(':')
        return (pci_ids[0] == vf_pci_ids[0])

    def __repr__(self):
        return f'{self.pci_id}: {self.sys_path}: {self.debugfs_path}'

class QatDevManager:
    def __init__(self):
        device = '4940'
        args: List[str] = ['lspci', '-d', f':{device}']
        devs = subprocess.check_output(args, universal_newlines=True).splitlines()
        self.qat_devs = [Qat4xxxDevice(d) for d in devs]

    def refresh(self):
        for d in self.qat_devs:
            d.collect_telemetry()

class MyListBox(u.ListBox):

    def keypress(self, size, key):
        if key in ('tab',):
            self.focus_position = (self.focus_position + 1) % len(self.body.contents)
            if self.focus_position == 0:
                return key
        else:
            return key

    def selectable(self):
        return len(self.body.contents) > 0

class App(object):

    def handle_key(self, key):
        if key in ('q',):
            raise u.ExitMainLoop()
        if key in ('tab',):
        
            next_focus = (self.columns.focus_position + 1) % len(self.columns.contents)
            self.columns.set_focus(next_focus)

    def __init__(self, qat_manager: QatDevManager):

        self.qat_manager = qat_manager

        self.pbs = []
        cols = []
        for dev in qat_manager.qat_devs:
            pb = u.ProgressBar('','loaded')
            col = u.Columns([('weight', 0.2,
                              u.Text('{}/PKE'.format(dev.pci_id))), LineBox(pb)])
            self.pbs.append(pb)
            cols.append(col)

        self.header = u.Text('qatop')
        self.footer = u.Text('Enter q to exit.')

        frame = u.Frame(u.Filler(
            u.GridFlow(cols, cell_width=130, h_sep=0, v_sep=0, align='left')),
                        header=self.header, footer=self.footer)

        palette = [("loaded", "black", "light cyan")]
        
        loop = u.MainLoop(frame, palette, unhandled_input=self.handle_key)

        loop.set_alarm_in(1, self.refresh)
        loop.run()

    def refresh(self, loop=None, data=None):
        self.qat_manager.refresh()
        i = 0
        for dev in qat_manager.qat_devs:
            self.pbs[i].set_completion(dev.pke.avg)
            i = i + 1
        loop.set_alarm_in(1, self.refresh)

if __name__ == '__main__':
    qat_manager = QatDevManager()
    app = App(qat_manager)
