# aurora_driver.py
#
# A weewx driver for the Power One Aurora PVI-6000 inverter.
#
# Copyright (C) 2016 Gary Roderick                  gjroderick<at>gmail.com
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see http://www.gnu.org/licenses/.
#
# Version: 0.2.0                                    Date: 31 January 2017
#
# Revision History
#   31 January 2017     v0.2.0      - no longer use the aurora application for
#                                     interrogating the inverter, communication
#                                     with the inverter is now performed
#                                     directly via the AuroraInverter class
#   1 January 2017      v0.1.0      - initial release
#
""" A weewx driver for the Power One Aurora PVI-6000 inverter."""

from __future__ import with_statement
import binascii
import serial
import struct
import syslog
import time


# weewx imports
import weewx.drivers

from weeutil.weeutil import timestamp_to_string, option_as_list, to_bool

# our name and version number
DRIVER_NAME = 'Aurora'
DRIVER_VERSION = '0.2.0'


def logmsg(level, msg):
    syslog.syslog(level, 'aurora: %s' % msg)


def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)


def logdbg2(msg):
    if weewx.debug >= 2:
        logmsg(syslog.LOG_DEBUG, msg)


def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)


def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)


def loader(config_dict, engine):  # @UnusedVariable
    return AuroraDriver(config_dict[DRIVER_NAME])


# ============================================================================
#                           Aurora Error classes
# ============================================================================


class DataFormatError(StandardError):
    """Exception raised when an error is thrown when processing data being sent
       to or from the inverter."""


# ============================================================================
#                            class AuroraDriver
# ============================================================================


