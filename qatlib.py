from enum import Enum

import json
import pathlib
import re
import subprocess
import time
from prettytable import PrettyTable
from pprint import *

INTEL_VENDOR = '0x8086'
QAT_PF_DEVICE_ID_LIST = ["0x4940", "0x4942", "0x4944", "0x4946"]
QAT_VF_DEVICE_ID_LIST = ["0x4941", "0x4943", "0x4945", "0x4947"]

def get_pci_ids(device_id, vendor_id=''):
  args: List[str] = ['lspci', '-d', f'{vendor_id}:{device_id}']
  devices = subprocess.check_output(args, universal_newlines=True).splitlines()
  return [v.split(' ')[0] for v in devices]

def get_vfio(bdf):
  vfio_path = pathlib.Path('/dev/vfio/')
  vfio_files = vfio_path.glob('*')
  for vfio_file in vfio_files:
    vfio_group = vfio_file.name
    if (vfio_group != 'vfio' and vfio_group != 'devices'):
      iommu_path = pathlib.Path(f"/sys/kernel/iommu_groups/{vfio_group}/devices/")
      devices = iommu_path.glob('*')
      for dev_path in devices:
        if dev_path.name == bdf:
          return int(vfio_group)
  return 0

class VFIOGroup(dict):
  def __init__(self, vfio_group, qat_dev):
    self.__setitem__('vfio_dev', f'/dev/vfio/{vfio_group}')
    self.sys_path = pathlib.Path(f"/sys/kernel/iommu_groups/{vfio_group}/devices/{qat_dev.bdf}")
    self.__setitem__('numa_node', self.numa())

  def numa(self):
    path = self.sys_path / "numa_node"
    with path.open() as f:
      data = f.read()
    return data.replace("\n", "")

  def __str__(self):
    return json.dumps(self, indent=2)

class CounterType(Enum):
  UTILIZATION = 'util'
  EXECUTION = 'exec'

class CounterEngine(Enum):
  CIPHER = 'cph'
  AUTHENTICATION = 'ath'
  PUBLIC_KEY_ENCRYPT = 'pke'
  UNIFIED_CRYPTO_SLICE = 'ucs'
  COMPRESSION = 'cpr'
  DECOMPRESSION = 'dcpr'
  TRANSLATOR = 'xlt'

QAT_PF_PCI_DEVICE_IDS = ["4940", "4942", "4944", "4946"]

class DeviceData(dict):
  """
  Parse data from
    /sys/kernel/debug/qat_<device>_<BDF>/telemetry/device_data
  and put it as a dictionary.
  """
  def __init__(self):
    self.regex = re.compile(f'(util|exec)_([a-zA-Z]+)(\\d+)')

  def avg(self,
      counter_type : CounterType,
      engine : CounterEngine):
    values = self.__getitem__(f'{counter_type.value}_{engine.value}')
    return sum(values)/len(values)

  def parse(self, data : str):
    self.clear()
    lines = data.splitlines()
    for l in lines:
      fields = l.split()
      counter_name = fields[0]
      value = fields[1]
      # counter_name start with [util|exec] and ends with number
      # -> slice -> must create an array
      m = self.regex.match(l)
      values = value
      if m:
        counter_name=f'{m.group(1)}_{m.group(2)}'
        #index = int(m.group(3))
        values = self.get(counter_name)
        if not values:
          values = []
        values.append(int(value))

      # filtering
      if not QatDevManager.filter_counter(counter_name):
          continue

      self.__setitem__(counter_name, values)

  def __str__(self):
    return json.dumps(self, indent=2)

class QatDeviceTelemetry(dict):
  def __init__(self, telemetry_path : pathlib.Path):
    self.debugfs_enabled = False
    self.telemetry_path = telemetry_path
    self.debugfs_control_path = self.telemetry_path  / 'control'
    try:
      f = self.debugfs_control_path.open()
      self.debugfs_enabled = True
    except Exception as e:
      print(e)
      pass

    self.__setitem__('device_data', DeviceData())

  def is_debugfs_enabled(self):
    return self.debugfs_enabled

  def debugfs_fn(func):
    def debugfs_wrapper(self):
      if self.is_debugfs_enabled():
        func(self)
    return debugfs_wrapper

  @debugfs_fn
  def enable_telemetry(self):
    telemetry_path = self.telemetry_path  / 'control'
    with telemetry_path.open('w+') as f:
      # 1 to enabled, 2,3,4 enabled and collect 2,3,4 values
      f.write('1\n')

  @debugfs_fn
  def collect(self):
    telemetry_path = self.telemetry_path  / 'device_data'
    lines = []
    with telemetry_path.open() as f:
      data = f.read()
      self.get('device_data').parse(data)

  @debugfs_fn
  def control(self):
    with self.debugfs_control_path.open() as f:
      return f.read()

  def __str__(self):
    return json.dumps(self, indent=2)

