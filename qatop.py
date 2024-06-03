#!/usr/bin/env python3

from enum import Enum
import pathlib
import re
import subprocess
import time
import urwid as u
from urwid import LineBox

class QatDevice():
    def __init__(self,
                 gen : str,
                 desc : str,
                 virtual):
        self.gen = gen
        self.pci_id = desc.split(' ')[0]
        self.virtual = virtual
        self.sys_path = pathlib.Path(f'/sys/bus/pci/devices/0000:{self.pci_id}/')
        self.debugfs_enabled = False
        if not self.virtual:
            self.debugfs_path = pathlib.Path(f'/sys/kernel/debug/qat_{self.gen}_0000:{self.pci_id}')
            self.debugfs_control_path = self.debugfs_path  / 'telemetry' / 'control'
            try:
                f = self.debugfs_control_path.open()
                self.debugfs_enabled = True
            except Exception as e:
                print(e)
                pass

    def is_debugfs_enabled(self):
        return self.debugfs_enabled

class StateCat(Enum):
    CIPHER = 'cph'
    AUTHENTICATION = 'ath'
    PUBLIC_KEY_ENCRYPT = 'pke'
    UNIFIED_CRYPTO_SLICE = 'ucs'
    COMPRESSION = 'cpr'
    DECOMPRESSION = 'dcpr'
    TRANSLATOR = 'xlt'

class PkeStat():
    def __init__(self, qatdev : QatDevice):
        self.qatdev = qatdev
        self.nb_lines = 0
        self.util_regex = re.compile('util_pke(\\d+)')
        self.avg = 0

    def refresh(self, lines):
        """
        lines : empty lines mean no data
        """
        nb_lines = 0
        total = 0.0
        self.util_pke = []

        if len(lines) == 0:
            self.avg = -1
            return

        for l in lines:
            m = self.util_regex.match(l)
            if m:
                nb_lines = nb_lines + 1
                v = int(l.split()[1])
                total = total + v
                self.util_pke.append(v)

        if self.nb_lines == 0:
            self.nb_lines = nb_lines
        assert nb_lines == self.nb_lines

        self.avg = total / nb_lines

class Qat4xxxDevice(QatDevice):
    def __init__(self, desc : str, virtual=False):
        super().__init__('4xxx', desc, virtual)
        self.virtual_functions = []
        if self.virtual:
            return
        device = '4941'
        args: List[str] = ['lspci', '-d', f':{device}']
        vfs = subprocess.check_output(args, universal_newlines=True).splitlines()
        for v in vfs:
            qat_dev = Qat4xxxDevice(v, virtual=True)
            if self.check_vf(qat_dev):
                self.virtual_functions.append(qat_dev)

        self.pke = PkeStat(self)
        self.enable_telemetry()

    def debugfs_fn(func):
        def debugfs_wrapper(self):
            if self.is_debugfs_enabled():
                func(self)
        return debugfs_wrapper

    def enable_telemetry(self):
        assert self.virtual == False, "Telemetry only available for PF"
        telemetry_path = self.debugfs_path  / 'telemetry' / 'control'
        with telemetry_path.open('w+') as f:
            # 1 to enabled, 2,3,4 enabled and collect 2,3,4 values 
            f.write('1\n')

    @debugfs_fn
    def collect_telemetry(self):
        telemetry_path = self.debugfs_path  / 'telemetry' / 'device_data'
        lines = []
        with telemetry_path.open() as f:
            data = f.read()
            lines = data.splitlines()
        self.pke.refresh(lines)

    @debugfs_fn
    def telemetry_control(self):
        assert self.virtual == False, "Telemetry only available for PF"
        with self.debugfs_control_path.open() as f:
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

class CustomProgressBar(u.ProgressBar):
    def __init__(self, dev, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dev = dev

    def get_text(self):
        if self.current < 0:
            return f"Not available (Missing : {self.dev.debugfs_path})"
        return super().get_text()

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
            pb = CustomProgressBar(dev, '', 'loaded')
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