class AuroraDriver(weewx.drivers.AbstractDevice):
    """Class representing connection to Aurora inverter."""

    # default field map
    DEFAULT_MAP = {'string1Voltage':  'STR1-V',
                   'string1Current':  'STR1-C',
                   'string1Power':    'STR1-P',
                   'string2Voltage':  'STR2-V',
                   'string2Current':  'STR2-C',
                   'string2Power':    'STR2-P',
                   'gridVoltage':     'Grid-V',
                   'gridCurrent':     'Grid-C',
                   'gridPower':       'Grid-P',
                   'gridFrequency':   'Grid-Hz',
                   'efficiency':      'DcAcCvrEff',
                   'inverterTemp':    'InvTemp',
                   'boosterTemp':     'EnvTemp',
                   'bulkVoltage':     'Bulk-V',
                   'isoResistance':   'IsoRes',
                   'in1Power':        'Pin1-W',
                   'in2Power':        'Pin2-W',
                   'bulkmidVoltage':  'BilkM-V',
                   'bulkdcVoltage':   'Bulk-DC',
                   'leakdcCurrent':   'Leak-DC',
                   'leakCurrent':     'Leak-C',
                   'griddcVoltage':   'GridV-DC',
                   'gridavgVoltage':  'GridAvg-V',
                   'gridnVoltage':    'GridN-V',
                   'griddcFrequency': 'GridDC-Hz',
                   'dayEnergy':       'DailyEnergy'
                  }

    # transmission state code map
    TRANSMISSION = {0: 'Everything is OK',
                    51: 'Command is not implemented',
                    52: 'Variable does not exist',
                    53: 'Variable value is out of range',
                    54: 'EEprom not accessible',
                    55: 'Not Toggled Service Mode',
                    56: 'Can not send the command to internal micro',
                    57: 'Command not Executed',
                    58: 'The variable is not available, retry'
                   }

    # inverter system module state code maps

    # global state
    GLOBAL = {0: 'Sending Parameters',
              1: 'Wait Sun/Grid',
              2: 'Checking Grid',
              3: 'Measuring Riso',
              4: 'DcDc Start',
              5: 'Inverter Start',
              6: 'Run',
              7: 'Recovery',
              8: 'Pause',
              9: 'Ground Fault',
              10: 'OTH Fault',
              11: 'Address Setting',
              12: 'Self Test',
              13: 'Self Test Fail',
              14: 'Sensor Test + Meas.Riso',
              15: 'Leak Fault',
              16: 'Waiting for manual reset ',
              17: 'Internal Error E026',
              18: 'Internal Error E027',
              19: 'Internal Error E028',
              20: 'Internal Error E029',
              21: 'Internal Error E030',
              22: 'Sending Wind Table',
              23: 'Failed Sending table',
              24: 'UTH Fault',
              25: 'Remote OFF',
              26: 'Interlock Fail',
              27: 'Executing Autotest',
              30: 'Waiting Sun',
              31: 'Temperature Fault',
              32: 'Fan Stacked',
              33: 'Int. Com. Fault',
              34: 'Slave Insertion',
              35: 'DC Switch Open',
              36: 'TRAS Switch Open',
              37: 'MASTER Exclusion',
              38: 'Auto Exclusion ',
              98: 'Erasing Internal EEprom',
              99: 'Erasing External EEprom',
              100: 'Counting EEprom',
              101: 'Freeze'
             }

    # inverter state
    INVERTER = {0: 'Stand By',
                1: 'Checking Grid',
                2: 'Run',
                3: 'Bulk OV',
                4: 'Out OC',
                5: 'IGBT Sat',
                6: 'Bulk UV',
                7: 'Degauss Error',
                8: 'No Parameters',
                9: 'Bulk Low',
                10: 'Grid OV',
                11: 'Communication Error',
                12: 'Degaussing',
                13: 'Starting',
                14: 'Bulk Cap Fail',
                15: 'Leak Fail',
                16: 'DcDc Fail',
                17: 'Ileak Sensor Fail',
                18: 'SelfTest: relay inverter',
                19: 'SelfTest: wait for sensor test',
                20: 'SelfTest: test relay DcDc + sensor',
                21: 'SelfTest: relay inverter fail',
                22: 'SelfTest timeout fail',
                23: 'SelfTest: relay DcDc fail',
                24: 'Self Test 1',
                25: 'Waiting self test start',
                26: 'Dc Injection',
                27: 'Self Test 2',
                28: 'Self Test 3',
                29: 'Self Test 4',
                30: 'Internal Error',
                31: 'Internal Error',
                40: 'Forbidden State',
                41: 'Input UC',
                42: 'Zero Power',
                43: 'Grid Not Present',
                44: 'Waiting Start',
                45: 'MPPT',
                46: 'Grid Fail',
                47: 'Input OC'
               }

    # DC/DC channel states
    DCDC = {0: 'DcDc OFF',
            1: 'Ramp Start',
            2: 'MPPT',
            3: 'Not Used',
            4: 'Input OC',
            5: 'Input UV',
            6: 'Input OV',
            7: 'Input Low',
            8: 'No Parameters',
            9: 'Bulk OV',
            10: 'Communication Error',
            11: 'Ramp Fail',
            12: 'Internal Error',
            13: 'Input mode Error',
            14: 'Ground Fault',
            15: 'Inverter Fail',
            16: 'DcDc IGBT Sat',
            17: 'DcDc ILEAK Fail',
            18: 'DcDc Grid Fail',
            19: 'DcDc Comm Error'
           }

    # alarm states
    ALARM = {0:  {'description': 'No Alarm',          'code': None},
             1:  {'description': 'Sun Low',           'code': 'W001'},
             2:  {'description': 'Input OC',          'code': 'E001'},
             3:  {'description': 'Input UV',          'code': 'W002'},
             4:  {'description': 'Input OV',          'code': 'E002'},
             5:  {'description': 'Sun Low',           'code': 'W001'},
             6:  {'description': 'No Parameters',     'code': 'E003'},
             7:  {'description': 'Bulk OV',           'code': 'E004'},
             8:  {'description': 'Comm.Error',        'code': 'E005'},
             9:  {'description': 'Output OC',         'code': 'E006'},
             10: {'description': 'IGBT Sat',          'code': 'E007'},
             11: {'description': 'Bulk UV',           'code': 'W011'},
             12: {'description': 'Internal error',    'code': 'E009'},
             13: {'description': 'Grid Fail',         'code': 'W003'},
             14: {'description': 'Bulk Low',          'code': 'E010'},
             15: {'description': 'Ramp Fail',         'code': 'E011'},
             16: {'description': 'Dc/Dc Fail',        'code': 'E012'},
             17: {'description': 'Wrong Mode',        'code': 'E013'},
             18: {'description': 'Ground Fault',      'code': '---'},
             19: {'description': 'Over Temp.',        'code': 'E014'},
             20: {'description': 'Bulk Cap Fail',     'code': 'E015'},
             21: {'description': 'Inverter Fail',     'code': 'E016'},
             22: {'description': 'Start Timeout',     'code': 'E017'},
             23: {'description': 'Ground Fault',      'code': 'E018'},
             24: {'description': 'Degauss error',     'code': '---'},
             25: {'description': 'Ileak sens.fail',   'code': 'E019'},
             26: {'description': 'DcDc Fail',         'code': 'E012'},
             27: {'description': 'Self Test Error 1', 'code': 'E020'},
             28: {'description': 'Self Test Error 2', 'code': 'E021'},
             29: {'description': 'Self Test Error 3', 'code': 'E019'},
             30: {'description': 'Self Test Error 4', 'code': 'E022'},
             31: {'description': 'DC inj error',      'code': 'E023'},
             32: {'description': 'Grid OV',           'code': 'W004'},
             33: {'description': 'Grid UV',           'code': 'W005'},
             34: {'description': 'Grid OF',           'code': 'W006'},
             35: {'description': 'Grid UF',           'code': 'W007'},
             36: {'description': 'Z grid Hi',         'code': 'W008'},
             37: {'description': 'Internal error',    'code': 'E024'},
             38: {'description': 'Riso Low',          'code': 'E025'},
             39: {'description': 'Vref Error',        'code': 'E026'},
             40: {'description': 'Error Meas V',      'code': 'E027'},
             41: {'description': 'Error Meas F',      'code': 'E028'},
             42: {'description': 'Error Meas Z',      'code': 'E029'},
             43: {'description': 'Error Meas Ileak',  'code': 'E030'},
             44: {'description': 'Error Read V',      'code': 'E031'},
             45: {'description': 'Error Read I',      'code': 'E032'},
             46: {'description': 'Table fail',        'code': 'W009'},
             47: {'description': 'Fan Fail',          'code': 'W010'},
             48: {'description': 'UTH',               'code': 'E033'},
             49: {'description': 'Interlock fail',    'code': 'E034'},
             50: {'description': 'Remote Off',        'code': 'E035'},
             51: {'description': 'Vout Avg error',    'code': 'E036'},
             52: {'description': 'Battery low',       'code': 'W012'},
             53: {'description': 'Clk fail',          'code': 'W013'},
             54: {'description': 'Input UC',          'code': 'E037'},
             55: {'description': 'Zero Power',        'code': 'W014'},
             56: {'description': 'Fan Stuck',         'code': 'E038'},
             57: {'description': 'DC Switch Open',    'code': 'E039'},
             58: {'description': 'Tras Switch Open',  'code': 'E040'},
             59: {'description': 'AC Switch Open',    'code': 'E041'},
             60: {'description': 'Bulk UV',           'code': 'E042'},
             61: {'description': 'Autoexclusion',     'code': 'E043'},
             62: {'description': 'Grid df/dt',        'code': 'W015'},
             63: {'description': 'Den switch Open',   'code': 'W016'},
             64: {'description': 'Jbox fail',         'code': 'W017'}
            }

    def __init__(self, aurora_dict):
        """Initialise an object of type AuroroaDriver."""

        self.model = aurora_dict.get('model', 'Aurora')
        logdbg('%s driver version is %s' % (self.model, DRIVER_VERSION))
        self.port = aurora_dict.get('port', '/dev/ttyUSB0')
        self.max_tries = int(aurora_dict.get('max_tries', 3))
        self.polling_interval = int(aurora_dict.get('loop_interval', 10))
        logdbg('inverter will be polled on port %s every %d seconds' % (self.port,
                                                                        self.polling_interval))
        self.address = int(aurora_dict.get('address', 2))
        self.use_inverter_time = to_bool(aurora_dict.get('use_inverter_time',
                                                         False))

        # get an AuroraInverter object
        self.inverter = AuroraInverter(self.port)
        # open up the connection to the inverter
        self.openPort()

        # set a number of properties based on system data from the inverter
        self._setup()

        # initialise last energy value
        self.last_energy = None

        # Build the manifest of readings to be included in the loop packet.
        # Build the Aurora reading to loop packet field map.
        (self.field_map, self.manifest) = self._build_map_manifest(aurora_dict)

    def openPort(self):
        """Open up the connection to the inverter."""

        self.inverter.open_port()

    def closePort(self):
        """Close the connection to the inverter."""

        self.inverter.close_port()

    def genLoopPackets(self):
        """Generator function that returns 'loop' packets.

        Poll the inverter every self.polling_interval seconds and generate a
        loop packet. Sleep between loop packets.
        """

        while int(time.time()) % self.polling_interval != 0:
            time.sleep(0.2)
        for count in range(self.max_tries):
            while True:
                try:
                    # get the current time as timestamp
                    _ts = int(time.time())
                    # poll the inverter and obtain raw data
                    logdbg2("genLoopPackets: polling inverter for data")
                    raw_packet = self.get_raw_packet()
                    logdbg2("genLoopPackets: received raw data packet: %s" % raw_packet)
                    # process raw data and return a dict that can be used as a
                    # LOOP packet
                    packet = self.process_raw_packet(raw_packet)
                    # add in special or differential fields
                    if packet:
                        # dateTime
                        if not self.use_inverter_time:
                            packet['dateTime'] = _ts
                        else:
                            packet['dateTime'] = packet['timeDate']
                        # usUnits
                        packet['usUnits'] = weewx.METRIC
                        # energy
                        # dayEnergy is cumulative by day but we need
                        # incremental values so we need to calculate it based
                        # on the last cumulative value
                        packet['energy'] = self.calculate_energy(packet['dayEnergy'],
                                                                 self.last_energy)
                        self.last_energy = packet['dayEnergy'] if 'dayEnergy' in packet else None
                        logdbg2("genLoopPackets: received loop packet: %s" % packet)
                        yield packet
                    # wait until its time to poll again
                    logdbg2("genLoopPackets: Sleeping")
                    while int(time.time()) % self.polling_interval != 0:
                        time.sleep(0.2)
                except IOError, e:
                    logerr("genLoopPackets: LOOP try #%d; error: %s" % (count + 1, e))
                    break
        logerr("genLoopPackets: LOOP max tries (%d) exceeded." % self.max_tries)
        raise weewx.RetriesExceeded("Max tries exceeded while getting LOOP data.")

    def get_raw_packet(self):
        """Get the raw loop data from the inverter."""

        _packet = {}
        for reading in self.manifest:
            _packet[reading] = self.do_cmd(reading).data
        return _packet

    def process_raw_packet(self, raw_packet):
        """Create a weewx loop packet from a raw loop data."""

        # map raw packet readings to loop packet fields using the field map
        _packet = {}
        for dest, src in self.field_map.iteritems():
            if src in raw_packet:
                _packet[dest] = raw_packet[src]
                # apply any special processing that may be required
                if src == 'isoR':
                    # isoR is reported in Mohms, we want ohms
                    try:
                        _packet[dest] *= 1000000.0
                    except TypeError:
                        # field is not numeric so leave it
                        pass
            else:
                _packet[dest] = None
        return _packet

    def do_cmd(self, reading, globall=0):
        """Send a command to the inverter and return the decoded response.

        Inputs:
            reading: One of the commands from the command vocabulary of the
                     AuroraInverter object, AuroraInverter.commands. String.
            globall: Global (globall=1) or Module (globall=0) measurements.

        Returns:
            Response Tuple with the inverters response to the command. If no
            response or response could not be decoded then (None, None, None)
            is returned.
        """

        return self.inverter.send_cmd_with_crc(reading, globall=globall)

    def getTime(self):
        """Get inverter system time and return as an epoch timestamp."""

        return self.do_cmd('timeDate').data

    def get_cumulated_energy(self, period=None):
        """Get 'cumulated' energy readings.

        Returns a dict with value for one or more periods. Valid dict keys are:
            'day'
            'week'
            'month'
            'year'
            'total'
            'partial'

        Input:
            period: Specify a single period for which cumulated energy is
                    required. If None or omitted cumulated values for all
                    periods will be returned. String, must be one of the above
                    dict keys, may be None. Default is None.
        Returns:
            Dict of requested cumulated energy values. If an invalid period is
            passed in then None is returned.
        """

        MANIFEST = {'day': 'dayEnergy',
                    'week': 'weekEnergy',
                    'month': 'monthEnergy',
                    'year': 'yearEnergy',
                    'total': 'totalEnergy',
                    'partial': 'partialEnergy'}

        _energy = {}
        if period is None:
            for _period, _reading in MANIFEST.iteritems():
                _energy[_period] = self.do_cmd(_reading).data
        elif period in MANIFEST:
            _energy[period] = self.do_cmd(period).data
        else:
            _energy = None
        return _energy

    def get_last_alarms(self):
        """Get the last four alarms."""

        return self.do_cmd('lastAlarms').data

    def get_dsp(self):
        """Get DSP data."""

        manifest = dict((k,v) for k,v in self.inverter.commands.iteritems() if v['cmd'] == 59)

        _dsp = {}
        for reading, params in manifest.iteritems():
            _dsp[reading] = self.do_cmd(reading, globall=1).data
        return _dsp

    @property
    def hardware_name(self):
        """Get the name by which this hardware is known."""

        return self.model

    @staticmethod
    def calculate_energy(newtotal, oldtotal):
        """Calculate the energy differential given two cumulative measurements."""

        if newtotal is not None and oldtotal is not None:
            if newtotal >= oldtotal:
                delta = newtotal - oldtotal
            else:
                delta = None
        else:
            delta = None
        return delta

    def _setup(self):
        """Retrieves data from the inverter and sets various properties."""

        # get and save our part number
        self.part_number = self.do_cmd('partNumber').data
        # get and save our version
        self.version = self.do_cmd('version').data
        # get and save our serial number
        self.serial_number = self.do_cmd('serialNumber').data
        # get and save our manufacture date
        self.manufacture_data = self.do_cmd('manufactureDate').data
        # get and save our firmware release number
        self.firmware_rel = self.do_cmd('firmwareRelease').data

    def _build_map_manifest(self, inverter_dict):
        """Build a field map and command manifest.

        Build a dict mapping Aurora readings to loop packet fields. Also builds
        a dict of commands to be used to obtain raw loop data from the
        inverter.

            Input:
                inverter_dict: An inverter config dict

            Returns:
                Tuple consisting of (field_map, manifest) where:

                field_map:  A is a dict mapping Aurora readings to loop packet
                            fields.
                manifest:   A dict of inverter readings and their associated
                            command parameters to be used as the raw data used
                            as the basis for a loop packet.
        """

        _manifest = []
        _field_map = {}
        _field_map_config = inverter_dict.get('FieldMap')
        for dest, src in _field_map_config.iteritems():
            if src in self.inverter.commands:
                _manifest.append(src)
                _field_map[dest] = src
            else:
                logdbg("Invalid inverter data field '%s' specified in config file. Field ignored." % src)
        return _field_map, _manifest