class QatDeviceDebugfs(dict):
  def __init__(self, debugfs_path : pathlib.Path):
    self.path = debugfs_path
    self.parser = {}
    files = self.path.glob('*')
    for f in files:
      fname = f
      self.__setitem__(f'f.name', {})

    self.__setitem__('telemetry', QatDeviceTelemetry(self.path / 'telemetry'))
    self.get('telemetry').enable_telemetry()

    self.__setitem__('dev_cfg', self.read('dev_cfg'))

  def read(self, name):
    dev_cfg_path = self.path  / name
    with dev_cfg_path.open() as f:
      return f.read()

  def __str__(self):
    return json.dumps(self, indent=2)

class Qat4xxxDevice():
  def __init__(self,
         device_id,
         pci_id : str,
         virtual=False,
         pf = None):
    self.pci_id = pci_id
    self.bdf = f"0000:{self.pci_id}"
    self.virtual = virtual
    self.pf = pf
    self.sys_path = pathlib.Path(f'/sys/bus/pci/devices/{self.bdf}/')

    if not self.virtual:
      self.debugfs = QatDeviceDebugfs(pathlib.Path(f'/sys/kernel/debug/qat_4xxx_{self.bdf}'))

    # vfio
    if self.virtual:
      self.vfio = None
      vfio_group = get_vfio(self.bdf)
      if vfio_group >= 0:
        self.vfio = VFIOGroup(vfio_group, self)

    # VFs
    self.vfs = []
    if self.virtual:
      return

    vf_device_id = str(int(device_id) + 1)
    pci_ids = get_pci_ids(vf_device_id)
    for pci_id in pci_ids:
      qat_dev = Qat4xxxDevice(vf_device_id, pci_id, virtual=True, pf=self)
      if self.check_vf(qat_dev):
        self.vfs.append(qat_dev)

  def check_vf(self, vf):
    # ed:00.0
    pci_ids=self.pci_id.split(':')
    vf_pci_ids=vf.pci_id.split(':')
    return (pci_ids[0] == vf_pci_ids[0])

  @property
  def cfg_services(self):
    path = self.sys_path / "qat" / "cfg_services"
    with path.open() as f:
      data = f.read()
    return data.replace("\n", "")

  def __repr__(self):
    if self.virtual:
      return f'{self.pci_id}: {self.vfio}'
    else:
      return f'{self.pci_id}: {self.sys_path}: {self.cfg_services}'

class QatDevManager:
  """
  Physical QAT device manager
  """
  counters = None
  def __init__(self, filter_devs = []):
    self.qat_devs = []

    for device_id in QAT_PF_PCI_DEVICE_IDS:
      pci_ids = get_pci_ids(device_id)
      _devs = []
      for pci_desc in pci_ids:
        pci_id = pci_desc.split(' ')[0]
        if (filter_devs is None) or (pci_id in filter_devs):
          _devs.append(Qat4xxxDevice(device_id, pci_id))
      self.qat_devs.extend(_devs)

  def filter_counter(counter_name):
    if (QatDevManager.counters and (counter_name not in QatDevManager.counters)):
      return False
    return True

  def collect_telemetry(self):
    for d in self.qat_devs:
      d.debugfs.get('telemetry').collect()

  def print_telemetry(self):
    for d in self.qat_devs:
      print(d.debugfs.get('telemetry'))

  def print_cfg(self):
    for d in self.qat_devs:
      print(f'BDF: {d.bdf}')
      print("---")
      print("dev_cfg:")
      pprint(d.debugfs.get('dev_cfg'))
      print("vfio:")
      pprint(d.vfs)