# ============================================================================
#                               class Aurora
# ============================================================================


class AuroraInverter(object):
    """Class to support serial comms with an Aurora PVI-6000 inverter."""


    def __init__(self, port, baudrate=19200, timeout=2.0,
                 wait_before_retry=1.0, command_delay=0.05):
        """Initialise the AuroraInverter object."""

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.wait_before_retry = wait_before_retry
        self.command_delay = command_delay
        # Aurora driver readings that I know how to obtain. Listed against
        # reading is the command and sub-command codes and applicable decode
        # function.
        self.commands = {
            'state':           {'cmd': 50, 'sub':  0, 'fn': self._dec_state},
            'partNumber':      {'cmd': 52, 'sub':  0, 'fn': self._dec_ascii},
            'version':         {'cmd': 58, 'sub':  0, 'fn': self._dec_ascii_and_state},
            'gridV':           {'cmd': 59, 'sub':  1, 'fn': self._dec_float},
            'gridC':           {'cmd': 59, 'sub':  2, 'fn': self._dec_float},
            'gridP':           {'cmd': 59, 'sub':  3, 'fn': self._dec_float},
            'frequency':       {'cmd': 59, 'sub':  4, 'fn': self._dec_float},
            'bulkV':           {'cmd': 59, 'sub':  5, 'fn': self._dec_float},
            'leakDcC':         {'cmd': 59, 'sub':  6, 'fn': self._dec_float},
            'leakC':           {'cmd': 59, 'sub':  7, 'fn': self._dec_float},
            'str1P':           {'cmd': 59, 'sub':  8, 'fn': self._dec_float},
            'str2P':           {'cmd': 59, 'sub':  9, 'fn': self._dec_float},
            'inverterT':       {'cmd': 59, 'sub': 21, 'fn': self._dec_float},
            'boosterT':        {'cmd': 59, 'sub': 22, 'fn': self._dec_float},
            'str1V':           {'cmd': 59, 'sub': 23, 'fn': self._dec_float},
            'str1C':           {'cmd': 59, 'sub': 25, 'fn': self._dec_float},
            'str2V':           {'cmd': 59, 'sub': 26, 'fn': self._dec_float},
            'str2C':           {'cmd': 59, 'sub': 27, 'fn': self._dec_float},
            'gridDcV':         {'cmd': 59, 'sub': 28, 'fn': self._dec_float},
            'gridDcFreq':      {'cmd': 59, 'sub': 29, 'fn': self._dec_float},
            'isoR':            {'cmd': 59, 'sub': 30, 'fn': self._dec_float},
            'bulkDcV':         {'cmd': 59, 'sub': 31, 'fn': self._dec_float},
            'gridAvV':         {'cmd': 59, 'sub': 32, 'fn': self._dec_float},
            'bulkMidV':        {'cmd': 59, 'sub': 33, 'fn': self._dec_float},
            'gridNV':          {'cmd': 59, 'sub': 34, 'fn': self._dec_float},
            'dayPeakP':        {'cmd': 59, 'sub': 35, 'fn': self._dec_float},
            'peakP':           {'cmd': 59, 'sub': 36, 'fn': self._dec_float},
            'gridNPhV':        {'cmd': 59, 'sub': 38, 'fn': self._dec_float},
            'serialNumber':    {'cmd': 63, 'sub':  0, 'fn': self._dec_ascii},
            'manufactureDate': {'cmd': 65, 'sub':  0, 'fn': self._dec_week_year},
            'timeDate':        {'cmd': 70, 'sub':  0, 'fn': self._dec_ts},
            'firmwareRelease': {'cmd': 72, 'sub':  0, 'fn': self._dec_ascii_and_state},
            'dayEnergy':       {'cmd': 78, 'sub':  0, 'fn': self._dec_int},
            'weekEnergy':      {'cmd': 78, 'sub':  1, 'fn': self._dec_int},
            'monthEnergy':     {'cmd': 78, 'sub':  3, 'fn': self._dec_int},
            'yearEnergy':      {'cmd': 78, 'sub':  4, 'fn': self._dec_int},
            'totalEnergy':     {'cmd': 78, 'sub':  5, 'fn': self._dec_int},
            'partialEnergy':   {'cmd': 78, 'sub':  6, 'fn': self._dec_int},
            'lastAlarms':      {'cmd': 86, 'sub':  0, 'fn': self._dec_alarms}
        }

    def open_port(self):
        """Open a serial port."""

        self.serial_port = serial.Serial(port=self.port, baudrate=self.baudrate,
                                         timeout=self.timeout)
        logdbg("Opened serial port %s; baud %d; timeout %.2f" % (self.port,
                                                                 self.baudrate,
                                                                 self.timeout))

    def close_port(self):
        """Close a serial port."""

        try:
            # This will cancel any pending loop:
            self.write('\n')
        except:
            pass
        self.serial_port.close()

    def write(self, data):
        """Send data to the inverter.

        Sends a data string to the inverter.

            Input:
                data: A string containing a sequence of bytes to be sent to the
                      inverter. Usually a sequence of bytes that have been
                      packed into a string.
        """

        try:
            N = self.serial_port.write(data)
        except serial.serialutil.SerialException, e:
            logerr("SerialException on write.")
            logerr("  ***** %s" % e)
            # reraise as a weewx error I/O error:
            raise weewx.WeeWxIOError(e)
        # Python version 2.5 and earlier returns 'None', so it cannot be used
        # to test for completion.
        if N is not None and N != len(data):
           raise weewx.WeeWxIOError("Expected to write %d chars; sent %d instead" % (len(data),
                                                                                      N))

    def read(self, bytes=8):
        """Read data from the inverter.

        Read a given number of bytes from the inverter. If the incorrect number
        of bytes is received then raise a WeeWxIOError().

            Input:
                bytes: The number of bytes to be read.

            Returns:
                A string of length bytes containing the data read from the
                inverter.
        """

        try:
            _buffer = self.serial_port.read(bytes)
        except serial.serialutil.SerialException, e:
            logerr("SerialException on read.")
            logerr("  ***** %s" % e)
            logerr("  ***** Is there a competing process running??")
            raise
            # reraise as a weewx error I/O error:
            raise weewx.WeeWxIOError(e)
        N = len(_buffer)
        if N != bytes:
            raise weewx.WeeWxIOError("Expected to read %d bytes; got %d instead" % (bytes,
                                                                                    N))
        return _buffer

    def send_cmd_with_crc(self, reading, globall=0, address=2, max_tries=3):
        """Send a command with CRC to the inverter and return the response.


            Inputs:
                reading:    The inverter reading being sought. String.
                globall:
                address:    The inverter address to be used, normally 2.
                max_tries:  The maximum number of attempts to send the data
                            before an error is raised.

            Returns:
                The decoded inverter response to the command as a Response
                Tuple.

        """

        # get the applicable command and sub-command codes
        cmd_num = self.commands[reading]['cmd']
        sub_cmd = self.commands[reading]['sub']
        # assemble our command
        s = struct.Struct('4B')
        _b = s.pack(*[b for b in (address, cmd_num, sub_cmd, globall)])
        # pad the command to 8 bytes
        _b_padded = self.pad(_b, 8)
        # add the CRC
        _data_with_crc = _b_padded + self.word2struct(self.crc16(_b_padded))
        # now send the data retrying up to max_tries times
        for count in xrange(max_tries):
            logdbg2("sent %s" % format_byte_to_hex(_data_with_crc))
            try:
                self.write(_data_with_crc)
                # wait before reading
                time.sleep(self.command_delay)
                # look for the response
                _resp = self.read_with_crc()
                decode_fn = self.commands[reading]['fn']
                return decode_fn(_resp)
            except weewx.WeeWxIOError:
                pass
            logdbg("send_cmd_with_crc: try #%d" % (count + 1,))
        logerr("Unable to send or receive data to/from the inverter")
        raise weewx.WeeWxIOError("Unable to send or receive data to/from the inverter")

    def read_with_crc(self, bytes=8):
        """Read an inverter response with CRC and return the data.

        Read a response from the inverter, check the CRC and if valid strip the
        CRC and return the data pay load.
            Input:
                bytes: The number of bytes to be read.

            Returns:
                A string of length bytes containing the data read from the
                inverter.
        """

        # read the response
        _response = self.read(bytes=bytes)
        # log the hex bytes received
        logdbg2("read %s" % format_byte_to_hex(_response))
        # check the CRC and strip out the pay load
        return self.strip_crc16(_response)

    @staticmethod
    def crc16(buf):
        """Calculate a CCITT CRC16 checksum of a series of bytes.

        Calculated as per the Checksum calculation section of the Aurora PV
        Inverter Series Communications Protocol.

        Use struct module to convert the input string to a sequence of bytes.
        Could use bytearray but that was not introduced until python 2.6.

        Input:
            buf: string of binary packed data for which the CRC is to be
                 calculated

        Returns:
            A two byte string containing the CRC.
        """

        POLY = 0x8408
        crc = 0xffff

        # if our input is nothing then that is simple
        if len(buf) == 0:
            return ~crc & 0xffff

        # Get a Struct object so we can unpack our input string. Our input
        # could be of any length so construct our Struct format string based on
        # the length of the input string.
        _format = ''.join(['B' for b in range(len(buf))])
        s = struct.Struct(_format)
        # unpack the input string into our sequence of bytes
        _bytes = s.unpack(buf)

        # now calculate the CRC of our sequence of bytes
        for _byte in _bytes:
            for i in range(8):
                if ((crc & 0x0001) ^ (_byte & 0x0001)):
                    crc = ((crc >> 1) ^ POLY) & 0xffff
                else:
                    crc >>= 1
                _byte >>= 1

        return ~crc & 0xffff

    @staticmethod
    def strip_crc16(buffer):
        """Strip CRC bytes from an inverter response."""

        # get the data payload
        data = buffer[:-2]
        # get the CRC bytes
        crc_bytes = buffer[-2:]
        # calculate the CRC of the received data
        crc = AuroraInverter.word2struct(AuroraInverter.crc16(data))
        # if our calculated CRC == received CRC then our data is valid and
        # return it, otherwise raise a CRCError
        if crc == crc_bytes:
            return data
        else:
            logerr("Inverter response failed CRC check:")
            logerr("  ***** response=%s" % (format_byte_to_hex(buffer)))
            logerr("  *****     data=%s        CRC=%s  expected CRC=%s" % (format_byte_to_hex(data),
                                                                           format_byte_to_hex(crc_bytes),
                                                                           format_byte_to_hex(crc)))
            raise weewx.CRCError("Inverter response failed CRC check")

    @staticmethod
    def word2struct(i):
        """Take a 2 byte word and reverse the byte order.

            Input:
                i: A 2 byte string containing the bytes to be processed.

            Returns:
                A 2 byte string consisting of the input bytes but in reverse
                order.
        """

        s = struct.Struct('2B')
        b = s.pack(i & 0xff, i // 256)
        return b

    @staticmethod
    def pad(buf, size):
        """Pad a string with nulls.

        Pad a string with nulls to make it a given size. If the string to be
        padded is longer than size then an exception is raised.

            Inputs:
                buff: The string to be padded
                size: The length of the padded string

            Returns:
                A padded string of length size.
        """

        PAD = ''.join(['\x00' for a in range(size)])

        if size > len(PAD):
            raise DataFormatError("pad: string to be padded must be <= %d characters in length" % size)
        return buf + PAD[:(size-len(buf))]

    @staticmethod
    def _dec_state(v):
        """Decode an inverter state request response.

        To be written.
        """

        pass

    @staticmethod
    def _dec_ascii(v):
        """Decode inverter response containing ASCII characters only.

        Decode a 6 byte response in the following format:

        byte 0: character 6 - most significant character
        byte 1: character 5
        byte 2: character 4
        byte 3: character 3
        byte 4: character 2
        byte 5: character 1 - least significant character

        Input:
            v: bytearray containing the 6 byte response

        Returns:
            A ResponseTuple where the transmission and global attributes are None
            and the data attribute is a 6 character ASCII string.
        """

        try:
            return ResponseTuple(None, None, str(v[0:6]))
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_ascii_and_state(v):
        """Decode inverter response containing ASCII characters and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: par 1
        byte 3: par 2
        byte 4: par 3
        byte 5: par 4

        where par 1..par 4 are ASCII characters used to determine the inverter
        version. To decode par characters refer to Aurora PV Inverter Series
        Communication Protocol rel 4.7 command 58.

        Input:
            v: bytearray containing the 6 byte response

        Returns:
            A ResponseTuple where the data attribute is a 4 character ASCII string.
        """

        try:
            return ResponseTuple(ord(v[0]), ord(v[1]), str(v[2:4]))
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_float(v):
        """Decode inverter response containing 4 byte float and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: val3
        byte 3: val2
        byte 4: val1
        byte 5: val0

        ANSI standard format float:

        bit bit         bit bit                             bit
        31  30          23  22                              0
        <S> <--Exponent-->  <------------Mantissa----------->

        val3 = bits 24-31
        val2 = bits 16-23
        val1 = bits  8-15
        val0 = bits  0-7

        where

            float = (-1)**S * 2**(Exponent-127) * 1.Mantissa

        Refer to the Aurora PV Inverter Series Communication Protocol rel 4.7
        command 59.

        Input:
            v: bytearray containing the 6 bytes to convert

        Returns:
            A ResponseTuple where the data attribute is a 4 byte float.
        """

        try:
            return ResponseTuple(ord(v[0]), ord(v[1]), struct.unpack('!f', v[2:])[0])
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_week_year(v):
        """Decode inverter response containing week and year and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: most significant week digit
        byte 3: least significant week digit
        byte 4: most significant year digit
        byte 5: least significant year digit

        Input:
            v: bytearray containing the 6 byte response

        Returns:
           A ResponseTuple where data attribute is a 2 way tuple of (week, year).
        """

        try:
            return ResponseTuple(ord(v[0]), ord(v[1]), (int(v[2:4]), int(v[4:6])))
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_ts(v):
        """Decode inverter response containing timestamp and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: time3
        byte 3: time2
        byte 4: time1
        byte 5: time0

        where

            time-date = time3 * 2**24 + time2 * 2**16 + time1 * 2**8 + time0
            2**x = 2 raised to the power of x
            time-date = number of seconds since midnight 1 January 2000

        Refer to the Aurora PV Inverter Series Communication Protocol rel 4.7
        command 70.

        Since weewx uses epoch timestamps the calculated date-time value is
        converted to an epoch timestamp before being returned in a ResponseTuple.

        Input:
            v: bytearray containing the 6 bytes to convert

        Returns:
            A ResponseTuple where the data attribute is an epoch timestamp.
        """

        try:
            return ResponseTuple(ord(v[0]), ord(v[1]), AuroraInverter._dec_int(v).data + 946648800)
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_int(v):
        """Decode inverter response containing 4 byte integer and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: int3
        byte 3: int2
        byte 4: int1
        byte 5: int0

        where

            integer value = int3 * 2**24 + int2 * 2**16 + int1 * 2**8 + int0
            2**x = 2 raised to the power of x

        Refer to the Aurora PV Inverter Series Communication Protocol rel 4.7
        command 78

        Input:
            v: bytearray containing the 6 bytes to convert

        Returns:
            A ResponseTuple where the data attribute is a 4 byte integer.
        """

        try:
            _int = ord(v[2]) * 2**24 + ord(v[3]) * 2**16 + ord(v[4]) * 2**8 + ord(v[5])
            return ResponseTuple(ord(v[0]), ord(v[1]), _int)
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)

    @staticmethod
    def _dec_alarms(v):
        """Decode inverter response contain last 4 alarms and inverter state.

        Decode a 6 byte response in the following format:

        byte 0: transmission state
        byte 1: global state
        byte 2: alarm code 1 (oldest)
        byte 3: alarm code 2
        byte 4: alarm code 3
        byte 5: alarm code 4 (latest)

        Input:
            v: bytearray containing the 6 byte response

        Returns:
           A ResponseTuple where data attribute is a 4 way tuple of alarm codes.
        """

        try:
            _alarms = tuple([int(a) for a in v[2:6]])
            return ResponseTuple(ord(v[0]), ord(v[1]), _alarms)
        except (IndexError, TypeError):
            return ResponseTuple(None, None, None)


# ============================================================================
#                             Utility functions
# ============================================================================


def format_byte_to_hex(bytes):
    """Format a sequence of bytes as a string of space separated hex bytes.

        Input:
            bytes: A string or sequence containing the bytes to be formatted.

        Returns:
            A string of space separated hex digit pairs representing the input
            byte sequence.
    """

    return ' '.join(['%02X' % ord(b) for b in bytes])

# ============================================================================
#                            class ResponseTuple
# ============================================================================

# An inverter response consists of 8 bytes as follows:
#
#   byte 0: transmission state
#   byte 1: global state
#   byte 2: data
#   byte 3: data
#   byte 4: data
#   byte 5: data
#   byte 6: CRC low byte
#   byte 7: CRC high byte
#
# The CRC bytes are stripped away by the Aurora class class when validating the
# inverter response. The four data bytes may represent ASCII characters, a
# 4 byte float or some other coded value. An inverter response can be
# represented as a 3-way tuple called a response tuple:
#
# Item  Attribute       Meaning
# 0     transmission    The transmission state code (an integer)
# 1     global          The global state code (an integer)
# 2     data            The four bytes in decoded form (eg 4 character ASCII string, ANSI float)
#
# Some inverter responses do not include the transmission state and global
# state, in these cases those response tuple attributes are set to None.
#
# It is also valid to have a data attribute of None. In these cases the data
# could not be decoded and the driver will handle this appropriately.

class ResponseTuple(tuple):

    def __new__(cls, *args):
        return tuple.__new__(cls, args)

    @property
    def transmission_state(self):
        return self[0]

    @property
    def global_state(self):
        return self[1]

    @property
    def data(self):
        return self[2]


# ============================================================================
#                          Main Entry for Testing
# ============================================================================

# define a main entry point for basic testing without the weewx engine and
# service overhead. To invoke this:
#
# PYTHONPATH=/home/weewx/bin python /home/weewx/bin/user/aurora.py
#
# Driver will then output loop packets until execution is halted.
#

if __name__ == '__main__':

    # python imports
    import optparse

    # weewx imports
    import weecfg

    def sort(rec):
        return ", ".join(["%s: %s" % (k, rec.get(k)) for k in sorted(rec,
                                                                     key=str.lower)])

    usage = """%prog [options] [--help]"""

    syslog.openlog('aurora', syslog.LOG_PID | syslog.LOG_CONS)
    syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--config', dest='config_path', type=str,
                      metavar="CONFIG_FILE",
                      help="Use configuration file CONFIG_FILE.")
    parser.add_option('--version', dest='version', action='store_true',
                      help='Display driver version.')
    parser.add_option('--loop', dest='loop', action='store_true',
                      help='Output inverter loop data.')
    parser.add_option('--dump', dest='dump', action='store_true',
                      help='Dump inverter readings to screen.')
    (options, args) = parser.parse_args()

    if options.version:
        print "Aurora driver version %s" % DRIVER_VERSION
        exit(0)

    # get config_dict to use
    config_path, config_dict = weecfg.read_config(options.config_path, args)
    print "Using configuration file %s" % config_path

    # get a config dict for the inverter
    aurora_dict = config_dict.get('Aurora', None)
    # get an AuroraDriver object
    inverter = AuroraDriver(aurora_dict)

    if options.loop:
        while True:
            for packet in inverter.genLoopPackets():
                print "LOOP:  ", timestamp_to_string(packet['dateTime']), sort(packet)
        exit(0)

    if options.dump:
        print "%17s: %s" % ("Part Number", inverter.part_number)
        print "%17s: %s" % ("Version", inverter.version)
        print "%17s: %s" % ("Serial Number", inverter.serial_number)
        print "%17s: %s" % ("Manufacture Date", inverter.manufacture_data)
        print "%17s: %s" % ("Firmware Release", inverter.firmware_rel)
        for reading in inverter.manifest:
            print "%17s: %s" % (reading, inverter.do_cmd(reading).data)
        exit(0)
